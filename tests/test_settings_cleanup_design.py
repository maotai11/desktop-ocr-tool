"""The cleanup hierarchy edits drafts, preserves literals and validates before writes."""
from copy import deepcopy

import pytest
from PySide6.QtWidgets import QGroupBox, QToolButton

from src.core.config import DEFAULT_SETTINGS
from src.ui.settings_dialog import SettingsDialog


class Config:
    def __init__(self, **clipboard):
        self.data = deepcopy(DEFAULT_SETTINGS)
        self.data['clipboard'].update(clipboard)
        self.writes = []

    def get(self, *keys, default=None):
        value = self.data
        for key in keys:
            if not isinstance(value, dict) or key not in value:
                return default
            value = value[key]
        return value

    def set(self, section, key, value):
        self.writes.append((section, key, value))
        self.data[section][key] = value


@pytest.fixture(autouse=True)
def no_registry_write(monkeypatch):
    monkeypatch.setattr('src.core.autostart.set_autostart', lambda enabled: None)


def test_four_short_rules_share_one_parent_with_accessible_annotations(qtbot):
    dialog = SettingsDialog(Config())
    qtbot.addWidget(dialog)
    group = next(group for group in dialog.findChildren(QGroupBox) if group.title() == '複製清理')
    assert len(dialog._copy_cleanup_checks) == 4
    for key, check in dialog._copy_cleanup_checks.items():
        assert group.isAncestorOf(check) and '移除' not in check.text()
        assert check.toolTip() and dialog._copy_cleanup_modes[key].toolTip()
        assert dialog._copy_cleanup_replacements[key].accessibleName()
    assert len(group.findChildren(QToolButton)) == 4


def test_cancel_keeps_all_settings_unchanged(qtbot):
    cfg = Config()
    original = deepcopy(cfg.data)
    dialog = SettingsDialog(cfg)
    qtbot.addWidget(dialog)
    key = 'copy_remove_newlines'
    dialog._copy_cleanup_checks[key].setChecked(True)
    dialog._copy_cleanup_modes[key].setCurrentIndex(1)
    dialog._copy_cleanup_replacements[key].setText(' · ')
    dialog.reject()
    assert cfg.data == original and not cfg.writes


def test_save_and_reopen_preserves_mode_empty_literal_and_dormant_text(qtbot):
    cfg = Config(copy_replace_commas='原來的替代文字')
    dialog = SettingsDialog(cfg)
    qtbot.addWidget(dialog)
    key = 'copy_remove_newlines'
    dialog._copy_cleanup_checks[key].setChecked(True)
    dialog._copy_cleanup_modes[key].setCurrentIndex(1)
    dialog._save()
    assert cfg.get('clipboard', 'copy_cleanup_mode_newlines') == 'replace'
    assert cfg.get('clipboard', 'copy_replace_newlines') == ''
    assert cfg.get('clipboard', 'copy_replace_commas') == '原來的替代文字'
    reopened = SettingsDialog(cfg)
    qtbot.addWidget(reopened)
    assert reopened._copy_cleanup_modes[key].currentData() == 'replace'


def test_sixty_four_astral_characters_round_trip_without_utf16_truncation(qtbot):
    literal = '😀' * 64
    cfg = Config(copy_remove_newlines=True, copy_cleanup_mode_newlines='replace', copy_replace_newlines=literal)
    dialog = SettingsDialog(cfg)
    qtbot.addWidget(dialog)
    assert dialog._copy_cleanup_replacements['copy_remove_newlines'].text() == literal
    dialog._save()
    assert cfg.get('clipboard', 'copy_replace_newlines') == literal


@pytest.mark.parametrize('literal', ['x' * 65, '\n', 123])
def test_invalid_active_literal_blocks_before_any_other_setting_is_written(qtbot, monkeypatch, literal):
    cfg = Config(copy_remove_newlines=True, copy_cleanup_mode_newlines='replace', copy_replace_newlines=literal)
    warnings = []
    monkeypatch.setattr('src.ui.settings_dialog.QMessageBox.warning', lambda *args: warnings.append(args))
    dialog = SettingsDialog(cfg)
    qtbot.addWidget(dialog)
    dialog._sp_archive_days.setValue(45)
    dialog._save()
    assert warnings and not cfg.writes and not dialog._closed
    assert cfg.get('clipboard', 'copy_replace_newlines') == literal


def test_unrelated_save_preserves_invalid_dormant_literal(qtbot):
    cfg = Config(copy_cleanup_mode_newlines='remove', copy_replace_newlines=123)
    dialog = SettingsDialog(cfg)
    qtbot.addWidget(dialog)
    dialog._sp_archive_days.setValue(45)
    dialog._save()
    assert cfg.get('clipboard', 'copy_replace_newlines') == 123
    assert cfg.get('history', 'auto_archive_days') == 45


def test_late_annotation_click_after_close_does_not_reopen_tooltip(qtbot, monkeypatch):
    shown = []
    monkeypatch.setattr('src.ui.settings_dialog.QToolTip.showText', lambda *args: shown.append(args))
    dialog = SettingsDialog(Config())
    qtbot.addWidget(dialog)
    dialog.show()
    dialog._tabs.setCurrentIndex(3)
    button = dialog.findChildren(QToolButton)[-1]
    dialog.reject()
    button.click()
    assert not shown


@pytest.mark.parametrize('mode', [None, 'unknown', 123])
def test_unknown_active_mode_requires_explicit_choice_before_writing(qtbot, monkeypatch, mode):
    cfg = Config(copy_remove_commas=True, copy_cleanup_mode_commas=mode)
    warnings = []
    monkeypatch.setattr('src.ui.settings_dialog.QMessageBox.warning', lambda *args: warnings.append(args))
    dialog = SettingsDialog(cfg)
    qtbot.addWidget(dialog)
    dialog._save()
    assert warnings and not cfg.writes and dialog._tabs.currentIndex() == 3
    assert cfg.get('clipboard', 'copy_cleanup_mode_commas') == mode
