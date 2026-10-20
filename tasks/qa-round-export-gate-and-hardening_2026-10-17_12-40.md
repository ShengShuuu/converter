# QA round fixes (backend): export gate for missing item rows, xlsx hardening, id reuse, next-url - 2026-10-17 12:40

Source: full-stack QA review of the single-order flow (`single-order-review-backend_2026-10-17_12-00.md`).
Parallel: frontend-engineer fixing 2 other findings in `app.js` / templates. Next: code-reviewer-qa.

## Plan
- [x] HIGH-1 zero line items / item-table flags block export until acknowledged
- [x] HIGH-2 xlsx formula injection (`write_xlsx` forces string cells to `data_type = "s"`)
- [x] MEDIUM-3 control characters crash xlsx export (stripped in `write_xlsx`)
- [x] MEDIUM-5 ids reused after delete (`AUTOINCREMENT` on `templates` and `conversions`)
- [x] LOW-6 open redirect via `next` containing tab/newline/backslash
- [x] Regression tests for each; full suite `-W error`: 292 passed (278 existing + 14 new)

## HIGH-1 mechanism (for the frontend)
- `converter/app/review.py`:
  - `item_table_warnings(order) -> list[str]`: plain-language reasons the item rows may be missing or wrong.
    - zero lines: `[NO_LINE_ITEMS_MESSAGE]` (+ `describe_flag("table_header_not_found")` when that flag is set).
    - lines present: messages for `table_end_not_found` / `orphan_table_text` if flagged. `table_header_not_found` is ignored once lines exist (the user added them by hand; those cells carry their own tiers).
  - `ReviewView.item_table_warnings` (always computed, template-independent).
  - `ReviewSummary.messages` now starts with `NO_LINE_ITEMS_MESSAGE` when the order has zero lines (review page). `unchecked_count` is NOT changed - it still counts field cells only.
  - `Preview.item_table_warnings`: same list, but only when the template's `column_map` maps any `line.*` field (header-only templates are not blocked).
  - `Preview.needs_acknowledgement` = `unchecked_count > 0 or item_table_warnings`.
  - `Preview.acknowledgement_reason()`: the 422 message; leads with the item-table warnings ("No item rows were found on this order ..."), then the unchecked count if any.
- `routes_conversions.py`: export gate uses `preview.needs_acknowledgement` (old inline gate commented out). Same `acknowledge_unchecked` form field as before. Preview context gains `item_table_warnings`.
- `preview.html` (edited by backend, ack block only): the acknowledgement notice now renders when `unchecked or item_table_warnings`, lists the warnings under "Item rows may be missing", and the checkbox label covers both cases. Without this the user would get a 422 with no checkbox to tick.
- Not changed: `flatten_orders` still emits one header-only row for a lineless order (deliberate: header fields still export). So `preview.rows` is never empty and the `{% else %}` "Nothing to export yet" empty state plus `disabled if not preview.rows` on the Download button remain dead code. Frontend should key any empty-state/table messaging off `item_table_warnings`, not `preview.rows`.
- Review page: `_review_summary.html` can show "No fields left to check" next to the "No item rows were found" message (counts are field-only). Frontend may want to adjust that badge when `review.item_table_warnings` is non-empty.

## Other fixes
- `export/xlsx.py`: string values pass through `openpyxl.cell.cell.ILLEGAL_CHARACTERS_RE.sub("", ...)` then get `cell.data_type = "s"`. The route's `except ValueError` is unchanged: `IllegalCharacterError` can no longer come from cell values.
- `store/schema.sql`: `INTEGER PRIMARY KEY AUTOINCREMENT` on both tables. No migration (nothing deployed; no local `data/converter.db` existed). Any DB created before this keeps the old behaviour since `init_db` skips by `user_version`.
- `app/web.py` `safe_next`: rejects any char `<= " "`, `\` or DEL anywhere, plus `//` prefix (old check commented out).

## Review
- Tests: `tests/test_review.py` (2), `tests/test_export_xlsx.py` (2), `tests/test_store.py` (1), `tests/test_app.py` (6 parametrized next-url cases + relative-path keep + pasted `\x0b` end-to-end export + zero-lines export gate).
- Acknowledgement block rendering checked for warnings-only, both, and unchecked-only cases.
