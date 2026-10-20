# Single-order upload/review/export backend - 2026-10-17 12:00

Backend half of: upload ONE scanned Sales Order -> OCR -> review/correct -> choose/create template -> preview -> download.
Frontend half (templates/static) built in parallel: `tasks/review-ui-templates-frontend_2026-10-17_12-05.md`.
Next: code-reviewer-qa.

## Plan
- [x] 1. `always_verify_fields` on `LayoutProfile` (typed as the profile's field Literals) + set in `odoo_sale_order_v1.json`; 182 existing tests still pass
- [x] 2. `ocr/pipeline.py`: shared `_ocr_raster`, new `ocr_image_file`, per-stage `stage_seconds`, `raster_path` on `PageOcrResult`; old `ocr_pdf_page` body kept commented. `page_count` already existed in `rasterize.py` (reused)
- [x] 2b. Measure real single-page timing (below)
- [x] 3. `store/`: schema, `db.py` (connect/init_db/recover_interrupted), `repositories.py` (plain functions, `update_order` under `BEGIN IMMEDIATE`)
- [x] 4. `mapping`: `CANONICAL_FIELDS`, `FIELD_ALIASES`, `suggest_column_map`
- [x] 5. `export/csv.py`: `write_csv`
- [x] 6. `worker/runner.py`: `process_conversion`, `extract_from_saved_words`; `worker/__main__.py` docstring marks the polling loop as deferred
- [x] 7. `app/`: `main.py` factory + lifespan, `routes_conversions.py`, `routes_templates.py`, `review.py`, `field_input.py`, `flag_messages.py`, `web.py`
- [x] 8. `pyproject.toml`: `jinja2` dep, templates/static package-data, dev dep `httpx` -> `httpx2`
- [x] Tests (96 new) + real-binary end-to-end run

## Measured single-page OCR timing (real file, no contention, this machine)
Read in place from `Downloads\1st week of Jan Davao.pdf`; split pages and all outputs kept in scratch and deleted afterwards.

| Run | rasterize | preprocess | OSD | OCR | extract | total |
|---|---|---|---|---|---|---|
| page 1 of 10-page PDF (not an SO) | 9.22s / 8.77s | 0.58 / 0.56 | 2.35 / 1.52 | 1.07 / 1.05 | - | 13.2s / 11.9s |
| page 3 (SO) of 10-page PDF | 5.79 | 0.53 | 1.79 | 1.16 | 0.006 | 9.3s |
| page 6 (SO) of 10-page PDF | 7.85 | 0.51 | 1.42 | 0.85 | 0.003 | 10.6s |
| page 10 (SO) of 10-page PDF | 14.54 | 0.75 | 1.37 | 1.02 | 0.005 | 17.7s |
| page 6 split to a 1-page PDF | 6.37 | 0.49 | 1.39 | 1.00 | - | 9.3s |
| page 10 split to a 1-page PDF | 14.94 | 1.06 | 1.54 | 1.22 | - | 18.8s |
| page 10 via running app (uvicorn) | 15.60 | 0.87 | 2.08 | 3.91 | 0.06 | ~22.5s |

- **Single page: ~9-23 s.** UI copy suggestion: "usually 10-25 seconds" (architect's "10-30" is not wrong; frontend's placeholder "under a minute" is safe but vague).
- **Rasterization (`pdftoppm` at 300 dpi) is 60-80% of the time, not Tesseract** (Tesseract OCR itself is ~1 s at PSM 11). If speed matters later, look at `pdfimages` (extract the embedded CamScanner JPEG directly) before touching OCR settings.

## Decisions / deviations from the brief
1. **Field paths keep the existing `order.<field>` / `line.<field>` convention** (`ODOO_SALE_ORDER_COLUMNS`, `resolve_field`), so `CANONICAL_FIELDS` keys are e.g. `order.order_ref`, `line.quantity`, not bare `order_ref`.
2. **`write_csv(orders, output_path, headers, column_map)`** - needs `headers` (the brief's signature had none); `write_xlsx` reads them from the template file, CSV has no file to read.
3. **`save_extraction` split in two**: `save_page_info(id, score, width, height, rotated_by)` (also needed for `not_sales_order`) and `save_extraction(id, order, profile_name)` (writes both `extracted_order_json` and `order_json`, sets `done`, resets confirmations).
4. **`create_conversion(db, original_filename, source_suffix)` / `create_template(...)`** compute `source_path` / `file_path` from the new id inside the same transaction. Paths are stored **relative to the data dir** (`conversions/{id}/source.pdf`, `templates/{id}.xlsx`).
5. **Template headers**: `find_header_sheet` needs a known required-header set, so it can't discover an unknown template's headers. Added `read_template_headers(workbook)` next to it in `export/xlsx.py` (active sheet first; only non-empty text cells, since non-text header cells can't be `column_map` keys).
6. **Tier names follow the frontend contract**: `not_read` (not_found/unparsed/column_not_found) > `check` (any other flag) > `verify` (unflagged, in `always_verify_fields`) ; `checked` = confirmed or edited (overrides flags, messages hidden); `ok` otherwise. Unknown flag kinds fall into `check`, never silently `ok`.
7. **Cross-checks recomputed live** in `review.py` via `validate.amounts_disagree` and `validate.validate_order`; the stored `amount_mismatch` / `lines_total_mismatch` flags are ignored as stale. `lines_total_mismatch` attaches to `untaxed_total` when present (Odoo compares lines to it), else `total` - exposed as `totals.compared_field` (frontend already uses it).
8. **Status polling sends `HX-Redirect` only on `done`**; `failed`/`not_sales_order` render inline in `_status.html`, which stops polling (frontend's design).
9. **Date input**: review uses a plain text input, so besides the two `datetime-local` formats it also accepts `%Y-%m-%d %H:%M:%S`, `%Y-%m-%d %H:%M`, `%Y-%m-%d` (all explicit `strptime`). Pre-fill format `%Y-%m-%d %H:%M:%S`.
10. **Multi-page error** is prefixed with the page count: "This PDF has N pages. Only single-page ...".
11. **Export gate counts only attention fields that reach the export** (mapped columns). An unmapped flagged field does not block the download.
12. **CSV formula-injection guard**: text cells starting with `= + - @ \t \r` are prefixed with `'`. Decimals/datetimes are untouched (a negative amount stays `-5`).
13. **Three `async def` remain, none in route modules, none blocking**: the lifespan, the CSP/nosniff middleware, and `web.submitted_form` (awaits the form body for the mapping editor's dynamic `map_{index}` fields so the handler itself stays `def`).
14. **Logging**: the app attaches a handler to the `converter` logger at startup (uvicorn only configures its own loggers, so the runner's per-stage timing line was invisible in the first e2e run).
15. **`extract-anyway` runs synchronously** in the request (extraction is ~5 ms) and redirects to review.
16. Venv changes: installed `jinja2` (was missing) and `httpx2` (Starlette 1.7's TestClient emits a deprecation warning with `httpx`, which fails `-W error`).

## Fragment / element IDs (cross-check with frontend)
- OOB: `#review-summary`, `#totals-check` (both always carry `hx-swap-oob="true"`).
- Main swaps: `_field_cell.html` (PATCH/confirm, 422 with `cell.error` + typed value on invalid input), `_line_items.html` / `#line-items` (add/delete line), `_status.html` / `#conversion-status`.
- Field keys: `total`, `order_date`, ... and `line.{index}.{field}`; DOM ids are the frontend's `cell-{field}` / `cell-line-{i}-{field}`.

## Verification
- `pytest -W error`: **278 passed** (182 existing + 96 new: store 12, runner 10, review 18, field_input 22, mapping 6, csv 4, ocr pipeline 2, app 22; counts include parametrized cases).
- Concurrency test proven sensitive: with the `BEGIN IMMEDIATE` removed it fails (lost update), restored it passes. A control test also demonstrates the lost update with a naive read-modify-write.
- App tests re-run with Jinja `StrictUndefined`: no backend-supplied context variable missing (only the frontend's optional `current_step` in `templates_list.html`, harmless under the default `Undefined`).
- `converter/extraction/` byte-identical to the session start (sha256 of every file).
- Real binaries end to end (uvicorn, Tesseract, Poppler, real SO page split to a 1-page PDF in scratch): upload -> done in ~20 s, review renders (17 cells flagged), page image 200, PATCH/confirm 200, template upload/mapping/choose, preview 200, export blocked 422, acknowledged export 200 -> one-sheet xlsx with numeric cells. 2-page PDF -> failed with the page-count message. No tracebacks in the server log. All scratch copies of the scan deleted.

## Known gaps / follow-ups
- **xlsx formula injection (existing code, not changed - brief said use `write_xlsx` as-is):** openpyxl stores any string starting with `=` as a formula, so an OCR'd/typed value like `=HYPERLINK(...)` becomes a live formula in the download. CSV is guarded; xlsx needs the same treatment in `write_xlsx` in a separate change.
- Upload size cap is checked after Starlette has spooled the whole request to a temp file (no streaming cap); fine for an internal tool, otherwise cap at a reverse proxy.
- Header fields have no bbox in the `Order` model, so only line cells can highlight on the scan.
- `raw_text` (OCR text behind a `not_read` cell) is always `None` - the model doesn't keep raw text.
- Real-page extraction quality is unchanged (Order Date/Salesperson below-label, Total bar unread) - see CLAUDE.md "Still open".
- CLAUDE.md "Architecture shape" still says single-page timing is "not yet precisely measured" - the numbers above can replace that (not edited by this agent).

## Review
Implementation complete and verified as above; ready for code-reviewer-qa.
