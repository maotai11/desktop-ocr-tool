# -*- coding: utf-8 -*-
import json
import logging
import time
from typing import Optional
import cv2
import numpy as np

logger = logging.getLogger(__name__)


class OcrEngine:
    """
    OCR 引擎，優先使用 PaddleOCR PP-OCRv5，退而使用 RapidOCR PP-OCRv4。
    """

    def __init__(self, det_path: str, rec_path: str, cls_path: str,
                 confidence_accept: float = 0.85,
                 confidence_review: float = 0.60):
        self._det_path = det_path
        self._rec_path = rec_path
        self._cls_path = cls_path
        self._confidence_accept = confidence_accept
        self._confidence_review = confidence_review
        self._engine = None
        self._engine_type = None
        self._ready = False

    def load(self):
        logger.info("開始載入 OCR 引擎...")
        t0 = time.time()

        # 1st choice: PaddleOCR 3.x → PP-OCRv5 (繁體中文)
        # 僅在本機模型已就緒時啟用；首次需先執行 scripts/download_paddleocr_models.bat
        try:
            import os, threading
            os.environ.setdefault('PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK', 'True')

            import sys
            # Frozen EXE: importlib.metadata lacks .dist-info -> patch PaddleX deps checker
            if getattr(sys, 'frozen', False):
                try:
                    import paddlex.utils.deps as _pdx_deps
                    _pdx_deps.is_dep_available = lambda dep: True
                    _pdx_deps.is_extra_available = lambda extra: True
                except Exception:
                    pass

            if getattr(sys, 'frozen', False):
                # 從 PyInstaller EXE 執行：模型在 sys._MEIPASS
                _base = sys._MEIPASS
            else:
                _here = os.path.dirname(os.path.abspath(__file__))
                _base = os.path.dirname(os.path.dirname(_here))  # project root
            _paddle_dir = os.path.join(_base, 'models', 'paddleocr')
            det_dir = os.path.join(_paddle_dir, 'det')
            rec_dir = os.path.join(_paddle_dir, 'rec')
            cls_dir = os.path.join(_paddle_dir, 'cls')

            def _has_model(d):
                return os.path.isfile(os.path.join(d, 'inference.yml'))

            if not (_has_model(det_dir) and _has_model(rec_dir) and _has_model(cls_dir)):
                raise FileNotFoundError(
                    "PP-OCRv5 本機模型未就緒，請先執行 scripts/download_paddleocr_models.bat"
                )

            logger.info(f"使用本機 PP-OCRv5 模型: {_paddle_dir}")
            from paddleocr import PaddleOCR

            # 用 daemon thread + timeout 防止 PaddleOCR init 卡死
            _result: list = [None, None]
            def _init():
                try:
                    _result[0] = PaddleOCR(
                        use_textline_orientation=True,  # 3.4+ 新參數名
                        lang='chinese_cht',
                        device='cpu',
                        text_detection_model_dir=det_dir,
                        text_recognition_model_dir=rec_dir,
                        textline_orientation_model_dir=cls_dir,
                    )
                except Exception as e:
                    _result[1] = e

            _t = threading.Thread(target=_init, daemon=True)
            _t.start()
            _t.join(timeout=90)
            if _t.is_alive():
                raise TimeoutError("PaddleOCR 初始化逾時（>90s）")
            if _result[1]:
                raise _result[1]

            self._engine = _result[0]
            self._engine_type = 'paddleocr'
            logger.info("使用 PaddleOCR PP-OCRv5 (chinese_cht) 引擎")
        except Exception as e:
            logger.warning(f"PaddleOCR 不可用 ({e})，嘗試 RapidOCR")
            # 2nd choice: RapidOCR PP-OCRv4
            try:
                from rapidocr_onnxruntime import RapidOCR
                self._engine = RapidOCR()
                self._engine_type = 'rapidocr'
                logger.info("使用 RapidOCR PP-OCRv4 引擎")
            except ImportError:
                logger.warning("RapidOCR 不可用，使用直接 ONNX 模式")
                self._load_onnx_direct()
                self._engine_type = 'onnx_direct'

        # Warm-up with dummy image
        dummy = np.zeros((64, 256, 3), dtype=np.uint8)
        try:
            self._do_ocr_array(dummy)
        except Exception as e:
            logger.warning(f"暖機推論失敗（可忽略）: {e}")

        elapsed = time.time() - t0
        logger.info(f"OCR 引擎載入完成 [{self._engine_type}]，耗時 {elapsed:.2f}s")
        self._ready = True

    def _load_onnx_direct(self):
        import onnxruntime as ort
        opts = ort.SessionOptions()
        opts.inter_op_num_threads = 2
        opts.intra_op_num_threads = 2
        import os
        if os.path.exists(self._det_path):
            self._det_sess = ort.InferenceSession(self._det_path, opts)
        if os.path.exists(self._rec_path):
            self._rec_sess = ort.InferenceSession(self._rec_path, opts)
        if os.path.exists(self._cls_path):
            self._cls_sess = ort.InferenceSession(self._cls_path, opts)
        logger.info("ONNX 直接載入完成")

    def is_ready(self) -> bool:
        return self._ready

    def run_ocr(self, image: np.ndarray, mode: str = 'screen') -> dict:
        if not self._ready:
            return {'text': '', 'confidence': 0, 'status': 'failed',
                    'detail': [], 'elapsed_ms': 0, 'error': 'OCR 引擎未就緒'}
        t0 = time.time()
        try:
            results = self._do_ocr_array(image)
            elapsed_ms = int((time.time() - t0) * 1000)
            return self._process_results(results, elapsed_ms)
        except Exception as e:
            elapsed_ms = int((time.time() - t0) * 1000)
            logger.error(f"OCR 執行失敗: {e}", exc_info=True)
            return {'text': '', 'confidence': 0, 'status': 'failed',
                    'detail': [], 'elapsed_ms': elapsed_ms, 'error': str(e)}

    def _do_ocr_array(self, image: np.ndarray):
        if self._engine_type == 'paddleocr':
            # PaddleOCR 3.4+: ocr() 不接受額外關鍵字參數
            result = self._engine.ocr(image)
            if result and result[0]:
                return result[0]
            return []
        if self._engine_type == 'rapidocr':
            result, _ = self._engine(image)
            return result or []
        return []

    def _process_results(self, results, elapsed_ms: int) -> dict:
        if not results:
            return {'text': '', 'confidence': 0.0, 'status': 'done',
                    'detail': [], 'elapsed_ms': elapsed_ms}

        detail = []
        texts = []
        confs = []
        for item in results:
            if item is None:
                continue
            try:
                if len(item) >= 3:
                    box, text, conf = item[0], item[1], float(item[2])
                elif len(item) == 2:
                    box = item[0]
                    text, conf = item[1][0], float(item[1][1])
                else:
                    continue
                if text and conf > 0.1:
                    detail.append({'box': box, 'text': text, 'confidence': conf})
                    texts.append(text)
                    confs.append(conf)
            except Exception:
                continue

        full_text = '\n'.join(texts)
        avg_conf = sum(confs) / len(confs) if confs else 0.0

        if avg_conf >= self._confidence_accept:
            status = 'done'
        else:
            status = 'needs_review'

        return {
            'text': full_text,
            'confidence': avg_conf,
            'status': status,
            'detail': detail,
            'elapsed_ms': elapsed_ms
        }

    def run_ocr_from_path(self, image_path: str, mode: str = 'screen') -> dict:
        img = cv2.imread(image_path)
        if img is None:
            return {'text': '', 'confidence': 0, 'status': 'failed',
                    'detail': [], 'elapsed_ms': 0, 'error': '無法讀取圖片'}
        return self.run_ocr(img, mode)
