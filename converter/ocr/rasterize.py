"""PDF page count (pdfinfo) and per-page rasterization to PNG (pdftoppm)."""

import re
from pathlib import Path

from converter.ocr.tools import OcrToolError, poppler_binary, run_tool

RASTER_DPI = 300
_PAGES_LINE = re.compile(r"^Pages:\s+(\d+)\s*$", re.MULTILINE)


def page_count(pdf_path: Path) -> int:
    info = run_tool([poppler_binary("pdfinfo"), str(pdf_path)]).stdout
    match = _PAGES_LINE.search(info)
    if match is None:
        raise OcrToolError(f"pdfinfo reported no page count for {pdf_path.name}")
    return int(match.group(1))


def rasterize_page(pdf_path: Path, page_number: int, output_dir: Path, dpi: int = RASTER_DPI) -> Path:
    """Render one 1-indexed PDF page to a PNG in output_dir and return its path."""
    output_dir.mkdir(parents=True, exist_ok=True)
    output_prefix = output_dir / f"page-{page_number:03d}"
    run_tool(
        [
            poppler_binary("pdftoppm"),
            "-f", str(page_number),
            "-l", str(page_number),
            "-r", str(dpi),
            "-png",
            "-singlefile",
            str(pdf_path),
            str(output_prefix),
        ]
    )
    image_path = output_prefix.with_suffix(".png")
    if not image_path.is_file():
        raise OcrToolError(f"pdftoppm produced no image for page {page_number} of {pdf_path.name}")
    return image_path
