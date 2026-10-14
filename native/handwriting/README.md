# Offline stroke helper

Zinnia source: commit 581faa8f6f15e4a7b21964be3a5ec36265c80e5b, BSD COPYING in vendor/zinnia. UPSTREAM_MANIFEST.json binds original Git blobs and SHA256. Only feature.cpp differs by patches/0001-bound-vertex-tree.patch.

The original feature tree allocated by recursive binary-tree index before its later feature cutoff. The guard returns before allocation for indexes greater than 50, preserving inclusive index50. Descendants cannot affect retained ancestors. Exact equal endpoints return zero instead of NaN; non-degenerate geometry is unchanged. No S-expression parser is reachable from the helper.

Fixed unmodified Tegaki model:52,210,768 bytes; SHA256 cd47f16b64e7ecaa4c2813d0f98231dfea7b411f48be1ead5c9b2eeb19330b11;11,853 labels. A Unicode filesystem path is read into owned bytes, the same buffer is hash-checked and fully bounded-walked before native open. Buffer outlives recognizer and borrowed labels; replies copy labels before destruction. Arbitrary models are unsupported.

## Protocol and ownership

20-byte little-endian header: ZHW1, version1, type, reserved0, uint64 generation, uint32 payload size. START precedes model load. RECOGNIZE uses generated sequential IDs:≤128 strokes,≤512 points/stroke,≤16,384 total points, integer XY0–1000. Maximum payload65,794 bytes. RESULT contains fixed status and≤10 UTF-8 labels of≤15 bytes with finite ranking scores, not probabilities. STOP/EOF exits.

Qt owns one active and one replaceable latest snapshot, validates framing/UTF8/alphabet/generation, clears stale candidates and caps separate stderr. Errors are fixed plain text. Clicking copies exact text with existing self MIME; no external input simulation.

Windows assigns a non-inherited kill-on-close Job before START, failing closed on assignment failure. Private memory is capped256MiB, extra children forbidden. Whole model load deadline10s; each recognition3s, partial output cannot extend it. Close sends STOP then kills after750ms. QProcess remains owned until finished; no GUI blocking wait. This is crash/resource isolation, not a sandbox.

Build with scripts/build_handwriting_helper.py. Windows10+ x64 uses installed MSVC Release /MT and system BCrypt. Linux/OpenSSL is development-only. Source model replay and synthetic/malformed tests are smoke/robustness, not human accuracy. The earlier assessment's LeakSanitizer compatibility failure remains unresolved; no leak-free certification is claimed.
