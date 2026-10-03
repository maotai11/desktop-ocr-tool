# -*- coding: utf-8 -*-
"""
PaddleOCR v5 (PP-OCRv5) Provider — 適配 PaddleOCR 3.x API

optional dependency: paddleocr>=3.0 (PaddlePaddle stack)
若未安裝，is_available() 回傳 False，不影響主流程。

設計決策：
- 模組頂層 try/except ImportError 決定 _PADDLEOCR_AVAILABLE flag
- 模型載入採懶惰載入（首次 recognize() 時），避免 app 啟動時阻塞
- 使用 PaddleOCR 3.x 全新 API（PaddleX backend）
- 支援繁體中文（lang='chinese_cht'）
- confidence_accept 門檻為建構子參數，預設 0.85
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

import numpy as np
from PIL import Image

from ..secondary_engine import SecondaryEngineBase
from ..postprocessor import sort_boxes_and_merge

logger = logging.getLogger(__name__)

# --- optional dependency guard -------------------------------------------

try:
    from paddleocr import PaddleOCR
    _PADDLEOCR_AVAILABLE = True
    logger.debug("paddleocr 套件已找到")
except ImportError:
    PaddleOCR = None  # type: ignore[assignment]
    _PADDLEOCR_AVAILABLE = False
    logger.debug("paddleocr 未安裝，PaddleOCRv5Provider 不可用")

# --- 簡繁轉換（複用主引擎做法）------------------------------------------

try:
    import zhconv as _zhconv
    def _s2t(text: str) -> str:
        return _zhconv.convert(text, 'zh-hant')
except ImportError:
    def _s2t(text: str) -> str:
        return text


# --- 語言配置 -----------------------------------------------------------

# PaddleOCR 3.x 支援的語言
SUPPORTED_LANGS = {
    'chinese_cht': '繁體中文',
    'ch': '簡體中文',
    'en': '英文',
    'japan': '日文',
    'korean': '韓文',
}


# -------------------------------------------------------------------------

class PaddleOCRv5Provider(SecondaryEngineBase):
    """PaddleOCR v5 (PP-OCRv5) 引擎 adapter。

    作為 RapidOCR 主引擎的備援，適用於手寫或低信心場景。
    paddleocr 未安裝時，is_available() 回傳 False，recognize() 回傳 failed。
    """

    def __init__(
        self,
        lang: str = 'chinese_cht',
        use_doc_orientation_classify: bool = False,
        use_doc_unwarping: bool = False,
        use_textline_orientation: bool = False,
        device: str = 'cpu',
        show_log: bool = False,
        confidence_accept: float = 0.85,
        text_detection_model_dir: str | None = None,
        text_recognition_model_dir: str | None = None,
    ):
        """
        Parameters
        ----------
        lang : str
            語言代碼，預設 'chinese_cht'（繁體中文）
            支援: 'chinese_cht', 'ch', 'en', 'japan', 'korean'
        use_doc_orientation_classify : bool
            是否啟用文檔方向分類（預設 False）
        use_doc_unwarping : bool
            是否啟用文檔展開校正（預設 False）
        use_textline_orientation : bool
            是否啟用文字行方向分類（預設 False）
        device : str
            推理裝置，'cpu' 或 'gpu'（預設 'cpu'）
        show_log : bool
            是否印出 PaddleOCR 內部 log（預設 False）
        confidence_accept : float
            信心門檻：高於此值 status='done'，否則 'needs_review'
        """
        self._lang = lang
        self._use_doc_orientation_classify = use_doc_orientation_classify
        self._use_doc_unwarping = use_doc_unwarping
        self._use_textline_orientation = use_textline_orientation
        self._device = device
        self._show_log = show_log
        self._confidence_accept = confidence_accept
        self._local_dirs = (text_detection_model_dir, text_recognition_model_dir)
        self._engine = None
        self._load_error: str | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # SecondaryEngineBase interface
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return 'paddleocr_v5'

    def is_available(self) -> bool:
        """回傳 True 若 paddleocr 套件已安裝且無先前載入錯誤。

        不代表模型已載入（模型在首次 recognize() 時懶惰載入）。
        """
        return _PADDLEOCR_AVAILABLE and self._load_error is None

    def recognize(self, image: np.ndarray, mode: str = 'screen') -> dict:
        """執行 PP-OCRv5 推論。

        Parameters
        ----------
        image : np.ndarray
            BGR 格式影像（與 OcrEngine.run_ocr 相同輸入格式）
        mode : str
            推論模式，例如 'screen'、'handwriting'

        Returns
        -------
        dict
            標準結果格式
        """
        if not self.is_available():
            return {
                'text': '', 'confidence': 0.0, 'status': 'failed',
                'detail': [], 'elapsed_ms': 0,
                'error': self._load_error or 'paddleocr 未安裝',
            }

        t0 = time.time()

        if not self._ensure_loaded():
            return {
                'text': '', 'confidence': 0.0, 'status': 'failed',
                'detail': [], 'elapsed_ms': 0,
                'error': self._load_error or 'PaddleOCRv5 載入失敗',
            }

        try:
            # PaddleOCR 3.x accepts BGR numpy input; PIL was not its contract.
            raw = list(self._engine.predict(input=image))
            elapsed_ms = int((time.time() - t0) * 1000)
            return self._map_result(raw, elapsed_ms)

        except Exception as e:
            elapsed_ms = int((time.time() - t0) * 1000)
            logger.error(f"PaddleOCRv5 辨識失敗: {e}", exc_info=True)
            return {
                'text': '', 'confidence': 0.0, 'status': 'failed',
                'detail': [], 'elapsed_ms': elapsed_ms, 'error': str(e),
            }

    # ------------------------------------------------------------------
    # 內部輔助
    # ------------------------------------------------------------------

    def _ensure_loaded(self) -> bool:
        """懶惰載入 PaddleOCR 模型，thread-safe。回傳 True 表示已就緒。"""
        if self._engine is not None:
            return True
        if not _PADDLEOCR_AVAILABLE:
            return False
        if not all(p and Path(p).is_dir() for p in self._local_dirs):
            self._load_error = 'Offline provider requires local detection and recognition model directories'
            return False
        if self._use_doc_orientation_classify or self._use_doc_unwarping or self._use_textline_orientation:
            self._load_error = 'Auxiliary model download is disabled in offline mode'
            return False
        with self._lock:
            if self._engine is not None:  # double-checked locking
                return True
            try:
                logger.info(
                    "載入 PaddleOCRv5 引擎 (lang=%s, device=%s)...",
                    self._lang,
                    self._device
                )

                # PaddleOCR 3.x API
                self._engine = PaddleOCR(
                    use_doc_orientation_classify=self._use_doc_orientation_classify,
                    use_doc_unwarping=self._use_doc_unwarping,
                    use_textline_orientation=self._use_textline_orientation,
                    lang=self._lang,
                    device=self._device,
                    text_detection_model_dir=self._local_dirs[0],
                    text_recognition_model_dir=self._local_dirs[1],
                )

                logger.info("PaddleOCRv5 引擎已就緒")
                return True

            except Exception as e:
                self._load_error = str(e)
                logger.error(f"PaddleOCRv5 載入失敗: {e}", exc_info=True)
                return False

    def _map_result(self, raw, elapsed_ms: int) -> dict:
        """PaddleOCR 3.x mapping; filtered rec_polys aligns with rec_texts.

        Unexpected or inconsistent contracts fail explicitly instead of silently
        returning a successful empty result. 2.x adapters are not supported.
        """
        from collections.abc import Mapping
        detail = []
        for page in raw or []:
            if page is None:
                continue
            if not isinstance(page, Mapping):
                raise ValueError('Unsupported PaddleOCR result; expected 3.x Mapping')
            boxes, texts, scores = page['rec_polys'], page['rec_texts'], page['rec_scores']
            if not (len(boxes) == len(texts) == len(scores)):
                raise ValueError('PaddleOCR result lengths do not match')
            for box, text, confidence in zip(boxes,texts,scores):
                confidence = float(confidence)
                if text and confidence > .1:
                    detail.append(dict(box=box.tolist() if hasattr(box,'tolist') else box,
                                       text=_s2t(text), confidence=confidence))
        conf = sum(d['confidence'] for d in detail)/len(detail) if detail else 0.
        return dict(text=sort_boxes_and_merge(detail), confidence=conf,
                    status='done' if not detail or conf>=self._confidence_accept else 'needs_review',
                    detail=detail, elapsed_ms=elapsed_ms, engine=self.name,
                    model_version='unknown')
