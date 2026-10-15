# Offline stroke helper

Zinnia source: commit 581faa8f6f15e4a7b21964be3a5ec36265c80e5b, BSD COPYING in vendor/zinnia. UPSTREAM_MANIFEST.json binds original Git blobs and SHA256. Only feature.cpp differs by patches/0001-bound-vertex-tree.patch.

The original feature tree allocated by recursive binary-tree index before its later feature cutoff. The guard returns before allocation for indexes greater than 50, preserving inclusive index50. Descendants cannot affect retained ancestors. Exact equal endpoints return zero instead of NaN; non-degenerate geometry is unchanged. No S-expression parser is reachable from the helper.

Fixed unmodified Tegaki model:52,210,768 bytes; SHA256 cd47f16b64e7ecaa4c2813d0f98231dfea7b411f48be1ead5c9b2eeb19330b11;11,853 labels. A Unicode filesystem path is read into owned bytes, the same buffer is hash-checked and fully bounded-walked before native open. Buffer outlives recognizer and borrowed labels; replies copy labels before destruction. Arbitrary models are unsupported.

## Protocol and ownership

20-byte little-endian header: ZHW1, version2, type, reserved0, uint64 generation, uint32 payload size. START precedes model load. RECOGNIZE uses generated sequential IDs:≤128 strokes,≤512 points/stroke,≤16,384 total points, integer XY0–1000. Maximum payload65,794 bytes. RESULT starts with uint16 status, uint8 raw_count, uint8 normalized_count. Each lane has ≤30 labels; each record is uint8 UTF-8 byte length (1–15), exact UTF-8 label, and finite float32 score. Maximum reply payload1,204 bytes. Ranks are implicit per-lane positions; sources are implicit fixed lanes, not supplied arbitrary tags. Duplicates within a lane are invalid; cross-lane labels merge with both ranks. Scores are not probabilities and are never added across lanes. Raw first10 remain stable, then normalized/raw remaining candidates interleave uniquely (≤60). STOP/EOF exits.

Qt owns one active and one replaceable latest snapshot, validates framing/UTF8/alphabet/generation, clears stale candidates and caps separate stderr. Errors are fixed plain text. Clicking copies exact text with existing self MIME; no external input simulation.

Windows assigns a non-inherited kill-on-close Job before START, failing closed on assignment failure. Private memory is capped256MiB, extra children forbidden. Whole model load deadline10s; each recognition3s, partial output cannot extend it. Close sends STOP then kills after750ms. QProcess remains owned until finished; no GUI blocking wait. This is crash/resource isolation, not a sandbox.

Build with scripts/build_handwriting_helper.py. Windows10+ x64 uses installed MSVC Release /MT and system BCrypt. Linux/OpenSSL is development-only. Source model replay and synthetic/malformed tests are smoke/robustness, not human accuracy. The earlier assessment's LeakSanitizer compatibility failure remains unresolved; no leak-free certification is claimed.

## Manual image worker

The exact same frozen EXE has one early `--handwriting-image-helper` route before QApplication, single-instance lock, tray, database or normal OCR-worker imports. There are no path/model parameters. It waits for the bounded v2 START before NumPy/OpenCV/ORT imports. The parent assigns its kill-on-close Job first:512MiB private memory, one process. Image READY is eight bytes `<HHI>`:zero status, zero count, worker PID; it must exactly match QProcess.processId(), otherwise fail closed. This protects the worker/onefile assumption but Windows frozen stdio/PID/Job ownership still requires actual Windows verification.

The child validates fixed bundled v6-small manifest/recognition metadata, uses only its recognition model with one CPU thread, and consumes one bounded integer-stroke request. It renders raw and fixed bbox-normalized strokes on a white256px square, black3px strokes. Each raster may yield one CJK scalar; empty/multi-character text is not split or script-converted. At most two unique labels are returned, with image provenance. No original stroke candidate is discarded; the UI budget is≤62 unique selectable labels. Completion exits and releases the ORT session. Load15s / request5s deadlines, cancel/clear/redraw/close kill/reap through the same process owner. Child EOF before START exits without heavy imports.

The source/frozen `--self-test-handwriting-image REPORT` gate checks actual inference, worker PID, Job assignment, a second cancelled request, and both exits; this is functional evidence only. Windows one-file pipe bridging, process-death cleanup, OS-blocked outbound behavior and clean-host resource use are not established by Linux headless tests.
