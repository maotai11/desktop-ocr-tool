# Third-party model notices

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
