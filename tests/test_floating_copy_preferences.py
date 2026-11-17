"""Quick copy preferences operate directly without unrelated settings work."""
from copy import deepcopy

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMessageBox

from src.clipboard.text_cleanup import prepare_copy_text
from test_widget_interactions import _make_widget


@pytest.fixture
def widget(qtbot, tmp_path):
    result = _make_widget(tmp_path)
    result._tag_repo = None
    qtbot.addWidget(result)
    result.on_ocr_engine_ready()
    result.show()
    return result


def test_modes_persist_once_and_never_emit_broad_settings_signal(widget, monkeypatch):
    cfg = widget._cfg
    writes, broad = [], []
    original = cfg.set
    monkeypatch.setattr(cfg, 'set', lambda *args: (writes.append(args), original(*args)))
    widget.settings_saved.connect(lambda: broad.append(True))
    widget._copy_mode_buttons['select'].click()
    widget._copy_mode_buttons['select'].click()
    assert cfg.get('clipboard', 'ocr_copy_mode') == 'select'
    assert writes == [('clipboard', 'ocr_copy_mode', 'select')] and not broad
    assert widget._copy_mode_buttons['select'].isChecked()
    cfg.set('clipboard', 'ocr_copy_mode', 'all')
    widget.settings_saved.emit()
    assert widget._copy_mode_buttons['all'].isChecked()


@pytest.mark.parametrize('error,notice', [(OSError, '可寫入'), (ValueError, '格式有誤')])
def test_mode_write_failure_restores_checked_state_and_uses_no_modal(widget, monkeypatch, error, notice):
    def fail(*_):
        raise error('owned synthetic failure')
    monkeypatch.setattr('src.ui.copy_preferences.set_copy_mode', fail)
    widget._copy_mode_buttons['select'].click()
    assert widget._copy_mode_buttons['all'].isChecked()
    assert widget._cfg.get('clipboard', 'ocr_copy_mode', default='all') == 'all'
    assert widget._copy_settings_notice.isVisible()
    assert notice in widget._copy_settings_notice.text()
    assert not widget.findChildren(QMessageBox)
    widget.hide()
    assert not widget._copy_settings_notice.isVisible()


def test_cleanup_apply_changes_copy_output_without_overwriting_mode_or_other_settings(widget):
    cfg = widget._cfg
    original_ui = {key: deepcopy(value) for key, value in cfg._values.items() if key[0] != 'clipboard'}
    broad = []
    widget.settings_saved.connect(lambda: broad.append(True))
    widget._btn_copy_cleanup.click()
    popup = widget._copy_cleanup_popup
    assert popup is not None
    for key, literal in [('copy_remove_commas', ' / '), ('copy_remove_newlines', ' · ')]:
        popup._copy_cleanup_checks[key].setChecked(True)
        popup._copy_cleanup_modes[key].setCurrentIndex(1)
        popup._copy_cleanup_replacements[key].setText(literal)
    widget._copy_mode_buttons['select'].click()
    popup._apply_button.click()
    assert widget._copy_cleanup_popup is None
    assert cfg.get('clipboard', 'ocr_copy_mode') == 'select'
    assert prepare_copy_text('A,B\r\nC', cfg) == 'A / B · C'
    assert '2 項啟用' in widget._btn_copy_cleanup.text() and not broad
    assert {key: value for key, value in cfg._values.items() if key[0] != 'clipboard'} == original_ui


@pytest.mark.parametrize('retire', ['cancel', 'hide', 'shutdown'])
def test_retired_cleanup_draft_cannot_commit_late_apply(widget, retire):
    original = deepcopy(widget._cfg._values)
    widget._btn_copy_cleanup.click()
    popup = widget._copy_cleanup_popup
    popup._copy_cleanup_checks['copy_remove_commas'].setChecked(True)
    popup._copy_cleanup_modes['copy_remove_commas'].setCurrentIndex(1)
    popup._copy_cleanup_replacements['copy_remove_commas'].setText('temporary')
    if retire == 'cancel':
        popup.reject()
    elif retire == 'hide':
        widget.hide()
    else:
        widget.begin_shutdown()
    popup._save()
    assert widget._copy_cleanup_popup is None and widget._cfg._values == original


def test_hidden_or_shutdown_mode_actions_do_not_write(widget):
    original = deepcopy(widget._cfg._values)
    widget.hide()
    widget._copy_mode_buttons['select'].click()
    assert widget._cfg._values == original
    widget.show()
    assert widget._copy_mode_buttons['all'].isChecked()
    widget.begin_shutdown()
    widget._set_quick_copy_mode('select')
    assert widget._cfg._values == original


def test_real_settings_reentry_has_one_modal_and_blocks_quick_edits(widget):
    observed = []
    QTimer.singleShot(0, widget.open_settings)

    def observe_and_close():
        widget._copy_mode_buttons['select'].setChecked(True)
        widget._set_quick_copy_mode('select')
        widget._open_copy_cleanup()
        observed.append((len(widget._settings_dialogs), widget._copy_cleanup_popup,
                         widget._cfg.get('clipboard', 'ocr_copy_mode', default='all'),
                         widget._copy_mode_buttons['all'].isChecked()))
        for dialog in tuple(widget._settings_dialogs):
            dialog.reject()

    QTimer.singleShot(40, observe_and_close)
    widget.open_settings()
    assert observed == [(1, None, 'all', True)]


def test_open_full_settings_discards_quick_draft_and_syncs_accepted_mode(widget):
    widget._btn_copy_cleanup.click()
    popup = widget._copy_cleanup_popup
    popup._copy_cleanup_checks['copy_remove_commas'].setChecked(True)

    def save_mode():
        dialog = next(iter(widget._settings_dialogs))
        dialog._ocr_copy_mode.setCurrentIndex(1)
        dialog._save()

    QTimer.singleShot(0, save_mode)
    widget.open_settings()
    assert popup._closed and widget._copy_cleanup_popup is None
    assert not widget._cfg.get('clipboard', 'copy_remove_commas', default=False)
    assert widget._copy_mode_buttons['select'].isChecked()


@pytest.mark.parametrize('size', [(348, 560), (320, 480)])
def test_main_controls_and_recent_content_fit_small_window(widget, qtbot, size):
    widget.resize(*size)
    qtbot.waitUntil(lambda: widget._scroll.height() > 80)
    assert (widget.width(), widget.height()) == size
    for control in [widget._btn_copy_cleanup, *widget._copy_mode_buttons.values(),
                    widget._btn_ocr, widget._btn_screenshot, widget._btn_handwriting,
                    widget._btn_documents, widget._btn_console, widget._btn_data_dir, widget._btn_pause]:
        assert control.isVisible() and control.width() >= control.fontMetrics().horizontalAdvance(control.text())
    widget._btn_copy_cleanup.click()
    popup = widget._copy_cleanup_popup
    popup._copy_cleanup_checks['copy_remove_newlines'].setChecked(True)
    popup._copy_cleanup_modes['copy_remove_newlines'].setCurrentIndex(1)
    qtbot.waitUntil(lambda: popup.frameGeometry().width() >= 300)
    assert widget.screen().availableGeometry().contains(popup.frameGeometry())
    popup.reject()
