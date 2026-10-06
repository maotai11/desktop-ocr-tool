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
