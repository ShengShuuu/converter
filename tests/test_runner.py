"""process_conversion (page-by-page batch) with the OCR stage stubbed by the synthetic TSV fixtures (no binaries)."""

from decimal import Decimal
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from converter.extraction import parse_tesseract_tsv
from converter.ocr.pipeline import PageOcrResult
from converter.ocr.tools import OcrToolError
from converter.store import repositories as repo
from converter.store.db import init_db
from converter.worker import runner

FIXTURES = Path(__file__).parent / "fixtures"
GARBAGE_TSV = (
    "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
    "5\t1\t1\t1\t1\t1\t150\t600\t170\t60\t96.5\tCOLLECTION\n"
    "5\t1\t1\t1\t1\t2\t340\t600\t170\t60\t95.0\tRECEIPT\n"
)


def image_size(path: Path) -> tuple[int, int]:
    with Image.open(path) as image:
        return image.size


def fixture_words(name: str):
    return parse_tesseract_tsv((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def env(tmp_path):
    db_path = tmp_path / "converter.db"
    init_db(db_path)
    return db_path, tmp_path


def upload(env, suffix=".pdf") -> int:
    db_path, data_dir = env
    conversion = repo.create_conversion(db_path, f"scan{suffix}", suffix)
    source = data_dir / conversion.source_path
    source.parent.mkdir(parents=True)
    source.write_bytes(b"%PDF-1.4 stub" if suffix == ".pdf" else b"stub")
    return conversion.id


# def stub_ocr(monkeypatch, tmp_path, words, *, pages=1, rotated_by=0, error=None):  -- v1: one page, same words
def stub_ocr(monkeypatch, tmp_path, words, *, pages=1, rotated_by=0, error=None, page_words=None, page_errors=None):
    """Replace the OCR stage (writing a fake raster into work_dir like the real one); returns the calls made.

    page_words / page_errors, keyed by page number, override words / error for single pages.
    """
    raster = tmp_path / "raster.png"
    Image.fromarray(np.full((40, 30, 3), 255, np.uint8)).save(raster)
    calls = []
    page_words = page_words or {}
    page_errors = page_errors or {}

    def result(page_number, work_dir):
        work_dir.mkdir(parents=True, exist_ok=True)
        (work_dir / f"page-{page_number:03d}.png").write_bytes(b"raster")
        page_error = page_errors.get(page_number, error)
        if page_error:
            raise page_error
        words_on_page = page_words.get(page_number, words)
        return PageOcrResult(page_number, words_on_page, None, rotated_by, raster, raster, {"rasterize": 0.1})

    monkeypatch.setattr(runner, "page_count", lambda path: pages)
    monkeypatch.setattr(
        runner, "ocr_pdf_page", lambda path, page, work: calls.append(("pdf", page)) or result(page, work)
    )
    monkeypatch.setattr(runner, "ocr_image_file", lambda path, work: calls.append(("image", 1)) or result(1, work))
    return calls


def page(env, conversion_id, page_number=1) -> repo.Page:
    return repo.get_page(env[0], conversion_id, page_number)


def statuses(env, conversion_id) -> list[str]:
    return [p.status for p in repo.list_pages(env[0], conversion_id)]


def test_sales_order_pdf_is_extracted(env, monkeypatch, tmp_path):
    words = fixture_words("odoo_sale_order_S08824.tsv")
    calls = stub_ocr(monkeypatch, tmp_path, words)
    conversion_id = upload(env)
    db_path, data_dir = env

    runner.process_conversion(conversion_id, db_path, data_dir)

    conversion = repo.get_conversion(db_path, conversion_id)
    stored = page(env, conversion_id)
    assert calls == [("pdf", 1)]
    assert (conversion.status, conversion.error, conversion.page_count) == ("done", None, 1)
    assert (stored.status, stored.error, stored.profile_name) == ("done", None, "odoo_sale_order_v1")
    assert stored.order.order_ref == "S08824"
    assert stored.order.lines[1].subtotal == Decimal("5760")
    assert stored.order == stored.extracted_order
    assert stored.classifier_score >= runner.CLASSIFIER_MIN_RATIO
    assert (stored.page_width, stored.page_height, stored.rotated_by) == (30, 40, 0)
    output_dir = repo.page_dir(data_dir, conversion_id, 1)
    assert runner.load_words(output_dir / runner.WORDS_FILE) == words
    assert image_size(output_dir / runner.DISPLAY_IMAGE_FILE) == (30, 40)


def test_vat_layout_without_total_bar_is_extracted_with_flags(env, monkeypatch, tmp_path):
    stub_ocr(monkeypatch, tmp_path, fixture_words("odoo_sale_order_S08824_vat_no_total_bar.tsv"))
    conversion_id = upload(env)

    runner.process_conversion(conversion_id, *env)

    stored = page(env, conversion_id)
    assert stored.status == "done"
    assert "not_found:total" in stored.order.flags


def test_display_image_is_rotated_like_the_ocr_image(env, monkeypatch, tmp_path):
    stub_ocr(monkeypatch, tmp_path, fixture_words("odoo_sale_order_S08824.tsv"), rotated_by=90)
    conversion_id = upload(env)

    runner.process_conversion(conversion_id, *env)

    stored = page(env, conversion_id)
    assert (stored.page_width, stored.page_height, stored.rotated_by) == (40, 30, 90)
    assert image_size(runner.display_image_path(env[1], conversion_id, 1)) == (40, 30)


# def test_image_upload_uses_image_ocr(...)  -- superseded by the one-page-batch test below
def test_image_upload_is_a_one_page_batch(env, monkeypatch, tmp_path):
    calls = stub_ocr(monkeypatch, tmp_path, fixture_words("odoo_sale_order_S08824.tsv"), pages=7)
    conversion_id = upload(env, ".jpg")

    runner.process_conversion(conversion_id, *env)

    assert calls == [("image", 1)]
    assert repo.get_conversion(env[0], conversion_id).page_count == 1
    assert statuses(env, conversion_id) == ["done"]


def test_other_document_is_not_a_sales_order(env, monkeypatch, tmp_path):
    stub_ocr(monkeypatch, tmp_path, parse_tesseract_tsv(GARBAGE_TSV))
    conversion_id = upload(env)

    runner.process_conversion(conversion_id, *env)

    stored = page(env, conversion_id)
    assert repo.get_conversion(env[0], conversion_id).status == "done"
    assert stored.status == "not_sales_order"
    assert stored.order is None and stored.classifier_score == 0.0
    assert runner.saved_words_path(env[1], conversion_id, 1).is_file()


def test_extract_anyway_reuses_saved_words_without_ocr(env, monkeypatch, tmp_path):
    stub_ocr(monkeypatch, tmp_path, parse_tesseract_tsv(GARBAGE_TSV))
    conversion_id = upload(env)
    runner.process_conversion(conversion_id, *env)
    calls = stub_ocr(monkeypatch, tmp_path, [], error=AssertionError("OCR must not run again"))

    runner.extract_from_saved_words(conversion_id, 1, *env)

    stored = page(env, conversion_id)
    assert calls == []
    assert stored.status == "done" and "table_header_not_found" in stored.order.flags


def test_ocr_tool_failure_marks_the_page_failed(env, monkeypatch, tmp_path):
    stub_ocr(monkeypatch, tmp_path, [], error=OcrToolError("tesseract exited 1: boom"))
    conversion_id = upload(env)

    runner.process_conversion(conversion_id, *env)

    stored = page(env, conversion_id)
    assert (stored.status, stored.error) == ("failed", "tesseract exited 1: boom")
    assert repo.get_conversion(env[0], conversion_id).status == "done"


def test_unexpected_error_marks_the_page_failed(env, monkeypatch, tmp_path):
    stub_ocr(monkeypatch, tmp_path, [], error=KeyError("level"))
    conversion_id = upload(env)

    runner.process_conversion(conversion_id, *env)

    stored = page(env, conversion_id)
    assert stored.status == "failed" and stored.error.startswith("Unexpected error: KeyError")


# def test_multi_page_pdf_is_rejected_without_ocr(...)  -- v1 rejected every PDF with more than one page


@pytest.mark.parametrize(
    ("pages", "message"),
    [
        (0, "This file is empty or unreadable: no pages were found in it."),
        (51, "This file has 51 pages, which is too many (max 50). Split it and upload the parts."),
    ],
)
def test_empty_or_oversized_pdf_fails_without_ocr(env, monkeypatch, tmp_path, pages, message):
    calls = stub_ocr(monkeypatch, tmp_path, [], pages=pages)
    conversion_id = upload(env)

    runner.process_conversion(conversion_id, *env)

    conversion = repo.get_conversion(env[0], conversion_id)
    assert calls == []
    assert (conversion.status, conversion.error, conversion.page_count) == ("failed", message, None)
    assert repo.list_pages(env[0], conversion_id) == []


def test_fifty_pages_is_accepted(env, monkeypatch, tmp_path):
    calls = stub_ocr(monkeypatch, tmp_path, parse_tesseract_tsv(GARBAGE_TSV), pages=50)
    conversion_id = upload(env)

    runner.process_conversion(conversion_id, *env)

    assert [page_number for _, page_number in calls] == list(range(1, 51))
    assert repo.page_summary(env[0], conversion_id).counts["not_sales_order"] == 50


def test_page_count_tool_failure_fails_the_conversion(env, monkeypatch, tmp_path):
    stub_ocr(monkeypatch, tmp_path, [])

    def broken_page_count(path):
        raise OcrToolError("pdfinfo exited 1: Syntax Error")

    monkeypatch.setattr(runner, "page_count", broken_page_count)
    conversion_id = upload(env)

    runner.process_conversion(conversion_id, *env)

    conversion = repo.get_conversion(env[0], conversion_id)
    assert (conversion.status, conversion.error) == ("failed", "pdfinfo exited 1: Syntax Error")


def test_conversion_deleted_mid_processing_does_not_raise(env, monkeypatch, tmp_path):
    stub_ocr(monkeypatch, tmp_path, [], error=OcrToolError("boom"))
    conversion_id = upload(env)
    repo.delete_conversion(env[0], env[1], conversion_id)

    runner.process_conversion(conversion_id, *env)


# --- multi-page batches ---


def three_page_batch(monkeypatch, tmp_path, **overrides):
    """Page 1: S08824 Sales Order, page 2: another document, page 3: the S07002 letterhead Sales Order."""
    page_words = {
        1: fixture_words("odoo_sale_order_S08824.tsv"),
        2: parse_tesseract_tsv(GARBAGE_TSV),
        3: fixture_words("odoo_sale_order_S07002_letterhead.tsv"),
    }
    return stub_ocr(monkeypatch, tmp_path, [], pages=3, page_words=page_words, **overrides)


def test_every_page_is_classified_and_extracted_independently(env, monkeypatch, tmp_path):
    calls = three_page_batch(monkeypatch, tmp_path)
    conversion_id = upload(env)

    runner.process_conversion(conversion_id, *env)

    assert calls == [("pdf", 1), ("pdf", 2), ("pdf", 3)]
    assert repo.get_conversion(env[0], conversion_id).status == "done"
    assert statuses(env, conversion_id) == ["done", "not_sales_order", "done"]
    assert page(env, conversion_id, 1).order.order_ref == "S08824"
    assert page(env, conversion_id, 3).order.order_ref == "S07002"
    for number in (1, 2, 3):
        assert runner.saved_words_path(env[1], conversion_id, number).is_file()
        assert runner.display_image_path(env[1], conversion_id, number).is_file()


def test_extracted_order_carries_its_real_page_number(env, monkeypatch, tmp_path):
    three_page_batch(monkeypatch, tmp_path)
    conversion_id = upload(env)

    runner.process_conversion(conversion_id, *env)

    third = page(env, conversion_id, 3).order
    assert third.source_pages == [3]
    assert third.lines and {line.bbox.page for line in third.lines} == {3}
    assert page(env, conversion_id, 1).order.source_pages == [1]


def test_one_failing_page_does_not_stop_the_batch(env, monkeypatch, tmp_path):
    three_page_batch(monkeypatch, tmp_path, page_errors={2: OcrToolError("pdftoppm exited 99: bad page")})
    conversion_id = upload(env)

    runner.process_conversion(conversion_id, *env)

    assert statuses(env, conversion_id) == ["done", "failed", "done"]
    assert page(env, conversion_id, 2).error == "pdftoppm exited 99: bad page"
    conversion = repo.get_conversion(env[0], conversion_id)
    assert (conversion.status, conversion.error) == ("done", None)


def test_unexpected_error_on_one_page_does_not_stop_the_batch(env, monkeypatch, tmp_path):
    three_page_batch(monkeypatch, tmp_path, page_errors={1: ValueError("odd raster")})
    conversion_id = upload(env)

    runner.process_conversion(conversion_id, *env)

    assert statuses(env, conversion_id) == ["failed", "not_sales_order", "done"]
    assert page(env, conversion_id, 1).error == "Unexpected error: ValueError: odd raster"


def test_work_dirs_are_removed_even_when_a_page_fails(env, monkeypatch, tmp_path):
    three_page_batch(monkeypatch, tmp_path, page_errors={2: OcrToolError("boom")})
    conversion_id = upload(env)

    runner.process_conversion(conversion_id, *env)

    for number in (1, 2, 3):
        assert not (repo.page_dir(env[1], conversion_id, number) / runner.WORK_SUBDIR).exists()


def test_retry_rereads_only_the_failed_pages(env, monkeypatch, tmp_path):
    three_page_batch(monkeypatch, tmp_path, page_errors={2: OcrToolError("boom")})
    conversion_id = upload(env)
    runner.process_conversion(conversion_id, *env)

    def edit(order, confirmed):
        order.customer = "EDITED"
        confirmed.add("customer")

    repo.update_order(env[0], conversion_id, 1, edit)
    edited_first = page(env, conversion_id, 1)
    calls = three_page_batch(monkeypatch, tmp_path)

    assert repo.requeue_failed_pages(env[0], conversion_id) == 1
    runner.process_conversion(conversion_id, *env)

    assert calls == [("pdf", 2)]
    assert statuses(env, conversion_id) == ["done", "not_sales_order", "done"]
    first = page(env, conversion_id, 1)
    assert first.order == edited_first.order and first.order.customer == "EDITED"
    assert first.confirmed_fields == {"customer"}
    assert repo.get_conversion(env[0], conversion_id).page_count == 3


def test_conversion_deleted_mid_batch_stops_and_leaves_no_files(env, monkeypatch, tmp_path):
    calls = three_page_batch(monkeypatch, tmp_path)
    conversion_id = upload(env)
    stubbed_ocr = runner.ocr_pdf_page

    def ocr_then_delete(path, page_number, work_dir):
        result = stubbed_ocr(path, page_number, work_dir)
        if page_number == 2:
            repo.delete_conversion(env[0], env[1], conversion_id)
        return result

    monkeypatch.setattr(runner, "ocr_pdf_page", ocr_then_delete)

    runner.process_conversion(conversion_id, *env)

    assert [page_number for _, page_number in calls] == [1, 2]
    assert not repo.conversion_dir(env[1], conversion_id).exists()
    with pytest.raises(repo.NotFoundError):
        repo.get_conversion(env[0], conversion_id)


def test_request_stop_halts_the_batch_between_pages(env, monkeypatch, tmp_path):
    calls = three_page_batch(monkeypatch, tmp_path)
    conversion_id = upload(env)
    stubbed_ocr = runner.ocr_pdf_page

    def ocr_then_stop(path, page_number, work_dir):
        runner.request_stop()
        return stubbed_ocr(path, page_number, work_dir)

    monkeypatch.setattr(runner, "ocr_pdf_page", ocr_then_stop)
    try:
        runner.process_conversion(conversion_id, *env)
    finally:
        runner.reset_stop_request()

    assert [page_number for _, page_number in calls] == [1]
    assert statuses(env, conversion_id) == ["done", "queued", "queued"]
    assert repo.get_conversion(env[0], conversion_id).status == "processing"


def test_reset_stop_request_lets_a_new_batch_run(env, monkeypatch, tmp_path):
    calls = three_page_batch(monkeypatch, tmp_path)
    conversion_id = upload(env)
    runner.request_stop()
    runner.reset_stop_request()

    runner.process_conversion(conversion_id, *env)

    assert len(calls) == 3
