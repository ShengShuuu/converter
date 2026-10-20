# Peso-glyph tolerance, phantom-line flag, absorbed-noise test coverage (2026-10-17 11:39)

Follow-up to `silent-failure-flags-and-row-start-guard_2026-10-17_11-30.md`, after code-reviewer-qa feedback (MEDIUM-1, MEDIUM-2 test gap) and a user policy decision on the peso suffix.

## Plan
- [x] FIX 1: phantom line item from digit-like noise must carry a flag even without a parsed total.
- [x] FIX 2: pin the full state of `lines[0]` in each absorbed-noise scenario (border pipe, pen tick, peso fragment).
- [x] FIX 3 (user-approved): strip a short peso-like glyph adjacent to an amount before parsing; keep strict rejection otherwise.
- [x] Verify every new test fails on pre-fix code; full suite with `-W error`.
- [x] Re-score the cached real-file OCR words.

## Changes
- `converter/extraction/validate.py`: `validate_line` adds `not_found:product` when a line has no product text.
  - Chosen over "no product AND no subtotal": a stray "0" in the amount column gives subtotal 0, which that rule misses. It is also invisible to `lines_total_mismatch`, because 0 does not change the line sum.
  - The flag name follows `<kind>:<field>` on `OrderLine.flags`, like `unparsed:subtotal`.
- `converter/extraction/normalize.py`: new `parse_money`.
  - It first tries strict `parse_decimal`. Only if that fails does it accept an amount with a leading `P`, or with up to 2 trailing glyphs from `P ? ) F`. These are the glyphs the real scan produced for the peso sign.
  - Centavos (`.dd`) are required whenever a glyph is stripped. Real page 10 read "2,812.50 ₱" as `2812509)`; without that rule the parser would have silently accepted 2812509.
  - `parse_decimal` itself is unchanged and still strict. Quantities still use it.
- `converter/extraction/extract.py`: line `unit_price`/`subtotal` and the totals now use `parse_money`. The old calls are kept commented out.
- Note: a literal `₱`, leading or trailing, was already accepted before this change, because `_CURRENCY_MARKS` strips it anywhere in the string. The real gap was only the OCR misreads.

## Tests (+36, 120 -> 156)
- `tests/test_normalize.py`:
  - 11 accept cases, including trailing `₱`, trailing `P` / `?` / `)` / `F`, and a leading `₱` / `P`.
  - 13 reject cases, including a bad digit, a misplaced comma, `2812509)`, a missing centavo digit, a glyph inside the number, a leading `F`, and a pen tick.
  - 1 test that `parse_decimal` stays strict.
- `tests/test_validate.py`: a line without a product is flagged.
- `tests/test_extraction.py`:
  - `test_peso_suffix_on_real_form_amounts_is_parsed`: 3 cases.
  - `test_absorbed_noise_full_state_of_first_line`: 3 cases, using full `model_dump` equality.
  - `test_digit_noise_phantom_item_is_flagged_missing_product`: 4 cases. "0" in quantity, unit price, and amount, plus "1" in amount.

## Review
- `.venv/Scripts/python -m pytest -W error -q`: 156 passed.
- Revert checks, run on a scratch copy of the repo:
  - FIX 1 reverted: 5 tests fail.
  - FIX 3 reverted (`parse_money = parse_decimal`): 10 tests fail. One of them is `peso_fragment_recovered`.
  - Over-loose mutant (any non-digit run stripped, no centavo requirement): 6 reject tests fail.
  - The border_pipe and pen_tick full-state cases pass on both old and new code. That is expected: they document current behavior, and they fail if it drifts.
- FIX 2 finding: in pen_tick, the real `2,600.00` unit price is destroyed (`None` plus `unparsed:unit_price`). The flag makes this visible, but the value is lost. In peso_fragment, the absorbed "P" is now stripped and the subtotal is recovered; before FIX 3 it was lost.
- Extraction layer is still pure. Grep for subprocess / `open(` / requests hits only docstrings.
- Real file: I re-scored the cached OCR words from the first real run (scratchpad, no re-OCR). Result: 9/41 before this change and 9/41 after, with identical output.
  - The real subtotal tokens are `7ZOL0P` and `2812509){`. They are garbled beyond a glyph suffix, so stripping a peso glyph does not recover them.
  - The totals were never OCR'd (Total-bar contrast).
  - With the current OCR output, this fix does not move real accuracy.

## Known limitations
- Absorbed noise can still destroy a correctly read value (pen_tick). It is flagged, but not recovered.
- A wrapped product-description line that follows a phantom row is absorbed into the phantom, so the phantom gets product text and no `not_found:product` flag.
- `_CURRENCY_MARKS` still strips `₱`/`PHP`/`$` anywhere in the string, not only at the edges. This is pre-existing and was left untouched.
- Still open, not touched in this round: value-below-label, Total-bar contrast, classifier margin, LOW-4/LOW-5.
- CLAUDE.md "Still open" still lists the peso-suffix decision as pending. The subagent did not edit CLAUDE.md; the main session or the user should update it.
