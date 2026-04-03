# -*- coding: utf-8 -*-
"""
Patch H3 回歸測試：config / settings / diagnostics 接線

涵蓋：
- DEFAULT_SETTINGS 包含 5 個新 H3 key
- ConfigManager.get() 對新 key 回傳正確預設值
- OcrEngine.configure() 接受 H3 新參數
- OcrEngine.get_secondary_info() 結構正確
- _should_use_secondary() 遵守 secondary_for_handwriting / secondary_for_low_confidence / threshold
- SettingsDialog OCR tab 包含第二引擎區塊 widget
- SettingsDialog._save() 寫入新 config key 並更新 engine（mock engine）
- SettingsDialog._update_sec_diag() 顯示安裝狀態

測試縫說明：
- eng._ready / eng._engine 直接設定（無 public API 可替代）
- SettingsDialog 依賴 qapp fixture（pytest-qt）
"""
import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# A. DEFAULT_SETTINGS 包含 5 個新 H3 key
# ---------------------------------------------------------------------------

class TestConfigDefaults:
    def test_enable_secondary_engine_default_false(self):
        from src.core.config import DEFAULT_SETTINGS
        assert DEFAULT_SETTINGS['ocr']['enable_secondary_engine'] is False

    def test_secondary_engine_provider_default(self):
        from src.core.config import DEFAULT_SETTINGS
        assert DEFAULT_SETTINGS['ocr']['secondary_engine_provider'] == 'paddleocr_v5_mobile'

    def test_secondary_engine_for_handwriting_default_true(self):
        from src.core.config import DEFAULT_SETTINGS
        assert DEFAULT_SETTINGS['ocr']['secondary_engine_for_handwriting'] is True

    def test_secondary_engine_for_low_confidence_default_true(self):
        from src.core.config import DEFAULT_SETTINGS
        assert DEFAULT_SETTINGS['ocr']['secondary_engine_for_low_confidence'] is True

    def test_secondary_engine_confidence_threshold_default(self):
        from src.core.config import DEFAULT_SETTINGS
        assert DEFAULT_SETTINGS['ocr']['secondary_engine_confidence_threshold'] == pytest.approx(0.85)

    def test_config_manager_get_returns_defaults(self, tmp_path):
        """ConfigManager 在全新目錄下能正確讀取 5 個新 key 的預設值。"""
        import os
        from unittest.mock import patch as _patch
        with _patch('src.core.config.get_project_root', return_value=str(tmp_path)):
            from src.core.config import ConfigManager
            cfg = ConfigManager()
        assert cfg.get('ocr', 'enable_secondary_engine') is False
        assert cfg.get('ocr', 'secondary_engine_provider') == 'paddleocr_v5_mobile'
        assert cfg.get('ocr', 'secondary_engine_for_handwriting') is True
        assert cfg.get('ocr', 'secondary_engine_for_low_confidence') is True
        assert cfg.get('ocr', 'secondary_engine_confidence_threshold') == pytest.approx(0.85)


# ---------------------------------------------------------------------------
# B. OcrEngine H3 configure + get_secondary_info
# ---------------------------------------------------------------------------

class TestOcrEngineH3:
    def _make_engine(self):
        from src.ocr.engine import OcrEngine
        eng = OcrEngine()
        eng._ready = True        # test seam
        eng._engine = MagicMock(return_value=([], None))
        return eng

    def test_configure_accepts_secondary_for_handwriting(self):
        eng = self._make_engine()
        eng.configure(secondary_for_handwriting=False)  # no exception
        assert eng._secondary_for_handwriting is False

    def test_configure_accepts_secondary_for_low_confidence(self):
        eng = self._make_engine()
        eng.configure(secondary_for_low_confidence=False)
        assert eng._secondary_for_low_confidence is False

    def test_configure_accepts_secondary_confidence_threshold(self):
        eng = self._make_engine()
        eng.configure(secondary_confidence_threshold=0.70)
        assert eng._secondary_confidence_threshold == pytest.approx(0.70)

    def test_get_secondary_info_structure(self):
        eng = self._make_engine()
        info = eng.get_secondary_info()
        assert 'name' in info
        assert 'available' in info
        assert 'enabled' in info

    def test_get_secondary_info_defaults(self):
        eng = self._make_engine()
        info = eng.get_secondary_info()
        assert info['name'] == 'none'        # NullSecondaryEngine
        assert info['available'] is False
        assert info['enabled'] is False

    def test_get_secondary_info_after_set(self):
        from src.ocr.providers.paddleocr_v5_provider import PaddleOCRv5Provider
        eng = self._make_engine()
        provider = PaddleOCRv5Provider()
        eng.set_secondary_engine(provider)
        eng.configure(enable_secondary_engine=True)
        info = eng.get_secondary_info()
        assert info['name'] == 'paddleocr_v5_mobile'
        assert info['enabled'] is True


# ---------------------------------------------------------------------------
# C. _should_use_secondary() 遵守 H3 新旗標
# ---------------------------------------------------------------------------

class TestShouldUseSecondaryH3:
    def _make_ready_engine(self, **conf_kwargs):
        """建立帶可用 fake provider + enable_secondary_engine=True 的 engine。"""
        import numpy as np
        from src.ocr.engine import OcrEngine
        from src.ocr.secondary_engine import SecondaryEngineBase

        class AlwaysAvailable(SecondaryEngineBase):
            @property
            def name(self): return 'test'
            def is_available(self): return True
            def recognize(self, img, mode='screen'):
                return {'text': '', 'confidence': 0.0, 'status': 'done',
                        'detail': [], 'elapsed_ms': 0}

        eng = OcrEngine()
        eng._ready = True
        eng._engine = MagicMock(return_value=([], None))
        eng.set_secondary_engine(AlwaysAvailable())
        eng.configure(enable_secondary_engine=True, **conf_kwargs)
        return eng

    def _weak_result(self, conf=0.5, status='needs_review'):
        return {'text': '低信心', 'confidence': conf, 'status': status,
                'detail': [], 'elapsed_ms': 10}

    def _good_result(self):
        return {'text': '高信心', 'confidence': 0.95, 'status': 'done',
                'detail': [], 'elapsed_ms': 10}

    def test_secondary_for_handwriting_false_suppresses_handwriting_trigger(self):
        """secondary_for_handwriting=False 時，手寫路徑不觸發。"""
        eng = self._make_ready_engine(secondary_for_handwriting=False)
        eng.configure(enable_handwriting_mode=True)
        assert eng._should_use_secondary(self._good_result(), 'screen') is False

    def test_secondary_for_handwriting_true_allows_handwriting_trigger(self):
        """secondary_for_handwriting=True（預設）時，手寫路徑仍觸發。"""
        eng = self._make_ready_engine(secondary_for_handwriting=True)
        assert eng._should_use_secondary(self._good_result(), 'handwriting') is True

    def test_secondary_for_low_confidence_false_suppresses_low_conf_trigger(self):
        """secondary_for_low_confidence=False 時，低信心路徑不觸發。"""
        eng = self._make_ready_engine(secondary_for_low_confidence=False)
        assert eng._should_use_secondary(self._weak_result(), 'screen') is False

    def test_secondary_for_low_confidence_true_allows_low_conf_trigger(self):
        """secondary_for_low_confidence=True 時，低信心路徑觸發。"""
        eng = self._make_ready_engine(secondary_for_low_confidence=True)
        assert eng._should_use_secondary(self._weak_result(), 'screen') is True

    def test_confidence_threshold_controls_trigger_point(self):
        """conf=0.75：threshold=0.80 → 觸發；threshold=0.70 → 不觸發。"""
        result = {'text': '文字', 'confidence': 0.75, 'status': 'needs_review',
                  'detail': [], 'elapsed_ms': 10}
        eng_low = self._make_ready_engine(secondary_confidence_threshold=0.80)
        assert eng_low._should_use_secondary(result, 'screen') is True

        eng_high = self._make_ready_engine(secondary_confidence_threshold=0.70)
        assert eng_high._should_use_secondary(result, 'screen') is False

    def test_both_flags_false_never_triggers(self):
        """兩個 for_* 旗標都 False 時，任何情況都不觸發。"""
        eng = self._make_ready_engine(
            secondary_for_handwriting=False,
            secondary_for_low_confidence=False,
        )
        eng.configure(enable_handwriting_mode=True)
        assert eng._should_use_secondary(self._weak_result(), 'handwriting') is False


# ---------------------------------------------------------------------------
# D. SettingsDialog OCR tab 包含第二引擎 widget
# ---------------------------------------------------------------------------

class TestSettingsDialogH3:
    def _make_dialog(self, qapp, tmp_path):
        from src.ui.settings_dialog import SettingsDialog
        cfg = MagicMock()
        cfg.get.side_effect = lambda *args, **kwargs: kwargs.get('default')
        return SettingsDialog(cfg, parent=None, ocr_engine=None)

    def test_dialog_has_sec_enabled_checkbox(self, qapp, tmp_path):
        dlg = self._make_dialog(qapp, tmp_path)
        assert hasattr(dlg, '_sec_enabled')
        dlg.close()

    def test_dialog_has_sec_provider_combobox(self, qapp, tmp_path):
        dlg = self._make_dialog(qapp, tmp_path)
        assert hasattr(dlg, '_sec_provider')
        assert dlg._sec_provider.count() >= 1
        dlg.close()

    def test_dialog_has_sec_diag_label(self, qapp, tmp_path):
        dlg = self._make_dialog(qapp, tmp_path)
        assert hasattr(dlg, '_sec_diag_label')
        assert dlg._sec_diag_label.text() != ''  # 已填入狀態文字
        dlg.close()

    def test_dialog_has_sec_handwriting_checkbox(self, qapp, tmp_path):
        dlg = self._make_dialog(qapp, tmp_path)
        assert hasattr(dlg, '_sec_handwriting')
        dlg.close()

    def test_dialog_has_sec_low_conf_checkbox(self, qapp, tmp_path):
        dlg = self._make_dialog(qapp, tmp_path)
        assert hasattr(dlg, '_sec_low_conf')
        dlg.close()

    def test_dialog_has_sec_threshold_spinbox(self, qapp, tmp_path):
        from PySide6.QtWidgets import QDoubleSpinBox
        dlg = self._make_dialog(qapp, tmp_path)
        assert hasattr(dlg, '_sec_threshold')
        assert isinstance(dlg._sec_threshold, QDoubleSpinBox)
        dlg.close()

    def test_save_writes_all_five_config_keys(self, qapp, tmp_path):
        """_save() 應寫入全部 5 個 H3 config key。"""
        from src.ui.settings_dialog import SettingsDialog

        cfg = MagicMock()
        cfg.get.side_effect = lambda *args, **kwargs: kwargs.get('default')
        mock_engine = MagicMock()

        dlg = SettingsDialog(cfg, parent=None, ocr_engine=mock_engine)
        dlg._sec_enabled.setChecked(False)
        dlg._save()

        written_keys = {call.args[1] for call in cfg.set.call_args_list}
        assert 'enable_secondary_engine' in written_keys
        assert 'secondary_engine_provider' in written_keys
        assert 'secondary_engine_for_handwriting' in written_keys
        assert 'secondary_engine_for_low_confidence' in written_keys
        assert 'secondary_engine_confidence_threshold' in written_keys
        dlg.close()

    def test_save_calls_engine_configure_with_h3_keys(self, qapp, tmp_path):
        """_save() 應呼叫 ocr_engine.configure() 並包含 H3 新參數。"""
        from src.ui.settings_dialog import SettingsDialog

        cfg = MagicMock()
        cfg.get.side_effect = lambda *args, **kwargs: kwargs.get('default')
        mock_engine = MagicMock()

        dlg = SettingsDialog(cfg, parent=None, ocr_engine=mock_engine)
        dlg._sec_enabled.setChecked(False)
        dlg._save()

        # configure() 應被呼叫
        assert mock_engine.configure.called
        # 合併所有 configure() 的 kwargs
        all_kwargs = {}
        for call in mock_engine.configure.call_args_list:
            all_kwargs.update(call.kwargs)
        assert 'enable_secondary_engine' in all_kwargs
        assert 'secondary_for_handwriting' in all_kwargs
        assert 'secondary_for_low_confidence' in all_kwargs
        assert 'secondary_confidence_threshold' in all_kwargs
        dlg.close()
