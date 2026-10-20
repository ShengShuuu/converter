# Customer block ambiguity flag + test-helper fixes (2026-10-16 15:05)

Follow-up to the code-reviewer-qa findings on `customer-and-payment-terms-anchors_2026-10-16_14-52.md`. The user approved the cheap mitigation now; full heuristic tuning still waits for a real scan.

## Plan
- [x] Split rows at column gaps when building the customer block. Only same-column (horizontally overlapping) text joins.
- [x] New `ambiguous:customer` flag for shaky block geometry
- [x] `block_max_gap_ratio` must be `gt=0`
- [x] Validator rejects a duplicate positional field
- [x] `_moved_to_row` moves by row position. The payment-merge test now asserts `payment_terms` and product text.
- [x] Rename the test that encoded the lone-address-line bug as expected behavior
- [x] Full suite green with `-W error`

## Changes
- `converter/extraction/anchors.py`
  - `block_above` is commented out and replaced by `find_block_above(lines, anchor_line_index, max_gap_ratio, column_gap_ratio, min_rival_lines) -> TextBlock | None`.
  - Each row is split with `split_at_column_gaps`, which shares the `is_column_gap` predicate now also used by `value_words_after`.
  - A row segment joins the block only if it horizontally overlaps the block line below it. Rows with no overlapping segment are skipped, not treated as a stop.
  - Every column segment on the row directly above the anchor seeds a candidate. The tallest candidate wins. `has_rival_block` was originally set on a tie; see Follow-up for the change.
  - `TextBlock` records the internal gap ratios and the gap that stopped the walk.
- `converter/extraction/extract.py`
  - `_block_is_ambiguous` adds `ambiguous:<field>` when any of these hold:
    - fewer than `min_block_lines` lines
    - any internal or stopping gap within `block_borderline_margin` of `block_max_gap_ratio`
    - a block line's left edge offset from the line below by more than `block_max_left_offset_ratio` x line height
    - another candidate block of at least `min_block_lines` lines (originally: an equal-height tie)
  - The customer value is still the first block line, as printed.
- `converter/profiles/__init__.py`
  - `PositionalFieldSpec` changes:
    - `block_max_gap_ratio` is `Field(1.0, gt=0)`
    - new fields: `min_block_lines` (default 2), `block_borderline_margin` (0.25), `block_max_left_offset_ratio` (1.5)
  - The validator rejects the same field listed twice in `positional_fields`.
- `converter/profiles/odoo_sale_order_v1.json`: `"min_block_lines": 2` set explicitly.

## Design note
Overlap and alignment are checked against the block's own lower line, not the "Order #" line. On this form the partner block is in the right column (left ~1100-1500 px) while "Order #" is at the left margin (~150 px). Requiring overlap with "Order #" would reject the real customer on both fixtures.

## Review
- `.venv/Scripts/python -m pytest -W error -q`: 76 passed (66 before, -2 replaced, +12 new).
- New tests in `tests/test_extraction.py`:
  - `test_shaky_customer_block_is_flagged_ambiguous` (6 cases): stamp between block and Order #, stamp in the other column, misaligned stamp tucked under the block, letterhead just under the gap threshold, block fragmented just over it, two equally tall blocks
  - `test_other_column_text_at_block_height_is_not_merged_into_customer` (2 cases)
  - `test_profile_rejects_invalid_block_settings` (zero gap ratio, negative gap ratio, duplicate positional field)
- `test_customer_block_ends_at_order_line` is commented out. It is replaced by `test_lone_address_line_without_name_is_flagged_ambiguous`.
- `_moved_to_row` is now row-based (`top >= PAYMENT_ROW_TOP`). The `payment_line_merged_into_total_row` case now asserts `payment_terms == "POS / THERMAL PAYMENT"` and the full product descriptions. Run against the old text-matching helper, that case yields `payment_terms=None` and products with "/" stripped, so the new assertions catch the old bug.
- `test_payment_terms_read_when_merged_into_totals_row` is commented out because the case above now covers it.
- Clean fixtures (S08824 and the S07002 letterhead) still produce zero customer flags.

## Follow-up (code-reviewer-qa round 2)
- MEDIUM-2: `has_rival_block` used to fire only on an exact height tie. A taller block in another column (e.g. a left-column "Invoicing and Shipping Address" block beside the right-column customer) won silently with zero flags.
  - `find_block_above` now takes `min_rival_lines` (passed `spec.min_block_lines`). `has_rival_block` is set when any non-winning candidate has at least that many lines.
  - The old tie check is commented out above the fix.
  - New test `test_taller_block_in_other_column_is_flagged_ambiguous` (4-line and 3-line left-column blocks). Run against the old logic, both cases give the wrong customer with `flags == []`.
- LOW-5: profile models now share a `ProfileModel` base with `ConfigDict(extra="forbid", allow_inf_nan=False)`.
  - Unknown keys (e.g. `min_block_line`) and `inf`/`nan` floats are now rejected at load time. This applies to every profile model and every float field.
  - New tests: `test_profile_json_rejects_non_finite_numbers_and_unknown_keys` (inf gap ratio, nan gap ratio, inf borderline margin, misspelled key) and `test_unmodified_profile_json_still_loads`.
- `.venv/Scripts/python -m pytest -W error -q`: 83 passed.

## Known limitations
- Synthetic fixtures only. The thresholds (margin 0.25, left offset 1.5 line heights, min 2 lines) are unverified against a real scan.
- When the block is flagged ambiguous, the value is still the first line, which may be wrong (e.g. "RECEIVED" or "Shipping"). The flag makes it visible for review; it does not correct it.
- A partner with no address line prints a one-line block, so it will be flagged ambiguous (false positive).
  - `min_block_lines: 1` removes that false positive, but it also fully reopens the original single-line-stamp bug: a one-line "RECEIVED" stamp above "Order #" would again be accepted as the customer with no flag at all.
  - It also lets any one-line rival in another column raise `ambiguous:customer`.
  - Do not change this setting without real scans showing that name-only partner blocks are common.
- Other-column text can hide or outrank the real block. The rival-detection scan only covers rows from the winning block's top row down to the anchor line — a block lying entirely BELOW the real customer block's bottom (closer to "Order #") is never scanned as a rival at all, regardless of its height, and wins silently:
  - a 1-line stamp directly above "Order #" (caught as `too_short`) — flagged
  - a 2+-line bottom-aligned rival block overlapping the customer block's row span (MEDIUM-2, fixed) — flagged
  - a 2+-line top-aligned rival block that overlaps the customer block's row span but ends lower (MEDIUM-6, fixed in `customer-block-top-aligned-rival_2026-10-16_15-39.md`) — flagged
  - **any block (of any height, in any column) lying entirely between the real customer block and "Order #", with no row overlap with the customer block at all — still wins with zero flags (MEDIUM-9, not fixed, broader than the MEDIUM-1 case below).**
  - a 2-line (or shorter) stamp positioned where the customer block would be, internally well-aligned, with gaps outside the borderline band (MEDIUM-1, not fixed) — still silent
- **MEDIUM-9 and MEDIUM-1 share one root cause and are not caught by the current fixes.** A possible cheap fix for MEDIUM-9 (not yet applied, decide before Phase 1): also flag any non-winner block of `min_rival_lines`+ lines whose bottom sits within ~`block_max_gap_ratio` line heights above the winner's top, in any column — this would not catch the single/double-line same-column stamp case (MEDIUM-1), which still needs real scan data.
  - Other possible future directions for MEDIUM-1/MEDIUM-9, not implemented, to revisit with real scans:
    - check that line heights are consistent within a block
    - remove red/blue stamp ink during OCR image preprocessing, which is already planned for the OCR pipeline
- Nearby stray words merge silently (LOW-4).
  - A stray word close enough to the customer name/address row (inside the column-gap threshold) merges into the customer value with no flag, e.g. "ACME TRADING CORP. PAID".
  - A stray word between the name words has the same effect, e.g. "ACME TRADING ok CORP.".
  - This is a geometric limitation of the column/row grouping. It is acceptable for now; revisit with real scan data.
- Two settings are coupled. `profile.value_max_gap_ratio` is also the column-split threshold for customer-block detection (`find_block_above`'s `column_gap_ratio`), on top of ending label values.
  - Tuning it for one changes the other. A larger value merges more neighbouring text into the customer block; a smaller one splits customer lines into separate columns.
  - Split it into its own profile setting if the two ever need different values.
