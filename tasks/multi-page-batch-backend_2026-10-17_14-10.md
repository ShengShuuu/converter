# Multi-page batch backend (schema/store/runner/routes) - 2026-10-17 14:10

Backend half of the multi-page batch extension (CLAUDE.md "Multi-page batch"): one upload -> every page OCR'd,
classified, extracted one by one -> batch list -> per-page review -> one template -> combined preview -> one download.
Frontend half (templates) is being built in parallel by frontend-engineer. Next: code-reviewer-qa.
No separate architect task file existed; the plan came in the delegation prompt.

## Plan
- [x] Schema reshape: `conversions` (upload-level) + `pages` (one row per page), `user_version = 2`; v1 DDL kept commented
- [x] `db.py`: `SCHEMA_VERSION = 2`; `init_db` raises `RuntimeError` for a stale `0 < user_version < 2` DB; `recover_interrupted` also fails queued/processing pages
- [x] Stale dev DB `data/converter.db` (1 failed v1 conversion of the real 10-page PDF, no templates/review data) moved to the scratchpad, not deleted
- [x] `repositories.py`: slim `Conversion`, new `Page`, `PageSummary`, page functions
- [x] `runner.py`: `process_conversion` loops `claim_next_page`; per-page error isolation; real `page_number` into `extract_order`; work dir cleanup in `finally`; `request_stop()`
- [x] `main.py`: `reset_stop_request()` on startup, `request_stop()` before `executor.shutdown`
- [x] `review.py`: `build_batch_preview` / `BatchPreview` / `OrderAttention` (existing `build_review`/`build_preview`/`Preview` untouched)
- [x] `routes_conversions.py`: batch list, page-scoped review/autosave, skip, per-page retry, batch export gate
- [x] Tests (store, runner, review, app) + full suite under `-W error`
- [x] Real-binary run on the real multi-page PDF (below)

## Decisions / deviations from the delegation prompt
1. **`request_stop()` needs a partner `reset_stop_request()`.** The stop flag is process-wide, and every app shutdown sets it, so the lifespan clears it on startup (otherwise a restarted app in the same process, e.g. every TestClient, would stop every batch at once). `tests/conftest.py` has an autouse fixture clearing it around each test, because runner tests call `process_conversion` directly without a lifespan.
2. **Retry always resubmits `process_conversion`** (after requeueing) instead of "only if the conversion is not running". With one executor thread a second submission just runs after the first and finds nothing or the requeued page; checking "is it running?" would race with a batch that has just claimed its last page but not yet set `done` (the requeued page would sit queued until restart).
3. **Skip is allowed from `done` and `failed` only** (`repo.SKIPPABLE_STATUSES`), idempotent on `skipped`, 409 for queued/processing (the worker owns those). Not from `not_sales_order`: that already is "skipped by the classifier".
4. **Extract-anyway is allowed from `not_sales_order` and `skipped`** (the prompt said `not_sales_order`). Allowing it on `skipped` makes it the undo for a mistaken skip; it re-extracts from the saved OCR words, so edits made before the skip are discarded (documented in the contract).
5. **Deleted-mid-batch cleanup:** a page that finishes OCR after the conversion was deleted re-created `words.json`/`display.png` under the removed directory (true in v1 too, for its single page). The job boundary now removes the conversion directory again when it sees `NotFoundError`.
6. **`PageSummary`** (counts + `total`/`pending`/`finished`) instead of a bare dict, so templates don't repeat the arithmetic. Counted with `GROUP BY status`, never stored.
7. **`BatchPreview.item_table_warnings`** (page-labelled flat list) exists in addition to the per-order lists, and is still passed as the top-level `item_table_warnings`, so the not-yet-updated `preview.html` keeps working.
8. **Duplicate `order_ref` detection** is case- and whitespace-insensitive (`S08824` vs ` s08824 `); orders without a ref are never duplicates.
9. **Export gate and exported orders come from one `list_pages` read**, so a page skipped between the check and the write cannot slip into or out of the file unchecked. Same for the review page's prev/next navigation.
10. **Error copy:** "This file is empty or unreadable: no pages were found in it." / "This file has N pages, which is too many (max 50). Split it and upload the parts." Page-level restart message: `PAGE_INTERRUPTED_ERROR` in `db.py`.
11. **Conversion `done` with zero `done` pages** (all pages not_sales_order/failed/skipped) is valid: the list shows each page's outcome; `can_export` is false; template/preview redirect to the list, POST template/export answer 409.
12. **Reviewing a finished page while later pages are still being read is allowed** (only template/preview/export wait for the whole batch).

## Matches the architect's assumptions (checked against real code)
- `page_count()` exists in `converter/ocr/rasterize.py` (pdfinfo). `ocr_pdf_page(pdf_path, page_number, work_dir)` and `ocr_image_file(image_path, work_dir)` already took what was needed: no OCR signature changes.
- `extract_order(words, profile, page_number=1)` already parameterised; v1 runner hardcoded `page_number=1` (fixed; covered by `test_extracted_order_carries_its_real_page_number`).
- `flatten_orders`/`write_xlsx`/`write_csv` already take `list[Order]`: untouched. `converter/extraction/`, `converter/mapping/`, `converter/export/` untouched.
- `PRAGMA foreign_keys=ON` was already in `connect()`; the cascade test fails if it is removed (mutation-checked).
- The "deleted conversion" signal is `repo.NotFoundError`; `_process_page` re-raises it, everything else is caught per page.

## Verification
- Real templates only: `pytest -W error` -> 333 passed, 10 failed, all 10 `TemplateNotFound: batch.html / _batch_status.html` (frontend-engineer's files, not written yet at the time of this run).
- With minimal stand-in `batch.html` / `_batch_status.html` loaded from the scratchpad through a pytest plugin (not in the repo): **343 passed** under `-W error`.
- Same, with Jinja `StrictUndefined`: 336 passed; the only 7 failures are the pre-existing `current_step` in `templates_list.html` (template routes, untouched, harmless under the default Undefined, already noted in the v1 round). So every context variable the current review/preview/choose-template templates read is supplied.
- Mutation checks (each reverted afterwards): hardcoded `page_number=1` -> page-number tests fail; no `PRAGMA foreign_keys` -> cascade test fails; cleanup moved out of `finally` -> work-dir test fails; per-page OcrToolError branch removed -> batch isolation tests fail.
- `pyproject.toml`: no change needed (no new dependency; `store/*.sql` already in package-data).

## Contract for frontend-engineer (exact template names and context variables)

### Template names
| Template | Status | Rendered by |
|---|---|---|
| `batch.html` | **NEW** (takes over `conversion_status.html`'s role) | `GET /conversions/{id}`, full page |
| `_batch_status.html` | **NEW** polling fragment | `GET /conversions/{id}/status`, and the htmx response of every batch-list action (retry / per-page retry / skip / failed extract-anyway) |
| `review.html`, `_field_cell.html`, `_line_items.html`, `_line_row.html`, `_review_summary.html`, `_totals_check.html` | same names; switch hardcoded `/conversions/{{ conversion.id }}/...` to `{{ order_url }}/...` | per-page routes |
| `preview.html`, `choose_template.html` | same names, new context below | batch-level routes |
| `conversion_status.html`, `_status.html` | **no longer rendered by any route** (safe to delete or keep) | - |

`batch.html` gets exactly the same context as `_batch_status.html`, so it can just `{% include "_batch_status.html" %}`.
I suggest the fragment's root element is `id="batch-status"` with `hx-get="{{ batch_url }}/status" hx-trigger="every 2s" hx-swap="outerHTML"` only while `is_processing`, and the per-page action buttons use `hx-target="#batch-status" hx-swap="outerHTML"`. No route sends `HX-Redirect` on done any more.

### `batch.html` / `_batch_status.html` context
- `conversion`: `Conversion` - `id, original_filename, source_path, status ('queued'|'processing'|'done'|'failed'), error, page_count (None until counted), template_id, created_at, updated_at`. `conversion.error` is set only for a conversion-level failure (empty/unreadable file, more than 50 pages, pdfinfo failure, restart interruption).
- `progress`: `PageSummary` - `progress.counts` is a dict with every key always present: `queued, processing, done, not_sales_order, failed, skipped`; properties `progress.total`, `progress.pending` (queued+processing), `progress.finished` (= total - pending). Show it as "{{ progress.finished }} of {{ progress.total }} pages". `progress.total` is 0 until the worker has counted pages (show "Counting pages..." / waiting).
- `pages`: `list[BatchPageRow]`, sorted by page number. Fields:
  - `page_number: int`, `status: str` (page statuses above), `error: str | None` (failed pages)
  - `review_url: str | None` - `/conversions/{id}/pages/{n}`, set only when `status == 'done'`
  - `order_ref, customer: str | None`, `line_count: int | None`, `total: str | None` (formatted Decimal) - only for done pages
  - `unchecked_count: int`, `item_table_warnings: list[str]`, property `needs_attention: bool`
  - `classifier_score: float | None` (rounded to 2 dp; useful on `not_sales_order` rows next to `classifier_threshold`)
  - `can_extract_anyway: bool` (status `not_sales_order` or `skipped`), `can_skip: bool` (status `done` or `failed`), `can_retry: bool` (status `failed`)
- `is_processing: bool` - poll while true.
- `can_export: bool` - conversion done and at least one done page; gate the "Continue to template" button on this.
- `elapsed_seconds: int` - since the conversion row last changed.
- `classifier_threshold: float` (0.6)
- `batch_url: str` (`/conversions/{id}`), `template_url: str` (`/conversions/{id}/template`)

### Batch-list action endpoints (all POST, all return `_batch_status.html` for htmx, 303 to `batch_url` otherwise)
- `POST /conversions/{id}/retry` - requeue every failed page (and restart a conversion-level failure)
- `POST /conversions/{id}/pages/{n}/retry` - requeue one failed page
- `POST /conversions/{id}/pages/{n}/skip` - status -> `skipped`; allowed from `done`/`failed`, idempotent on `skipped`, **409** if the page is queued/processing
- `POST /conversions/{id}/pages/{n}/extract-anyway` - allowed from `not_sales_order`/`skipped`. On success answers **`HX-Redirect: /conversions/{id}/pages/{n}`** (straight to review, like v1). If the saved words are missing the page becomes `failed` and the batch fragment comes back instead. Note: extract-anyway on a page that was `done` and then skipped re-extracts from OCR and discards earlier edits (it is the "undo skip" path).
- `DELETE /conversions/{id}` - unchanged

### `review.html` context (`GET /conversions/{id}/pages/{n}`; a page that is not `done` redirects 303 to the batch list)
- `order_url: str` - **`/conversions/{id}/pages/{n}`, the single integration point.** Every review URL hangs off it:
  `{{ order_url }}/page-image`, `{{ order_url }}/order/{field}` (PATCH), `{{ order_url }}/lines/{i}/{field}` (PATCH), `{{ order_url }}/lines` (POST add), `{{ order_url }}/lines/{i}` (DELETE), `{{ order_url }}/confirm/{key}` (POST)
- `conversion` (`Conversion`, above), `page` (`Page`: `page_number, status, profile_name, classifier_score, rotated_by, page_width, page_height, extracted_order, order, confirmed_fields`, ...)
- unchanged from v1: `page_width, page_height, order_cells, line_rows, summary, totals`
- review navigation (over `done` pages only, in page order): `batch_url`, `template_url`, `done_page_count: int`, `done_page_position: int` (1-based), `prev_page_url: str | None`, `next_page_url: str | None`

### Partial fragments returned by autosave routes
- `_field_cell.html`: `conversion, page, order_url, cell`
- `_line_items.html`: `conversion, page, order_url, line_rows`
- `_review_summary.html` / `_totals_check.html` (OOB): `conversion, page, order_url, summary, totals`
Same element IDs / OOB behaviour as v1 (`#review-summary`, `#totals-check`, `#line-items`, `cell-{field}`, `cell-line-{i}-{field}`).

### `choose_template.html` context
`conversion, templates, selected_template_id, error, batch_url` (new: use `batch_url` for "Back", and `('%s/template' | format(batch_url))` for the `next` link; the form still posts to `/conversions/{id}/template`).
GET redirects to the batch list unless the conversion is `done` with at least one `done` page; POST answers 409 in that case.

### `preview.html` context (`GET /conversions/{id}/preview`, and the 422 re-render from `POST /conversions/{id}/export`)
- `conversion`, `template`, `format`, `export_error: str | None`, `batch_url`, `template_url`
- `preview`: `BatchPreview`
  - `preview.columns: list[str]`, `preview.rows: list[list[PreviewCell]]` (`cell.value`, `cell.tier`) - all orders' rows, in page order
  - `preview.row_pages: list[int]` - same length as `rows`; display-only "Page" column (not exported). Use `zip` via `loop.index0`: `preview.row_pages[loop.index0]`
  - `preview.order_count: int`
  - `preview.orders_needing_attention: list[OrderAttention]` - each `page_number, order_ref, unchecked_count, item_table_warnings: list[str]`, properties `label` ("Page 6 (S08830)" / "Page 6"); link each to `/conversions/{{ conversion.id }}/pages/{{ a.page_number }}`
  - `preview.failed_pages: list[int]` - **requires acknowledgement**: their orders are missing from the file
  - `preview.skipped_pages: list[int]` - `not_sales_order` + `skipped`; **informational only, never requires acknowledgement**
  - `preview.duplicate_order_refs: dict[str, list[int]]` - order_ref -> pages; **requires acknowledgement**
  - `preview.unchecked_count: int` - sum over orders (not a union of field keys)
  - `preview.needs_acknowledgement: bool`
- Top-level shortcuts of the same values: `unchecked_count, orders_needing_attention, failed_pages, skipped_pages, duplicate_order_refs, needs_acknowledgement`.
- **Removed:** top-level `item_table_warnings` (it is per order now: `orders_needing_attention[i].item_table_warnings`).
- The export form still posts `format` and `acknowledge_unchecked=1` to `/conversions/{id}/export`. Show the acknowledgement checkbox when `needs_acknowledgement`.
- Export filename: one order -> `{order_ref}.xlsx`; several -> sanitized stem of the uploaded filename; fallback `conversion-{id}`.

### `upload.html`
Unchanged context (`conversions`, `error`). Conversion statuses are now only `queued/processing/done/failed` (`not_sales_order` is a page status now). The "multi-page PDFs are not accepted" copy is now wrong.

## Real-binary end to end (uvicorn + Tesseract + Poppler, real 10-page `1st week of Jan Davao.pdf`)
Run against a scratchpad data dir; all scan copies deleted afterwards.
- Upload -> conversion `done`, `page_count` 10, all 10 pages attempted sequentially in **~3.5 min** (rasterize 5-33 s/page dominates, as measured in v1).
- Classification: pages 3, 6, 10 `done` (scores 0.67 / 0.89 / 1.00), the other 7 `not_sales_order` (0.00-0.44). Matches the v1 round's manual finding (SOs on pages 3, 6, 10).
- `source_pages` = `[3]`, `[6]`, `[10]` (real page numbers, not 1).
- No `work/` dirs left behind; per-page dirs 2-7 MB each (mostly the 300 dpi `display.png`).
- Review pages 3/6/10 -> 200, page images 200 `image/png`; PATCH customer / confirm 200; a `not_sales_order` page's review URL -> 303 to the list.
- Extract-anyway on page 1 -> `HX-Redirect: /conversions/1/pages/1`; then skip -> status `skipped` (the response itself was 500 only because `_batch_status.html` does not exist yet; it is the sole traceback in the server log).
- Template choose -> preview 200 -> export without ack 422 -> with ack 200 `1st_week_of_Jan_Davao.xlsx`, single `Converted` sheet, 4 rows from the 3 orders (skipped page 1 excluded); CSV 200 with BOM.
- Extracted order refs on real scans were `$07341`, `SO07340`, `lediie` - extraction quality on real scans is unchanged and still the known open item (not touched here).

## Known gaps / follow-ups
- **Batch templates (`batch.html`, `_batch_status.html`) are the frontend's and did not exist when this round finished**; 10 app tests fail with `TemplateNotFound` until they land. If the frontend chose different names, rename in `routes_conversions.py` (`conversion_page`, `_batch_fragment`) or in the templates.
- Existing review partials still hardcode `/conversions/{{ conversion.id }}/...`; until the frontend switches them to `{{ order_url }}`, autosave from the browser hits non-existent v1 URLs (tests call the new URLs directly, so they pass).
- 50-page batches: ~15-20 min of sequential OCR and up to ~250 MB of display PNGs per upload at the measured rates. Fine for an internal tool; if it matters, downscale `display.png` or look at `pdfimages` (noted in v1).
- Graceful-stop behaviour was tested in-process (`request_stop` halts between pages; lifespan calls it); not exercised with a real Ctrl+C on Windows.
- `upload.html` copy ("multi-page PDFs are not accepted", "One page only") is now wrong - frontend.

## Review
Backend half complete and verified as above. Next: code-reviewer-qa, after the frontend's batch templates land (then the plain `pytest -W error` run should be 343 passed).
