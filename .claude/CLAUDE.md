# Response Language
- Lahat ng plans, questions, at suggestions ay isusulat sa Tagalog, kahit Ingles ang gamit ng user sa chat.
- Dapat natural at "humanized" ang tono — parang kausap mo talaga tao, hindi stiff o literal na translation.
- Code, file names, at technical terms (variable names, function names, commit messages) ay nananatili sa Ingles — Tagalog lang ang explanation/conversation layer.

---

# Project: Scanned Order-to-Excel Converter

A standalone Python web tool. **Core scope (confirmed 2026-10-17, extended 2026-10-17 to multi-page batch): upload a scanned PDF or image (single or multi-page) → every page is OCR'd, classified, and extracted independently (one page = one order; non-Sales-Order pages are skipped and shown as such, not silently dropped) → user reviews/corrects each extracted order from a batch list → user selects or creates a target Excel template once for the whole batch → combined preview across all reviewed orders → single download `.xlsx`/`.csv`.** Each Sales Order is assumed to fit on one page (no cross-page order merging) — confirmed with the user, not expected in real files. Max 50 pages per upload. No login/auth — an internal tool.

## Confirmed tech stack
- Language: Python 3.10 (dev venv; `pyproject.toml` targets 3.12 for eventual deploy — keep all code 3.10-compatible until the venv is upgraded: no `datetime.UTC`, `StrEnum`, `typing.Self`, `tomllib`)
- Web framework / server: FastAPI + Uvicorn (`--workers 1`, required — see Architecture shape)
- Frontend: Jinja2 templates + HTMX (server interactions, autosave) + Alpine.js (bbox highlight, keyboard nav) — no Node build step
- Data models / validation: Pydantic v2
- Database: SQLite via stdlib `sqlite3` (WAL mode), thin repository layer
- Background processing: in-process `ThreadPoolExecutor(max_workers=1)` inside the FastAPI app (changed 2026-10-17 from a separate worker process — see Architecture shape)
- PDF rasterization: `pdf2image` (Poppler `pdftoppm`, 300 dpi) — not PyMuPDF (AGPL)
- Image preprocessing: OpenCV (`opencv-python-headless`) + numpy; Pillow for I/O and EXIF orientation
- OCR: Tesseract binary via `subprocess` (TSV output, OSD for rotation) — not pytesseract
- Fuzzy matching: rapidfuzz
- Excel export: openpyxl (writes into the user's uploaded template, preserving formatting)
- CSV export: stdlib `csv` with UTF-8 BOM (`utf-8-sig`) — Decimal as plain `1234.50` (no thousands separator), datetime as `%Y-%m-%d %H:%M:%S`
- Money / dates: `decimal.Decimal`; explicit `strptime` formats (never float, never `dateutil` guessing)
- Testing: pytest + FastAPI `TestClient`, golden fixtures from hand-labelled sample pages
- Deploy: run directly on the host (Python 3.12 + Tesseract/Poppler installed locally) — no Docker; published as a plain GitHub repo (changed 2026-10-20, Docker dropped as unneeded for this internal tool)

## Architecture shape
**One process (changed 2026-10-17, user-approved).** The original two-process design (API + separate OCR worker) was built for multi-page batch processing; the confirmed core scope is single-order upload. **Measured single-page timing (2026-10-17, real scanned pages, no contention): 9-23 seconds end to end.** Most of that (60-80%) is PDF-to-image rasterization (`pdftoppm` at 300 dpi), not Tesseract itself (~1s). UI copy should say "usually 10-25 seconds" (the frontend currently has a placeholder saying "under a minute" — needs updating to match this measurement). A single FastAPI process runs OCR via an in-process `ThreadPoolExecutor(max_workers=1)`, with status tracked in SQLite and the browser polling via HTMX (`hx-trigger="every 2s"`). `max_workers=1` because timing data showed OCR contention (not CPU count) was the bottleneck.
- Must run with a single Uvicorn worker (`--workers 1`) — in-flight job state depends on it.
- On startup, any `conversions` row stuck in `queued`/`processing` (from a prior crash/restart) is marked `failed` with a clear error and a Retry button, since in-process state doesn't survive a restart.
- No microservices, no Redis/Kafka/Kubernetes, no Docker — this stays a small internal tool run directly on the host. See `tasks/` for the full architecture/process-flow documents.

**Multi-page batch (added 2026-10-17, user-directed, closed out and QA-verified 2026-10-19):** one upload can contain many pages (max 50); each page is processed sequentially (no parallelism) within a single `ThreadPoolExecutor(max_workers=1)` submission that loops claiming the next queued page. Schema: `conversions` tracks the upload (filename, page_count, overall status, template_id); a child `pages` table holds one row per page (own status/extracted_order_json/order_json/confirmed_fields_json — a page IS an order attempt, since a non-Sales-Order page also gets a row with status `not_sales_order`). Progress ("N of M pages done") is derived from `COUNT(*) ... GROUP BY status`, never stored as a separate counter, to avoid drift. Routes are keyed by `page_number` (stable across "extract anyway"/skip actions), not by a list index. A manual "Skip this page" action exists alongside "Extract anyway" for when the classifier gets it wrong in either direction — "extract anyway" on a `skipped` page that still has a saved order restores it losslessly (no re-extraction, edits/confirmations intact), only falling through to real re-extraction for a `not_sales_order` page or a skipped page with no prior order. Export combines all `done` pages' orders into one file; the per-field review/flag/tier logic from the single-order round is reused unchanged per page, just aggregated (summed counts, not merged field-key sets, across orders) for the batch-level export gate; export is also blocked while any page is still `queued`/`processing`, even if the overall conversion already shows `done` (this is NOT bypassable by the "acknowledge unchecked fields" checkbox, unlike the failed-page warning, since that page's data would otherwise be silently missing from the file). A dedicated test (`test_every_rendered_review_url_is_page_scoped_and_resolves` in `tests/test_app.py`) renders real templates and replays every URL they emit to guard against a hardcoded conversion-level URL regressing into a page-scoped partial — this exact bug class (tests passing while the real browser flow was 404ing) slipped through once already during this feature's development, so this guard is load-bearing, not decorative.

## Always-verify fields
Regardless of flags, these fields get a distinct "always verify against the scan" visual treatment in the review UI, because real-file testing showed they're unreliable even when nothing else is flagged (the Total bar isn't read at all on any tested real page, and numbers are frequently OCR-garbled): `total`, `untaxed_total`, `subtotal` (per line), `unit_price` (per line), `order_date`. Defined in the layout profile as `always_verify_fields` (per `converter-dev`'s rule against hardcoding form-specific knowledge in code) — add this field to `LayoutProfile` since profile models use `extra="forbid"`.

## Canonical data model
`Order { id, order_ref, order_date, customer, salesperson, payment_terms, untaxed_total, total, source_pages, flags, lines[] }`
`OrderLine { product, quantity, unit_price, subtotal, taxes, confidence, bbox, flags }`
Every field is nullable — a blank subtotal/taxes is valid extracted data, not an error.

## Resolved decisions
- **Subtotal:** each line item carries its own amount (per-line, not an order-total-on-first-row-only pattern).
- **Product field:** the full printed description goes into the output (not a short code extracted from it).
- **Extra template sheets:** `write_xlsx` always strips every sheet except the target/header sheet from the output, regardless of what template is uploaded.
- **Customer:** read from the top of the form, before the "Order #" line — the first line of the unlabeled partner/address block.
- **Payment Terms:** read from the "Mode of Payment:" line near the bottom of the form (values look like "GCASH PAYMENT", "CASH ON DELIVERY", "POS / THERMAL PAYMENT", etc.).
- **No Odoo re-import involved.** This app's output is a standalone Excel/CSV file — it is not re-imported into Odoo. Odoo import field-path semantics are not a constraint; the target template headers just happen to look like Odoo's export format.

## Real-file milestone (2026-10-17)
A real scanned sample PDF was provided (10 pages, mixed document types — only 3 pages are actual Sales Order forms; the rest are Collection Receipts, Delivery Forms, a Sales Invoice, etc.). The OCR worker (`converter/ocr/`) was built and run end-to-end for the first time. Page classification correctly identified all 3 Sales Order pages and rejected the 7 others. Extraction accuracy on the 3 real pages was measured at 9/41 fields correct (field-level, against hand-transcribed ground truth) and has not moved despite several rounds of extraction-layer fixes — see below.
**The real sample file itself must never be copied into the repo or into `tests/fixtures/`** (it contains real customer PII — names, addresses, phone numbers, TINs). Read it directly from wherever the user keeps it; derived test fixtures must stay synthetic. **`data/` (runtime uploads, OCR output, the SQLite DB) is gitignored as of 2026-10-20** — a real end-to-end test run had left a real conversion's PII sitting in `data/conversions/` with no ignore file in place; it's been deleted and `.gitignore` now covers `data/`. Always delete `data/` contents after testing with the real file, don't rely solely on the ignore files.

**Fixes applied so far (all on the pure extraction layer, none moved the 9/41 accuracy number — they improved visibility/flagging, not OCR correctness):**
- `not_found:total` when a required totals row is genuinely absent (per-`TotalSpec` `required` option); `column_not_found:<field>` when an optional column's header wasn't matched; a row-start digit guard (`has_digit`) to reduce phantom line items from non-digit noise; `not_found:product` to flag a near-empty phantom line even when a stray OCR token happens to look like a digit; `parse_money` to tolerate a short peso-like glyph (leading or trailing) adjacent to a number before parsing.
- See `tasks/silent-failure-flags-and-row-start-guard_2026-10-17_11-30.md`, `tasks/first-real-scan-ocr-pipeline_2026-10-16_19-12.md`, `tasks/peso-glyph-and-phantom-line-flags_2026-10-17_11-39.md`.

**Conclusion: the extraction-layer logic is no longer the bottleneck. The real bottleneck is OCR/image-preprocessing quality itself** — real subtotal/total cells come out of Tesseract as severely garbled tokens (e.g. "7ZOL0P", "2812509){") that no amount of post-hoc parsing tolerance can safely recover, and the Total bar's text (printed on a dark/colored background) is not read by Tesseract at all on any tested page, even after preprocessing. Further extraction-logic fixes are not expected to move the accuracy number — the next work has to target OCR/preprocessing (image quality, PSM tuning, contrast handling for the Total bar) or reconsider whether traditional OCR can meet accuracy needs for this specific form/scan quality.

**Preprocessing round (2026-10-20): the scan input quality itself is the measured ceiling, not the preprocessing code.** ~60 preprocessing/Tesseract configurations were tried and measured against hand-transcribed ground truth (dark-bar contrast handling, red/blue ink removal tuning at multiple strengths, CLAHE, adaptive/Sauvola binarization, separate bar-crop OCR with different PSMs) — none improved accuracy without a corresponding regression elsewhere; accuracy stayed at 9/41. Root cause, independently verified via `pdfimages -list` and JPEG quantization-table inspection: the 3 real Sales Order pages in the source PDF were saved by CamScanner at roughly JPEG quality 50-65 (271-286 ppi actual captured resolution), while other pages in the SAME export are quality 100 — so rasterizing above 300dpi cannot recover detail that was never captured, and this is a scan/export setting problem, not a code problem. No preprocessing code was changed as a result (the attempt is fully documented, with exact rejected parameters and why, in `tasks/ocr-preprocessing-total-bar-and-ink_2026-10-20_09-37.md` — read it before re-attempting any of these approaches). A secondary cause: Tesseract's default page-wide Otsu threshold means a pixel edit localized to one region (e.g. inside the Total bar) can change OCR output in unrelated regions of the same page — so small (±1-3 field) accuracy deltas between preprocessing variants on a 3-page sample are noise, not signal; compare at the OCR-token level or via isolated-crop OCR to get a meaningful read.
**Recommended next step: re-scan/re-export the source documents at maximum/"Original" quality in CamScanner (not a software fix available on our side) — this is the single most effective lever identified.**

## Still open
- The salesperson suffix sometimes seen in sample data (e.g. "- Tech. Support") — source on the physical form not yet confirmed; extract the salesperson value as printed and flag if a suffix convention needs to be added later.
- **Value-below-label positioning:** on the real form, Order Date and Salesperson values print on the line below their labels, not beside them as our synthetic fixtures assumed — needs a new `value_position: "below"` profile option.
- **Total-bar OCR contrast — ROOT CAUSE IDENTIFIED (2026-10-20), not a code problem.** The word "Total" itself IS now readable (min-channel + white top-hat preprocessing reads it on 3/3 real pages, vs 0/3 at baseline), but the amount next to it is not, on 2 of 3 pages — because the source scan's JPEG quality (~50-65) doesn't contain enough detail to begin with, confirmed via quantization-table inspection. Adaptive/Sauvola binarization and isolated bar-crop OCR (multiple PSMs) were all tried and added nothing. **The fix is a higher-quality re-scan, not more preprocessing work** — see the "Preprocessing round (2026-10-20)" note above and `tasks/ocr-preprocessing-total-bar-and-ink_2026-10-20_09-37.md`. Do not re-attempt these same ~60 configurations without new input data.
- **General OCR garbling of printed numbers** (e.g. "7ZOL0P" for a subtotal) — confirmed (2026-10-20) to be a source-scan-quality ceiling, not something higher rasterization DPI or further preprocessing can fix (the source is only 271-286 ppi to begin with). Needs a higher-quality re-scan.
- **Total-bar label rejected as a product name on page 6 (NEW, 2026-10-20):** the "Total" label's x-position on this page's bar falls inside the DESCRIPTION column's span, so `_find_totals_row` (`converter/extraction/extract.py:232`) rejects it as if it were a product line rather than the totals row. Unlike the OCR-quality items above, this IS a fixable extraction-layer bug — worth a small follow-up round.
- **No `low_confidence:<field>` flag exists for totals values (NEW, 2026-10-20):** a totals value with no other flag only gets the soft `always_verify_fields` "verify" tier in the UI, which the "acknowledge unchecked" export checkbox can bypass — unlike a hard block. A bar-crop OCR experiment this round (not shipped) produced a confidently-wrong `total=4` with zero flags, which would only be caught by the always-verify tier, not by any confidence signal. Worth adding alongside the page-6 fix above.
- **No `total` vs `untaxed_total` sanity check (NEW, 2026-10-20):** the line-sum cross-check (`converter/extraction/validate.py:41`, `converter/app/review.py:192`) compares extracted lines against `untaxed_total` when present, but never cross-checks `total` itself — so the UI's "totals agree" indicator can show green next to a `total` value that's actually wrong (e.g. `total=4` with a correct `untaxed_total=4312.50` both present). A cheap `<kind>:<field>` flag (e.g. `amount_mismatch` on `total`, or `total_below_untaxed`) would close this.
- **Page-classifier confidence margin:** classification anchors are currently unweighted, so a real Sales Order page passed by a single matching anchor while an unrelated Sales Invoice page scored close to the threshold on generic column labels alone. Works correctly today (10/10 on the real file) but the margin is thin — consider weighting toward form-specific labels (Order #, Mode of Payment) over generic table headers.
- `column_not_found:<field>` currently can't distinguish "this column doesn't exist on this form variant" from "the column header was printed but OCR missed it" — needs a real non-VAT-layout page to confirm whether the Taxes column is always printed before tightening this.
- `not_found:<field>` on a totals field currently doesn't distinguish "the whole row is missing" from "the row was found but had no value in the amount column" — informational gap, not a silent failure.
- Known extraction gap (MEDIUM, deferred to real-scan tuning, not a blocker on its own): a stray block of text (a stamp, or unrelated text in another column) positioned entirely between the real customer block and the "Order #" line, with no row overlap with the real customer block, is accepted as the customer with zero flags. Documented in `tasks/customer-block-ambiguity-and-test-fixes_2026-10-16_15-05.md`'s "Known limitations" (MEDIUM-9/MEDIUM-1).
- **Closed (2026-10-17):** both MEDIUM findings from code-reviewer-qa's last review round are resolved — (1) a phantom line item that absorbs a wrapped description line is now flagged via `not_found:quantity`/`not_found:unit_price`; (2) currency marks (`₱`, `PHP`, `$`) are now stripped only at the string's edges, so a mid-number misread is rejected instead of silently parsed. See `tasks/phantom-wrapped-line-and-currency-edge-strip_2026-10-17_14-00.md`. **Residual (LOW, acceptable for now):** when this happens, only the phantom line gets flagged — the ORIGINAL line above it, whose product field got silently truncated (losing the absorbed wrapped text), still has zero flags. Revisit once real-scan data shows how often this actually occurs. The extraction layer is considered closed for now; remaining accuracy work is OCR/preprocessing (items above).

---

# Workflow Orchestration

## 1. Plan Mode Default
- Enter plan mode for ANY non-trivial task (3+ steps or architectural decisions)
- If something goes sideways, STOP and re-plan immediately
- Use plan mode for verification steps, not just building
- Write detailed specs upfront to reduce ambiguity

## 2. Subagent Strategy
- Use subagents liberally to keep main context window clean
- Offload research, exploration, and parallel analysis to subagents
- For complex problems, throw more compute at it via subagents
- One task per subagent for focused execution

## 3. Self-Improvement Loop
- After ANY correction from the user: update `tasks/lessons.md` with the pattern
- Write rules for yourself that prevent the same mistake
- Ruthlessly iterate on these lessons until mistake rate drops
- Review lessons at session start for relevant project

## 4. Verification Before Done
- Never mark a task complete without proving it works
- Diff behavior between main and your changes when relevant
- Ask yourself: "Would a staff engineer approve this?"
- Run tests, check logs, demonstrate correctness

## 5. Demand Elegance (Balanced)
- For non-trivial changes: pause and ask "is there a more elegant way?"
- If a fix feels hacky: "Knowing everything I know now, implement the elegant solution"
- Skip this for simple, obvious fixes -- don't over-engineer
- Challenge your own work before presenting it

## 6. Autonomous Bug Fixing
- When given a bug report: just fix it. Don't ask for hand-holding
- Point at logs, errors, failing tests -- then resolve them
- Zero context switching required from the user
- Go fix failing CI tests without being told how

---

# Task Management

File naming: every session/fix gets its own file at `tasks/<problem-name>_<YYYY-MM-DD>_<HH-MM>.md` (kebab-case problem name, 24h time) — never reuse or overwrite a prior session's file.

1. **Plan First:** Write plan to that session's file with checkable items.
2. **Verify Plan:** Check in before starting implementation.
3. **Track Progress:** Mark items complete as you go.
4. **Explain Changes:** High-level summary at each step.
5. **Document Results:** Add a review section to the same file once done.
6. **Capture Lessons:** Update `tasks/lessons.md` (shared, ongoing) after corrections.

---

# Where Fixes Go

This is a from-scratch Python project (no legacy/vendor code to avoid touching). All work goes under the project package (e.g. `converter/app`, `converter/worker`, `converter/ocr`, `converter/extraction`, `converter/mapping`, `converter/export`, `converter/store`) and `tests/`. Vendored third-party JS (`htmx.min.js`, `alpine.min.js`) under `static/` is never hand-edited — replace the file wholesale on a version bump instead of patching it in place.

---

# Code Style — Every Fix

- Comments must be clean, precise, and short — one line max, only when the WHY isn't obvious from the code itself. No restating what the code does, no multi-line blocks.
- Names (functions, variables, methods) must be clear and self-explanatory enough that most comments become unnecessary — prefer a better name over a comment explaining a bad one.
- Apply this to every fix, not just new features — when touching existing code, clean up unclear naming/comments in the lines actually changed (don't do a drive-by rewrite of untouched code).
- No emojis anywhere — not in code, comments, commit messages, or docstrings.
- Money is always `decimal.Decimal`, never `float`. Dates are parsed with explicit `strptime` formats, never guessed.
- FastAPI route handlers that touch SQLite, the filesystem, or openpyxl are `def`, not `async def` — they're blocking calls and an `async def` handler doing blocking I/O stalls the event loop.

# Post-Fix Verification Loop

After every fix/change is implemented (not just at the end of a big task):
1. Run `code-reviewer-qa` to verify the fix end-to-end (tests, logs, actual behavior) — same as the existing Verification Before Done rule.
2. Re-run the `.claude` end-to-end self-check: confirm agents/skills frontmatter is still valid, no broken references, and this CLAUDE.md is still internally consistent.
3. Check for new gaps: did this fix reveal a missing agent, missing skill, or missing rule here? If yes, propose and add it before considering the task closed.

---

# Dev Team (Agents & Skills)

Use the specialized subagents below instead of doing their work directly in the main context. Match the task to the agent, not the other way around.

## Agent Invocation Rule
A subagent starts with a blank context — it does not automatically inherit this file. Every prompt handed to an agent must restate, in short form, the three standing constraints that apply project-wide:
1. Scope: this is the standalone Python converter project described above — never the Odoo/fc_erp live system, even if an agent's own definition mentions Odoo-flavored defaults.
2. Code Style: short/precise comments only where WHY isn't obvious, clear naming, no emojis, `Decimal` for money, explicit date formats, sync `def` for blocking FastAPI handlers.
3. Which agent should pick up the result next (e.g. `debugger` hands off to an implementer, implementer hands off to `code-reviewer-qa`).
Do not rely on the agent's own `.claude/agents/<name>.md` definition alone to carry these — that file is loaded for agents discovered via the Agent tool's own listing, but the delegation prompt itself should not assume it.

## Agents
- **debugger** — any pasted error/traceback/log/failing test from anywhere. Diagnoses root cause first; does not implement the fix itself.
- **architect** — any non-trivial feature (3+ steps or design tradeoffs) before implementation. Produces the plan; does not write code.
- **backend-engineer** — FastAPI routes, the OCR worker, the extraction/parsing layer, SQLite store, mapping engine, export (openpyxl/csv).
- **frontend-engineer** — Jinja2 templates, HTMX fragments, Alpine.js interactivity (review grid, bbox highlight), static assets.
- **code-reviewer-qa** — after implementation, before marking any task done. Reviews diffs and runs/writes pytest tests.
- **devops-deployment** — running the single app process directly on the host, verifying a running instance, Tesseract/Poppler environment setup.
- **integration-api** — any future external API/webhook integration (not currently in scope; this app's output is a standalone downloaded file, not re-imported anywhere).

## Skills (auto-load when relevant, by any agent or in the main context)
- **converter-dev** — this project's own conventions: extraction-layer purity, flag naming, layout profiles, TSV fixtures, xlsx export rules. Check for every change to `converter/`.
- **python-pro** — typing, async vs sync handler boundaries, structured error handling.
- **frontend-ui-ux** — any UI/component/layout/form work on the review/mapping/preview screens.

## Default flow for a non-trivial task
1. `architect` plans it (satisfies Plan Mode Default above).
2. `backend-engineer` and/or `frontend-engineer` implement, loading the relevant skill(s) above as needed.
3. `code-reviewer-qa` reviews + verifies before the task is marked complete (satisfies Verification Before Done above).

## Default flow for a bug report / pasted error
1. `debugger` diagnoses the root cause first (satisfies Autonomous Bug Fixing above — never patch blind).
2. The relevant implementer (`backend-engineer`/`frontend-engineer`/`integration-api`/`devops-deployment`) applies the fix at the confirmed location.
3. `code-reviewer-qa` verifies the fix before marking done.

---

# Core Principles

- **Simplicity First:** Make every change as simple as possible. Impact minimal code.
- **No Laziness:** Find root causes. No temporary fixes. Senior developer standards.
- **Minimal Impact:** Only touch what's necessary. No side effects with new bugs.
