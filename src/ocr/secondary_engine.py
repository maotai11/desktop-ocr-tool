# -*- coding: utf-8 -*-
"""
Patch H1 — 第二引擎抽象介面與 Null 實作

所有可插拔的第二 OCR 引擎都必須繼承 SecondaryEngineBase。
NullSecondaryEngine 作為預設佔位符，永遠回報「不可用」，確保未安裝任何
第二引擎時，現有 pipeline 行為完全不變。
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod

import numpy as np

logger = logging.getLogger(__name__)


class SecondaryEngineBase(ABC):
    """第二引擎統一介面。

    子類別需實作三個成員：
    - name (property) — 識別名稱，用於日誌
    - is_available() — 回傳 True 表示引擎已安裝且可用
    - recognize(image, mode) — 執行推論，回傳與 OcrEngine.run_ocr() 相同格式的 dict
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """引擎識別名稱（用於日誌與 UI 顯示）。"""

    @abstractmethod
    def is_available(self) -> bool:
        """回傳 True 若引擎已安裝、已載入、可執行推論。"""

    @abstractmethod
    def recognize(self, image: np.ndarray, mode: str = 'screen') -> dict:
        """執行 OCR 推論。

        Parameters
        ----------
        image : np.ndarray
            BGR 格式影像（與 OcrEngine.run_ocr 相同輸入格式）
        mode : str
            推論模式，例如 'screen'、'handwriting'

        Returns
        -------
        dict
            與 OcrEngine._process_results() 回傳格式相同：
            {
              'text': str,
              'confidence': float,
              'status': 'done' | 'needs_review' | 'failed',
              'detail': list,
              'elapsed_ms': int,
              'error': str   # 僅在 status='failed' 時存在
            }
        """


class NullSecondaryEngine(SecondaryEngineBase):
    """佔位符：永遠不可用，確保未安裝第二引擎時 pipeline 行為不變。"""

    @property
    def name(self) -> str:
        return 'none'

    def is_available(self) -> bool:
        return False

    def recognize(self, image: np.ndarray, mode: str = 'screen') -> dict:
        # 不應被呼叫，但作為安全網
        return {
            'text': '', 'confidence': 0.0, 'status': 'failed',
            'detail': [], 'elapsed_ms': 0,
            'error': 'NullSecondaryEngine.recognize() should not be called',
        }
