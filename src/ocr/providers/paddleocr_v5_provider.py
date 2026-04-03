# -*- coding: utf-8 -*-
"""
Patch H2 — PP-OCRv5 Mobile Provider Adapter

optional dependency: paddleocr (PaddlePaddle stack)
若未安裝，is_available() 回傳 False，不影響主流程。

設計決策：
- 模組頂層 try/except ImportError 決定 _PADDLEOCR_AVAILABLE flag
- 模型載入採懶惰載入（首次 recognize() 時），避免 app 啟動時阻塞
- 支援 PaddleOCR 2.x 與 3.x (PaddleX backend) 兩種結果格式
- confidence_accept 門檻為建構子參數，預設 0.85，保持與主引擎一致
"""
from __future__ import annotations

import logging
import threading
import time

import numpy as np

from ..secondary_engine import SecondaryEngineBase
from ..postprocessor import sort_boxes_and_merge

logger = logging.getLogger(__name__)

# --- optional dependency guard -------------------------------------------

try:
    import paddleocr as _paddleocr_pkg
    _PADDLEOCR_AVAILABLE = True
except ImportError:
    _paddleocr_pkg = None  # type: ignore[assignment]
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


# -------------------------------------------------------------------------

class PaddleOCRv5Provider(SecondaryEngineBase):
    """PP-OCRv5 Mobile 第二引擎 adapter。

    作為 RapidOCR 主引擎的備援，適用於手寫或低信心場景。
    paddleocr 未安裝時，is_available() 回傳 False，recognize() 回傳 failed。
    """

    def __init__(
        self,
        lang: str = 'ch',
        use_angle_cls: bool = True,
        show_log: bool = False,
        confidence_accept: float = 0.85,
        **extra_kwargs,
    ):
        """
        Parameters
        ----------
        lang : str
            PaddleOCR 語言代碼，預設 'ch'（支援中英混合）
        use_angle_cls : bool
            是否啟用方向分類器
        show_log : bool
            是否印出 PaddleOCR 內部 log（預設 False，避免干擾 app log）
        confidence_accept : float
            信心門檻：高於此值 status='done'，否則 'needs_review'
        **extra_kwargs
            透傳給 PaddleOCR 建構子的額外參數（e.g. use_gpu=False）
        """
        self._lang = lang
        self._use_angle_cls = use_angle_cls
        self._show_log = show_log
        self._confidence_accept = confidence_accept
        self._extra_kwargs = extra_kwargs
        self._engine = None
        self._load_error: str | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # SecondaryEngineBase interface
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return 'paddleocr_v5_mobile'

    def is_available(self) -> bool:
        """回傳 True 若 paddleocr 套件已安裝且無先前載入錯誤。

        不代表模型已載入（模型在首次 recognize() 時懶惰載入）。
        """
        return _PADDLEOCR_AVAILABLE and self._load_error is None

    def recognize(self, image: np.ndarray, mode: str = 'screen') -> dict:
        """執行 PP-OCRv5 Mobile 推論。

        若套件未安裝或載入失敗，直接回傳 status='failed'，不拋例外。
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
            raw = self._engine.ocr(image, cls=self._use_angle_cls)
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
        with self._lock:
            if self._engine is not None:  # double-checked locking
                return True
            try:
                logger.info(
                    "載入 PaddleOCRv5 Mobile 引擎 (lang=%s)...", self._lang
                )
                self._engine = _paddleocr_pkg.PaddleOCR(
                    lang=self._lang,
                    use_angle_cls=self._use_angle_cls,
                    show_log=self._show_log,
                    **self._extra_kwargs,
                )
                logger.info("PaddleOCRv5 Mobile 引擎已就緒")
                return True
            except Exception as e:
                self._load_error = str(e)
                logger.error(f"PaddleOCRv5 Mobile 載入失敗: {e}", exc_info=True)
                return False

    def _map_result(self, raw, elapsed_ms: int) -> dict:
        """將 PaddleOCR 原始結果映射到標準格式。

        支援兩種格式：
        - PaddleOCR 2.x: list of pages，每頁為 [box, [text, conf]] 的清單
        - PaddleOCR 3.x (PaddleX): list of OCRResult 物件，帶 dt_polys/rec_texts/rec_scores

        Parameters
        ----------
        raw :
            PaddleOCR .ocr() 回傳值
        elapsed_ms : int
            已耗時（毫秒），由 recognize() 傳入

        Returns
        -------
        dict
            標準結果格式，與 OcrEngine._process_results() 回傳相同結構
        """
        detail: list[dict] = []

        # --- 判斷格式並提取 detail ---
        if raw and raw[0] is not None and hasattr(raw[0], 'dt_polys'):
            # PaddleOCR 3.x (PaddleX backend)：OCRResult objects
            detail = self._extract_from_3x(raw)
        else:
            # PaddleOCR 2.x：list of pages
            detail = self._extract_from_2x(raw)

        if not detail:
            return {
                'text': '', 'confidence': 0.0, 'status': 'done',
                'detail': [], 'elapsed_ms': elapsed_ms,
            }

        full_text = sort_boxes_and_merge(detail)
        confs = [d['confidence'] for d in detail]
        avg_conf = sum(confs) / len(confs)
        status = 'done' if avg_conf >= self._confidence_accept else 'needs_review'

        return {
            'text': full_text,
            'confidence': avg_conf,
            'status': status,
            'detail': detail,
            'elapsed_ms': elapsed_ms,
        }

    @staticmethod
    def _extract_from_3x(raw) -> list[dict]:
        """PaddleOCR 3.x OCRResult 格式提取。"""
        detail: list[dict] = []
        for page_result in raw:
            if page_result is None:
                continue
            try:
                for box, text, conf in zip(
                    page_result.dt_polys,
                    page_result.rec_texts,
                    page_result.rec_scores,
                ):
                    conf_f = float(conf)
                    if text and conf_f > 0.1:
                        detail.append({
                            'box': (
                                box.tolist() if hasattr(box, 'tolist') else box
                            ),
                            'text': _s2t(text),
                            'confidence': conf_f,
                        })
            except Exception:
                continue
        return detail

    @staticmethod
    def _extract_from_2x(raw) -> list[dict]:
        """PaddleOCR 2.x list-of-pages 格式提取。"""
        detail: list[dict] = []
        for page in (raw or []):
            if not page:
                continue
            for item in page:
                if item is None:
                    continue
                try:
                    box = item[0]
                    text, conf = item[1][0], float(item[1][1])
                    if text and conf > 0.1:
                        detail.append({
                            'box': box,
                            'text': _s2t(text),
                            'confidence': conf,
                        })
                except Exception:
                    continue
        return detail
