# -*- coding: utf-8 -*-
"""Legacy optional-engine API tests and the core's explicit retirement contract.

Provider adapters can still be exercised directly; their settings do not imply
that an offline core bundle loads or routes to an optional engine.
"""
import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# A. Unsupported old settings are absent from fresh profiles, preserved on load.
# ---------------------------------------------------------------------------

class TestConfigDefaults:
    def test_fresh_profiles_do_not_advertise_retired_core_settings(self, tmp_path, monkeypatch):
        from src.core.config import ConfigManager, DEFAULT_SETTINGS, RETIRED_CORE_SETTINGS
        monkeypatch.setattr('src.core.config.get_project_root', lambda: str(tmp_path))
        cfg = ConfigManager()
        for dotted, reason in RETIRED_CORE_SETTINGS.items():
            section, key = dotted.split('.')
            assert reason and key not in DEFAULT_SETTINGS.get(section, {})
            assert cfg.get(section, key) is None
        assert cfg.get('history', 'auto_archive_days') == 90
        assert cfg.get('clipboard', 'monitor_clipboard') is False
        assert cfg.get('ocr', 'model_profile') == 'v6-small'

    def test_old_profiles_keep_unsupported_choices_during_roundtrip(self, tmp_path, monkeypatch):
        import json
        from src.core.config import ConfigManager
        monkeypatch.setattr('src.core.config.get_project_root', lambda: str(tmp_path))
        legacy = {'ocr': {'enable_secondary_engine': True, 'secondary_engine_provider': 'cnocr',
                          'model_rec': 'old-user-model.onnx'},
                  'preprocessing': {'enable_shadow_removal': True},
                  'history': {'max_items': 7, 'auto_delete_archived_days': 1}}
        (tmp_path / 'config').mkdir()
        path = tmp_path / 'config/settings.json'
        path.write_text(json.dumps(legacy), encoding='utf-8')
        cfg = ConfigManager()
        cfg.set('general', 'start_minimized', False)
        reloaded = ConfigManager()
        for section, values in legacy.items():
            for key, value in values.items():
                assert reloaded.get(section, key) == value

    def test_bundled_default_json_matches_supported_core_defaults(self):
        import json
        from pathlib import Path
        from src.core.config import DEFAULT_SETTINGS
        path = Path(__file__).resolve().parents[1] / 'config/default_settings.json'
        assert json.loads(path.read_text(encoding='utf-8')) == DEFAULT_SETTINGS

    def test_old_engine_flags_do_not_load_optional_providers_at_core_startup(self, tmp_path):
        import json
        import os
        from pathlib import Path
        import subprocess
        import sys
        root = Path(__file__).resolve().parents[1]
        if not (root / 'models/rec/pp-ocrv6_rec_small.onnx').is_file():
            pytest.skip('Prepared pinned model assets required for core startup')
        legacy = {'general': {'single_instance': False},
                  'ocr': {'engine': 'old-provider', 'enable_secondary_engine': True,
                          'secondary_engine_provider': 'cnocr',
                          'model_det': 'does-not-exist.onnx', 'model_rec': 'does-not-exist.onnx'}}
        (tmp_path / 'config').mkdir()
        (tmp_path / 'config/settings.json').write_text(json.dumps(legacy), encoding='utf-8')
        report = tmp_path / 'core-startup.json'
        script = '''import importlib.abc, sys
class NoOptionalProvider(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in ('paddleocr', 'cnocr'):
            raise AssertionError('Optional provider imported by core startup')
sys.meta_path.insert(0, NoOptionalProvider())
from src.main import run
raise SystemExit(run(['--smoke-app', sys.argv[1]]))
'''
        env = dict(os.environ, DESKTOP_OCR_HOME=str(tmp_path))
        env.setdefault('QT_QPA_PLATFORM', 'offscreen')
        result = subprocess.run([sys.executable, '-c', script, str(report)], cwd=root,
                                env=env, capture_output=True, timeout=45)
        assert result.returncode == 0, 'Core startup failed with a legacy provider profile'
        outcome = json.loads(report.read_text(encoding='utf-8'))
        assert outcome['passed'] and outcome['engine_ready'] and outcome['shutdown_clean']


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
        assert info['name'] == 'paddleocr_v5'
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
# D. Core settings save restart choices without activating optional providers.
# ---------------------------------------------------------------------------

def test_settings_restart_contract(qapp, tmp_path, monkeypatch):
    from src.core.config import ConfigManager
    from src.ui.settings_dialog import SettingsDialog
    monkeypatch.setattr('src.core.config.get_project_root',lambda:str(tmp_path))
    monkeypatch.setattr('src.core.autostart.set_autostart',lambda _:None)
    cfg=ConfigManager(); engine=MagicMock()
    dialog=SettingsDialog(cfg,ocr_engine=engine)
    dialog._cb_monitor.setChecked(True)
    dialog._sp_short_side.setValue(512)
    dialog._save()
    loaded=ConfigManager()
    assert loaded.get('clipboard','monitor_clipboard') is True
    assert loaded.get('ocr','max_image_short_side')==512
    engine.configure.assert_not_called()
    engine.set_secondary_engine.assert_not_called()
    assert not dialog._cb_auto_image.isEnabled()
    assert not dialog._cb_theme.isEnabled()
    dialog.deleteLater()
