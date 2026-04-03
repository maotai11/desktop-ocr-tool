# -*- coding: utf-8 -*-
"""
Patch H2 回歸測試：第二引擎 provider adapter

涵蓋：
- create_provider() 工廠行為
- list_known_providers()
- PaddleOCRv5Provider 介面合規
- paddleocr 未安裝時的 unavailable path
- _map_result() 2.x 與 3.x 格式映射  [internal-behavior]
- H1 + H2 整合：set_secondary_engine() + configure() + run_ocr() routing

所有測試在 paddleocr 未安裝的環境下也能通過（mock _PADDLEOCR_AVAILABLE）。

測試縫（test seam）說明：
- eng._ready / eng._engine 直接設定，跳過 load()（無 public API 可替代）
- provider._engine 在模擬「已載入」情境時直接設定（懶惰載入的測試縫）
"""
import numpy as np
import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# A. 工廠 create_provider()
# ---------------------------------------------------------------------------

class TestProviderFactory:
    def test_create_known_provider_returns_correct_type(self):
        from src.ocr.providers import create_provider
        from src.ocr.providers.paddleocr_v5_provider import PaddleOCRv5Provider
        p = create_provider('paddleocr_v5_mobile')
        assert isinstance(p, PaddleOCRv5Provider)

    def test_create_unknown_returns_null(self):
        from src.ocr.providers import create_provider
        from src.ocr.secondary_engine import NullSecondaryEngine
        p = create_provider('no_such_provider')
        assert isinstance(p, NullSecondaryEngine)

    def test_create_with_kwargs_forwarded(self):
        """kwargs 應被透傳到 provider 建構子。"""
        from src.ocr.providers import create_provider
        from src.ocr.providers.paddleocr_v5_provider import PaddleOCRv5Provider
        p = create_provider('paddleocr_v5_mobile', confidence_accept=0.75, lang='en')
        assert isinstance(p, PaddleOCRv5Provider)
        assert p._confidence_accept == pytest.approx(0.75)
        assert p._lang == 'en'

    def test_list_known_includes_paddleocr(self):
        from src.ocr.providers import list_known_providers
        assert 'paddleocr_v5_mobile' in list_known_providers()

    def test_list_known_returns_tuple(self):
        from src.ocr.providers import list_known_providers
        result = list_known_providers()
        assert isinstance(result, tuple)


# ---------------------------------------------------------------------------
# B. PaddleOCRv5Provider — 介面合規 + 未安裝路徑
# ---------------------------------------------------------------------------

class TestPaddleOCRv5ProviderInterface:
    def test_complies_with_secondary_engine_base(self):
        from src.ocr.providers.paddleocr_v5_provider import PaddleOCRv5Provider
        from src.ocr.secondary_engine import SecondaryEngineBase
        assert isinstance(PaddleOCRv5Provider(), SecondaryEngineBase)

    def test_name_is_correct(self):
        from src.ocr.providers.paddleocr_v5_provider import PaddleOCRv5Provider
        assert PaddleOCRv5Provider().name == 'paddleocr_v5_mobile'

    def test_is_available_false_when_not_installed(self):
        """_PADDLEOCR_AVAILABLE=False 時，is_available() 應為 False。"""
        from src.ocr.providers.paddleocr_v5_provider import PaddleOCRv5Provider
        p = PaddleOCRv5Provider()
        with patch('src.ocr.providers.paddleocr_v5_provider._PADDLEOCR_AVAILABLE', False):
            assert p.is_available() is False

    def test_is_available_false_after_load_error(self):
        """載入失敗後，is_available() 應為 False。"""
        from src.ocr.providers.paddleocr_v5_provider import PaddleOCRv5Provider
        p = PaddleOCRv5Provider()
        p._load_error = '模擬載入錯誤'
        with patch('src.ocr.providers.paddleocr_v5_provider._PADDLEOCR_AVAILABLE', True):
            assert p.is_available() is False

    def test_recognize_returns_failed_when_not_installed(self):
        """paddleocr 未安裝時，recognize() 應回傳 status='failed'，不拋例外。"""
        from src.ocr.providers.paddleocr_v5_provider import PaddleOCRv5Provider
        p = PaddleOCRv5Provider()
        with patch('src.ocr.providers.paddleocr_v5_provider._PADDLEOCR_AVAILABLE', False):
            result = p.recognize(np.zeros((10, 10, 3), np.uint8))
        assert result['status'] == 'failed'
        assert result['text'] == ''
        assert 'error' in result

    def test_recognize_error_message_mentions_paddleocr(self):
        """未安裝時 error 訊息應提及 paddleocr。"""
        from src.ocr.providers.paddleocr_v5_provider import PaddleOCRv5Provider
        p = PaddleOCRv5Provider()
        with patch('src.ocr.providers.paddleocr_v5_provider._PADDLEOCR_AVAILABLE', False):
            result = p.recognize(np.zeros((10, 10, 3), np.uint8))
        assert 'paddleocr' in result.get('error', '').lower()


# ---------------------------------------------------------------------------
# C. _map_result()  [internal-behavior test]
#    直接呼叫 _map_result，不需要 paddleocr 安裝。
# ---------------------------------------------------------------------------

class TestResultMapping2x:
    """PaddleOCR 2.x list-of-pages 格式映射。"""

    def _provider(self, confidence_accept=0.85):
        from src.ocr.providers.paddleocr_v5_provider import PaddleOCRv5Provider
        return PaddleOCRv5Provider(confidence_accept=confidence_accept)

    def _box(self):
        return [[0, 0], [100, 0], [100, 20], [0, 20]]

    def test_single_item_mapped_correctly(self):
        p = self._provider()
        raw = [[ [self._box(), ['Hello', 0.95]] ]]
        result = p._map_result(raw, elapsed_ms=50)
        assert 'Hello' in result['text']
        assert len(result['detail']) == 1
        assert result['elapsed_ms'] == 50

    def test_multi_item_all_included(self):
        p = self._provider()
        raw = [[
            [self._box(), ['Line1', 0.90]],
            [self._box(), ['Line2', 0.88]],
        ]]
        result = p._map_result(raw, elapsed_ms=10)
        assert len(result['detail']) == 2

    def test_none_item_skipped(self):
        """頁面中的 None 項目應被跳過，不拋例外。"""
        p = self._provider()
        raw = [[ None, [self._box(), ['Valid', 0.88]] ]]
        result = p._map_result(raw, elapsed_ms=10)
        assert len(result['detail']) == 1
        assert result['detail'][0]['text'] == 'Valid'

    def test_empty_page_returns_done_empty(self):
        p = self._provider()
        result = p._map_result([[]], elapsed_ms=10)
        assert result['status'] == 'done'
        assert result['text'] == ''
        assert result['confidence'] == 0.0

    def test_none_raw_returns_done_empty(self):
        p = self._provider()
        result = p._map_result(None, elapsed_ms=10)
        assert result['status'] == 'done'
        assert result['text'] == ''

    def test_high_conf_gives_done_status(self):
        p = self._provider(confidence_accept=0.80)
        raw = [[ [self._box(), ['Clear', 0.92]] ]]
        result = p._map_result(raw, elapsed_ms=10)
        assert result['status'] == 'done'

    def test_low_conf_gives_needs_review(self):
        p = self._provider(confidence_accept=0.80)
        raw = [[ [self._box(), ['Fuzzy', 0.65]] ]]
        result = p._map_result(raw, elapsed_ms=10)
        assert result['status'] == 'needs_review'

    def test_very_low_conf_item_filtered_out(self):
        """信心 <= 0.1 的項目應被過濾（與主引擎行為一致）。"""
        p = self._provider()
        raw = [[ [self._box(), ['Noise', 0.05]] ]]
        result = p._map_result(raw, elapsed_ms=10)
        assert result['text'] == ''
        assert len(result['detail']) == 0


# ---------------------------------------------------------------------------
# D. H1 + H2 整合：set_secondary_engine + configure + run_ocr routing
# ---------------------------------------------------------------------------

class TestH1H2Integration:
    """驗證 H2 provider 可被 H1 fallback routing 正確呼叫。"""

    def _make_ocr_engine_with_provider(self, available: bool = True):
        """建立帶有 PaddleOCRv5Provider 的 OcrEngine，mock paddleocr 可用性。"""
        from src.ocr.engine import OcrEngine
        from src.ocr.providers.paddleocr_v5_provider import PaddleOCRv5Provider

        eng = OcrEngine()
        eng._ready = True        # test seam：跳過 load()
        eng._engine = MagicMock(return_value=([], None))  # test seam：主引擎回空

        provider = PaddleOCRv5Provider()
        # mock paddleocr 可用性
        patcher = patch(
            'src.ocr.providers.paddleocr_v5_provider._PADDLEOCR_AVAILABLE',
            available,
        )
        patcher.start()

        if available:
            # mock _ensure_loaded 直接成功，engine 設為 mock
            provider._engine = MagicMock()
            provider._engine.ocr.return_value = [[
                [[[0,0],[100,0],[100,20],[0,20]], ['手寫文字', 0.91]]
            ]]

        eng.set_secondary_engine(provider)
        eng.configure(enable_secondary_engine=True)   # public API
        return eng, patcher

    def test_provider_called_when_primary_empty(self):
        """主引擎回空 + provider 可用 → provider.recognize() 應被呼叫，結果採用。"""
        eng, patcher = self._make_ocr_engine_with_provider(available=True)
        try:
            result = eng.run_ocr(np.zeros((64, 256, 3), np.uint8), 'screen')
            assert result['text'] == '手寫文字'
            assert result['status'] in ('done', 'needs_review')
        finally:
            patcher.stop()

    def test_provider_not_called_when_unavailable(self):
        """provider.is_available()=False → fallback routing 跳過，保留主引擎結果。"""
        eng, patcher = self._make_ocr_engine_with_provider(available=False)
        try:
            result = eng.run_ocr(np.zeros((64, 256, 3), np.uint8), 'screen')
            # 主引擎回空 → status='done'（空結果），不應取到 provider 的文字
            assert result['text'] == ''
        finally:
            patcher.stop()

    def test_provider_replaced_by_null_stops_fallback(self):
        """set_secondary_engine(NullSecondaryEngine()) 後，fallback 不再觸發。"""
        from src.ocr.engine import OcrEngine
        from src.ocr.secondary_engine import NullSecondaryEngine

        eng = OcrEngine()
        eng._ready = True
        eng._engine = MagicMock(return_value=([], None))
        eng.configure(enable_secondary_engine=True)   # public API
        eng.set_secondary_engine(NullSecondaryEngine())

        result = eng.run_ocr(np.zeros((64, 256, 3), np.uint8), 'screen')
        assert result['text'] == ''  # Null engine 不提供文字
