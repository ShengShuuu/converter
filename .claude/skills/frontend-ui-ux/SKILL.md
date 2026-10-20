---
name: frontend-ui-ux
description: Use whenever building or reviewing frontend UI — components, layouts, forms, navigation, styling, or any user-facing screen — regardless of framework. Covers UI/UX conventions (layout, states, accessibility, responsiveness) to keep interfaces consistent and usable. Trigger on "UI", "component", "page", "screen", "form", "layout", "design", "responsive", "accessibility".
---

# Frontend UI/UX

Framework-agnostic checklist for building or reviewing user-facing interfaces. Apply this on top of whatever stack the project uses (React, Vue, plain HTML, etc.) — check the codebase first for existing conventions and match them before introducing new patterns.

## 1. Before writing UI code
- Look for existing components/patterns in the codebase that already solve this (buttons, inputs, modals, layout wrappers). Reuse before creating new ones.
- Identify every state the screen/component can be in: empty, loading, populated, error, and (if applicable) partial/stale data. Design for all of them, not just the happy path.
- Confirm what happens on small screens — don't assume desktop-only unless the project explicitly is.

## 2. Structure & layout
- Use semantic HTML elements (`button`, `nav`, `header`, `form`, `label`) instead of generic `div`/`span` with click handlers.
- Keep a consistent spacing/sizing scale — don't invent one-off pixel values when the project already has spacing tokens/utilities.
- Group related actions and information visually; the most important action on a screen should be the most visually prominent.

## 3. States that are easy to forget
- **Empty state**: what does the user see with zero data? Never just a blank screen.
- **Loading state**: skeleton, spinner, or inline indicator — pick what matches existing patterns.
- **Error state**: a clear message and, where possible, a way to recover (retry, go back) — never a silent failure.
- **Disabled/pending state**: buttons/forms should reflect in-flight actions (disable + indicate) to prevent double-submits.

## 4. Accessibility (non-negotiable baseline)
- Every interactive element must be reachable and operable via keyboard (tab order, `Enter`/`Space` activation).
- Every input has an associated `label` (visible or `aria-label`), not just a placeholder.
- Color is never the only signal (e.g. errors need text/icon, not just red).
- Interactive elements have visible focus states — don't strip outlines without replacing them.
- Images/icons that convey meaning have `alt` text or `aria-label`; purely decorative ones are hidden from screen readers.

## 5. Responsiveness
- Test/design for at least mobile and desktop widths; don't assume a fixed viewport.
- Avoid fixed pixel widths on containers that hold text or user content — prefer flexible/relative sizing.
- Touch targets need adequate size and spacing on small screens (avoid cramped tap areas).

## 6. Consistency
- Match existing typography scale, color tokens, and component variants already used in the project instead of introducing new ones ad hoc.
- If a new pattern is genuinely needed, note it clearly so it can be reused rather than duplicated with slight variations elsewhere.

## 7. Filmetrics Brand System
Source: Filmetrics Corporation Brand Guidelines (Aug 2019). Apply these tokens for any Filmetrics-branded UI unless the project already defines its own design tokens — in that case, match the project's tokens instead of re-deriving from this list.

**Colors**
| Role | Name | Hex |
|---|---|---|
| Primary | Deep Blue | `#004A73` |
| Primary | Cerulean | `#009BC9` |
| Secondary | Dark Navy | `#00375E` |
| Secondary | Light Blue | `#B0CEE8` |
| Secondary | Neutral Gray | `#333333` |

- Secondary colors are for support only — never use them in place of the primary Deep Blue / Cerulean.
- Color is never the brand's only differentiator on a screen — still follow the accessibility rule above (don't rely on color alone for meaning).

**Typography**
- Primary/print typeface: **Nexa** (Light, Bold)
- Web typeface: **Source Sans Pro** (Light, Regular, Bold, Black, plus italics) — use this for actual web UI text since Nexa is a display/print face.

**Logo & graphic elements**
- Maintain minimum clearspace around the logo equal to the logo's own vertical height (x), on all sides, at any size.
- Never rotate, skew, stretch, recolor off-brand, add drop shadows/outlines, or rearrange parts of the logo (Symbol Mark or Combination Mark).
- On colored or dark backgrounds, use the black/white logo lockup; full-color logo is for white/light backgrounds only.
- The interconnecting bar graphic (from the graphic element system) can reinforce brand identity in headers/footers of a design, but don't overuse it as decoration in functional UI (forms, dashboards) where it would compete with content.
- Tagline "Connecting Identities" is a brand asset, not a UI microcopy pattern — don't repurpose it as button/label text.

## 8. Avoiding the "AI-generated" look
Generic AI output has recognizable tells. Actively avoid these instead of defaulting to them:

**Don't:**
- Purple-to-blue (or pink-to-orange) gradient hero sections/buttons as a default flourish
- Every card wrapped in the same `rounded-2xl shadow-lg p-6` treatment with no variation in hierarchy
- Emoji used as substitute icons (✨🚀💡) instead of a real icon set or none at all
- Centered-everything layouts with large, vague headlines ("Unlock Your Potential") and no concrete content
- Glassmorphism / frosted-blur cards applied reflexively regardless of brand or content
- Default framework font stack (e.g. Inter everywhere) when the project/brand has a defined typeface
- Excessive whitespace and giant icon-in-a-circle illustrations used to pad out thin content
- Three-column "feature grid with icon + heading + paragraph" as the go-to for any content type

**Do instead:**
- Start from the project's or brand's actual tokens (colors, type, spacing) — see the Filmetrics Brand System above when applicable — rather than a generic default palette.
- Let content density and real data drive layout; don't force real screens into a generic marketing-page template.
- Vary visual weight intentionally: one primary action per view, clear hierarchy, not every element equally emphasized.
- Use an icon set consistently (or none) — never emoji as UI iconography.
- Prefer concrete, specific copy over vague superlative marketing language in UI text.
- If referencing an existing design system or comparable product for inspiration, say so and adapt deliberately rather than reproducing generic template patterns.

## 9. Before marking done
- Run the app and actually exercise the feature in a browser when possible — golden path AND edge cases (empty/error/loading).
- Check behavior at a narrow viewport, not just default desktop width.
- If visual/browser verification wasn't possible, say so explicitly rather than claiming it was tested.
