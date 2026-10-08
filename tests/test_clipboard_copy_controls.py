"""Clipboard output contracts, using synthetic text and real Qt/SQLite.

Offscreen key/clipboard tests are not a Windows external-app or SendInput gate.
"""
import copy
import itertools
import json
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt

from src.clipboard.text_cleanup import COPY_CLEANUP_OPTIONS, prepare_copy_text
from src.clipboard.writer import copy_text, copy_text_batch, get_last_write_id
from src.core.config import DEFAULT_SETTINGS
from src.core.constants import CUSTOM_MIME_TYPE
from src.data.models import OcrResultDTO
from src.workers.pipeline import Pipeline


class Config:
    def __init__(self, **options):
        self.data = copy.deepcopy(DEFAULT_SETTINGS)
        self.data['clipboard'].update(options)

    def get(self, *keys, default=None):
        data = self.data
        for key in keys:
            if not isinstance(data, dict) or key not in data:
                return default
            data = data[key]
        return data

    def set(self, *keys_and_value):
        *keys, value = keys_and_value
        data = self.data
        for key in keys[:-1]:
            data = data.setdefault(key, {})
        data[keys[-1]] = value


@pytest.mark.parametrize('flags', list(itertools.product((False, True), repeat=3)))
def test_independent_opt_in_options_preserve_every_other_character(flags):
    text = "-1,234.50 +5，678.90 '12‘34’56＇\r\n78\u0085\u20289\u2029 % / : () e+3\t 空格"
    keys = [key for key, _, _ in COPY_CLEANUP_OPTIONS]
    cfg = Config(**dict(zip(keys, flags)))
    before = copy.deepcopy(cfg.data)
    removed = ''.join(chars for enabled, (_, _, chars) in zip(flags, COPY_CLEANUP_OPTIONS) if enabled)
    expected = ''.join(char for char in text if char not in removed)
    assert prepare_copy_text(text, cfg) == expected
    assert cfg.data == before
    assert prepare_copy_text(text, None) == text


def test_old_settings_merge_cleanup_defaults_off(tmp_path, monkeypatch):
    from src.core.config import ConfigManager
    monkeypatch.setenv('DESKTOP_OCR_HOME', str(tmp_path))
    (tmp_path / 'config').mkdir()
    original = {'clipboard': {'monitor_clipboard': True, 'auto_save_text': False},
                'hotkeys': {'paste_last': 'Alt+V'}}
    (tmp_path / 'config' / 'settings.json').write_text(json.dumps(original), encoding='utf-8')
    cfg = ConfigManager()
    for key, _, _ in COPY_CLEANUP_OPTIONS:
        assert cfg.get('clipboard', key) is False
    assert cfg.get('clipboard', 'monitor_clipboard') is True
    assert cfg.get('clipboard', 'auto_save_text') is False
    assert cfg.get('hotkeys', 'paste_last') == 'Alt+V'
    text = "-1,234.5'\n678"
    assert prepare_copy_text(text, cfg) == text


def test_copy_empty_cleaned_output_keeps_previous_clipboard_and_id(qapp):
    cfg = Config(copy_remove_commas=True, copy_remove_apostrophes=True, copy_remove_newlines=True)
    assert copy_text('previous synthetic clipboard')
    write_id = get_last_write_id()
    assert copy_text(",'‘’＇，\r\n", cfg) == ''
    assert copy_text(' \n\t ', cfg) == ''
    assert qapp.clipboard().text() == 'previous synthetic clipboard'
    assert get_last_write_id() == write_id


def test_copy_preserves_plain_text_and_self_ignore_mime(qapp):
    cfg = Config(copy_remove_commas=True, copy_remove_apostrophes=True)
    write_id = copy_text("−1,234.50' +7，890.00%", cfg)
    assert qapp.clipboard().text() == '−1234.50 +7890.00%'
    assert bytes(qapp.clipboard().mimeData().data(CUSTOM_MIME_TYPE)).decode() == write_id


def test_batch_cleanup_never_joins_distinct_records(qapp):
    cfg = Config(copy_remove_commas=True, copy_remove_apostrophes=True, copy_remove_newlines=True)
    assert copy_text_batch(["1,234\n567", ",'\r\n", "-8,901.23'"], cfg)
    assert qapp.clipboard().text() == '1234567\n\n-8901.23'


def persist(item_repo, item_id, text, status):
    job, revision = item_repo.begin_ocr_attempt(item_id)
    dto = OcrResultDTO(text=text, status=status, confidence=.63, job_id=job,
                       base_edit_revision=revision,
                       provenance_json=json.dumps({'hypotheses': {'first_pass': [{'raw_text': text}]}},
                                                  ensure_ascii=False))
    assert item_repo.update_ocr_result(item_id, dto)
    return dto


def pipeline(item_repo, cfg=None, closing=False):
    statuses = []
    return SimpleNamespace(repo=item_repo, cfg=cfg, closing=closing,
                           widget=SimpleNamespace(set_ocr_status=statuses.append,
                                                  set_ocr_progress=lambda _: None)), statuses


@pytest.mark.parametrize('status', ['done', 'needs_review', 'confirmed'])
def test_current_candidate_copies_without_changing_transcript_or_review_state(
        qapp, item_repo, make_image_dto, status):
    item_id = item_repo.insert(make_image_dto())
    original = "-1,234.50'\n567"
    dto = persist(item_repo, item_id, original, status)
    before = item_repo.get_by_id(item_id)
    attempts_before = item_repo.get_ocr_attempts(item_id)
    owner, statuses = pipeline(item_repo, Config(copy_remove_commas=True, copy_remove_apostrophes=True))
    qapp.clipboard().setText('previous')
    Pipeline.persisted(owner, item_id, dto)
    assert qapp.clipboard().text() == '-1234.50\n567'
    assert item_repo.get_by_id(item_id) == before
    assert item_repo.get_ocr_attempts(item_id) == attempts_before
    if status == 'needs_review':
        assert '待確認' in statuses[-1] and '已複製候選' in statuses[-1]


@pytest.mark.parametrize('change', ['delete', 'delete_restore', 'edit', 'empty_edit', 'new_job', 'closing'])
def test_review_ack_guards_keep_previous_clipboard(qapp, item_repo, make_image_dto, change):
    item_id = item_repo.insert(make_image_dto())
    dto = persist(item_repo, item_id, 'usable uncertain candidate', 'needs_review')
    if change in ('delete', 'delete_restore'):
        item_repo.soft_delete(item_id)
        if change == 'delete_restore':
            item_repo.restore(item_id)
    elif change in ('edit', 'empty_edit'):
        item_repo.update_edited_text(item_id, 'manual' if change == 'edit' else '')
    elif change == 'new_job':
        item_repo.begin_ocr_attempt(item_id)
    owner, _ = pipeline(item_repo, closing=change == 'closing')
    qapp.clipboard().setText('previous')
    Pipeline.persisted(owner, item_id, dto)
    assert qapp.clipboard().text() == 'previous'


@pytest.mark.parametrize('text,status', [('failed text', 'failed'), ('', 'done'), ('  \n\t', 'needs_review')])
def test_unusable_result_never_overwrites_previous_clipboard(qapp, item_repo, make_image_dto, text, status):
    item_id = item_repo.insert(make_image_dto())
    dto = persist(item_repo, item_id, text, status)
    owner, _ = pipeline(item_repo)
    qapp.clipboard().setText('previous')
    Pipeline.persisted(owner, item_id, dto)
    assert qapp.clipboard().text() == 'previous'


def test_cleaned_empty_review_candidate_does_not_claim_copied(qapp, item_repo, make_image_dto):
    item_id = item_repo.insert(make_image_dto())
    dto = persist(item_repo, item_id, ",'\n", 'needs_review')
    cfg = Config(copy_remove_commas=True, copy_remove_apostrophes=True, copy_remove_newlines=True)
    owner, statuses = pipeline(item_repo, cfg)
    qapp.clipboard().setText('previous')
    Pipeline.persisted(owner, item_id, dto)
    assert qapp.clipboard().text() == 'previous'
    assert '待確認' in statuses[-1] and '已複製' not in statuses[-1]


@pytest.fixture
def floating_widget(qtbot, tmp_path, item_repo, tag_repo, file_mgr, db_worker):
    from src.ui.widget import FloatingWidget
    widget = FloatingWidget(item_repo, file_mgr, db_worker, None, Config(), str(tmp_path), tag_repo=tag_repo)
    qtbot.addWidget(widget)
    return widget


def test_widget_copy_batch_and_paste_last_use_cleanup_without_confirming(
        qapp, floating_widget, item_repo, make_image_dto, monkeypatch):
    widget = floating_widget
    item_id = item_repo.insert(make_image_dto())
    persist(item_repo, item_id, "-1,234.50'\n567", 'needs_review')
    before = item_repo.get_by_id(item_id)
    widget._on_item_copy(item_id)
    assert qapp.clipboard().text() == before.text_content
    widget._cfg = Config(copy_remove_commas=True, copy_remove_apostrophes=True, copy_remove_newlines=True)
    widget._on_item_copy(item_id)
    assert qapp.clipboard().text() == '-1234.50567'
    widget._selected_items = {item_id}
    widget._batch_copy()
    assert qapp.clipboard().text() == '-1234.50567'
    pasted = []
    monkeypatch.setattr('src.clipboard.paste_simulator.simulate_paste', pasted.append)
    widget.paste_last_item()
    assert pasted == ['-1234.50567']
    assert item_repo.get_by_id(item_id) == before


def test_paste_last_cleaned_empty_does_not_simulate_keys(floating_widget, item_repo, make_text_dto, monkeypatch):
    widget = floating_widget
    item_repo.insert(make_text_dto(",'\n"))
    widget._cfg = Config(copy_remove_commas=True, copy_remove_apostrophes=True, copy_remove_newlines=True)
    pasted = []
    monkeypatch.setattr('src.clipboard.paste_simulator.simulate_paste', pasted.append)
    widget.paste_last_item()
    assert pasted == []


def test_main_selected_and_context_copy_use_current_cleanup(
        qtbot, qapp, tmp_path, item_repo, tag_repo, file_mgr, db_worker, make_image_dto):
    from src.ui.main_window import MainWindow
    item_id = item_repo.insert(make_image_dto())
    persist(item_repo, item_id, "-1,234.50'\n567", 'needs_review')
    item = item_repo.get_by_id(item_id)
    cfg = Config(copy_remove_commas=True, copy_remove_apostrophes=True)
    window = MainWindow(item_repo, tag_repo, file_mgr, db_worker, None, cfg, str(tmp_path))
    window._selected_item = item
    window._copy_selected()
    assert qapp.clipboard().text() == '-1234.50\n567'
    cfg.set('clipboard', 'copy_remove_newlines', True)
    window._copy_item_text(item)
    assert qapp.clipboard().text() == '-1234.50567'
    assert item_repo.get_by_id(item_id) == item
    # Closing immediately after copy must cancel status callbacks owned by UI.
    window.deleteLater()
    qtbot.wait(2100)


@pytest.mark.parametrize('status', ['done', 'needs_review', 'confirmed'])
def test_editor_initial_focus_accepts_paste_without_confirming(qtbot, qapp, item_repo, file_mgr, make_image_dto, status):
    from src.ui.editor_window import EditorWindow
    item_id = item_repo.insert(make_image_dto())
    persist(item_repo, item_id, 'original', status)
    before = item_repo.get_by_id(item_id)
    editor = EditorWindow(before, item_repo, file_mgr)
    qtbot.addWidget(editor)
    editor.show()
    qtbot.waitUntil(lambda: editor.focusWidget() is editor._text_edit)
    qapp.clipboard().setText('synthetic paste')
    qtbot.keyClick(editor.focusWidget(), Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
    assert 'synthetic paste' in editor._text_edit.toPlainText()
    assert editor._candidate_preview.isReadOnly()
    assert item_repo.get_by_id(item_id) == before
    if status == 'needs_review':
        assert not editor._review_bar.isHidden()
        assert not editor._btn_confirm.isEnabled()


def test_settings_cleanup_defaults_cancel_and_save(qtbot, monkeypatch):
    from src.ui.settings_dialog import SettingsDialog
    monkeypatch.setattr('src.core.autostart.set_autostart', lambda _: None)
    cfg = Config()
    dialog = SettingsDialog(cfg)
    qtbot.addWidget(dialog)
    assert all(not check.isChecked() for check in dialog._copy_cleanup_checks.values())
    for check in dialog._copy_cleanup_checks.values():
        check.setChecked(True)
    dialog.reject()
    assert all(cfg.get('clipboard', key) is False for key in dialog._copy_cleanup_checks)
    dialog = SettingsDialog(cfg)
    qtbot.addWidget(dialog)
    dialog._copy_cleanup_checks['copy_remove_commas'].setChecked(True)
    dialog._save()
    assert cfg.get('clipboard', 'copy_remove_commas') is True
    assert cfg.get('clipboard', 'copy_remove_apostrophes') is False
    assert cfg.get('clipboard', 'copy_remove_newlines') is False
    assert cfg.get('clipboard', 'monitor_clipboard') is False


def test_settings_cleanup_save_reload_roundtrip(qtbot, tmp_path, monkeypatch):
    from src.core.config import ConfigManager
    from src.ui.settings_dialog import SettingsDialog
    monkeypatch.setenv('DESKTOP_OCR_HOME', str(tmp_path))
    monkeypatch.setattr('src.core.autostart.set_autostart', lambda _: None)
    cfg = ConfigManager()
    cfg.set('hotkeys', 'paste_last', 'Alt+V')
    dialog = SettingsDialog(cfg)
    qtbot.addWidget(dialog)
    for check in dialog._copy_cleanup_checks.values():
        check.setChecked(True)
    dialog._save()
    reloaded = ConfigManager()
    assert all(reloaded.get('clipboard', key) is True for key, _, _ in COPY_CLEANUP_OPTIONS)
    assert reloaded.get('clipboard', 'monitor_clipboard') is False
    assert reloaded.get('hotkeys', 'paste_last') == 'Alt+V'
    assert prepare_copy_text("-1,234.50'\n567", reloaded) == '-1234.50567'
