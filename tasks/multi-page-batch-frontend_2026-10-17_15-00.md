# Multi-page batch frontend (templates) - 2026-10-17 15:00

Frontend half of the multi-page batch extension. Contract: `tasks/multi-page-batch-backend_2026-10-17_14-10.md`
("Contract for frontend-engineer"), checked against `routes_conversions.py` / `review.py`. Next: code-reviewer-qa.

## Plan
- [x] 1 (CRITICAL). Review partials: replace hardcoded `/conversions/{{ conversion.id }}/...` with `{{ order_url }}/...`
      (`review.html`, `_field_cell.html`, `_line_row.html`, `_line_items.html`, `_totals_check.html`; `_review_summary.html` has no URL)
- [x] 1b. Review page batch navigation (prev/next order, back to batch list, continue link -> `template_url`)
- [x] 2. `_batch_status.html` (polled fragment + target of every batch-list action) and `batch.html` (includes it)
- [x] 3. Page-status visual language (CSS), card layout on narrow screens
- [x] 4. `upload.html` copy (multi-page, max 50, non-SO pages skipped)
- [x] 5. `choose_template.html`: `batch_url` for Back / `next`
- [x] 6. `preview.html`: Page column, orders needing attention, failed/skipped/duplicate notices, combined ack
- [x] 7. `conversion_status.html` / `_status.html`: kept, marked superseded
- [x] 8. `pytest -W error` -> 343 passed
- [x] 9. Real run (uvicorn + Tesseract + Poppler, real 10-page PDF) through the rendered templates

## Decisions
1. **Page-status visual language** (`.page-status--*`, `_macros.page_status_badge`): analogous to the field tiers, each status has its own label + glyph + border style, never colour alone:
   queued "Waiting" (dotted, ...), processing "Reading" (blue, small spinner), done "Sales Order" (solid brand fill, check),
   not_sales_order "Not a Sales Order" (grey dashed, en dash), failed "Failed" (red, !), skipped "Skipped" (grey, x, struck through).
   Glyphs use `content: "x" / ""` so they stay out of the accessible name where supported.
   A done row's review state reuses the existing tier badges: `check` "N fields need checking" or `checked` "All fields checked".
   not_sales_order/skipped rows get a muted background so actionable rows stand out.
2. **Layout:** one `<ol>` of page rows (not a `<table>`); CSS grid stacks each row as a card below 768px, aligned columns above. No duplicate markup.
3. **Polling** stops automatically: `hx-get/hx-trigger="every 2s"` rendered on `#batch-status` only while `is_processing`. Action buttons target `#batch-status` (outerHTML) with `hx-sync="#batch-status:replace"` so an action aborts an in-flight poll instead of racing it.
4. **Focus:** every action button/link has a stable id, so htmx's same-id refocus keeps keyboard focus across 2 s polls. When an action removes the clicked button (skip/retry), `data-focus-after="page-N"` moves focus to the row (`tabindex=-1`, described by its status badge). This needed one generic branch in `app.js` (`data-focus-after` = element id).
5. **Confirms:** Skip on a `done` page and Extract anyway on a `skipped` page use `hx-confirm` (both discard edits per the backend contract). Skip on `failed` and Extract anyway on `not_sales_order` don't.
6. **Review page:** "Order N of M - page X" nav with Previous/Next order and "All pages". Bottom action is "Next order", or on the last order "Back to all pages" plus "Continue to template".
7. **`conversion_status.html` / `_status.html`:** no route renders them (verified by grep). Kept with a one-line "superseded" Jinja comment for rollback, not deleted (no git in this project).
8. **Preview:** old single-order acknowledgement block kept commented out above the new one. The new block is shown when `needs_acknowledgement` and covers orders needing attention (linked to each page's review), failed pages and duplicate refs. Skipped pages get a separate info notice that never asks for acknowledgement.

## Verification
- `pytest -W error`: **343 passed** (before and after the final edits).
- Real run: uvicorn + real Tesseract/Poppler, real 10-page PDF read in place from Downloads, data dir in the scratchpad (deleted afterwards).
  - Mid-batch: fragment polled (`every 2s`), "1 of 10 pages done", rows processing/queued/not_sales_order. After it finished, polling stopped; pages 3/6/10 were done and the other 7 not_sales_order (matches the backend).
  - **URL fix:** on review page 6, every rendered `hx-patch/hx-post/hx-delete/src` URL is under `/conversions/1/pages/6` (none outside). Each URL was taken from the rendered HTML and replayed with `HX-Request`: PATCH customer, PATCH line quantity, confirm, add line, delete line, "use sum" PATCH: all 200 with the expected fragments and OOB parts. Page image 200 `image/png`. Prev/next = pages 3/10.
  - Batch actions from rendered URLs: extract-anyway page 1 -> `HX-Redirect: /conversions/1/pages/1`; skip page 1 (now done) -> 200 fragment, row `skipped`, no polling attrs, undo button carries its confirm; repeat skip 200; skip on a not_sales_order page 409 (no Skip button is rendered for it).
  - Template -> preview 200: Page column, "3 orders, 4 rows", attention links to pages 3/6/10, skipped-pages notice for 1,2,4,5,7,8,9, ack checkbox. Export without ack 422 (blocked notice, autofocused checkbox); with ack xlsx 200 `1st_week_of_Jan_Davao.xlsx`, one `Converted` sheet, 4 data rows; CSV 200 with BOM.
  - Headless Chrome (`--dump-dom`, console logged to stderr; the logger was checked with a probe page): no console errors or CSP violations on the batch, review and preview pages. Alpine initialised on review.
  - Screenshots checked: batch list mid-run (600px, cards) and finished (1280px, aligned rows), review (1280), preview (1280).
  - Server log: 0 tracebacks; the only 4xx were the intentional 409/422s and `/favicon.ico` 404s.
- Not verified: real clicks/keyboard in an interactive browser (no automation library in the venv, and I did not add one). So focus restore after skip/retry, `hx-confirm` dialogs, `hx-sync` abort and arrow-key nav were checked by reading the code only. True phone width (<500px) is also unverified: headless Chrome won't go below ~500px, and the untouched upload page clips the same way, so the 390px shot proves nothing.
- A harness gotcha (not an app bug): Git Bash rewrites curl args like `next=/conversions/...` into Windows paths. Use `MSYS_NO_PATHCONV=1`.

## Files
`templates/`: `batch.html` (new), `_batch_status.html` (new), `_macros.html`, `review.html`, `_field_cell.html`, `_line_row.html`, `_line_items.html`, `_totals_check.html`, `upload.html`, `choose_template.html`, `preview.html`, `conversion_status.html` / `_status.html` (comment only); `static/app.css`, `static/app.js` (one branch).

## Review
Done; next is code-reviewer-qa. Suggested focus: an interactive browser pass on keyboard focus after Skip/Retry and on phone width.
