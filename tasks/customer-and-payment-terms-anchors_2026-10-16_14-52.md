# Customer + Payment Terms anchors (2026-10-16 14:52)

Implements the decisions resolved in CLAUDE.md "Resolved decisions".

## Plan
- [x] Confirm subtotal is per-line (`extract._build_line` reads each row's own `subtotal` cell) - no change
- [x] Confirm product keeps the full printed description (all product-column words incl. wrapped lines) - no change
- [x] Confirm extra template sheets are always stripped (`write_xlsx` loops over every sheet name, any template) - no change
- [x] Customer: positional anchor, first line of the block directly above the "Order #" line
- [x] Payment Terms: label anchor "Mode of Payment", value after the label
- [x] Remove `customer`/`payment_terms` from `pending_fields`
- [x] Salesperson extracted as printed (no suffix logic) - unchanged, still covered by `test_header_fields`
- [x] Regression tests incl. not-found cases; full suite green with `-W error`

## Changes
- `converter/profiles/__init__.py`: new `PositionalFieldSpec` (`field`, `position: "first_line_of_block_above"`, `anchor_field`, `block_max_gap_ratio`) and `LayoutProfile.positional_fields`. A validator rejects an `anchor_field` that is not a labeled header field, and a field defined as both labeled and positional.
- `converter/profiles/odoo_sale_order_v1.json`: `payment_terms` added to `header_fields` (label "Mode of Payment"). `customer` added to `positional_fields`, anchored to `order_ref` with gap ratio 1.0. `pending_fields` is now `{}`; the mechanism stays.
  - Rollback note (JSON has no comments). The removed entries were:
    `"customer": "No confirmed anchor for the customer block on the printed form yet."`,
    `"payment_terms": "Open decision: Payment Terms source on the physical form (candidate: 'Mode of Payment:' line) is unconfirmed."`
- `converter/extraction/anchors.py`: `block_above(lines, anchor_line_index, max_gap_ratio)`. It starts from the line directly above the anchor and walks upward while the vertical gap is at most `max_gap_ratio` x the median word height. A letterhead separated by a larger gap is therefore excluded.
- `converter/extraction/extract.py`: `_extract_positional_fields`. The customer is the first block line, joined as printed (not split at commas). Flags: `not_found:customer` when the anchor label is missing or nothing sits above it, and `low_confidence:customer` below the threshold. Payment terms reuse the existing label-anchor path, which gives `not_found:payment_terms` / `low_confidence:payment_terms` for free.
- Salesperson-suffix gap: not added to `pending_fields`. That would emit a false `source_pending:salesperson` flag even though salesperson is extracted. It stays documented in CLAUDE.md "Still open".

## Review
- `.venv/Scripts/python -m pytest -W error -q`: 66 passed (56 before, -1 replaced, +11 new).
- New fixture: `tests/fixtures/odoo_sale_order_S07002_letterhead.tsv` (Tesseract 5 TSV). It has a letterhead above the partner block, the customer "DON JOSE HEIGHTS HOA, INC, ROSE L. DE TORRES", and "Mode of Payment: GCASH PAYMENT".
- New/updated tests:
  - `tests/test_extraction.py`:
    - customer/payment extracted on S08824
    - letterhead skipped and the contact suffix kept as printed
    - block ends at the Order # line
    - `not_found:customer`: no text above Order #, and Order # missing
    - `low_confidence:customer`
    - `not_found:payment_terms`: no payment line, and label without a value
    - payment terms read when merged into the totals row
    - profile validator rejects bad positional specs
  - `tests/test_export_xlsx.py`: the S08824 export now carries customer + payment terms in their columns. The old expectation is commented out above.
- `test_unconfirmed_fields_stay_blank_and_flagged` commented out (replaced).

## Known limitations
- Synthetic fixtures only. Nothing is verified against a real scan, because none has been provided yet.
- "First line of the block above Order #" assumes the letterhead (if present) is separated from the partner block by more than ~1 line height. If a real scan prints them tight together, the letterhead's first line would be taken as the customer. Tune `block_max_gap_ratio` or add a horizontal-region constraint once a real file exists.
- The whole visual line is taken, so any other text at the same height (e.g. a stamp beside the customer name) is included in `customer`.
- Pre-existing test-helper quirk: `MODE_OF_PAYMENT_WORDS` contains "/", so `_moved_to_row` in `test_extra_text_on_totals_row_is_flagged_not_a_phantom_item[payment_line_merged_into_total_row]` also moves the product-description slashes. That test's assertions still hold; left untouched.
