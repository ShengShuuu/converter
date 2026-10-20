# Review UI templates (frontend half) — 2026-10-17 12:05

Frontend half of the single-order upload -> review -> template -> preview -> download flow.
Built in parallel with backend-engineer (routes/store/runner); this file is the contract the
backend renders against. code-reviewer-qa picks up after both halves land.

## Plan
- [x] Vendor htmx 2.0.11 + Alpine CSP build 3.17.4 into `converter/app/static/`
- [x] `base.html` shell: step indicator, CSP-safe htmx config, global request-error alert
- [x] `app.css`: 4 field tiers (+ neutral), responsive (16px gutter, no page h-scroll), focus states
- [x] `app.js`: Alpine `review` component (bbox overlay, keyboard nav, jump-to-next), global htmx error/focus handling
- [x] Upload screen + recent conversions list
- [x] Status page + polled `_status.html` fragment (queued/processing/failed/not_sales_order/done)
- [x] Review screen: `review.html`, `_field_cell.html`, `_line_row.html`, `_line_items.html`, `_review_summary.html`, `_totals_check.html`
- [x] Template screens: `choose_template.html`, `templates_list.html`, `template_mapping.html`
- [x] `preview.html` with export form + blocked-export warning state
- [x] Render every template against a fake context harness; headless-Chrome screenshots at desktop + 375px

## Decisions / deviations from the brief (each with the reason)
1. **Alpine CSP build** (`@alpinejs/csp`) is vendored as `alpine.min.js`, not the standard build.
   The standard build evaluates directive strings with `new Function`, which a strict CSP
   (`script-src 'self'`, no `unsafe-eval`) blocks. Consequence: directives reference named
   properties/methods registered in `static/app.js` (`x-data="review"`), never inline objects
   like `x-data="{ box: null }"`.
2. **htmx config via `<meta name="htmx-config">`**: `allowEval:false`, `includeIndicatorStyles:false`
   (htmx otherwise injects an inline `<style>`), and `responseHandling` adds **422 = swap** so the
   backend can return inline validation errors (`_form_error.html`) with a 422 status.
3. **Editable cells are NOT inside a `<form>`.** htmx 2 includes every input of the enclosing form in
   non-GET requests and the form's values take priority, so every PATCH would carry every cell's
   `value`. The editor root is `<div id="review-editor">` and cells use
   `hx-sync="#review-editor:queue all"` — same serialization intent as `closest form:queue all`.
4. **Extra files beyond the brief's list:** `_line_items.html` (Add/Delete re-render the whole list,
   since deleting shifts every later row index), `_form_error.html` (422 error fragment),
   `_macros.html` (status badge + datetime formatting), `static/app.js`.
5. **Page dimensions live on the image figure, not each cell:** `data-page-width`/`data-page-height`
   on `<figure>`; each cell carries only `data-bbox="left,top,width,height"`.
6. **UI copy is English** (internal business tool). CLAUDE.md's Tagalog rule covers chat responses,
   not app UI copy — correct this if the user wants Tagalog UI text.
7. **Processing-time copy is a placeholder** ("usually under a minute") until backend records a real
   single-page timing.

## Backend contract (context variables each template expects)
All pages: `conversion` (when applicable) with `.id`. Dicts or objects both work (Jinja attr access).

### Field cell (`_field_cell.html`, variable `cell`)
| attr | type | notes |
|---|---|---|
| `field` | str | canonical field name, e.g. `total`, `subtotal` |
| `line_index` | int or None | None = header field -> PATCH `/conversions/{id}/order/{field}`; int -> `/conversions/{id}/lines/{index}/{field}` |
| `key` | str | used verbatim in `POST /conversions/{id}/confirm/{key}` and in element ids (must be id-safe: letters, digits, `-`, `_`, `.`) |
| `label` | str | visible label |
| `value` | str or None | display/edit string (backend formats Decimal/datetime) |
| `raw_text` | str or None | raw OCR text, shown as "Read as: ..." on `not_read` |
| `tier` | `not_read` / `check` / `verify` / `checked` / `ok` | backend computes (same logic it uses for the counter + export gate) |
| `messages` | list[str] | plain-language explanations of the flags |
| `bbox` | obj with `left,top,width,height` or None | OCR-pixel coordinates of the page image; None = no overlay |
| `kind` | `text` / `money` / `number` / `datetime` | sets `inputmode`; optional, default text |
| `placeholder` | str or None | optional, e.g. `MM/DD/YYYY` |

Expected tier mapping (backend side): `not_found:*`, `unparsed:*`, `column_not_found:*` -> `not_read`;
`low_confidence:*`, `ambiguous:*`, `unexpected_format:*`, `extra_text:*`, `amount_mismatch`,
`lines_total_mismatch` -> `check`; unflagged field in profile `always_verify_fields` -> `verify`;
user edited or confirmed -> `checked`; else `ok`. `ok` renders neutral (no badge, no confirm button).

PATCH/confirm request body: form field **`value`** (PATCH only). Response: re-rendered
`_field_cell.html` (main swap, `outerHTML` of `#cell-{key}`) + `_review_summary.html` +
`_totals_check.html` (both carry `hx-swap-oob="true"` always).

### `review.html`
`conversion`, `page_width`, `page_height` (pixel size of the image the bboxes refer to — must match the
image served by `/page-image`, after any OSD rotation), `order_cells` (dict field -> cell for
`order_ref, order_date, customer, salesperson, payment_terms, untaxed_total, total`; missing keys are
skipped), `line_rows`, `summary`, `totals`.
- `line_rows`: list of `{index, cells: {product, quantity, unit_price, taxes, subtotal}, messages}`.
- `summary`: `{unchecked_count, field_count, messages: [str]}` -> "N of M fields need checking".
- `totals`: `{lines_sum: str|None, total: str|None, agrees: bool}`; "Use sum of lines" button shows when
  `lines_sum` is set and not `agrees`; it PATCHes `/order/total` with `value=lines_sum`, targets `#cell-total`.
- `POST /conversions/{id}/lines` and `DELETE /conversions/{id}/lines/{index}` return `_line_items.html`
  (needs `conversion`, `line_rows`) + the two OOB fragments.

### Other screens
- `upload.html`: `conversions` [{id, original_filename, status, created_at}]. `POST /conversions`
  (htmx, field `file`): success -> `HX-Redirect`; rejection -> 422 + `_form_error.html` (`message`).
  `DELETE /conversions/{id}` -> 200 with empty body (htmx 2 does not swap a 204).
- `conversion_status.html` / `_status.html`: `conversion` {id, status, original_filename, error,
  classifier_score, classifier_threshold}, `elapsed_seconds`. Retry/extract-anyway return `_status.html`.
- `choose_template.html`: `conversion`, `templates` [{id, name, created_at, header_count}],
  `selected_template_id`, `error`. Plain form POST, field `template_id`.
- `templates_list.html`: `templates`, `next`, `error`. Plain multipart POST `/templates`, fields
  `name`, `file`, `next`. `DELETE /templates/{id}` -> 200 empty.
- `template_mapping.html`: `template` {id, name}, `rows` [{index, header, selected, suggested}],
  `field_options` [{value, label}], `next`, `error`. Plain POST, selects named `map_{index}`, `""` = blank.
- `preview.html`: `conversion`, `preview` {columns: [str], rows: [[{value, tier}]]}, `unchecked_count`,
  `export_error` (str|None, set when the backend blocked the export), `format` (re-selected value).
  Plain POST `/conversions/{id}/export`, fields `format` (`xlsx`/`csv`), `acknowledge_unchecked=1`.

## Recommended response header (backend)
`Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'`

## Contract reconciliation with the backend (it landed mid-session)
Checked against `converter/app/review.py`, `routes_conversions.py`, `routes_templates.py`, `web.py`. It matches the
contract above, with these adjustments made on the template side:
- Field keys are `line.{i}.{field}` (backend `line_field_key`). Templates use the key only in the confirm URL
  (urlencoded); DOM ids are built from `field`/`line_index`, so the key format can't break CSS selectors.
- `totals.compared_field` (`untaxed_total` when the form printed one, else `total`): `_totals_check.html` now
  PATCHes and targets that field and labels it accordingly, instead of hard-coding `total`.
- `FieldCell.error` (422 on an unparseable value): the cell renders `field-cell--invalid` + "Not saved: ..."
  and hides "This is correct" (confirming would approve the stored value, not the visible one).
- `Template` has `headers` (no `header_count`): the column count is `t.headers | length`.
- Backend already sends the CSP header and uses `safe_next`; the templates also guard `next` themselves.

## Review
Verification done:
- Harness (scratchpad only, not in repo): fake contexts + in-memory endpoints, strict CSP header. Every template
  rendered with a logging Undefined: only the expected optional variables (`error`, `cell.placeholder`) were undefined.
- Headless Chrome self-test against the harness: 28/28 passed. That covers bbox overlay on focus and hidden when there's
  no bbox, ArrowDown to the same column on the next line, Escape revert, PATCH autosave swapping the cell to checked plus
  both OOB fragments updating, confirm, focus moving on after confirm, "Use sum of lines", add line (focus goes to the
  new row), delete line (reindexed, focus goes to Add line), jump-to-next advancing, live-region announcement,
  upload 422 error fragment, status polling every 2s, zero JS errors and zero CSP violations.
- No horizontal page scroll at 375px (iframe test) on review, upload, preview (blocked), mapping, choose template,
  status and templates list.
- Real backend (`create_app` + TestClient, temp data dir, no-op executor, order seeded through the repo API): the full
  flow renders and round-trips. Covered upload validation, status states, review, PATCH ok/422, confirm, add/delete
  line, template upload, mapping, choose, preview, blocked export, acknowledged CSV export and delete.
- `pytest -W error`: 256 passed. No Python files were touched by this task.

Not verified:
- Real OCR output (no real scan was run through the UI). Header fields have no bbox in the model yet, so only line
  cells highlight on the scan.
- Manual visual check in a real (non-headless) browser, screen-reader behavior, and touch devices.
- Processing-time copy is still a placeholder.
