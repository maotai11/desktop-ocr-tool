"""Copy preferences must not become partly active when profile writes fail."""
from copy import deepcopy
from pathlib import Path

import pytest

from src.core.config import ConfigManager


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setenv('DESKTOP_OCR_HOME', str(tmp_path))
    return ConfigManager()


def test_related_copy_preferences_use_one_replace_and_preserve_other_settings(config, monkeypatch):
    import src.core.config as module

    original_replace = module.os.replace
    replacements = []

    def record_replace(source, destination):
        replacements.append((source, destination))
        original_replace(source, destination)

    monkeypatch.setattr(module.os, 'replace', record_replace)
    before = deepcopy(config._data)
    changes = {'copy_remove_newlines': True, 'copy_cleanup_mode_newlines': 'replace',
               'copy_replace_newlines': ' · ', 'ocr_copy_mode': 'select'}
    config.update_section('clipboard', changes)
    assert len(replacements) == 1
    reloaded = ConfigManager()
    for key, value in changes.items():
        assert reloaded.get('clipboard', key) == value
    for section in before.keys() - {'clipboard'}:
        assert reloaded._data[section] == before[section]
    assert not Path(config._settings_path + '.tmp').exists()


@pytest.mark.parametrize('failure', ['replace', 'serialize'])
def test_failed_copy_preference_save_restores_live_and_persisted_state(config, monkeypatch, failure):
    import src.core.config as module

    path = Path(config._settings_path)
    original_bytes = path.read_bytes()
    original_data = deepcopy(config._data)
    changes = {'ocr_copy_mode': 'select', 'copy_remove_newlines': True}
    if failure == 'replace':
        def fail_replace(*_):
            raise PermissionError('synthetic profile is read-only')
        monkeypatch.setattr(module.os, 'replace', fail_replace)
        expected_error = PermissionError
    else:
        changes['copy_replace_newlines'] = object()
        expected_error = TypeError
    with pytest.raises(expected_error):
        config.update_section('clipboard', changes)
    assert config._data == original_data
    assert path.read_bytes() == original_bytes
    assert not Path(config._settings_path + '.tmp').exists()
