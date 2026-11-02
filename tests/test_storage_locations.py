"""The location picker follows actual portable/custom paths without creating files."""
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from src.ui.storage_locations import get_storage_locations


def _by_key(locations):
    return {location.key: location for location in locations}


def test_locations_follow_custom_data_and_home_without_creating_folders(tmp_path, monkeypatch):
    from src.core.config import ConfigManager
    app_home = tmp_path / '可攜 程式'
    monkeypatch.setenv('DESKTOP_OCR_HOME', str(app_home))
    cfg = ConfigManager()
    custom_data = tmp_path / '附件 & 資料'
    cfg.set('general', 'data_directory', str(custom_data))
    before = set(tmp_path.rglob('*'))

    locations = _by_key(get_storage_locations(cfg.get_data_directory(), cfg))

    assert locations['history'].path == custom_data
    for key in ('captures', 'annotations', 'thumbnails', 'exports'):
        assert locations[key].path == custom_data / key
    assert locations['config'].path == app_home / 'config'
    assert locations['logs'].path == app_home / 'logs'
    assert locations['program'].path == app_home
    assert set(tmp_path.rglob('*')) == before
    assert not locations['exports'].available
    assert str(custom_data / 'exports') in locations['exports'].tooltip


def test_locations_keep_active_data_when_setting_is_changed_until_restart(tmp_path, monkeypatch):
    from src.core.config import ConfigManager
    monkeypatch.setenv('DESKTOP_OCR_HOME', str(tmp_path))
    cfg = ConfigManager()
    active_data = Path(cfg.get_data_directory())
    cfg.set('general', 'data_directory', str(tmp_path / 'next-launch-data'))

    locations = _by_key(get_storage_locations(active_data, cfg))

    assert locations['history'].path == active_data
    assert locations['captures'].path == active_data / 'captures'


def test_live_frozen_extraction_is_the_only_program_temp_location(tmp_path, monkeypatch):
    app_home = tmp_path / 'portable'
    extracted = tmp_path / '_MEI current run'
    extracted.mkdir()
    monkeypatch.setenv('DESKTOP_OCR_HOME', str(app_home))
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, '_MEIPASS', str(extracted), raising=False)

    location = _by_key(get_storage_locations(tmp_path / 'data', SimpleNamespace()))['runtime_temp']

    assert location.path == extracted
    assert location.available
    assert str(extracted) in location.tooltip


def test_source_does_not_offer_shared_system_temp_or_stale_meipass(tmp_path, monkeypatch):
    monkeypatch.setenv('DESKTOP_OCR_HOME', str(tmp_path))
    monkeypatch.setattr(sys, 'frozen', False, raising=False)
    monkeypatch.setattr(sys, '_MEIPASS', str(tmp_path / '_MEI old run'), raising=False)

    location = _by_key(get_storage_locations(tmp_path / 'data', SimpleNamespace()))['runtime_temp']

    assert location.path is None
    assert not location.available
    assert '目前無' in location.label


@pytest.mark.skipif(sys.platform == 'win32' or not hasattr(os, 'getuid'), reason='POSIX file lock only')
def test_only_actual_owned_posix_lock_directory_is_offered(tmp_path, monkeypatch):
    from src.core import single_instance
    from src.ui import storage_locations
    monkeypatch.setenv('DESKTOP_OCR_HOME', str(tmp_path / 'home'))
    monkeypatch.setattr(storage_locations.tempfile, 'gettempdir', lambda: str(tmp_path))
    lock_dir = tmp_path / f'desktop-ocr-{os.getuid()}'
    lock_dir.mkdir()
    lock_path = lock_dir / 'instance.lock'
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        monkeypatch.setattr(single_instance, '_posix_lock', descriptor)
        locations = _by_key(get_storage_locations(tmp_path / 'data', SimpleNamespace()))
        assert locations['instance_temp'].path == lock_dir
        # An unrelated held file cannot make a guessed lock path look app-owned.
        with (tmp_path / 'unrelated').open('w') as other:
            monkeypatch.setattr(single_instance, '_posix_lock', other.fileno())
            locations = _by_key(get_storage_locations(tmp_path / 'data', SimpleNamespace()))
            assert 'instance_temp' not in locations
    finally:
        os.close(descriptor)
