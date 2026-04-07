# -*- coding: utf-8 -*-
"""
CnOCR Provider Adapter — 輕量化繁體中文 OCR 引擎

optional dependency: cnocr
若未安裝，is_available() 回傳 False，不影響主流程。

設計決策：
- 模組頂層 try/except ImportError 決定 _CNOCR_AVAILABLE flag
- 模型載入採懶惰載入（首次 recognize() 時），避免 app 啟動時阻塞
- 預設使用 ONNX 後端（比 PyTorch 快 2 倍，適合生產環境）
- 支援繁體中文模型 chinese_cht_PP-OCRv3
- 支援多種場景模型（文檔/場景/垂直文字）
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

import numpy as np

from ..secondary_engine import SecondaryEngineBase
from ..postprocessor import sort_boxes_and_merge

logger = logging.getLogger(__name__)

# --- optional dependency guard -------------------------------------------

try:
    import cnocr as _cnocr_pkg
    _CNOCR_AVAILABLE = True
    logger.debug("cnocr 套件已找到")
except ImportError:
    _cnocr_pkg = None  # type: ignore[assignment]
    _CNOCR_AVAILABLE = False
    logger.debug("cnocr 未安裝，CnOcrProvider 不可用")

# --- 簡繁轉換（複用主引擎做法）------------------------------------------

try:
    import zhconv as _zhconv
    def _s2t(text: str) -> str:
        return _zhconv.convert(text, 'zh-hant')
except ImportError:
    def _s2t(text: str) -> str:
        return text


# --- 模型配置 -----------------------------------------------------------

# CnOCR 預設模型映射
MODEL_CONFIGS = {
    'traditional_chinese': {
        'rec_model_name': 'chinese_cht_PP-OCRv3',
        'det_model_name': 'ch_PP-OCRv5_det',
        'description': '繁體中文辨識（預設）',
    },
    'document': {
        'rec_model_name': 'doc-densenet_lite_136-gru',
        'det_model_name': 'naive_det',
        'description': '文檔截圖（快速）',
    },
    'scene': {
        'rec_model_name': 'scene-densenet_lite_136-gru',
        'det_model_name': 'ch_PP-OCRv5_det',
        'description': '場景文字（複雜背景）',
    },
    'number': {
        'rec_model_name': 'number-densenet_lite_136-fc',
        'det_model_name': 'naive_det',
        'description': '純數字（身分證/銀行卡）',
    },
}


# -------------------------------------------------------------------------

class CnOcrProvider(SecondaryEngineBase):
    """CnOCR 繁體中文引擎 adapter。

    作為 RapidOCR 主引擎的備援，專精於繁體中文辨識。
    cnocr 未安裝時，is_available() 回傳 False，recognize() 回傳 failed。
    """

    def __init__(
        self,
        model_profile: str = 'traditional_chinese',
        use_gpu: bool = False,
        rec_batch_size: int = 4,
        resized_shape: int = 768,
        box_score_thresh: float = 0.3,
        min_box_size: int = 8,
        confidence_accept: float = 0.85,
        cand_alphabet: str | None = None,
    ):
        """
        Parameters
        ----------
        model_profile : str
            模型配置檔名稱，預設 'traditional_chinese'
            可選: 'traditional_chinese', 'document', 'scene', 'number'
        use_gpu : bool
            是否使用 GPU 加速（預設 False，使用 ONNX CPU）
        rec_batch_size : int
            辨識批次大小（預設 4）
        resized_shape : int
            偵測模型輸入尺寸（預設 768，影響準確率）
        box_score_thresh : float
            偵測信心門檻（預設 0.3）
        min_box_size : int
            最小文字框尺寸（預設 8，過濾小雜訊）
        confidence_accept : float
            信心門檻：高於此值 status='done'，否則 'needs_review'
        cand_alphabet : str | None
            限制辨識字元（例如 '0123456789'），預設 None 不限制
        """
        self._model_profile = model_profile
        self._use_gpu = use_gpu
        self._rec_batch_size = rec_batch_size
        self._resized_shape = resized_shape
        self._box_score_thresh = box_score_thresh
        self._min_box_size = min_box_size
        self._confidence_accept = confidence_accept
        self._cand_alphabet = cand_alphabet
        self._engine = None
        self._load_error: str | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # SecondaryEngineBase interface
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return 'cnocr_traditional_chinese'

    def is_available(self) -> bool:
        """回傳 True 若 cnocr 套件已安裝且無先前載入錯誤。

        不代表模型已載入（模型在首次 recognize() 時懶惰載入）。
        """
        return _CNOCR_AVAILABLE and self._load_error is None

    def recognize(self, image: np.ndarray, mode: str = 'screen') -> dict:
        """執行 CnOCR 推論。

        若套件未安裝或載入失敗，直接回傳 status='failed'，不拋例外。
        
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
                'error': self._load_error or 'cnocr 未安裝',
            }

        t0 = time.time()

        if not self._ensure_loaded():
            return {
                'text': '', 'confidence': 0.0, 'status': 'failed',
                'detail': [], 'elapsed_ms': 0,
                'error': self._load_error or 'CnOCR 載入失敗',
            }

        try:
            # CnOCR 需要 RGB 格式，轉換 BGR -> RGB
            if len(image.shape) == 3 and image.shape[2] == 3:
                image_rgb = image[:, :, ::-1]  # BGR -> RGB
            else:
                image_rgb = image

            # 執行 OCR
            raw = self._engine.ocr(
                image_rgb,
                rec_batch_size=self._rec_batch_size,
                resized_shape=self._resized_shape,
                preserve_aspect_ratio=True,
                min_box_size=self._min_box_size,
                box_score_thresh=self._box_score_thresh,
            )
            elapsed_ms = int((time.time() - t0) * 1000)
            return self._map_result(raw, elapsed_ms)
            
        except Exception as e:
            elapsed_ms = int((time.time() - t0) * 1000)
            logger.error(f"CnOCR 辨識失敗: {e}", exc_info=True)
            return {
                'text': '', 'confidence': 0.0, 'status': 'failed',
                'detail': [], 'elapsed_ms': elapsed_ms, 'error': str(e),
            }

    # ------------------------------------------------------------------
    # 內部輔助
    # ------------------------------------------------------------------

    def _ensure_loaded(self) -> bool:
        """懶惰載入 CnOCR 模型，thread-safe。回傳 True 表示已就緒。"""
        if self._engine is not None:
            return True
        if not _CNOCR_AVAILABLE:
            return False
        with self._lock:
            if self._engine is not None:  # double-checked locking
                return True
            try:
                logger.info(
                    "載入 CnOCR 引擎 (profile=%s)...", self._model_profile
                )
                
                # 取得模型配置
                config = MODEL_CONFIGS.get(
                    self._model_profile,
                    MODEL_CONFIGS['traditional_chinese']
                )
                
                # 決定後端
                if self._use_gpu:
                    rec_backend = 'pytorch'
                    context = 'cuda'
                else:
                    rec_backend = 'onnx'
                    context = 'cpu'
                
                # 建立 CnOcr 實例
                init_kwargs = {
                    'rec_model_name': config['rec_model_name'],
                    'det_model_name': config['det_model_name'],
                    'rec_model_backend': rec_backend,
                }
                
                if self._use_gpu:
                    init_kwargs['context'] = context
                
                if self._cand_alphabet is not None:
                    init_kwargs['cand_alphabet'] = self._cand_alphabet
                
                self._engine = _cnocr_pkg.CnOcr(**init_kwargs)
                
                logger.info(
                    "CnOCR 引擎已就緒 (backend=%s, profile=%s)",
                    rec_backend,
                    self._model_profile
                )
                return True
                
            except Exception as e:
                self._load_error = str(e)
                logger.error(f"CnOCR 載入失敗: {e}", exc_info=True)
                return False

    def _map_result(self, raw, elapsed_ms: int) -> dict:
        """將 CnOCR 原始結果映射到標準格式。

        Parameters
        ----------
        raw :
            CnOCR .ocr() 回傳值
            格式: [{'text': str, 'score': float, 'position': np.array}, ...]
        elapsed_ms : int
            已耗時（毫秒），由 recognize() 傳入

        Returns
        -------
        dict
            標準結果格式
        """
        if not raw:
            return {
                'text': '', 'confidence': 0.0, 'status': 'done',
                'detail': [], 'elapsed_ms': elapsed_ms,
            }

        # 轉換為內部 detail 格式
        detail: list[dict] = []
        for item in raw:
            text = item.get('text', '')
            confidence = float(item.get('score', 0.0))
            position = item.get('position')
            
            if text and confidence > 0.1:
                # 簡→繁轉換
                text = _s2t(text)
                
                detail.append({
                    'box': position.tolist() if hasattr(position, 'tolist') else position,
                    'text': text,
                    'confidence': confidence,
                })

        if not detail:
            return {
                'text': '', 'confidence': 0.0, 'status': 'done',
                'detail': [], 'elapsed_ms': elapsed_ms,
            }

        # 合併文字
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
