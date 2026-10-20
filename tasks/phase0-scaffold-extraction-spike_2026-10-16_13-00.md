# Phase 0: scaffold + synthetic extraction/export spike (2026-10-16 13:00)

## Plan
- [x] Scaffold package layout + pyproject.toml (no PyMuPDF, no pytesseract)
- [x] Canonical Pydantic Order/OrderLine models (all scalars nullable)
- [x] Layout profile `odoo_sale_order_v1.json`
- [x] Pure extraction layer (TSV parse, line grouping, anchors, table columns/rows, normalize, validate, classify)
- [x] Synthetic Tesseract TSV fixture + pytest end-to-end extraction tests
- [x] openpyxl export into copied template + pytest
- [x] Stubs only (docstring) for ocr/, worker/, app/, store/, export/csv.py
- [x] pytest green

## Review
- 38 tests pass (`.venv/Scripts/python -m pytest`), also with `-W error`.
- Synthetic fixture proves extraction code on a clean page only; real OCR accuracy is NOT measured (no real scan yet).
- Blockers: real scanned sample file; Payment Terms source (open); Order Reference repetition for Odoo re-import (open).
- New observation: sample output's `Order Lines/Subtotal` looks like the order total on the first line only (S07001, S07023), not per-line amount - needs user confirmation.
- Environment: only Python 3.10 installed locally; code is 3.10-compatible, pyproject targets 3.12 per stack.
