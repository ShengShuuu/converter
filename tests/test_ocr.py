"""OCR-stage unit tests: synthetic images and stubbed binary output only, no real scans or installed binaries needed."""

import subprocess
from pathlib import Path

import numpy as np
import pytest

from converter.ocr import pipeline, rasterize, tesseract, tools
from converter.ocr.preprocess import invert_dark_bands, remove_red_ink, rotate_clockwise
from converter.ocr.tools import OcrToolError

OSD_OUTPUT = (
    "Page number: 0\nOrientation in degrees: 90\nRotate: 270\n"
    "Orientation confidence: 7.30\nScript: Latin\nScript confidence: 2.11\n"
)
TSV_OUTPUT = (
    "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
    "1\t1\t0\t0\t0\t0\t0\t0\t2480\t3509\t-1\t\n"
    "5\t1\t1\t1\t1\t1\t150\t600\t170\t60\t96.5\tOrder\n"
)


def stub_run_tool(monkeypatch, module, stdout="", error=None):
    calls = []

    def fake_run_tool(args, timeout=tools.DEFAULT_TIMEOUT_SECONDS):
        calls.append(args)
        if error:
            raise error
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(module, "run_tool", fake_run_tool)
    return calls


def test_binaries_come_from_env_vars(monkeypatch):
    monkeypatch.setenv("POPPLER_PATH", "/opt/poppler/bin")
    monkeypatch.setenv("TESSERACT_CMD", "/opt/tesseract/tesseract")
    assert tools.poppler_binary("pdftoppm") == str(Path("/opt/poppler/bin") / "pdftoppm")
    assert tools.tesseract_binary() == "/opt/tesseract/tesseract"


def test_binaries_fall_back_to_path(monkeypatch):
    monkeypatch.delenv("POPPLER_PATH", raising=False)
    monkeypatch.delenv("TESSERACT_CMD", raising=False)
    assert tools.poppler_binary("pdfinfo") == "pdfinfo"
    assert tools.tesseract_binary() == "tesseract"


def test_run_tool_reports_missing_binary_as_ocr_tool_error():
    with pytest.raises(OcrToolError, match="not found"):
        tools.run_tool(["definitely-not-an-installed-ocr-binary"])


def test_page_count_parses_pdfinfo(monkeypatch):
    calls = stub_run_tool(monkeypatch, rasterize, stdout="Title: x\nPages:           10\nEncrypted: no\n")
    assert rasterize.page_count(Path("batch.pdf")) == 10
    assert isinstance(calls[0], list)


def test_page_count_without_pages_line_is_an_error(monkeypatch):
    stub_run_tool(monkeypatch, rasterize, stdout="Title: x\n")
    with pytest.raises(OcrToolError):
        rasterize.page_count(Path("batch.pdf"))


def test_rasterize_page_passes_page_range_and_dpi_as_arguments(monkeypatch, tmp_path):
    calls = stub_run_tool(monkeypatch, rasterize)
    (tmp_path / "page-003.png").write_bytes(b"png")
    assert rasterize.rasterize_page(Path("a; rm -rf x.pdf"), 3, tmp_path) == tmp_path / "page-003.png"
    args = calls[0]
    assert args[args.index("-f") + 1] == "3" and args[args.index("-l") + 1] == "3"
    assert args[args.index("-r") + 1] == "300"
    assert "a; rm -rf x.pdf" in args


def test_rasterize_page_without_output_image_is_an_error(monkeypatch, tmp_path):
    stub_run_tool(monkeypatch, rasterize)
    with pytest.raises(OcrToolError, match="no image"):
        rasterize.rasterize_page(Path("batch.pdf"), 1, tmp_path)


def test_detect_orientation_parses_osd(monkeypatch):
    stub_run_tool(monkeypatch, tesseract, stdout=OSD_OUTPUT)
    assert tesseract.detect_orientation(Path("p.png")) == tesseract.Orientation(270, 7.3)


def test_detect_orientation_is_unknown_when_osd_fails(monkeypatch):
    stub_run_tool(monkeypatch, tesseract, error=OcrToolError("Too few characters"))
    assert tesseract.detect_orientation(Path("p.png")) is None


def test_ocr_words_parses_tsv_into_word_records(monkeypatch):
    calls = stub_run_tool(monkeypatch, tesseract, stdout=TSV_OUTPUT)
    words = tesseract.ocr_words(Path("p.png"), page_number=4)
    assert [(w.text, w.left, w.conf, w.page) for w in words] == [("Order", 150, 96.5, 4)]
    assert calls[0][-1] == "tsv"


def test_rotate_clockwise_matches_osd_convention():
    image = np.zeros((2, 3), np.uint8)
    image[0, 0] = 255
    assert rotate_clockwise(image, 0) is image
    assert rotate_clockwise(image, 90).shape == (3, 2) and rotate_clockwise(image, 90)[0, 1] == 255
    assert rotate_clockwise(image, 270)[2, 0] == 255


def test_remove_red_ink_keeps_black_and_blue_tinted_text():
    page = np.full((40, 40, 3), 255, np.uint8)
    page[5:10, 5:10] = (200, 30, 40)  # red pen
    page[20:25, 5:10] = (30, 35, 60)  # blue-cast black print, as measured on the real scan
    page[20:25, 25:30] = (20, 30, 70)  # navy stamp
    cleaned = remove_red_ink(page)
    assert (cleaned[5:10, 5:10] == 255).all()
    assert (cleaned[20:25, 5:10] == page[20:25, 5:10]).all()
    assert (cleaned[20:25, 25:30] == page[20:25, 25:30]).all()


def test_invert_dark_bands_turns_light_on_dark_bar_text_dark():
    page = np.full((300, 600, 3), 255, np.uint8)[:, :, 0].copy()
    page[100:180, 50:550] = 120  # dark Total bar
    page[130:150, 450:500] = 230  # light text inside it
    page[20:40, 50:60] = 0  # ordinary dark text outside: must be untouched
    result = invert_dark_bands(page)
    assert result[140, 470] < result[110, 100]
    assert result[30, 55] == 0 and result[250, 300] == 255


def stub_page_ocr(monkeypatch, tmp_path, orientation):
    image_path = tmp_path / "upload.png"
    pipeline.save_png(np.full((20, 10, 3), 255, np.uint8), image_path)
    monkeypatch.setattr(pipeline, "detect_orientation", lambda path: orientation)
    monkeypatch.setattr(pipeline, "ocr_words", lambda path, page_number: [])
    monkeypatch.setattr(pipeline, "rasterize_page", lambda pdf, page, work_dir: image_path)
    return image_path


def test_ocr_image_file_skips_rasterize_and_keeps_the_original_image(monkeypatch, tmp_path):
    image_path = stub_page_ocr(monkeypatch, tmp_path, tesseract.Orientation(90, 5.0))
    result = pipeline.ocr_image_file(image_path, tmp_path / "work")
    assert (result.page_number, result.rotated_by, result.raster_path) == (1, 90, image_path)
    assert set(result.stage_seconds) == {"preprocess", "osd", "ocr"}
    assert pipeline.load_rgb(result.ocr_image_path).shape[:2] == (10, 20)


def test_ocr_pdf_page_times_every_stage_and_ignores_weak_osd(monkeypatch, tmp_path):
    stub_page_ocr(monkeypatch, tmp_path, tesseract.Orientation(90, 0.5))
    result = pipeline.ocr_pdf_page(Path("scan.pdf"), 3, tmp_path / "work")
    assert (result.page_number, result.rotated_by) == (3, 0)
    assert set(result.stage_seconds) == {"rasterize", "preprocess", "osd", "ocr"}
