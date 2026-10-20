"""One page (PDF page or image file) to OCR words: rasterize, preprocess, OSD rotation fix, Tesseract TSV."""

import time
from dataclasses import dataclass, field
from pathlib import Path

from converter.extraction.words import OcrWord
from converter.ocr.preprocess import load_rgb, preprocess_image, rotate_clockwise, save_png
from converter.ocr.rasterize import rasterize_page
from converter.ocr.tesseract import MIN_ORIENTATION_CONFIDENCE, Orientation, detect_orientation, ocr_words


@dataclass(frozen=True)
class PageOcrResult:
    page_number: int
    words: list[OcrWord]
    orientation: Orientation | None
    rotated_by: int
    ocr_image_path: Path
    # Unprocessed color page image; rotate it by rotated_by to line it up with the word bboxes.
    raster_path: Path | None = None
    stage_seconds: dict[str, float] = field(default_factory=dict)


# def ocr_pdf_page(pdf_path: Path, page_number: int, work_dir: Path) -> PageOcrResult:
#     raster_path = rasterize_page(pdf_path, page_number, work_dir)
#     cleaned = preprocess_image(load_rgb(raster_path))
#     ocr_image_path = save_png(cleaned, work_dir / f"page-{page_number:03d}-clean.png")
#
#     orientation = detect_orientation(ocr_image_path)
#     rotated_by = 0
#     if orientation and orientation.rotate_clockwise and orientation.confidence >= MIN_ORIENTATION_CONFIDENCE:
#         rotated_by = orientation.rotate_clockwise
#         save_png(rotate_clockwise(cleaned, rotated_by), ocr_image_path)
#
#     return PageOcrResult(page_number, ocr_words(ocr_image_path, page_number), orientation, rotated_by, ocr_image_path)


def ocr_pdf_page(pdf_path: Path, page_number: int, work_dir: Path) -> PageOcrResult:
    started = time.perf_counter()
    raster_path = rasterize_page(pdf_path, page_number, work_dir)
    return _ocr_raster(raster_path, page_number, work_dir, {"rasterize": time.perf_counter() - started})


def ocr_image_file(image_path: Path, work_dir: Path) -> PageOcrResult:
    """OCR a directly uploaded page image (JPG/PNG/TIFF); EXIF orientation is applied by load_rgb."""
    return _ocr_raster(image_path, 1, work_dir, {})


def _ocr_raster(raster_path: Path, page_number: int, work_dir: Path, stage_seconds: dict[str, float]) -> PageOcrResult:
    work_dir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    cleaned = preprocess_image(load_rgb(raster_path))
    ocr_image_path = save_png(cleaned, work_dir / f"page-{page_number:03d}-clean.png")
    stage_seconds["preprocess"] = time.perf_counter() - started

    started = time.perf_counter()
    orientation = detect_orientation(ocr_image_path)
    rotated_by = 0
    if orientation and orientation.rotate_clockwise and orientation.confidence >= MIN_ORIENTATION_CONFIDENCE:
        rotated_by = orientation.rotate_clockwise
        save_png(rotate_clockwise(cleaned, rotated_by), ocr_image_path)
    stage_seconds["osd"] = time.perf_counter() - started

    started = time.perf_counter()
    words = ocr_words(ocr_image_path, page_number)
    stage_seconds["ocr"] = time.perf_counter() - started

    return PageOcrResult(page_number, words, orientation, rotated_by, ocr_image_path, raster_path, stage_seconds)
