# Milestone: first real scanned file through the OCR pipeline (2026-10-16 19:12)

First real-data test. Before this, all extraction work was validated only on synthetic TSV fixtures. Source file: a 10-page CamScanner PDF (real customer PII). It is read in place from the user's Downloads folder, never copied into the repo. All derived artifacts (rasters, OCR word dumps, scoring scripts, ground truth) live in the session scratchpad, outside the project tree.

## Plan
- [x] Build `converter/ocr/` (previously one-line stubs): rasterize, preprocess, tesseract, a shared subprocess helper, and a page pipeline
- [x] Run all 10 pages through rasterize -> preprocess -> OSD -> OCR -> classify -> extract
- [x] Score the 3 Sales Order pages field by field against the caller's hand transcription
- [x] Check that the classifier rejects the 7 non-Sales-Order pages
- [x] Unit tests for the new OCR modules; full suite green with `-W error`
- [ ] Follow-ups listed under "Findings" (need user input / a separate fix round; extraction layer intentionally untouched here)

## Changes (all additive; `converter/extraction/` and `converter/profiles/` untouched)
- `converter/ocr/tools.py` (new):
  - `run_tool`: argument-list `subprocess.run`, with a timeout and `check=True`.
  - Any failure (missing binary, timeout, non-zero exit) becomes `OcrToolError`.
  - Binary locations come from env vars: `POPPLER_PATH` is the Poppler bin dir; `TESSERACT_CMD` is the tesseract executable. Both fall back to `PATH`.
- `converter/ocr/rasterize.py`: `page_count` (`pdfinfo`) and `rasterize_page` (`pdftoppm -f N -l N -r 300 -png -singlefile`).
- `converter/ocr/preprocess.py`:
  - EXIF-aware load
  - red-ink removal
  - grayscale + min-max contrast
  - dark-band inversion
  - clockwise rotation
- `converter/ocr/tesseract.py`:
  - `detect_orientation` (`--psm 0`). It returns `None` when OSD cannot decide; that means unknown, not a failure.
  - `ocr_words`: TSV at `--psm 11`, parsed by the existing `extraction.words.parse_tesseract_tsv`.
- `converter/ocr/pipeline.py`: `ocr_pdf_page(pdf_path, page_number, work_dir) -> PageOcrResult`. Rotation is applied only when the OSD confidence is >= 2.0.
- `tests/test_ocr.py` (new, 13 tests): synthetic arrays and stubbed binary output only.

## Evidence behind the preprocessing choices (measured, not guessed)
- HSV-saturation ink removal (the CLAUDE.md starting point) was rejected:
  - On this scan, black print has a blue cast, and dark pixels get unstable high saturation.
  - It ate text and blotched the teal Total bar.
- The navy PAID stamp's chroma overlaps black text (median 34 vs up to 37), so it cannot be removed by colour. It is left in the image.
- Red pen is separable: `R - max(G,B) >= 15`, then dilated 7 px to catch the dark anti-aliased fringe.
- 29 preprocessing x PSM combinations were scored against ground truth. All landed at 6-9 of 41 fields correct, so OCR tuning is not the bottleneck. The chosen configuration (red-only removal + band inversion + PSM 11) was the best or tied for best.
- Tried and rejected:
  - table-rule removal: no consistent gain
  - Otsu inside the dark band: turned the gradient bar into a solid blob

## Results (final module path, all 10 pages)
- Classification: 10/10 correct.
  - SO pages scored 0.67 / 0.89 / 1.00.
  - Non-SO pages scored 0.00-0.44. The highest is the Sales Invoice at 0.44, all from generic column labels.
- OSD: the two sideways Collection Receipts were rotated 270 and verified to read upright. The other pages were 0.
- Field accuracy on the 3 SO pages (41 scored fields):

| Outcome | Count | Detail |
|---|---|---|
| Correct, non-blank | 7 | payment_terms x3, 1 customer, 1 quantity, 1 unit price, 1 taxes |
| Correct blank | 3 | 2 genuine (untaxed_total on non-VAT pages); 1 coincidental |
| Wrong, flagged | 8 | |
| Wrong, only row-level flags | 1 | |
| Missing, flagged | 17 | |
| Missing, only indirectly flagged | 2 | via `table_header_not_found` / `table_end_not_found` |
| Missing or wrong, no flag | 3 | real bugs, see below |

## Findings for follow-up (not fixed here: extraction layer was out of scope this session)
1. BUG (silent): a totals row that is never found produces no flag.
   - `_extract_totals` just `continue`s, so on page 10 `total=None` with zero flags.
   - Fix needs a per-totals `required` flag in the profile. Otherwise non-VAT pages would get a spurious `not_found:untaxed_total`.
2. BUG (silent): an optional table column (`required: false`) whose header OCR fails is never flagged, and its values migrate into the neighbouring column.
   - On page 6 the header was read as "UNITPRICE|", so `unit_price=None` and `taxes="3,690.00"`, with no flag.
3. STRUCTURAL: Order Date and Salesperson values are printed on the line BELOW their labels on the real form. The synthetic fixtures put them on the same line.
   - Result: 100% `not_found` (flagged, so visible).
   - OCR read the line below correctly for 2 of 3 dates (page 6's time came out as "1*.00:00", which would be flagged `unparsed`) and 2 of 3 salespersons, so a profile `value_position: below` option would recover most of them.
4. STRUCTURAL: the real form prints the peso sign as a suffix ("7,380.00 ₱"). Tesseract `eng` reads the glyph as P / ? / ) / F, which glues onto the number.
   - Every subtotal and totals value is affected; they are flagged `unparsed`.
   - Needs a decision on tolerant suffix stripping vs strict rejection.
5. STRUCTURAL: any single noise token (border pipe, pen-tick residue, peso-glyph fragment) in a `starts_row` column starts a new line item.
   - Page 10's 2 items became 6 rows; page 6 absorbed "Mode of Payment" / "Page: 1/1" after table-end detection failed.
   - Suggest requiring a starts_row cell to contain a digit, plus a `line_count` sanity flag.
6. OCR: the Total bar is detected and inverted on all 3 pages, but contrast after inversion is still too low.
   - "Total" and its value were not read on any page.
   - Next step: Sauvola/adaptive binarization within the band, or OCR of the band crop on its own with `--psm 7`.
7. OCR: page 3 is a faint printout. The customer block and table header OCR'd to garbage.
   - It was flagged (`low_confidence:customer`, `table_header_not_found`), but its line items are lost.
8. CLASSIFIER: anchors are unweighted.
   - The form-specific header labels (Order #, Order Date, Salesperson, Mode of Payment) count the same as generic column labels shared with invoices and receipts.
   - Page 3 passed by one anchor; the Sales Invoice reached 0.44 on column labels alone.
   - Suggest requiring a minimum number of header-label hits.
9. VAT layout (page 10):
   - "VAT 12.0000%" in the taxes column is handled structurally (taxes is free text); the second line read it exactly.
   - The 3-row totals block did not break structure: "Untaxed Amount" ended the table, and the struck-through "VAT 12%" row was ignored as intended.
   - The values themselves were lost to OCR (untaxed: `unparsed`) and to finding 1 (total: silent).
10. Spurious flags on correct values: `low_confidence:payment_terms` and `ambiguous:customer` appear on correct values. That is acceptable noise, but worth watching for alert fatigue.

## Review
- `.venv/Scripts/python -m pytest -W error -q`: 107 passed (94 before + 13 new OCR tests).
- The real-file run used `-W error`: no warnings, no tracebacks.
- Wall clock: ~80 s for 10 pages with 4 threads. Per page 10-47 s under contention; the Sales Invoice was slowest.
- The extraction layer is unchanged (file times predate this session) and still free of I/O and subprocess calls. No real-scan data is in the repo.

## Known limitations
- One batch, three SO pages: a tiny sample. The thresholds in `preprocess.py` are fitted to it and may need retuning on a second batch.
- The worker polling loop, SQLite job wiring and per-job work dirs are still stubs (out of scope).
