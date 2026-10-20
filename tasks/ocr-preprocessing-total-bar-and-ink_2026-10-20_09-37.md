# OCR/preprocessing round: Total bar, red/blue ink, faint print (2026-10-20 09:37)

Goal: move real-file accuracy off 9/41 by fixing the OCR/preprocessing blockers listed in CLAUDE.md's "Still open" section. Scope was `converter/ocr/preprocess.py` and `converter/ocr/tesseract.py` only. `converter/extraction/` was not touched.

Source file: the 10-page CamScanner PDF, read in place from the user's Downloads folder.
- Pages scored: 3 (S07341), 6 (S07340), 10 (S07343), against the user-confirmed ground truth.
- All rasters, OCR dumps and scoring scripts lived in the session scratchpad and were deleted at the end. Nothing derived from the real file is in the repo.

## Plan
- [x] Re-read the current preprocess/tesseract code and the 2026-10-16 OCR task file, so already-failed approaches are not repeated
- [x] Build a scratch harness that runs the real path: rasterize -> preprocess variant -> OSD -> Tesseract TSV -> `extract_order`
  - Scores field by field on the same 41 fields as prior rounds
  - Also reports raw-OCR "probes": whether each printed token appears anywhere in the word list
- [x] Reproduce the baseline: 9/41 fields, 8/27 probes. This matches the known number.
- [x] Measure each technique alone against baseline, then in combination
- [x] Keep only changes that add correct fields without regressing others
- [x] Full pytest suite: 348 passed with `-W error`

## Outcome: no code change kept
Nothing met the keep criterion, so `preprocess.py` and `tesseract.py` are unchanged.

Real-file accuracy is still **9/41**. About 60 configurations were measured. The best single runs reached 10-11/41, but those gains did not hold up:
- They were not stable under small parameter changes.
- Each one traded fields between pages instead of adding them.

## Results, field-level, out of 41

| Variant | Fields | p3 / p6 / p10 | Verdict |
|---|---|---|---|
| baseline (current code) | 9 | 2 / 3 / 4 | reference |
| R: inpaint red ink instead of whitening it | 6 | 2 / 2 / 2 | worse; dropped |
| R: no red removal | 6 | 2 / 2 / 2 | worse; current whitening is justified |
| S: blue-stamp removal, B-max(R,G) >= 40 / 25 | 5 / 6 | p10 drops to 0 | worse; confirms the 10-16 finding that the navy stamp overlaps black print |
| C: page CLAHE, clip 2 / 3 | 8 / 7 | page 3 header found, page 10 collapses | net negative |
| B: bar = min(R,G,B) + white top-hat + percentile stretch | 10 | 2 / 2 / 6 | gains and losses outside the bar (see coupling below); not a real +1 |
| T: Tesseract `thresholding_method=1` / `=2` | 6 / 7 | | worse |
| P: own adaptive-Gaussian binarization, 4 settings x 2 | 5-9 | | noise band |
| P/Q: own Sauvola, 9 window/k settings | 6-11 | non-monotonic (window 81: k .05/.10/.15 gives 11/6/9) | noise; adopting the peak would be selection bias |
| Y: separate bar-crop OCR (PSM 6/7, x1-3, gray/Otsu) merged into page words | 9 in all 12 settings | | no gain; one setting produced a silent wrong total (below) |

## Headline questions
1. **Is the Total bar readable?**
   - **The label: yes, newly.** With min-channel + white top-hat inside the detected bar, "Total" is read on 3 of 3 pages; the baseline reads it on 0 of 3.
   - **The amount: no.** Across every bar treatment, only page 6 ever yielded a usable value: "7380.00?" at conf 19-49. Pages 3 and 10 never produced the number.
2. **Do the red-circled totals on pages 6/10 survive?**
   - Inside the dark bar, the red pen is not red. Red ink over teal/slate prints near-black: only 20-71 pixels per bar pass `R - max(G,B) >= 15`.
   - So no colour rule can remove it there. The min-channel top-hat ignores the pen because the pen is darker than the bar, which is the right behaviour.
   - Where a pen tick physically crosses a digit (the "8" of 4,830.00, the "7" of 7,380.00), that digit is damaged, and nothing measured here recovered it.
3. **Page 10's VAT-row strikethrough.**
   - The current whitening removes it. The row's printed values stay as unreadable as the rest of the totals block.
   - No variant read "517.50" or improved "Untaxed Amount 4,312.50" beyond what the baseline already reads.
   - The baseline's `unparsed:untaxed_total` is unchanged.

## Root causes found (the useful output of this round)
1. **The input image itself is the ceiling.** `pdfimages -list` shows the Sales Order pages are embedded as extremely compressed JPEGs:

   | Page | ppi | Size | Ratio |
   |---|---|---|---|
   | 3 | 271 | 150 KB | 0.8% |
   | 6 | 271 | 200 KB | 1.1% |
   | 10 | 286 | 298 KB | 1.4% |
   | 1 (non-SO) | 271 | 1806 KB | 12% |
   | 2 (non-SO) | 273 | 2104 KB | 11% |

   - This is roughly 10-15x less data than other pages in the same file.
   - The bar digits are blurred and blocky (8x8 JPEG artifacts are visible after top-hat), and page 3's strokes are soft gray (darkest 1% of the ink = 93, vs 9 on page 6).
   - Rasterizing above 300 dpi cannot help, because the source is 271-286 ppi.
   - Recommendation: ask the user to re-export from CamScanner at higher quality ("Original" or max quality, not a compressed PDF). This is likely the single most effective accuracy fix available.
2. **Tesseract's single global threshold couples the whole page.**
   - Changing pixels only inside the Total bar (rows 1813-1901 on page 10) changed words at rows 547-800: "GOLDMAN'S" became "WOMANS", and a stray "a" became the customer.
   - Tesseract binarizes the whole page with one Otsu threshold, so any local edit flips faint glyphs everywhere.
   - This is why the 10-16 round's 29 configurations all landed at 6-9, and why per-field gains in this round never held: ±1-3 fields between variants is noise at this sample size.
   - Any future preprocessing experiment must account for this. Either compare variants at the OCR-token level, or isolate local edits by OCR'ing the region separately (approach Y).
3. **Even perfect bar OCR yields the total on only 1 of 3 pages with the current extractor.** Tested by injecting ideal "Total" and amount words at the real bar positions:
   - Page 3: `table_header_not_found` (faint page), so totals are never searched.
   - Page 6: the bar's "Total" label sits at x≈1285, inside the DESCRIPTION column span. `_find_totals_row` rejects it as a product name (extract.py:232). With the label shifted 100 px right, the total is read.
   - Page 10: works.
   - This is an extraction-layer item. It was out of scope here and is not fixed.

## Rejected approaches and why (do not retry blindly)
- **Inpainting red ink (cv2.inpaint TELEA) instead of whitening**
  - Measured 6/41 vs 9/41.
  - The inpaint drags dark text into the masked fringe and shifted the global threshold.
- **Blue-stamp removal by blue dominance**
  - Measured 5-6/41.
  - It wipes blue-cast black print. Same conclusion as 10-16.
- **Page-wide CLAHE**
  - It found page 3's table header for the first time, but collapsed page 10 to 1-2/17.
  - Net negative.
- **Tesseract internal local thresholding (`thresholding_method` 1/2)**
  - Worse at default parameters.
- **Own adaptive/Sauvola binarization**
  - Sauvola window 81, k 0.2 gave page 6 its best-ever 7/12 (order_ref, quantity, unit_price, subtotal 7380.00, taxes all correct), but broke page 10's shaded table header ("AMOUNT" became "AMACAIMT").
  - No setting helped all three pages.
- **Separate bar-crop OCR merged into the page words (approach Y)**
  - It is isolated by construction (zero regression outside the bar) and reads the "Total" label, but added 0 correct fields.
  - In one setting it turned page 10's visible `not_found:total` into **`total = 4` with no total flag**. "4 FX" from the circled amount parsed as 4.
  - Do not ship any bar-crop pass without a confidence gate on the totals value.
- **Global Otsu inside the bar**
  - Re-confirmed as bad. The bar gradient turns into blobs.

## Findings for follow-up (not done here)
- **User action:** re-export scans at higher CamScanner quality, then re-run this measurement. Expect the bar top-hat (approach B/Y) to be worth re-testing on better input.
- **Extraction:** the Total-bar label can sit inside the DESCRIPTION column span on this form (page 6), so the totals row is rejected. This needs its own extraction-layer round.
- **Extraction:** totals values get no `low_confidence:<field>` flag. A conf-40 "4" was accepted silently. `total` is in `always_verify_fields`, so the UI still highlights it, but it is worth a flag.
- **Extraction:** `parse_money` does not accept a trailing `"` as a peso misread. Page 6's bar read "7380.00"" in one setting. Low priority: only add this if better input shows it recurring.

## Review
- `C:\Users\robtan\Desktop\converter\.venv\Scripts\python -m pytest -W error -q`: 348 passed, unchanged.
- No files under `converter/` or `tests/` were modified (checked by mtime against the harness creation time).
- No tracebacks in any harness run.
- Scratch artifacts (rasters, crops, word dumps, harness with ground truth) were deleted after the run.
- Sample size caveat: three pages from one batch. Every conclusion above is about this file's scan quality; a better-quality re-export may change which techniques pay off.
