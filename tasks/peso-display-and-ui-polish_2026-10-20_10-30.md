# Peso display and UI polish pass - 2026-10-20 10:30

Frontend round. Two confirmed asks: (1) show a peso sign on monetary values in the UI only, (2) visual polish across every screen
(generic professional business-tool look, no structural/functional redesign). Next: code-reviewer-qa.

## Constraints
- Display-only: `converter/extraction/` untouched; stored `Decimal` values, export `.xlsx`/`.csv`, and every input's editable `value` stay plain numbers.
- No route/URL/context/template-logic change that affects behavior. Field-tier (not_read/check/verify/checked) system kept exactly.
- No inline styles/scripts (strict CSP). Accessibility properties never removed.

## Plan
- [x] 1. Baseline screenshots of every screen (scratchpad copy of `data/`, headless Chrome) before changes
- [x] 2. One `peso` Jinja filter (new `converter/app/display.py`), registered in `main.py`; `is_money_path` Jinja global for the preview's money columns
- [x] 3. Apply: `_field_cell.html` (non-editable peso adornment beside money inputs), `_totals_check.html`, `_batch_status.html` (row total), `preview.html` (money columns + note that the file keeps plain numbers)
- [x] 4. Tests for the filter + an app-level display-only test
- [x] 5. CSS polish pass in `app.css`
- [x] 6. `pytest -W error`, real app run, screenshots desktop + narrow, bbox overlay check, console/CSP check

## Decisions
1. **Peso format:** `peso("2900.00") -> "₱2,900.00"`. Thousands separators on display only; decimal places shown exactly as stored (no rounding, so `12.345` stays `₱12.345`). Negative -> `-₱50.00`. `None`/blank -> empty string. Non-numeric text (should not happen, values are Decimal) is shown unchanged rather than hidden.
2. **Money detection** reuses `field_input.FIELD_KINDS` (`field_kind(field) == "money"`): unit_price, subtotal, untaxed_total, total. No duplicated field list in templates.
3. **Review inputs:** the `₱` is an absolutely-positioned, `aria-hidden`, `pointer-events: none` adornment inside a new `.cell-input-wrap` div; the input gets extra left padding. The input `value` is untouched. Screen readers get "Amount in pesos." through `aria-describedby` (label text left unchanged, since `app.js` reuses it for the scan caption).
4. **Preview:** money columns are right-aligned, nowrap, prefixed with `₱`. Lead copy now says "Amounts are shown with ₱ here; the file holds plain numbers."
5. **Batch list** row meta: `Total: ₱5,800.00`.

## Structural (markup/layout) changes, with before/after
- `_field_cell.html`: input now wrapped in `<div class="cell-input-wrap[ --money]">` (before: input was a direct child of `.field-cell`). `app.js` only uses `closest(".field-cell")` / `.cell-input`, so behavior is unchanged (verified in browser).
- `batch.html`: `page_class` block set to `page--medium` (max-width 1100px; before: default 900px). The page-status row's body column no longer wraps every warning into 3 lines.
- `.review__next`: now separated from the editor by a top border with more top padding (old rule kept commented out above).
- Everything else is styling only.

## Visual changes (app.css)
- New tokens: `--brand-tint`, `--text-strong`, `--border-subtle`, `--surface-muted`, `--radius-lg`, `--radius-pill`, `--shadow-sm/md`, `--space-7`, type scale `--text-xs..--text-2xl`. One-off font sizes (0.8/0.9/0.925/0.95/1.05/1.1rem) replaced by the scale.
- Header: 3px cerulean bottom accent; Templates link no longer underlined (underline on hover).
- Panels/editor sections/scan pane/review summary: softer border + subtle shadow, 8px radius; a panel's leading h2 and editor section titles get a hairline divider.
- Headings: h1 1.375rem (1.625rem on desktop), consistent 700 weight; labels/strong text in a darker `--text-strong`.
- Buttons: hover transitions (disabled under reduced motion), primary has a slight shadow, ghost/secondary hovers tinted; disabled primary keeps its color on hover.
- Inputs: hover border darkens; file input is a dashed drop-zone-looking field with a styled "Choose File" button; radios/checkboxes use brand `accent-color`.
- Template choice: hover tint, selected card gets a brand inset bar.
- Batch list: `done` rows get a shadow, informational rows (not a Sales Order/skipped) are more compact with muted smaller text so actionable rows stand out; status badges slightly tightened.
- Progress row (processing state): tinted strip, larger "N of M pages done".
- Review: line items get a muted background with an uppercase small "LINE 1" header; totals-check figures are larger with small uppercase captions; "This is correct" buttons get a white background so they read on tinted tier cells; tier legend sits in a bordered box.
- Mapping table: uppercase small column headers, row hover, bordered "Suggested" tag.
- Preview table: stronger header, money columns right-aligned.
- Back links (`All uploads`, `All templates`, `All pages`) get a leading arrow (CSS `content` with empty alt text).
- Empty states: centered with more padding.
- **Unchanged:** tier color variables, `.tier-badge*`, `.tier--*`, `.field-cell--invalid/--save-failed`, `.page-status--*` glyph/border-style system, focus ring (3px cerulean, still on every element), `.scan__canvas/.scan__image/.scan__box` geometry.

## Verification
- `pytest -W error`: **360 passed** (348 before + 10 `test_display.py` peso cases + `test_is_money_path` + `test_peso_sign_is_display_only` in `test_app.py`). No existing assertion needed changing.
  - `test_peso_sign_is_display_only` checks: input `value="8360.00"` with no `₱` in any `value=""`; `<dd>₱8,360.00</dd>` in totals; `aria-describedby` includes the currency hint; batch list `Total: ₱8,360.00`; preview shows the subtotal with `₱`; exported CSV has no `₱` and the plain value.
- Real app run (uvicorn, scratchpad copy of `data/`, real OCR for a fresh upload; copy deleted afterwards). Money values seeded through the real PATCH endpoints.
- Headless Chrome via CDP (Node 22 built-in WebSocket, scratchpad script, nothing installed):
  - bbox overlay: for all 10 cells with a bbox, overlay position/size matched `bbox * img.clientWidth / page_width` within 1px at 1280px and 500px, zoomed and unzoomed; caption updates.
  - Keyboard: ArrowDown line 0 qty -> line 1 qty; ArrowUp from line 0 unit price -> line 0 qty (DOM fallback); Enter on last line -> next field; real Tab -> `:focus-visible` with 3px cerulean outline.
  - Clicking the peso adornment focuses the input.
  - Console: no errors, no CSP violations on upload, batch, review, template choice, templates, mapping, preview (only `/favicon.ico` 404).
- Screenshots (before/after) at 1280 and 500px for every screen, plus the mid-processing batch state and a bbox highlight.
- Not verified: true phone width under ~500px (headless Chrome minimum, as in the previous round); `hx-confirm` dialogs and failed/skipped page rows were not on screen in this data set (their CSS rules were only re-tokenized, not restructured).

## Files
`converter/app/display.py` (new), `converter/app/main.py`, `converter/app/static/app.css`,
`converter/app/templates/`: `_field_cell.html`, `_totals_check.html`, `_batch_status.html`, `preview.html`, `batch.html`;
`tests/test_display.py` (new), `tests/test_app.py`.

## Review
Done; next is code-reviewer-qa.
