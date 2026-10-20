# Converter UI redesign + Segoe UI

## Plan
- [x] Confirm scope with user: full app redesign, Segoe UI as primary font (asked via AskUserQuestion, user picked "full redesign" + "Segoe UI primary").
- [x] frontend-engineer: restyle app.css/app.js + templates (upload dropzone, stepper, card sections), simplify user-facing copy across templates, make Segoe UI the primary font-family.
- [x] code-reviewer-qa: review diff, run full test suite, verify HTMX/Alpine attributes, accessibility, no emoji, action semantics unchanged.
- [x] Fix the two LOW findings worth fixing immediately (see below).

## Result
- Visual redesign done: upload.html/templates_list.html get a real dropzone, base.html gets a restyled stepper with a brand mark header, app.css adds `.upload-hero`/`.dropzone`/`.btn--large` tokens on top of the existing Filmetrics brand palette (#004A73/#009BC9 unchanged).
- `app.css` `:root` font-family now leads with `"Segoe UI"` (was Source Sans Pro first, Segoe UI fallback).
- User-facing copy simplified across review/batch/preview/template-mapping screens (e.g. "Extract anyway" -> "Read it anyway", "Line items" -> "Items", "Preview" -> "Check before you download") without changing any hx-post/hx-get targets or button semantics.
- Full pytest suite: 380/380 passing, including the load-bearing `test_every_rendered_review_url_is_page_scoped_and_resolves` regression guard.
- code-reviewer-qa found no blocking defects. Fixed two LOW findings directly in review.html:
  - Restored the dropped export-gate hint ("downloading will ask you to confirm any fields you haven't checked yet") next to the "Choose Excel template" link.
  - Reworded the review banner from "compare the colored boxes" (color-only instruction) to reference the actual badge text ("Check this" / "Compare with scan").
- Not fixed (left as a follow-up, cosmetic only, not blocking): `converter/app/review.py` still emits older-style wording ("No item rows were found...", "field(s) have not been checked") that now sits next to the new template copy ("items", "not checked yet") on the same screen. Fixing requires a backend-engineer pass plus updating 5 test assertions in tests/test_app.py and tests/test_review.py that match the current strings.
- Not verified in a real (non-headless) browser: actual drag-and-drop gesture, Firefox/Safari `<progress>` rendering, Segoe UI fallback rendering on non-Windows OSes. The frontend-engineer did verify via headless Chrome at 1280px and 390px (no horizontal overflow, no console errors) and via FastAPI TestClient.
- Pre-change backup of templates/app.css/app.js was kept in the session scratchpad only (no git repo in this project) — not persisted anywhere durable since the change is already confirmed working.
