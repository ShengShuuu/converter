# Silent-failure flags + row-start digit guard (2026-10-17 11:30)

Follow-up to `first-real-scan-ocr-pipeline_2026-10-16_19-12.md`, findings 1, 2 and 5.
This round fixes the bugs where a value ends up blank or wrong with no flag at all. It does NOT raise overall accuracy: the first real run got 7 of 41 fields right, and the deferred items below still apply.

## Plan
- [x] Bug 1: a required totals row that is never found raises `not_found:<field>`.
  - The profile now has a per-totals `required` option, so an optional row (Untaxed Amount on non-VAT pages) stays unflagged.
- [x] Bug 2: an unmatched table column header raises `column_not_found:<field>`. The header-matching logic is unchanged.
- [x] Finding 5: a row-start cell must contain a digit to open a new line item.
- [x] Regression tests. Each one fails against the old logic (checked by temporary revert, then restored).
- [x] Re-ran the real PDF pages 3, 6 and 10; derived artifacts went to the session scratchpad only.

## Changes
- `converter/profiles/__init__.py`: `TotalSpec.required: bool = True`, matching `ColumnSpec`.
- `converter/profiles/odoo_sale_order_v1.json`: `untaxed_total` now has `"required": false`. `total` stays required.
- `converter/extraction/extract.py`:
  - `_extract_totals`: a missing row raises `not_found:<field>` when `spec.required`. The old `continue` is kept commented out.
  - `extract_order`: raises an order-level `column_not_found:<field>` for every profile column that has no header cell on the accepted header row.
  - Order level matches `table_header_not_found` / `table_end_not_found`. Column geometry is a page property, not a line property.
- `converter/extraction/normalize.py`: new `has_digit`. No digit helper existed before.
- `converter/extraction/table.py`: `group_rows` starts a row only when a row-start cell has a digit. The old condition is kept commented out.
- `tests/fixtures/odoo_sale_order_S08824_vat_no_total_bar.tsv` (new, synthetic, no PII):
  - VAT layout with Untaxed Amount and VAT 12% rows, and no Total row.
  - This reproduces the page-10 shape.
- `tests/conftest.py`: `vat_no_total_bar_words` fixture.
- `tests/test_extraction.py`: 8 new tests.
  - Required total missing; optional untaxed absent not flagged; `required` read from profile.
  - Garbled "UNITPRICE|" header; no spurious column flag.
  - Noise in quantity / unit_price / amount columns (3 cases).
- `tests/test_normalize.py`: `has_digit` (5 cases).

## Review
- `.venv/Scripts/python -m pytest -W error -q`: 120 passed (107 before + 13 new).
- Revert check:
  - Bug 1 revert fails 2 tests.
  - Bug 2 revert fails 1.
  - Digit guard revert fails 3.
  - `test_absent_optional_totals_row_is_not_flagged` passes either way on purpose. It guards against the spurious-flag risk.
- The extraction layer is still pure: no subprocess / `open(` / requests. The only grep hits are docstrings.
- Real file, same OCR as the first run:
  - Page 6 now flags `column_not_found:unit_price` and `not_found:total`. Before, both were silent.
  - Page 6 now has 1 line item instead of 4; the 3 phantom rows came from noise.
  - Page 10 now flags `not_found:total`. Before, it was silent.
  - Page 10 now has 2 line items instead of 6. The real form has 2.
  - Old vs new row grouping: no value the old grouping parsed is lost.
- Trade-off on page 10: noise lines that used to form phantom rows are now absorbed into the real row.
  - The second item's taxes text went from "VAT 12.0000%" to "VAT 12.0000% | I } ' : A".
  - It is still flagged `low_confidence:taxes`, as it was before.
  - Cleaning noise out of free-text cells is a separate follow-up.

## Known limitations / left before a second real-file accuracy run
1. Value printed below its label (order_date, salesperson): needs a profile `value_position: "below"` option.
2. Peso sign printed as a suffix ("7,380.00 ₱", OCR'd as P/?/)/F): this is a user policy decision, tolerant suffix stripping vs strict rejection. Every subtotal/total on real pages is currently `unparsed`.
3. Total-bar contrast in `converter/ocr/preprocess.py`: "Total" and its value are not OCR'd on any real page. It is now flagged `not_found:total`, but still not read.
4. Classifier margin: anchors are unweighted. Page 3 passed by one anchor, and the Sales Invoice scored 0.44.
5. Digit guard limits:
   - Noise that OCRs as a digit (e.g. a pipe read as "1") still opens a phantom row.
   - Absorbed noise lands in the previous row's cells. It is visible only through `unparsed:` / `low_confidence:` flags.
6. `column_not_found` says a column is missing. It does not say which neighbouring column took its values; the reviewer has to check the whole page.
7. Only synthetic fixtures plus one real batch so far.
