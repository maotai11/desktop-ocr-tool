# -*- coding: utf-8 -*-
"""
Patch H4 補強測試：fallback 邊界情況 + provider 失敗路徑 + 3.x 格式映射

涵蓋 H2 原始測試集的三個空白：
- PaddleOCR 3.x OCRResult 物件格式映射  [internal-behavior]
- _ensure_loaded() 失敗路徑（exception → _load_error 設定）
- recognize() 當 _ensure_loaded() 回 False 時不拋例外

所有測試在 paddleocr 未安裝環境下均可通過（mock _PADDLEOCR_AVAILABLE）。

測試縫說明：
- provider._load_error 直接設定（無 public API 可觸發「載入失敗」狀態）
- provider._engine 直接設定為 MagicMock（懶惰載入的測試縫）
"""
import numpy as np
import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# A. PaddleOCR 3.x OCRResult 格式映射  [internal-behavior]
# ---------------------------------------------------------------------------

class _FakeOCRResult3x:
    """模擬 PaddleOCR 3.x 的 OCRResult 物件（帶 dt_polys / rec_texts / rec_scores）。"""

    def __init__(self, items: list[tuple]):
        """items: [(box, text, conf), ...]"""
        self.dt_polys = [item[0] for item in items]
        self.rec_texts = [item[1] for item in items]
        self.rec_scores = [item[2] for item in items]


class TestResultMapping3x:
    """PaddleOCR 3.x OCRResult 物件格式映射。"""

    def _provider(self, confidence_accept=0.85):
        from src.ocr.providers.paddleocr_v5_provider import PaddleOCRv5Provider
        return PaddleOCRv5Provider(confidence_accept=confidence_accept)

    def _box(self):
        return [[0, 0], [100, 0], [100, 20], [0, 20]]

    def test_3x_single_item_detected(self):
        p = self._provider()
        raw = [_FakeOCRResult3x([(self._box(), '手寫文字', 0.91)])]
        result = p._map_result(raw, elapsed_ms=30)
        assert '手寫文字' in result['text']
        assert len(result['detail']) == 1
        assert result['elapsed_ms'] == 30

    def test_3x_multiple_items(self):
        p = self._provider()
        raw = [_FakeOCRResult3x([
            (self._box(), '第一行', 0.90),
            (self._box(), '第二行', 0.88),
        ])]
        result = p._map_result(raw, elapsed_ms=40)
        assert len(result['detail']) == 2

    def test_3x_low_conf_gives_needs_review(self):
        p = self._provider(confidence_accept=0.80)
        raw = [_FakeOCRResult3x([(self._box(), '模糊字', 0.65)])]
        result = p._map_result(raw, elapsed_ms=20)
        assert result['status'] == 'needs_review'

    def test_3x_high_conf_gives_done(self):
        p = self._provider(confidence_accept=0.80)
        raw = [_FakeOCRResult3x([(self._box(), '清晰字', 0.92)])]
        result = p._map_result(raw, elapsed_ms=20)
        assert result['status'] == 'done'

    def test_3x_very_low_conf_filtered_out(self):
        """信心 <= 0.1 的項目應被過濾。"""
        p = self._provider()
        raw = [_FakeOCRResult3x([(self._box(), '雜訊', 0.05)])]
        result = p._map_result(raw, elapsed_ms=10)
        assert result['text'] == ''
        assert len(result['detail']) == 0

    def test_3x_none_page_skipped(self):
        """3.x 格式中，後續的 None 頁面不應拋例外。
        注意：detection 以 raw[0] 判斷格式，故第一頁必須是有效的 OCRResult 物件。
        """
        p = self._provider()
        # 第一頁有效（觸發 3.x detection），第二頁為 None（應被 _extract_from_3x 跳過）
        raw = [_FakeOCRResult3x([(self._box(), '有效文字', 0.89)]), None]
        result = p._map_result(raw, elapsed_ms=10)
        assert len(result['detail']) == 1
        assert result['detail'][0]['text'] == '有效文字'

    def test_3x_empty_page_returns_done_empty(self):
        p = self._provider()
        raw = [_FakeOCRResult3x([])]
        result = p._map_result(raw, elapsed_ms=10)
        assert result['status'] == 'done'
        assert result['text'] == ''

    def test_3x_box_with_tolist_converted(self):
        """box 帶 .tolist() 方法時應自動轉換（numpy array）。"""
        p = self._provider()
        mock_box = MagicMock()
        mock_box.tolist.return_value = [[0, 0], [100, 0], [100, 20], [0, 20]]
        raw = [_FakeOCRResult3x([(mock_box, '文字', 0.90)])]
        result = p._map_result(raw, elapsed_ms=10)
        assert len(result['detail']) == 1
        # tolist() 應被呼叫
        mock_box.tolist.assert_called_once()


# ---------------------------------------------------------------------------
# B. _ensure_loaded() 失敗路徑
# ---------------------------------------------------------------------------

class TestEnsureLoadedFailure:
    """驗證 _ensure_loaded() 失敗後的狀態一致性。"""

    def test_load_exception_sets_load_error(self):
        """_ensure_loaded() 拋例外 → _load_error 應設定為錯誤訊息。"""
        from src.ocr.providers.paddleocr_v5_provider import PaddleOCRv5Provider

        p = PaddleOCRv5Provider()
        with patch('src.ocr.providers.paddleocr_v5_provider._PADDLEOCR_AVAILABLE', True), \
             patch('src.ocr.providers.paddleocr_v5_provider._paddleocr_pkg') as mock_pkg:
            mock_pkg.PaddleOCR.side_effect = RuntimeError('模型下載失敗')
            ok = p._ensure_loaded()

        assert ok is False
        assert p._load_error is not None
        assert '模型下載失敗' in p._load_error

    def test_load_error_makes_is_available_false(self):
        """_load_error 設定後，is_available() 應回 False（即使套件已安裝）。"""
        from src.ocr.providers.paddleocr_v5_provider import PaddleOCRv5Provider

        p = PaddleOCRv5Provider()
        p._load_error = '先前載入失敗'
        with patch('src.ocr.providers.paddleocr_v5_provider._PADDLEOCR_AVAILABLE', True):
            assert p.is_available() is False

    def test_recognize_returns_failed_after_load_error(self):
        """先前載入失敗後，recognize() 應回 status='failed'，不拋例外。"""
        from src.ocr.providers.paddleocr_v5_provider import PaddleOCRv5Provider

        p = PaddleOCRv5Provider()
        p._load_error = '先前載入失敗'
        with patch('src.ocr.providers.paddleocr_v5_provider._PADDLEOCR_AVAILABLE', True):
            result = p.recognize(np.zeros((10, 10, 3), np.uint8))

        assert result['status'] == 'failed'
        assert result['text'] == ''
        assert 'error' in result

    def test_ensure_loaded_idempotent_after_success(self):
        """_ensure_loaded() 成功後，第二次呼叫不重複載入。"""
        from src.ocr.providers.paddleocr_v5_provider import PaddleOCRv5Provider

        p = PaddleOCRv5Provider()
        mock_engine = MagicMock()
        p._engine = mock_engine  # test seam：直接設定為已載入

        with patch('src.ocr.providers.paddleocr_v5_provider._PADDLEOCR_AVAILABLE', True), \
             patch('src.ocr.providers.paddleocr_v5_provider._paddleocr_pkg') as mock_pkg:
            ok = p._ensure_loaded()
            # 不應再次呼叫 PaddleOCR()
            mock_pkg.PaddleOCR.assert_not_called()

        assert ok is True
        assert p._engine is mock_engine  # engine 沒被替換


# ---------------------------------------------------------------------------
# C. recognize() 當 _ensure_loaded() 回 False 時的保護
# ---------------------------------------------------------------------------

class TestRecognizeEnsureLoadedFalse:
    """recognize() 內部在 _ensure_loaded() 失敗時不應拋例外。"""

    def test_recognize_safe_when_ensure_loaded_fails(self):
        """_ensure_loaded() 回 False 時（例如例外），recognize() 安全降級。"""
        from src.ocr.providers.paddleocr_v5_provider import PaddleOCRv5Provider

        p = PaddleOCRv5Provider()
        # paddleocr「已安裝」但 PaddleOCR() 建構子失敗
        with patch('src.ocr.providers.paddleocr_v5_provider._PADDLEOCR_AVAILABLE', True), \
             patch('src.ocr.providers.paddleocr_v5_provider._paddleocr_pkg') as mock_pkg:
            mock_pkg.PaddleOCR.side_effect = OSError('磁碟空間不足')
            result = p.recognize(np.zeros((32, 128, 3), np.uint8))

        assert result['status'] == 'failed'
        assert result['text'] == ''
        assert 'error' in result

    def test_recognize_exception_in_engine_ocr_caught(self):
        """engine.ocr() 拋例外時，recognize() 應捕捉並回 status='failed'。"""
        from src.ocr.providers.paddleocr_v5_provider import PaddleOCRv5Provider

        p = PaddleOCRv5Provider()
        p._engine = MagicMock()  # test seam
        p._engine.ocr.side_effect = ValueError('無效輸入')
        with patch('src.ocr.providers.paddleocr_v5_provider._PADDLEOCR_AVAILABLE', True):
            result = p.recognize(np.zeros((32, 128, 3), np.uint8))

        assert result['status'] == 'failed'
        assert '無效輸入' in result.get('error', '')
