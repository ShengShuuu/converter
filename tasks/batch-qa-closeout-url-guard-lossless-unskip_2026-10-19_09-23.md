# Batch QA close-out: rendered-URL guard, lossless undo-skip, pending-page export gate (2026-10-19)

Closes the 2 MEDIUM + 4 LOW findings from the multi-page batch full-stack QA round. LOW-8 (order_ref
whitespace nuance) intentionally left as-is (documentation nuance, not a bug).

## Plan / progress
- [x] MEDIUM-1: permanent regression test that renders the review page, collects every `hx-get/post/patch/put/delete`
      and `src` URL (from the page AND every fragment those requests return), asserts each is under
      `/conversions/{id}/pages/{n}/`, and replays each one expecting 200.
- [x] MEDIUM-2: extract-anyway on a `skipped` page that still has `order_json` restores it to `done` without
      re-extracting (edits + confirmations kept). `not_sales_order` pages and order-less skipped pages still re-extract.
- [x] LOW-4: export (and preview) blocked with a clear, non-acknowledgeable message while any page is
      `queued`/`processing`, even if the conversion row says `done`. Batch list `can_export` also requires 0 pending.
- [x] LOW-5: the worker's failure-recording write is guarded; a double failure is logged with `log.exception`
      and the batch continues. Secondary safety net already existed: `recover_interrupted` fails every
      `queued`/`processing` page at startup regardless of the parent conversion's status (now covered by a test).
- [x] LOW-6: skip 409 message distinguishes "not a Sales Order" from "still being read".
- [x] LOW-7: "Page 3 could not be read, so its order is ..." (singular) vs "their orders are ..." (plural).

## Changes
- `converter/store/repositories.py`: new `restore_skipped_page` (conditional UPDATE: `status='skipped' AND order_json IS NOT NULL`).
- `converter/app/routes_conversions.py`: undo-skip branch in `extract_anyway` + `_review_redirect` helper;
  `skip_page` 409 messages; `_pending_pages_error` used by `_preview_page` and `export_conversion`; `can_export`.
- `converter/app/review.py`: `_pages_text` renamed to public `pages_text` (reused by the pending-page message); singular/plural fix.
- `converter/worker/runner.py`: `_mark_page_failed` wrapper.
- `converter/app/templates/_batch_status.html` (two one-line copy tweaks, old lines kept as Jinja comments):
  the extract-anyway-on-skipped `hx-confirm` ("edits are lost") removed, since both paths are now non-destructive
  (restore keeps edits; an order-less skipped page has no edits to lose). The Skip confirm now says
  "Extract anyway brings it back with your edits kept." instead of "loses your edits".
- `tests/test_app.py`: 5 new tests, 2 existing tests' assertions tightened/updated (old lines commented out).

## Review
- `pytest -W error`: 348 passed (343 existing + 5 new).
- Mutation check of the MEDIUM-1 test: (a) reverting `_line_row.html`'s delete URL to the v1 conversion-level URL,
  and (b) dropping `order_url` from the `add_line` handler's fragment context (only visible in the POST /lines
  fragment, not the initial page) both make the test fail with a message naming the offending URL. Both reverted.
- Not re-run against the real 10-page PDF: these changes are route/store/worker-error-path only, no OCR/extraction change.
- Frontend follow-up (optional, not blocking): the button on a skipped page is still labelled "Extract anyway";
  "Undo skip" / "Restore" would describe the now-lossless action better. Label/template work belongs to frontend-engineer.
