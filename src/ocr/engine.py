# -*- coding: utf-8 -*-
import json
import logging
import time
import cv2
import numpy as np

logger = logging.getLogger(__name__)


class OcrEngine:
    """OCR 引擎，使用 RapidOCR PP-OCRv4（ONNX Runtime，離線輕量）。"""

    def __init__(self, confidence_accept: float = 0.85,
                 confidence_review: float = 0.60):
        self._confidence_accept = confidence_accept
        self._confidence_review = confidence_review
        self._engine = None
        self._ready = False

    def load(self, progress_cb=None):
        """載入 RapidOCR 引擎。progress_cb(pct: int, msg: str) 可選。"""
        def _progress(pct, msg):
            logger.debug(f"OCR 載入進度 {pct}%: {msg}")
            if progress_cb:
                progress_cb(pct, msg)

        logger.info("開始載入 OCR 引擎 [RapidOCR PP-OCRv4]...")
        _progress(0, "初始化...")
        t0 = time.time()

        _progress(20, "載入 RapidOCR PP-OCRv4 (ONNX)...")
        from rapidocr_onnxruntime import RapidOCR
        self._engine = RapidOCR()
        logger.info("RapidOCR PP-OCRv4 引擎已建立")

        _progress(90, "暖機推論...")
        dummy = np.zeros((64, 256, 3), dtype=np.uint8)
        self._do_ocr_array(dummy)  # raises if fails → engine_failed

        elapsed = time.time() - t0
        logger.info(f"OCR 引擎載入完成，耗時 {elapsed:.2f}s")
        self._ready = True
        _progress(100, "就緒")

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
        result, _ = self._engine(image)
        return result or []

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
