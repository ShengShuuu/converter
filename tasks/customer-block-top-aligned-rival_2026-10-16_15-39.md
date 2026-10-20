# Top-aligned rival customer block + profile bounds (2026-10-16 15:39)

Follow-up to code-reviewer-qa round 3 on `customer-block-ambiguity-and-test-fixes_2026-10-16_15-05.md`. This is the last Phase 0 extraction-layer fix before waiting for a real scanned sample.

## Plan
- [x] MEDIUM-6: flag a rival block that is top-aligned with the customer block but ends lower
- [x] LOW-7: bounds on the top-level `LayoutProfile` numeric fields
- [x] LOW-8: stale `find_block_above` signature in the 15-05 task file
- [x] Fix the "flagged but not recovered" limitation line in the 15-05 task file
- [x] Regression tests that fail against the old logic; full suite green with `-W error`

## Changes
- `converter/extraction/anchors.py`
  - The old candidate-only rival check in `find_block_above` is commented out.
  - It is replaced by `_has_rival_block`, which seeds from every row in the winner's vertical span (its top row down to the row above "Order #"). It only uses text that is not part of the winner, and grows each segment upward.
  - `has_rival_block` is set when any such block reaches `min_rival_lines`. The old check only saw blocks ending on the row directly above "Order #". A taller left-column block that starts on the customer's top row ends lower, so it hid the real customer block entirely.
  - Winner words are excluded from rival growth, so a rival is never built partly from the customer's own lines.
- `converter/profiles/__init__.py` (additive bounds):
  - `fuzzy_threshold`: `gt=0, le=100` (rapidfuzz scale)
  - `line_tolerance_ratio`: `gt=0`
  - `value_max_gap_ratio`: `gt=0`
  - `column_pad_ratio`: `ge=0`
  - `low_confidence_threshold`: `ge=0, le=1` (same scale as `OrderLine.confidence`)
- `tasks/customer-block-ambiguity-and-test-fixes_2026-10-16_15-05.md`: line 16 signature now includes `min_rival_lines`. The "flagged but not recovered" limitation is rewritten to list which rival cases are now flagged.

## Review
- `.venv/Scripts/python -m pytest -W error -q`: 94 passed (83 before, +11 new).
- New `test_top_aligned_longer_block_in_other_column_is_flagged_ambiguous` (4-line and 3-line left-column blocks starting at the customer's row 300). Before the fix, both cases give the wrong customer with `flags == []`.
- New `test_profile_rejects_out_of_range_top_level_settings` (9 cases). With the bounds temporarily removed, all 9 fail.
- No false positives:
  - Clean S08824 and S07002 letterhead fixtures: zero customer flags.
  - 1-line other-column text at block height: no flag.
  - Letterhead text above the customer span: no flag.
  - All prior ambiguity tests are unchanged and pass.

## Known limitations
- Synthetic fixtures only; not validated against a real scan.
- A flagged rival is not recovered. The value is still the first line of the tallest block, so it may be "Shipping" instead of the customer. The flag makes it visible for review.
- MEDIUM-1 (2-line stamp in the customer's position) is still silent and waits for real scan data.
