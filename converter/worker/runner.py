"""Process one uploaded conversion page by page: OCR, page image, classification, extraction, store update."""

import json
import logging
import shutil
import threading
import time
from dataclasses import asdict
from pathlib import Path

from converter.extraction import OcrWord, extract_order
from converter.extraction.classify import anchor_match_ratio, select_profile
from converter.ocr.pipeline import PageOcrResult, ocr_image_file, ocr_pdf_page
from converter.ocr.preprocess import load_rgb, rotate_clockwise, save_png
from converter.ocr.rasterize import page_count
from converter.ocr.tools import OcrToolError
from converter.profiles import LayoutProfile, load_profile
from converter.store import repositories as repo

log = logging.getLogger(__name__)

SALES_ORDER_PROFILE = "odoo_sale_order_v1"
# Same default select_profile uses; surfaced so the UI can show score vs. threshold.
CLASSIFIER_MIN_RATIO = 0.6
MAX_PAGES = 50
WORDS_FILE = "words.json"
DISPLAY_IMAGE_FILE = "display.png"
WORK_SUBDIR = "work"
# MULTI_PAGE_ERROR = (
#     "This PDF has {pages} pages. Only single-page Sales Order uploads are supported. "
#     "Please split the PDF and upload one page."
# )
# EMPTY_PDF_ERROR = "This PDF has no pages."
EMPTY_FILE_ERROR = "This file is empty or unreadable: no pages were found in it."
TOO_MANY_PAGES_ERROR = "This file has {pages} pages, which is too many (max {max_pages}). Split it and upload the parts."

_stop_requested = threading.Event()


def request_stop() -> None:
    """Ask a running batch to stop before its next page; call before shutting the executor down."""
    _stop_requested.set()


def reset_stop_request() -> None:
    """Clear a previous request_stop(), so a newly started app processes batches again."""
    _stop_requested.clear()


def process_conversion(conversion_id: int, db_path: Path, data_dir: Path) -> None:
    """Job boundary: every failure ends as status 'failed' with a readable error, never an unhandled thread error."""
    try:
        _process_batch(conversion_id, db_path, data_dir)
    except repo.NotFoundError:
        log.info("conversion %s was deleted while processing", conversion_id)
        # A page finishing after the delete may have re-created files under the removed directory.
        shutil.rmtree(repo.conversion_dir(data_dir, conversion_id), ignore_errors=True)
    except OcrToolError as exc:
        log.warning("conversion %s: OCR tool failed: %s", conversion_id, exc)
        _mark_failed(conversion_id, db_path, str(exc))
    except Exception as exc:
        log.exception("conversion %s: processing failed", conversion_id)
        _mark_failed(conversion_id, db_path, f"Unexpected error: {type(exc).__name__}: {exc}")


def _mark_failed(conversion_id: int, db_path: Path, error: str) -> None:
    try:
        repo.set_status(db_path, conversion_id, repo.FAILED, error=error)
    except repo.NotFoundError:
        log.info("conversion %s was deleted while processing", conversion_id)


# v1 single-page body, replaced by _process_batch + _process_page:
# def _process(conversion_id: int, db_path: Path, data_dir: Path) -> None:
#     repo.set_status(db_path, conversion_id, repo.PROCESSING)
#     conversion = repo.get_conversion(db_path, conversion_id)
#     source_path = data_dir / conversion.source_path
#     output_dir = repo.conversion_dir(data_dir, conversion_id)
#     work_dir = output_dir / WORK_SUBDIR
#
#     if source_path.suffix.lower() == ".pdf":
#         pages = page_count(source_path)
#         if pages != 1:
#             error = MULTI_PAGE_ERROR.format(pages=pages) if pages > 1 else EMPTY_PDF_ERROR
#             repo.set_status(db_path, conversion_id, repo.FAILED, error=error)
#             return
#         result = ocr_pdf_page(source_path, 1, work_dir)
#     else:
#         result = ocr_image_file(source_path, work_dir)
#
#     save_words(result.words, output_dir / WORDS_FILE)
#     page_width, page_height = save_display_image(result, output_dir / DISPLAY_IMAGE_FILE)
#     shutil.rmtree(work_dir, ignore_errors=True)
#
#     profile = load_profile(SALES_ORDER_PROFILE)
#     score = anchor_match_ratio(result.words, profile)
#     repo.save_page_info(db_path, conversion_id, score, page_width, page_height, result.rotated_by)
#
#     started = time.perf_counter()
#     matched = select_profile(result.words, [profile], min_ratio=CLASSIFIER_MIN_RATIO)
#     if matched is None:
#         repo.set_status(db_path, conversion_id, repo.NOT_SALES_ORDER)
#     else:
#         _extract_and_save(conversion_id, db_path, result.words, matched)
#     timings = {**result.stage_seconds, "classify_extract": time.perf_counter() - started}
#     log.info(
#         "conversion %s: %s words, score %.2f, %s",
#         conversion_id,
#         len(result.words),
#         score,
#         ", ".join(f"{stage} {seconds:.2f}s" for stage, seconds in timings.items()),
#     )


def _is_pdf(source_path: Path) -> bool:
    return source_path.suffix.lower() == ".pdf"


def _process_batch(conversion_id: int, db_path: Path, data_dir: Path) -> None:
    repo.set_status(db_path, conversion_id, repo.PROCESSING)
    conversion = repo.get_conversion(db_path, conversion_id)
    # Set on the first run only; a retry reuses the existing page rows.
    if conversion.page_count is None:
        source_path = data_dir / conversion.source_path
        pages = page_count(source_path) if _is_pdf(source_path) else 1
        if pages == 0:
            repo.set_status(db_path, conversion_id, repo.FAILED, error=EMPTY_FILE_ERROR)
            return
        if pages > MAX_PAGES:
            error = TOO_MANY_PAGES_ERROR.format(pages=pages, max_pages=MAX_PAGES)
            repo.set_status(db_path, conversion_id, repo.FAILED, error=error)
            return
        repo.create_pages(db_path, conversion_id, pages)

    while True:
        if _stop_requested.is_set():
            # Left 'processing'; startup recovery fails the leftover pages so they can be retried.
            log.info("conversion %s: stopped before finishing because the app is shutting down", conversion_id)
            return
        page_number = repo.claim_next_page(db_path, conversion_id)
        if page_number is None:
            break
        _process_page(conversion, page_number, db_path, data_dir)
    # 'done' means every page was attempted; each page's own status says how it went.
    repo.set_status(db_path, conversion_id, repo.DONE)


def _process_page(conversion: repo.Conversion, page_number: int, db_path: Path, data_dir: Path) -> None:
    """One page's failure marks only that page failed, so the rest of the batch still gets read."""
    work_dir = repo.page_dir(data_dir, conversion.id, page_number) / WORK_SUBDIR
    try:
        _read_page(conversion, page_number, db_path, data_dir, work_dir)
    except repo.NotFoundError:
        raise
    except OcrToolError as exc:
        log.warning("conversion %s page %s: OCR tool failed: %s", conversion.id, page_number, exc)
        # repo.set_page_status(db_path, conversion.id, page_number, repo.FAILED, error=str(exc))
        _mark_page_failed(conversion.id, page_number, db_path, str(exc))
    except Exception as exc:
        log.exception("conversion %s page %s: processing failed", conversion.id, page_number)
        error = f"Unexpected error: {type(exc).__name__}: {exc}"
        # repo.set_page_status(db_path, conversion.id, page_number, repo.FAILED, error=error)
        _mark_page_failed(conversion.id, page_number, db_path, error)
    finally:
        # Each 300 dpi raster is tens of MB; never let a failed page leave them behind.
        shutil.rmtree(work_dir, ignore_errors=True)


def _mark_page_failed(conversion_id: int, page_number: int, db_path: Path, error: str) -> None:
    try:
        repo.set_page_status(db_path, conversion_id, page_number, repo.FAILED, error=error)
    except repo.NotFoundError:
        raise
    except Exception:
        # The page stays 'processing'; startup recovery (recover_interrupted) fails it on the next restart.
        log.exception(
            "conversion %s page %s: could not record the page as failed; it stays 'processing' until the app restarts",
            conversion_id,
            page_number,
        )


def _read_page(conversion: repo.Conversion, page_number: int, db_path: Path, data_dir: Path, work_dir: Path) -> None:
    output_dir = repo.page_dir(data_dir, conversion.id, page_number)
    source_path = data_dir / conversion.source_path
    if _is_pdf(source_path):
        result = ocr_pdf_page(source_path, page_number, work_dir)
    else:
        result = ocr_image_file(source_path, work_dir)

    save_words(result.words, output_dir / WORDS_FILE)
    page_width, page_height = save_display_image(result, output_dir / DISPLAY_IMAGE_FILE)

    profile = load_profile(SALES_ORDER_PROFILE)
    score = anchor_match_ratio(result.words, profile)
    repo.save_page_info(db_path, conversion.id, page_number, score, page_width, page_height, result.rotated_by)

    started = time.perf_counter()
    matched = select_profile(result.words, [profile], min_ratio=CLASSIFIER_MIN_RATIO)
    if matched is None:
        repo.set_page_status(db_path, conversion.id, page_number, repo.NOT_SALES_ORDER)
    else:
        _extract_and_save(conversion.id, page_number, db_path, result.words, matched)
    timings = {**result.stage_seconds, "classify_extract": time.perf_counter() - started}
    log.info(
        "conversion %s page %s: %s words, score %.2f, %s",
        conversion.id,
        page_number,
        len(result.words),
        score,
        ", ".join(f"{stage} {seconds:.2f}s" for stage, seconds in timings.items()),
    )


# def _extract_and_save(conversion_id: int, db_path: Path, words: list[OcrWord], profile: LayoutProfile) -> None:
#     order = extract_order(words, profile, page_number=1)
#     repo.save_extraction(db_path, conversion_id, order, profile.name)


def _extract_and_save(
    conversion_id: int, page_number: int, db_path: Path, words: list[OcrWord], profile: LayoutProfile
) -> None:
    order = extract_order(words, profile, page_number=page_number)
    repo.save_extraction(db_path, conversion_id, page_number, order, profile.name)


def extract_from_saved_words(conversion_id: int, page_number: int, db_path: Path, data_dir: Path) -> None:
    """Re-run extraction on a page's stored OCR words with the Sales Order profile, skipping classification and OCR."""
    words = load_words(saved_words_path(data_dir, conversion_id, page_number))
    _extract_and_save(conversion_id, page_number, db_path, words, load_profile(SALES_ORDER_PROFILE))


def saved_words_path(data_dir: Path, conversion_id: int, page_number: int) -> Path:
    return repo.page_dir(data_dir, conversion_id, page_number) / WORDS_FILE


def display_image_path(data_dir: Path, conversion_id: int, page_number: int) -> Path:
    return repo.page_dir(data_dir, conversion_id, page_number) / DISPLAY_IMAGE_FILE


def save_words(words: list[OcrWord], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([asdict(word) for word in words]), encoding="utf-8")


def load_words(path: Path) -> list[OcrWord]:
    return [OcrWord(**record) for record in json.loads(path.read_text(encoding="utf-8"))]


def save_display_image(result: PageOcrResult, path: Path) -> tuple[int, int]:
    """Color page rotated like the OCR image, so stamps/pen stay visible and word bboxes line up. Returns (w, h)."""
    upright = rotate_clockwise(load_rgb(result.raster_path), result.rotated_by)
    save_png(upright, path)
    return upright.shape[1], upright.shape[0]
