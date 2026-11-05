# Third-party notices


## Pinned Windows Python and OpenSSL runtime

- The Windows build uses CPython **3.12.16**, supplied by Astral's `python-build-standalone` **20261026**, x86-64 MSVC `install_only_stripped` distribution. This is a third-party CPython build, rather than the PSF Windows installer. The OCR package versions are unchanged.
- Exact build input: `cpython-3.12.16+20261026-x86_64-pc-windows-msvc-install_only_stripped.tar.gz`, SHA256 `1f2ab53880fc67ea323398c6388e05af28addc99a8e389209f08a51fdff4b149`; [provider release](https://github.com/astral-sh/python-build-standalone/releases/tag/20261026). The checked-in `scripts/windows_runtime.lock.json` records the archive and runtime file identities used during the build.
- Python's PSF license and included component notices are copied byte-for-byte from that archive's `python/LICENSE.txt` into [licenses/python-3.12.16-LICENSE.txt](licenses/python-3.12.16-LICENSE.txt), SHA256 `886a0ead2d89030ee62dbff52b04e47ab91998341295bb9c56fb952b4e081c7a`.
- The runtime's dynamic OpenSSL libraries are **3.5.9**. The complete upstream Apache-2.0 license is included in [licenses/openssl-3.5.9-LICENSE.txt](licenses/openssl-3.5.9-LICENSE.txt), SHA256 `7d5450cb2d142651b8afa315b5f238efc805dad827d91ba367d8516bc9d49e7a`. Source: [OpenSSL commit 45e844fa2a14ec92d146bd8f5778ac130b6625fb](https://github.com/openssl/openssl/tree/45e844fa2a14ec92d146bd8f5778ac130b6625fb), the `openssl-3.5.9` release tag. That source root has no separate NOTICE file.
- OpenSSL's upstream README copyright notice is retained here: **Copyright (c) 1998-2026 The OpenSSL Project Authors. Copyright (c) 1995-1998 Eric A. Young, Tim J. Hudson. All rights reserved.**
- These files identify the newly selected runtime and OpenSSL components. The native payload inventory separately records the files actually shipped; neither this notice nor the vulnerability scan is a complete license review of every transitive component.

## Image processing runtime

- Pillow 13.0.0: HPND / Pillow license; https://github.com/python-pillow/Pillow . Its upstream license metadata is included with the frozen application.
- opencv-python 5.0.0.93: OpenCV (Apache-2.0) plus wheel packaging/dependency notices; https://github.com/opencv/opencv-python . The build copies its distribution license metadata. This release uses a wheel with OpenEXR disabled and excludes optional FFmpeg video DLLs; still-image OCR does not require them.
- These concrete notices and the Python vulnerability scan do not constitute a complete third-party license or native-library CVE audit. See docs/SECURITY_REVIEW.md for the tested scope.

## Offline PDF rendering

- Document-template OCR uses pypdfium2 5.14.0 and its bundled PDFium library; no external PDF reader, Poppler installation or runtime download is used.
- pypdfium2 is distributed under BSD-3-Clause / Apache-2.0, with its documentation and dependency notices supplied by the upstream wheel. Source and license details: https://github.com/pypdfium2-team/pypdfium2/tree/5.14.0 .
- PDFium and its bundled dependencies have their own notices (including BSD, Apache, FreeType, ICU, JPEG, PNG, OpenJPEG and zlib notices). The one-file build includes the complete `pypdfium2-5.14.0.dist-info/licenses/` tree through package metadata, plus the native PDFium library and version data from `pypdfium2_raw`.
- Imported PDF/image files are read locally. They are not submitted to an online conversion service by this feature.

## OCR models

DesktopOCR redistributes fixed ONNX conversions of PaddleOCR model assets supplied by RapidAI. The packaged model manifests record exact source URLs, SHA256, versions and upstream model-card references.

- PaddleOCR / PP-OCRv6 model authors: PaddlePaddle Authors
- ONNX conversion/distribution: RapidAI / RapidOCR
- Declared model license: Apache License 2.0; a copy is included as LICENSE-APACHE-2.0.txt
- Model generation and source: https://github.com/PaddlePaddle/PaddleOCR/releases/tag/v3.7.0
- Conversion hash inventory: https://raw.githubusercontent.com/RapidAI/RapidOCR/v3.9.2/python/rapidocr/default_models.yaml
- PP-OCRv6 Small detector: https://huggingface.co/PaddlePaddle/PP-OCRv6_small_det
- PP-OCRv6 Small recognizer: https://huggingface.co/PaddlePaddle/PP-OCRv6_small_rec
- PP-OCRv6 Medium recognizer: https://huggingface.co/PaddlePaddle/PP-OCRv6_medium_rec
- The orientation-only PP-OCR mobile v2.0 classifier is retained unchanged; it is not an old text-recognition fallback

DesktopOCR does not modify the packaged model weight bytes. Application preprocessing, retry/review policy, model selection and packaging were changed. The model-card license declaration does not replace a full dependency-license audit. The release build manifest separately records that broader audit as NOT_RUN; this candidate does not claim all third-party compliance/signing gates are complete.


## Offline handwriting

- Zinnia author: Taku Kudo. BSD COPYING: native/handwriting/vendor/zinnia/COPYING. Exact source: https://github.com/taku910/zinnia/tree/581faa8f6f15e4a7b21964be3a5ec36265c80e5b/zinnia .
- InkPick adds the reviewable feature-bound patch in native/handwriting/patches/0001-bound-vertex-tree.patch and a separate helper/protocol. Original source attribution and notices remain.
- Tegaki Traditional Chinese model0.3 official archive: https://github.com/tegaki/tegaki/releases/download/v0.3/tegaki-zinnia-traditional-chinese-0.3.zip . Archive COPYING is GNU LGPL2.1; prepared text: models/handwriting/source/COPYING-LGPL-2.1.txt. Do not infer model license from the parent repository GPL.
- Unmodified model:52,210,768 bytes, SHA256 cd47f16b64e7ecaa4c2813d0f98231dfea7b411f48be1ead5c9b2eeb19330b11.
- Corresponding machine-readable XML:24,523,156 bytes, SHA25685ab37c56bcb14a54a7ae106f2504b6505cf6c9917ca8c636312153fd9d00e53. Portable ZIP handwriting-source contains XML, LGPL, model manifest, Zinnia BSD source/COPYING and local patch/helper sources. Exact bytes are verified before packaging/publication; links are supplemental.
- Model build/style warning: https://github.com/tegaki/tegaki/blob/master/tegaki-models/tegaki-zinnia-traditional-chinese/README.bootstrap.txt .11,853 labels; stroke order/style can affect results. No held-out human accuracy or universal rare-character coverage is asserted.

InkPick SVG and derived raster assets were authored for this project, without using the full concept-presentation bitmap as an icon. Runtime fonts use the OS; actual rare-glyph rendering remains a Windows acceptance check. Complete third-party license review is a separate open gate, not a replacement for these concrete distribution notices/source.
