# Enter-key no-op confirm (MEDIUM-4) and status timing copy (LOW-9)

Scope: `converter/app/static/app.js`, `converter/app/templates/_status.html` only. `preview.html` deliberately untouched (a backend fix round is editing it in parallel).

## Plan
- [x] MEDIUM-4: Enter in the last review field dispatched `change` unconditionally, so a no-op PATCH marked an unedited field (e.g. blank `total`) as confirmed. Only dispatch when `input.value !== input.defaultValue`.
- [x] LOW-9: replace placeholder "under a minute" copy with measured timing (single-page OCR 9-23 s end to end): "This usually takes 10-25 seconds." Drop the placeholder comment.
- [x] Verify behaviorally in headless Edge against the scratchpad UI harness; confirm the new check fails on the old code.
- [x] Full pytest run.

## Review
- `app.js` (Enter case in `onKeydown`): old line kept commented above the fix for rollback.
- `_status.html`: old copy kept as a Jinja comment above the new copy.
- Headless Edge self-test (`enterlast` mode, 10 checks), last field in the harness is `total` (blank, `not_read`):
  - fixed code: 10/10 pass. No-op Enter sends 0 PATCH, tier stays `not_read`; edit + Enter sends 1 PATCH and the cell becomes `tier--checked`; the swapped input's `defaultValue` is the saved value so a repeat Enter sends nothing; Enter in a non-last field still moves focus.
  - old code: 5 checks fail (no-op Enter sent a PATCH and turned the blank `total` `tier--checked`), reproducing the reviewer's report.
- Existing `review` self-test: 18-19/19. The one intermittent miss ("no-bbox field hides overlay") is a 100 ms timing race in the harness on the focus handler, which this change doesn't touch; it passed on a re-run.
- `pytest -W error`: 278 passed. A first run collided with the backend agent's in-progress edits (18 TypeErrors in `review.py`/`routes_conversions.py`); passed cleanly once those settled.
- Side effect to be aware of: after a 422 (rejected value kept in the input as its `value` attribute), pressing Enter again in the last field without changing the text no longer re-sends the same rejected value. That is intended, since it would just be rejected again.
- Not visually verified in a headed browser; behavior was checked by a scripted headless run only.
