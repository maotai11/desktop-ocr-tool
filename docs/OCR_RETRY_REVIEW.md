# OCR retries and human review

## User-visible behavior

- The OCR settings expose a per-region primary-engine pass limit of 1, 2 or 3, including the initial inference. The default is 2. Disabling automatic retry makes this 1 regardless of the stored limit. Settings take effect on restart.
- A retry runs only for empty usable text, mean confidence below the acceptance threshold (default 0.85), or any weak region (default below 0.60). The third variant also runs for unresolved conflicting text. These scores are **not measured accuracy** and cannot detect all missing/wrong text.
- Pass 1 uses the prepared BGR image; pass 2 uses CLAHE and denoising (plus Otsu in handwriting mode). Pass 3 uses grayscale/Otsu, or grayscale/unsharp when pass 2 was binarized. A retry variant is skipped if its image is pixel-identical to an earlier variant. Dimensions and the total recognition budget remain bounded across **all passes and tiles**.
- Disagreeing text does not replace prior usable text merely because it scores higher, including if passes 2 and 3 agree with one another. All raw alternatives, processing variants, trigger reasons and elapsed times are retained in existing attempt provenance. No database migration is required.
- An uncertain initial reading remains `needs_review` after retry. In rc.2, the pipeline displays 待確認 and copies a usable current candidate without marking it confirmed. Failed/empty, stale, deleted, or manually edited results retain publication guards. Copy-output cleanup is separately opt-in and never changes stored candidates. A high-confidence mistake may still escape the confidence trigger; retries and agreement do not certify correctness.
- The editor's 辨識候選／歷次紀錄 tab provides a lightweight attempt selector and loads only one provenance payload at a time. It shows the final machine transcript separately from each raw pass, including per-tile candidates and failed/skipped steps. Model identity is available in the source label/tooltip.
- Applying a whole-image candidate changes only the editor draft, as one undoable action. Truly partial tile candidates replace only a user-selected range. A verified single small-region tile covering the entire source can populate an empty draft. Only explicit Save persists candidate edits. Cancel discards the draft.
- Save and confirmation check the saved edit revision, OCR job and machine-text snapshot. An unseen newer result cannot be silently confirmed by an old editor. Existing manual edits, notes, attempt history and effective-text export behavior remain intact.

## Native comparison (before the later blank-border guard)

All three conditional policies used the same frozen 38 synthetic raster/ground-truth fixtures and full PP-OCRv6-small detector/recognizer profile. Input manifest SHA-256: `eb2d1f5af5aef71387eeda45d9962d6aae3d8149aa4ace441d7564b0cf35b65d`.

| Maximum total passes | Exact cases | Literal character errors | Actual region passes | Total inference time | Peak process RSS |
|---|---:|---:|---:|---:|---:|
| 1 | 33/38 | 5/262 | 34 | 9.21 s | 688.5 MB |
| 2 | 33/38 | 5/262 | 40 | 11.74 s | 696.5 MB |
| 3 | 33/38 | 5/262 | 46 | 14.51 s | 696.3 MB |

One error was a literal space. These are single observed timings, not p95 or p99 measurements. Four completely uniform fixtures skipped native inference. The same-model candidates are correlated. No ground truth participates in selection. This set does **not** establish improvement from a second or third pass, nor prove 2 passes globally optimal. It provides no reason to make 3 the default; 2 remains the conservative existing recovery budget. It is not a customer-photo corpus or clean Windows/DPI validation.

## Local reproduction without uploading test images

The frozen 38-case corpus and raw raster evidence are retained locally. Only the three small EXE self-test images are required in the repository; no private photographs are used by these scripts.

The repository includes a generator for the separate 27-case synthetic development set. With the exact Noto Sans CJK font and index used in the original run, all 27 regenerated raster hashes were checked to match. A different font is a different corpus, and this 27-case example does not reproduce the 38-case table above.

```sh
python scripts/generate_quality_fixtures.py --font /path/to/NotoSansCJK-Regular.ttc --font-index 3 --output-dir test_data/roi
python scripts/benchmark_retry_policy.py --inputs test_data/roi/inputs.json --profile v6-small --passes 1 --output test_data/retry-1.json
python scripts/benchmark_retry_policy.py --inputs test_data/roi/inputs.json --profile v6-small --passes 2 --output test_data/retry-2.json
python scripts/benchmark_retry_policy.py --inputs test_data/roi/inputs.json --profile v6-small --passes 3 --output test_data/retry-3.json
```

Each comparison process records exact input/model/source hashes, raw results, literal edit counts, inference time and actual pass counts. Review the generated pixels and font coverage before interpreting scores. Output folders must be empty so existing frozen fixtures are not overwritten.

## Final guarded comparison

The final full-v6-small run includes the strict uniform-interior frame-padding guard. The raster inputs, ground truth, model identity and recorded source hashes were identical across all three policies.

| Maximum total passes | Exact cases | Literal character errors | Actual region passes | Total inference time | Peak process RSS |
|---|---:|---:|---:|---:|---:|
| 1 | 34/38 | 4/262 | 34 | 8.15 s | 689.6 MB |
| 2 | 34/38 | 4/262 | 41 | 11.54 s | 696.6 MB |
| 3 | 34/38 | 4/262 | 48 | 12.15 s | 696.4 MB |

All policies eliminated the pre-guard blank-frame false positive. One literal spacing error and three glyph errors remain; two glyph errors carry confidence above the retry threshold. There is still no measured third-pass accuracy gain. The shipped default remains 2, without claiming it is a global optimum. These are single measurements with concurrent development workloads, so timing differences are descriptive only. The detailed raw reports remain local. The final Windows EXE verification summary is published separately under `docs/validation/`.

## Verification boundaries

`tests/test_reviewable_retries.py` covers limits, nonidentical variants, correlated conflicting amounts, early stop, skipped/failed retries, shared recognition budgets, SQLite provenance, Qt candidate application/undo/cancel/Save, full-ROI and partial-tile application, stale-job/same-job completion protection, clipboard safety, settings wiring, and graceful accepted-work drain.

The deterministic engine stubs in these tests validate orchestration, not recognition accuracy. Native accuracy and memory observations are in the separate fixture reports. Offscreen Linux screenshots check widget layout only. Shutdown drains accepted work; it cannot interrupt a hung native inference. No bounded-cancellation or clean-Windows claim follows from these tests.
