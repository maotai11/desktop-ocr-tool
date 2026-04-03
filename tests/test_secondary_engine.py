# -*- coding: utf-8 -*-
"""
Patch H1 回歸測試：第二引擎抽象介面 + OcrEngine fallback routing

涵蓋：
- SecondaryEngineBase 介面合規（子類別能正確實作）
- NullSecondaryEngine 行為
- OcrEngine._should_use_secondary() 觸發條件  [internal-behavior]
- OcrEngine._is_better() 比較邏輯              [internal-behavior]
- OcrEngine.set_secondary_engine() + configure(enable_secondary_engine=...) [public API]
- fast path（第二引擎關閉時）不受影響           [integration via run_ocr()]

標記說明：
- [internal-behavior] — 直接呼叫私有方法做白箱測試，因透過 run_ocr() 難以
  逐一覆蓋每個觸發條件組合，屬有意為之，不是遺漏。
- [public API] — 僅透過公開介面操作，不依賴私有欄位。

測試縫（test seam）說明：
- eng._ready = True / eng._engine = MagicMock(...)
  以上兩個私有欄位在所有測試中仍直接設定，原因是沒有 public API
  可以「注入 mock 引擎並跳過 load()」。這是可接受的測試縫，
  不屬於本次校正範圍。
"""
import numpy as np
import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# 輔助：最小可用的 FakeSecondaryEngine（合規子類別）
# ---------------------------------------------------------------------------

from src.ocr.secondary_engine import SecondaryEngineBase, NullSecondaryEngine


class FakeSecondaryEngine(SecondaryEngineBase):
    """測試用第二引擎，可透過建構子控制回傳結果。"""

    def __init__(self, available=True, result=None):
        self._available = available
        self._result = result or {
            'text': '第二引擎結果', 'confidence': 0.92,
            'status': 'done', 'detail': [], 'elapsed_ms': 50,
        }
        self.call_count = 0

    @property
    def name(self) -> str:
        return 'fake-engine'

    def is_available(self) -> bool:
        return self._available

    def recognize(self, image, mode='screen'):
        self.call_count += 1
        return self._result


# ---------------------------------------------------------------------------
# A. SecondaryEngineBase & NullSecondaryEngine
# ---------------------------------------------------------------------------

class TestSecondaryEngineBase:
    def test_null_engine_not_available(self):
        eng = NullSecondaryEngine()
        assert eng.is_available() is False

    def test_null_engine_name(self):
        assert NullSecondaryEngine().name == 'none'

    def test_null_engine_recognize_returns_failed(self):
        result = NullSecondaryEngine().recognize(np.zeros((10, 10, 3), np.uint8))
        assert result['status'] == 'failed'

    def test_fake_engine_complies_with_interface(self):
        eng = FakeSecondaryEngine()
        assert isinstance(eng, SecondaryEngineBase)
        assert eng.is_available() is True
        assert eng.name == 'fake-engine'
        res = eng.recognize(np.zeros((10, 10, 3), np.uint8))
        assert 'text' in res and 'confidence' in res and 'status' in res


# ---------------------------------------------------------------------------
# B. OcrEngine._should_use_secondary()  [internal-behavior test]
#    直接呼叫私有方法，以便逐一驗證各觸發條件，避免為每種組合建立完整 run_ocr mock。
# ---------------------------------------------------------------------------

def _make_engine(secondary_available=True, enable_secondary=True,
                 enable_handwriting=False):
    """建立帶有 fake secondary 的 OcrEngine（跳過 RapidOCR 載入）。

    test seam：eng._ready / eng._engine 直接設定，無 public API 可替代。
    enable_secondary_engine 開關改用 configure()（public API）設定。
    """
    from src.ocr.engine import OcrEngine
    eng = OcrEngine(enable_handwriting_mode=enable_handwriting)
    eng._ready = True        # test seam：跳過 load()
    eng._engine = MagicMock()
    eng._engine.return_value = ([], None)
    if enable_secondary:
        fake = FakeSecondaryEngine(available=secondary_available)
        eng.set_secondary_engine(fake)
        eng.configure(enable_secondary_engine=True)  # public API
    return eng


class TestShouldUseSecondary:
    def test_not_triggered_when_switch_off(self):
        from src.ocr.engine import OcrEngine
        eng = OcrEngine()
        eng._ready = True
        eng._engine = MagicMock(return_value=([], None))
        eng.set_secondary_engine(FakeSecondaryEngine(available=True))
        # 開關預設為 False
        primary_ok = {'text': 'OK', 'confidence': 0.9, 'status': 'done',
                      'detail': [], 'elapsed_ms': 10}
        assert eng._should_use_secondary(primary_ok, 'screen') is False

    def test_not_triggered_when_unavailable(self):
        eng = _make_engine(secondary_available=False)
        weak = {'text': '', 'confidence': 0.0, 'status': 'failed',
                'detail': [], 'elapsed_ms': 10}
        assert eng._should_use_secondary(weak, 'screen') is False

    def test_triggered_on_failed_status(self):
        eng = _make_engine()
        result = {'text': '', 'confidence': 0.0, 'status': 'failed',
                  'detail': [], 'elapsed_ms': 10}
        assert eng._should_use_secondary(result, 'screen') is True

    def test_triggered_on_needs_review(self):
        eng = _make_engine()
        result = {'text': '低信心', 'confidence': 0.55, 'status': 'needs_review',
                  'detail': [], 'elapsed_ms': 10}
        assert eng._should_use_secondary(result, 'screen') is True

    def test_triggered_on_empty_text(self):
        eng = _make_engine()
        result = {'text': '', 'confidence': 0.8, 'status': 'done',
                  'detail': [], 'elapsed_ms': 10}
        assert eng._should_use_secondary(result, 'screen') is True

    def test_triggered_in_handwriting_mode_flag(self):
        eng = _make_engine(enable_handwriting=True)
        # 即使主引擎結果好，handwriting 模式也觸發
        result = {'text': '高信心文字', 'confidence': 0.95, 'status': 'done',
                  'detail': [], 'elapsed_ms': 10}
        assert eng._should_use_secondary(result, 'screen') is True

    def test_triggered_in_handwriting_mode_param(self):
        eng = _make_engine()
        result = {'text': '高信心文字', 'confidence': 0.95, 'status': 'done',
                  'detail': [], 'elapsed_ms': 10}
        # mode='handwriting' 也觸發
        assert eng._should_use_secondary(result, 'handwriting') is True

    def test_not_triggered_when_primary_is_good(self):
        eng = _make_engine()
        result = {'text': '清晰文字', 'confidence': 0.95, 'status': 'done',
                  'detail': [], 'elapsed_ms': 10}
        # 正常 screen mode + 高信心 → 不觸發
        assert eng._should_use_secondary(result, 'screen') is False


# ---------------------------------------------------------------------------
# C. OcrEngine._is_better()  [internal-behavior test]
#    靜態方法，直接呼叫以驗證各比較分支，無需走完整 run_ocr() pipeline。
# ---------------------------------------------------------------------------

class TestIsBetter:
    def test_empty_candidate_is_not_better(self):
        from src.ocr.engine import OcrEngine
        assert OcrEngine._is_better(
            {'text': '', 'confidence': 0.9, 'status': 'done'},
            {'text': 'base', 'confidence': 0.7, 'status': 'done'}
        ) is False

    def test_any_text_beats_failed_baseline(self):
        from src.ocr.engine import OcrEngine
        assert OcrEngine._is_better(
            {'text': '識別到了', 'confidence': 0.5, 'status': 'needs_review'},
            {'text': '', 'confidence': 0.0, 'status': 'failed'}
        ) is True

    def test_higher_confidence_wins(self):
        from src.ocr.engine import OcrEngine
        assert OcrEngine._is_better(
            {'text': 'A', 'confidence': 0.9, 'status': 'done'},
            {'text': 'B', 'confidence': 0.7, 'status': 'needs_review'}
        ) is True

    def test_lower_confidence_loses(self):
        from src.ocr.engine import OcrEngine
        assert OcrEngine._is_better(
            {'text': 'A', 'confidence': 0.6, 'status': 'needs_review'},
            {'text': 'B', 'confidence': 0.8, 'status': 'done'}
        ) is False


# ---------------------------------------------------------------------------
# D. Integration: run_ocr() fast path 不受影響
# ---------------------------------------------------------------------------

class TestRunOcrFastPath:
    def _make_rapid_result(self, text='快速結果', conf=0.95):
        """RapidOCR 回傳格式：list of [box, text, conf]"""
        return [
            [[[0,0],[100,0],[100,20],[0,20]], text, conf]
        ]

    def test_fast_path_not_calling_secondary(self):
        """主引擎成功（status=done, text 非空）且 configure(enable_secondary_engine)
        從未呼叫（預設 False）→ FakeSecondaryEngine.call_count 應為 0。"""
        from src.ocr.engine import OcrEngine
        eng = OcrEngine()
        eng._ready = True        # test seam
        fake = FakeSecondaryEngine(available=True)
        eng.set_secondary_engine(fake)
        # configure(enable_secondary_engine=True) 未呼叫 → 開關維持預設 False
        eng._engine = MagicMock(return_value=(self._make_rapid_result(), None))  # test seam

        result = eng.run_ocr(np.zeros((64, 256, 3), np.uint8), 'screen')
        assert result['status'] == 'done'
        assert fake.call_count == 0

    def test_secondary_called_when_primary_fails(self):
        """主引擎回空（模擬失敗）且以 configure() 開啟開關 → 第二引擎應被呼叫。"""
        from src.ocr.engine import OcrEngine
        eng = OcrEngine()
        eng._ready = True        # test seam
        fake = FakeSecondaryEngine(available=True, result={
            'text': '手寫識別結果', 'confidence': 0.88,
            'status': 'done', 'detail': [], 'elapsed_ms': 60,
        })
        eng.set_secondary_engine(fake)
        eng.configure(enable_secondary_engine=True)   # public API
        eng._engine = MagicMock(return_value=([], None))  # test seam

        result = eng.run_ocr(np.zeros((64, 256, 3), np.uint8), 'screen')
        assert fake.call_count == 1
        assert result['text'] == '手寫識別結果'
        assert result['confidence'] == pytest.approx(0.88)

    def test_secondary_result_adopted_only_when_better(self):
        """第二引擎信心低於主引擎 → 保留主引擎結果。
        開關與 confidence_review 門檻均透過 configure()（public API）設定。
        """
        from src.ocr.engine import OcrEngine
        eng = OcrEngine()
        eng._ready = True        # test seam
        # 主引擎回 needs_review（conf=0.65）
        primary_raw = [
            [[[0,0],[100,0],[100,20],[0,20]], '主引擎文字', 0.65]
        ]
        # 第二引擎 conf=0.50（更差）
        fake = FakeSecondaryEngine(available=True, result={
            'text': '第二引擎文字', 'confidence': 0.50,
            'status': 'needs_review', 'detail': [], 'elapsed_ms': 60,
        })
        eng.set_secondary_engine(fake)
        eng.configure(                        # public API
            enable_secondary_engine=True,
            confidence_review=0.60,           # 0.65 > 0.60 → primary 為 needs_review → 觸發 secondary
        )
        eng._engine = MagicMock(return_value=(primary_raw, None))  # test seam

        result = eng.run_ocr(np.zeros((64, 256, 3), np.uint8), 'screen')
        assert fake.call_count == 1
        assert '主引擎文字' in result['text']
