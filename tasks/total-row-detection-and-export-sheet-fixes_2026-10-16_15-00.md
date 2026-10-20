# Total-row detection + export sheet fixes (2026-10-16 15:00)

Follow-up to `phase0-scaffold-extraction-spike_2026-10-16_13-00.md` review findings.

## Plan
- [x] Bug #1 (HIGH): a data row whose first word fuzzy-matches "Total" truncates the table silently
- [x] Bug #4 (MEDIUM): totals row found but value unparseable -> `total=None` with no flag
- [x] Bug #3 (MEDIUM): text left of "Total" on the same line blocks totals detection, creating a phantom line item
- [x] Bug #5 (LOW-MEDIUM): leftover template sheets carried into the export
- [x] Regression tests per bug (failure class, not just the reported case)
- [x] Full pytest run green
- [x] Bug #2 documented as known limitation, not fixed

## Changes
- `converter/extraction/extract.py`: `_extract_totals` now uses `_find_totals_row`. The label is searched anywhere in the line (not just word 0). A match only counts as the totals row when:
  1. the label word is NOT in the description (`product`) column, and
  2. no other word on the line sits in an item-only row-start column (`quantity`, `unit_price`, derived from the profile's `starts_row` columns minus `subtotal`).
  Value = words after the label in the amount (`subtotal`) column. Every other non-label word is extra text.
  New order flags: `unparsed:<total_field>` (value present but unreadable), `not_found:<total_field>` (label row found, no value), `extra_text:<total_field>` (stamp/handwriting/merged text on the totals row). `table_end_not_found` still fires when no real totals row exists, and all remaining lines are kept as items.
  Column spans are now computed before totals detection, because detection needs them.
- `converter/extraction/table.py`: added `column_of(word, spans)`.
- `converter/extraction/anchors.py`: `find_line_start_label` commented out (replaced; kept for rollback).
- `converter/export/xlsx.py`: `write_xlsx` removes every sheet except the header/target sheet (chartsheets included) and makes it the active sheet.

## Review
- `.venv/Scripts/python -m pytest -q -W error`: 56 passed (38 existing + 18 new).
- Ran the new tests against the old logic (monkeypatched, outside the repo). All 17 bug-targeted tests failed there, confirming they reproduce the bugs. The 18th is a no-false-positive guard.
- New tests:
  - `tests/test_extraction.py`: product codes TOTAL/Total/TOTAL:/T0TAL/TOTAL-X; "TOTAL" on a wrapped description line; real totals row missing; misread totals (`8,36O.00`, `8.360.00`, `8,360.O0`, `B,360.00`); totals value missing; stamp left of label; stamp between label and value; "Mode of Payment" line merged into the totals row with "Rcvd"; clean page raises no `:total` flags.
  - `tests/test_export_xlsx.py`: the real sample template exports only `Converted`; a template where the header sheet is not first (plus a stale sheet and a chartsheet) exports only that sheet.

## Known limitations
- Bug #2 (NOT fixed, by design): a stray word (handwriting/stamp) inside the table area that lands in a non-row-start column gets merged into the previous line item as if it were a wrapped continuation (`table.group_rows`). Designing a correct heuristic needs real OCR data from the user's scans.
- A totals label printed inside the description column (not the Odoo layout) is not accepted as the table end. It produces `table_end_not_found` rather than silent loss. Revisit if a second profile needs it.
- A totals row whose label is covered completely (e.g. by a stamp) is not detected. Its amount then becomes a phantom line item (subtotal = grand total), but the order carries `table_end_not_found`, so it is flagged for review, not silent.
