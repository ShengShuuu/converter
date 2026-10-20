---
name: converter-dev
description: Use when writing or reviewing code for this OCR-based scanned-order-to-Excel converter — the extraction layer, layout profiles, flags/confidence, TSV fixtures, or the xlsx/csv export. Trigger on "extraction", "layout profile", "OCR", "TSV fixture", "flag", "confidence", "Order/OrderLine", "write_xlsx".
---

# Converter Project Conventions

Project-specific conventions for `converter/` (see `CLAUDE.md` for the full architecture and confirmed tech stack). This skill is the project's own equivalent of a framework-standards skill — there is no Odoo involvement in this codebase.

## Running tests
This machine's venv is Python 3.10 (the stack targets 3.12, but 3.10-compatible code is required until the venv is upgraded). The package runs from source via pytest's `pythonpath`, not pip-installed.

```
C:\Users\robtan\Desktop\converter\.venv\Scripts\python -m pytest -W error
```
Always run with `-W error` — a silent `DeprecationWarning` in openpyxl/pdf2image is worth catching early.

## Extraction layer rules (`converter/extraction/`)
- **Pure logic only.** No `subprocess`, no file I/O, no `open(`, no `requests`. It takes Tesseract-TSV-shaped word records (text, bbox, confidence) and a `LayoutProfile`, and returns `Order`/`OrderLine` objects. Grep for these before merging any change here.
- **Money is always `Decimal`.** Never `float`. Parse by stripping currency marks and validating thousands-comma placement — a malformed number must produce `None` plus an `unparsed:<field>` flag, never a guessed value.
- **Dates use explicit `strptime` formats only**, taken from the layout profile. Never `dateutil`'s guessing behavior, never `fromisoformat` on an ambiguous string.
- **Every `Order`/`OrderLine` field is nullable.** A blank subtotal or taxes value is valid extracted data — don't default it to `0` or `""`.
- **Flag naming convention:** `<kind>:<field>`, e.g. `unparsed:total`, `not_found:total`, `extra_text:total`, `low_confidence:product`, `amount_mismatch`, `lines_total_mismatch`, `table_end_not_found`. Match this shape for any new flag — don't invent a different naming style.
- **Validation only adds flags; it never changes confidence or silently corrects a value.** Confidence comes from OCR scores.
- **A failure to find something must be visible.** The home-grown bug class in this codebase is "value becomes `None` with zero flags" — any code path that can produce a null/missing result needs a matching flag, or an explicit code comment explaining why none is needed.
- **Layout profiles** (`converter/profiles/*.json`) hold anchors, label aliases, column definitions, `positional_fields` (fields found relative to another anchor rather than by a label, e.g. customer), and `pending_fields` (fields with no confirmed source yet — empty as of this writing) — don't hardcode anchor text or column positions directly in extraction code; read them from the profile so a new form layout doesn't need new code.

## TSV fixtures (`tests/fixtures/*.tsv`)
Written in real Tesseract 5 TSV format (`level, page_num, block_num, par_num, line_num, word_num, left, top, width, height, conf, text`). When adding a regression test for an extraction bug, build a realistic TSV variation of the existing fixture rather than hand-constructing word objects in Python — it exercises the real parsing path (`converter/extraction/words.py`) too.

A synthetic fixture only proves the extraction *code* is correct on a clean case. It does not substitute for testing against a real scanned file — say so explicitly in any report that relies only on synthetic fixtures.

## Export layer (`converter/export/xlsx.py`)
- Writes into the user's uploaded template, preserving its header row, column order, and column widths.
- Must strip every sheet except the identified header/target sheet from the output — never carry over other sheets (e.g. a stale sheet from a prior conversion that was re-uploaded as a template).
- Writes typed cells (`Decimal`, `datetime`), never strings, so the output stays correctly typed for whatever consumes it next.
- Never modify the source file passed in as a template — always write to a new output path.

## Project-wide rollback convention
When replacing existing logic, comment out the old code above the fix with a one-line reason, rather than deleting it. This applies in `converter/` and `tests/`.
