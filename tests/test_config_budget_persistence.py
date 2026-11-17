"""A denied resource reservation must not partially activate new preferences."""
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
import json

import pytest

from src.core.config import ConfigManager
from src.core.resource_budget import ResourceBudgetExceeded


@pytest.mark.parametrize('operation', ['set', 'section'])
def test_denied_budget_keeps_live_config_file_and_temp_unchanged(tmp_path, monkeypatch, operation):
    monkeypatch.setenv('DESKTOP_OCR_HOME', str(tmp_path))
    cfg = ConfigManager()
    before = deepcopy(cfg._data)
    path = Path(cfg._settings_path)
    disk = path.read_bytes()

    @contextmanager
    def denied(*args, **kwargs):
        raise ResourceBudgetExceeded('disk')
        yield

    monkeypatch.setattr('src.core.resource_budget.disk_quota_guard', denied)
    with pytest.raises(ResourceBudgetExceeded):
        if operation == 'set':
            cfg.set('invoice_check', 'overlay_transparency', 20)
        else:
            cfg.update_section('clipboard', {'ocr_copy_mode': 'select'})
    assert cfg._data == before and path.read_bytes() == disk
    assert not path.with_suffix('.json.tmp').exists()


def test_successful_overlay_setting_is_persistent_and_preserves_ocr_profile(tmp_path, monkeypatch):
    monkeypatch.setenv('DESKTOP_OCR_HOME', str(tmp_path))
    cfg = ConfigManager()
    profile = cfg.get('ocr', 'model_profile')
    cfg.set('invoice_check', 'overlay_transparency', 65)
    reloaded = ConfigManager()
    assert reloaded.get('invoice_check', 'overlay_transparency') == 65
    assert reloaded.get('ocr', 'model_profile') == profile


def test_valid_legacy_config_is_not_backed_up_or_reset_when_migration_write_denied(tmp_path, monkeypatch):
    monkeypatch.setenv('DESKTOP_OCR_HOME', str(tmp_path))
    cfg = ConfigManager()
    path = Path(cfg._settings_path)
    payload = json.loads(path.read_text())
    payload['ocr']['max_image_short_side'] = 2000
    payload['clipboard']['ocr_copy_mode'] = 'select'
    original = json.dumps(payload).encode()
    path.write_bytes(original)

    @contextmanager
    def denied(*args, **kwargs):
        raise ResourceBudgetExceeded('disk')
        yield

    monkeypatch.setattr('src.core.resource_budget.disk_quota_guard', denied)
    loaded = ConfigManager()
    assert loaded.get('ocr', 'max_image_short_side') == 1920
    assert loaded.get('clipboard', 'ocr_copy_mode') == 'select'
    assert path.read_bytes() == original and not Path(str(path) + '.bak').exists()


def test_denied_first_run_does_not_create_config_directory(tmp_path, monkeypatch):
    monkeypatch.setenv('DESKTOP_OCR_HOME', str(tmp_path))

    @contextmanager
    def denied(*args, **kwargs):
        raise ResourceBudgetExceeded('disk')
        yield

    monkeypatch.setattr('src.core.resource_budget.disk_quota_guard', denied)
    with pytest.raises(ResourceBudgetExceeded):
        ConfigManager()
    assert not (tmp_path / 'config').exists()


def test_deep_missing_home_is_rejected_without_creating_ancestors(tmp_path, monkeypatch):
    home = tmp_path
    for _ in range(100):
        home /= 'a'
    monkeypatch.setenv('DESKTOP_OCR_HOME', str(home))
    with pytest.raises(FileNotFoundError):
        ConfigManager()
    assert not (tmp_path / 'a').exists()


def test_new_home_and_config_directories_are_included_in_actual_volume_reservation(tmp_path, monkeypatch):
    from src.core import resource_budget as budget
    home = tmp_path / 'new-home'
    monkeypatch.setenv('DESKTOP_OCR_HOME', str(home))
    # A tiny true limit leaves less room than the two directory allocation
    # units and the UTF-8 settings payload; nothing may be created on refusal.
    monkeypatch.setattr(budget, 'RESOURCE_LIMIT_BYTES', budget.storage_block_size(tmp_path) * 2)
    with pytest.raises(ResourceBudgetExceeded):
        ConfigManager()
    assert not home.exists()


def test_existing_external_data_counts_before_file_manager_or_logger_start(tmp_path, monkeypatch):
    from src.core import resource_budget as budget
    monkeypatch.setenv('DESKTOP_OCR_HOME', str(tmp_path))
    original = ConfigManager()
    external = tmp_path / 'external-profile'
    external.mkdir()
    old = external / 'keep.db'
    with old.open('wb') as stream:
        stream.write(b'old profile')
        stream.truncate(budget.RESOURCE_LIMIT_BYTES)
    settings = Path(original._settings_path)
    payload = json.loads(settings.read_text())
    payload['general']['data_directory'] = str(external)
    settings.write_text(json.dumps(payload), encoding='utf-8')
    before = settings.read_bytes()
    loaded = ConfigManager()
    assert loaded.get_data_directory() == str(external)
    with pytest.raises(ResourceBudgetExceeded):
        with budget.disk_quota_guard(reserve_bytes=1):
            pytest.fail('No write may precede accounting for the existing profile')
    assert settings.read_bytes() == before
    assert old.stat().st_size == budget.RESOURCE_LIMIT_BYTES
