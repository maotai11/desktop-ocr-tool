"""One cleanup editor drives both surfaces; popup never restarts other tools."""
from copy import deepcopy

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from src.core.config import ConfigManager
from src.ui.copy_preferences import CopyCleanupDialog, read_copy_mode, set_copy_mode, cleanup_summary
from src.ui.settings_dialog import SettingsDialog
from test_settings_cleanup_design import Config


def edit_newline(dialog, literal):
    key = 'copy_remove_newlines'
    dialog._copy_cleanup_checks[key].setChecked(True)
    dialog._copy_cleanup_modes[key].setCurrentIndex(1)
    dialog._copy_cleanup_replacements[key].setText(literal)


def test_full_settings_and_quick_popup_share_literal_and_dormant_draft_contract(qtbot, monkeypatch):
    monkeypatch.setattr('src.core.autostart.set_autostart', lambda *a: None)
    first, second = Config(copy_replace_commas='留存'), Config(copy_replace_commas='留存')
    quick, full = CopyCleanupDialog(first), SettingsDialog(second)
    qtbot.addWidget(quick); qtbot.addWidget(full)
    quick.show()
    edit_newline(quick, '😀' * 64)
    edit_newline(full, '😀' * 64)
    signals = []
    quick.preferences_saved.connect(lambda: signals.append(True))
    quick._save(); full._save()
    assert first.data['clipboard'] == second.data['clipboard']
    assert first.get('clipboard', 'copy_replace_commas') == '留存'
    assert all(section == 'clipboard' for section, _, _ in first.writes)
    assert signals == [True] and quick._closed


@pytest.mark.parametrize('finish', ['reject', 'hide', 'owner_closed'])
def test_hidden_cancelled_or_shutdown_popup_rejects_late_apply(qtbot, finish):
    cfg = Config(); owner = QWidget(); owner._ui_closing = False
    qtbot.addWidget(owner); owner.show()
    dialog = CopyCleanupDialog(cfg, owner); qtbot.addWidget(dialog); dialog.show()
    edit_newline(dialog, '+')
    before = deepcopy(cfg.data)
    if finish == 'owner_closed': owner._ui_closing = True
    else: getattr(dialog, finish)()
    dialog._save()
    assert cfg.data == before and not cfg.writes
    dialog.reject()


def test_quick_save_atomic_disk_failure_does_not_emit_or_change_memory(qtbot, monkeypatch, tmp_path):
    monkeypatch.setenv('DESKTOP_OCR_HOME', str(tmp_path))
    cfg = ConfigManager(); before = deepcopy(cfg._data)
    disk = (tmp_path / 'config' / 'settings.json').read_bytes()
    dialog = CopyCleanupDialog(cfg); qtbot.addWidget(dialog); dialog.show()
    edit_newline(dialog, '+')
    emitted = []
    dialog.preferences_saved.connect(lambda: emitted.append(True))
    monkeypatch.setattr('src.ui.copy_preferences.QMessageBox.warning', lambda *a: None)
    monkeypatch.setattr('src.core.config.os.replace', lambda *a: (_ for _ in ()).throw(OSError('denied')))
    dialog._save()
    assert cfg._data == before and not emitted and not dialog._closed
    assert (tmp_path / 'config' / 'settings.json').read_bytes() == disk
    dialog.reject()


def test_closing_owner_of_real_warning_closes_modal_child_before_unwind(qtbot):
    cfg = Config(); dialog = CopyCleanupDialog(cfg); qtbot.addWidget(dialog); dialog.show()
    edit_newline(dialog, 'x' * 65)
    observed = []
    watchdog = QTimer()
    watchdog.setSingleShot(True)
    def stop_warning():
        boxes = [box for box in dialog.findChildren(QMessageBox) if box.isVisible()]
        if not boxes: return
        warning = boxes[0]
        poll.stop()
        dialog.reject()
        observed.append((dialog._closed, warning.isVisible(), QApplication.activeModalWidget() is warning))
    poll = QTimer(); poll.setInterval(5); poll.timeout.connect(stop_warning); poll.start()
    def unwind_watchdog():
        for warning in dialog.findChildren(QMessageBox):
            warning.reject()
        dialog.reject()
    watchdog.timeout.connect(unwind_watchdog); watchdog.start(1000)
    dialog._save()
    poll.stop(); watchdog.stop()
    assert observed == [(True, False, False)]
    assert not cfg.writes


def test_mode_helpers_change_only_copy_mode_and_invalid_reads_do_not_write():
    cfg = Config(ocr_copy_mode='invalid')
    assert read_copy_mode(cfg) == 'all' and not cfg.writes
    assert not set_copy_mode(cfg, 'invalid') and not cfg.writes
    assert set_copy_mode(cfg, 'select')
    assert cfg.writes == [('clipboard', 'ocr_copy_mode', 'select')]
    assert cleanup_summary(cfg) == '原樣輸出'
    cfg.data['clipboard']['copy_remove_newlines'] = True
    assert cleanup_summary(cfg) == '1 項啟用'



def test_malformed_loaded_clipboard_section_reports_error_without_reset(qtbot, monkeypatch, tmp_path):
    monkeypatch.setenv('DESKTOP_OCR_HOME', str(tmp_path))
    cfg = ConfigManager()
    cfg._data['clipboard'] = []
    cfg.save()
    before = (tmp_path / 'config' / 'settings.json').read_bytes()
    warnings, emitted = [], []
    monkeypatch.setattr('src.ui.copy_preferences.QMessageBox.warning', lambda *args: warnings.append(args))
    dialog = CopyCleanupDialog(cfg); qtbot.addWidget(dialog); dialog.show()
    dialog.preferences_saved.connect(lambda: emitted.append(True))
    dialog._save()
    assert warnings and '格式' in warnings[0][2]
    assert cfg._data['clipboard'] == [] and not emitted and not dialog._closed
    assert (tmp_path / 'config' / 'settings.json').read_bytes() == before
    dialog.reject()
