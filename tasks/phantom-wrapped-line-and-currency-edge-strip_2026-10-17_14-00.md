# Phantom line absorbing a wrapped description; currency marks stripped only at edges (2026-10-17 14:00)

Follow-up to `peso-glyph-and-phantom-line-flags_2026-10-17_11-39.md`. This round closes the two MEDIUM findings from code-reviewer-qa's last review. Both are about visibility only; they are not expected to move real-scan accuracy.

## Plan
- [x] FINDING 1: flag a phantom line item that absorbs a wrapped description line.
- [x] FINDING 2: strip `_CURRENCY_MARKS` only at the leading or trailing edge of the string.
- [x] Write the regression tests first and confirm they fail on the pre-fix code.
- [x] Run the full suite with `-W error`, then run revert checks on a scratch copy.
- [x] Update CLAUDE.md "Still open".

## Changes
- `converter/extraction/validate.py`:
  - New `ALWAYS_PRINTED_LINE_FIELDS = ("quantity", "unit_price")`.
  - `validate_line` adds `not_found:<field>` when one of these is `None`.
  - Two cases are skipped:
    - The line already has `unparsed:<field>`. The text was there but unreadable, so `not_found` would be wrong and redundant.
    - The field's column header was never matched. The order already has `column_not_found:<field>`, so flagging every line again would only add noise.
  - The new `unmatched_columns` parameter defaults to `()`, so existing callers keep working. The old signature is kept commented out.
- `converter/extraction/extract.py`: passes `missing_columns` to `validate_line`. The old call is kept commented out.
- `converter/extraction/normalize.py`: `_CURRENCY_MARKS` is now `^\s*(PHP|₱|\$)|(PHP|₱|\$)\s*$`, which strips a mark only at the start or end of the string. The old regex is kept commented out. `parse_decimal` and `parse_money` both pick this up.

## Tests (+26, 156 -> 182)
- `tests/test_extraction.py::test_phantom_item_absorbing_wrapped_description_is_flagged` (3 cases):
  - A "0" is placed on the "24 MONTHS WARRANTY" wrapped line, in the quantity, unit price, or amount column.
  - Each case pins the phantom's exact flags, and confirms that the first item's product is truncated.
  - It also confirms `lines_total_mismatch` does NOT fire. This documents why the line-level flag is the only signal.
  - Pre-fix result: the phantom had `[]` flags.
- `tests/test_extraction.py::test_unmatched_optional_column_header_is_flagged`: now also asserts that there is no per-line `not_found:unit_price` when the column header was not matched.
- `tests/test_validate.py`:
  - `test_line_without_product_is_flagged` is updated. The old assert is kept commented out, because a bare phantom now also gets the quantity and unit price flags.
  - New tests: missing quantity or unit price is flagged; an unparsed value is not double-flagged; an unmatched column is not flagged per line.
- `tests/test_normalize.py`:
  - A mark inside the number is rejected: `1$00.00`, `26PHP00.00`, `1,5₱00.00`, `1₱500.00P`.
  - A mark at the edge is still stripped: `₱1,500.00`, `1,500.00 PHP`, `$…`, `…$`, lowercase `php`, and a mark on both ends.
  - Both groups are checked against both `parse_decimal` and `parse_money`.

## Review
- `.venv/Scripts/python -m pytest -W error -q`: 182 passed.
- Revert checks, run on a scratch copy:
  - `ALWAYS_PRINTED_LINE_FIELDS = ()`: 5 tests fail.
  - The old `_CURRENCY_MARKS` regex: 7 tests fail.
- Extraction layer is still pure. Grep for subprocess / `open(` / requests hits only docstrings.
- Evidence is synthetic fixtures only. Real-scan behavior is unmeasured in this round.

## Known limitations / behavior changes
- The truncated original item is not flagged itself, because it keeps its quantity and unit price. Only the phantom item is flagged. The reviewer still sees the issue because the phantom sits directly below the truncated item.
- These inputs are now rejected (`None` plus `unparsed:<field>`, never a wrong value) where the old code accepted them:
  - a mark in front of a minus sign (`-₱12.50`); `₱-12.50` still parses;
  - a literal `₱` followed by a misread glyph (`7,380.00₱)`);
  - a doubled mark (`PHPPHP1.00`).
  None of these has been seen on real scans.
- Row grouping (`table.py`) is unchanged. A phantom row is still created and is now flagged; it is not prevented.
