"""Tesseract binary calls: OSD rotation detection and TSV word OCR."""

import re
from dataclasses import dataclass
from pathlib import Path

from converter.extraction.words import OcrWord, parse_tesseract_tsv
from converter.ocr.tools import OcrToolError, run_tool, tesseract_binary

OCR_LANGUAGE = "eng"
OCR_DPI = 300
# Sparse text mode: extraction rebuilds lines geometrically, and PSM 11 scored best on the first real batch.
OCR_PAGE_SEGMENTATION = 11
# Below this, Tesseract's OSD guess is not trusted enough to rotate the page.
MIN_ORIENTATION_CONFIDENCE = 2.0

_ROTATE_LINE = re.compile(r"^Rotate:\s+(\d+)\s*$", re.MULTILINE)
_CONFIDENCE_LINE = re.compile(r"^Orientation confidence:\s+([\d.]+)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class Orientation:
    rotate_clockwise: int
    confidence: float


def detect_orientation(image_path: Path) -> Orientation | None:
    """Clockwise rotation that makes the page upright, or None when OSD finds too little text to decide."""
    try:
        output = run_tool([tesseract_binary(), str(image_path), "stdout", "--psm", "0", "--dpi", str(OCR_DPI)]).stdout
    except OcrToolError:
        # OSD exits non-zero on pages with too few characters; that means "unknown", not a failed page.
        return None
    rotate = _ROTATE_LINE.search(output)
    confidence = _CONFIDENCE_LINE.search(output)
    if rotate is None or confidence is None:
        return None
    return Orientation(int(rotate.group(1)), float(confidence.group(1)))


def ocr_words(image_path: Path, page_number: int = 1) -> list[OcrWord]:
    tsv = run_tool(
        [
            tesseract_binary(),
            str(image_path),
            "stdout",
            "-l", OCR_LANGUAGE,
            "--psm", str(OCR_PAGE_SEGMENTATION),
            "--dpi", str(OCR_DPI),
            "tsv",
        ]
    ).stdout
    return parse_tesseract_tsv(tsv, page=page_number)
