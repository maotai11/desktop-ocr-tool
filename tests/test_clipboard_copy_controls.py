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
from src.clipboard.writer import (
    copy_text,
    copy_text_batch,
    get_clipboard_epoch,
    get_last_write_id,
)
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
    assert cfg.get('clipboard', 'ocr_copy_mode') == 'all'
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
    statuses, selections = [], []
    owner = SimpleNamespace(repo=item_repo, cfg=cfg, closing=closing, selections=selections,
                            selection_requested=SimpleNamespace(emit=lambda *args: selections.append(args)),
                            widget=SimpleNamespace(set_ocr_status=statuses.append,
                                                   set_ocr_progress=lambda _: None))
    owner._selection_is_current = lambda item_id, dto: Pipeline._selection_is_current(owner, item_id, dto)
    return owner, statuses


@pytest.mark.parametrize('status', ['done', 'needs_review', 'confirmed'])
def test_current_candidate_waits_for_selection_and_copies_without_changing_saved_state(
        qapp, item_repo, make_image_dto, status):
    item_id = item_repo.insert(make_image_dto())
    original = "-1,234.50'\n567"
    dto = persist(item_repo, item_id, original, status)
    before = item_repo.get_by_id(item_id)
    attempts_before = item_repo.get_ocr_attempts(item_id)
    owner, statuses = pipeline(item_repo, Config(ocr_copy_mode='select', copy_remove_commas=True, copy_remove_apostrophes=True))
    qapp.clipboard().setText('previous')
    Pipeline.persisted(owner, item_id, dto)
    assert qapp.clipboard().text() == 'previous'
    assert owner.selections == [(item_id, dto)]
    if status == 'needs_review':
        assert '待確認' in statuses[-1] and '已複製' not in statuses[-1]
    Pipeline.copy_ocr_selection(owner, item_id, dto, original)
    assert qapp.clipboard().text() == '-1234.50\n567'
    assert item_repo.get_by_id(item_id) == before
    assert item_repo.get_ocr_attempts(item_id) == attempts_before
    assert '已複製所選文字' in statuses[-1]


@pytest.mark.parametrize('mode', ['all', 'select'])
@pytest.mark.parametrize('change', ['delete', 'delete_restore', 'edit', 'empty_edit', 'new_job', 'closing'])
def test_review_ack_guards_keep_previous_clipboard(qapp, item_repo, make_image_dto, change, mode):
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
    owner, _ = pipeline(item_repo, Config(ocr_copy_mode=mode), closing=change == 'closing')
    qapp.clipboard().setText('previous')
    Pipeline.persisted(owner, item_id, dto)
    assert qapp.clipboard().text() == 'previous'
    assert owner.selections == []


@pytest.mark.parametrize('mode', ['all', 'select'])
@pytest.mark.parametrize('text,status', [('failed text', 'failed'), ('', 'done'), ('  \n\t', 'needs_review')])
def test_unusable_result_never_overwrites_previous_clipboard(qapp, item_repo, make_image_dto, text, status, mode):
    item_id = item_repo.insert(make_image_dto())
    dto = persist(item_repo, item_id, text, status)
    owner, _ = pipeline(item_repo, Config(ocr_copy_mode=mode))
    qapp.clipboard().setText('previous')
    Pipeline.persisted(owner, item_id, dto)
    assert qapp.clipboard().text() == 'previous'
    assert owner.selections == []


def test_cleaned_empty_review_candidate_does_not_claim_copied(qapp, item_repo, make_image_dto):
    item_id = item_repo.insert(make_image_dto())
    dto = persist(item_repo, item_id, ",'\n", 'needs_review')
    cfg = Config(ocr_copy_mode='select', copy_remove_commas=True, copy_remove_apostrophes=True, copy_remove_newlines=True)
    owner, statuses = pipeline(item_repo, cfg)
    qapp.clipboard().setText('previous')
    Pipeline.persisted(owner, item_id, dto)
    assert qapp.clipboard().text() == 'previous'
    assert owner.selections == [(item_id, dto)]
    assert '待確認' in statuses[-1] and '已複製' not in statuses[-1]
    Pipeline.copy_ocr_selection(owner, item_id, dto, dto.text)
    assert qapp.clipboard().text() == 'previous'
    assert statuses[-1] == '沒有可複製的文字'


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
    assert dialog._ocr_copy_mode.currentData() == 'all'
    dialog._ocr_copy_mode.setCurrentIndex(dialog._ocr_copy_mode.findData('select'))
    assert all(not check.isChecked() for check in dialog._copy_cleanup_checks.values())
    for check in dialog._copy_cleanup_checks.values():
        check.setChecked(True)
    dialog.reject()
    assert cfg.get('clipboard', 'ocr_copy_mode') == 'all'
    assert all(cfg.get('clipboard', key) is False for key in dialog._copy_cleanup_checks)
    dialog = SettingsDialog(cfg)
    qtbot.addWidget(dialog)
    dialog._ocr_copy_mode.setCurrentIndex(dialog._ocr_copy_mode.findData('select'))
    dialog._copy_cleanup_checks['copy_remove_commas'].setChecked(True)
    dialog._save()
    assert cfg.get('clipboard', 'ocr_copy_mode') == 'select'
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
    dialog._ocr_copy_mode.setCurrentIndex(dialog._ocr_copy_mode.findData('select'))
    for check in dialog._copy_cleanup_checks.values():
        check.setChecked(True)
    dialog._save()
    reloaded = ConfigManager()
    assert reloaded.get('clipboard', 'ocr_copy_mode') == 'select'
    reopened = SettingsDialog(reloaded)
    qtbot.addWidget(reopened)
    assert reopened._ocr_copy_mode.currentData() == 'select'
    assert all(reloaded.get('clipboard', key) is True for key, _, _ in COPY_CLEANUP_OPTIONS)
    assert reloaded.get('clipboard', 'monitor_clipboard') is False
    assert reloaded.get('hotkeys', 'paste_last') == 'Alt+V'
    assert prepare_copy_text("-1,234.50'\n567", reloaded) == '123450567'


@pytest.mark.parametrize('source,expected', [
    ('「中文」，『全形』。！？；：（測試）【123】', '中文全形測試123'),
    ('_A—B(C)[D]{E}“F”‘G’…؟؛', 'ABCDEFG'),
    ('甲。𠮷！ＡＢＣ１２３，e\u0301 🙂 +−×÷=￥$€\t\n乙',
     '甲𠮷ＡＢＣ１２３e\u0301🙂+−×÷=￥$€乙'),
    ('-1,234.50％ / 2:30', '123450230'),
])
def test_all_punctuation_removes_unicode_punctuation_and_preserves_text_symbols(source, expected):
    assert prepare_copy_text(source, Config(copy_remove_punctuation=True)) == expected
    assert prepare_copy_text(source, Config()) == source


def test_all_punctuation_includes_whitespace_and_joins_batch_records(qapp):
    text = '甲，\n乙。\t丙！'
    cfg = Config(copy_remove_punctuation=True)
    assert copy_text(text, cfg)
    assert qapp.clipboard().text() == '甲乙丙'
    cfg.set('clipboard', 'copy_remove_newlines', True)
    assert copy_text_batch([text, '丁（戊）'], cfg)
    assert qapp.clipboard().text() == '甲乙丙丁戊'


@pytest.mark.parametrize('remove_newlines', [False, True])
def test_batch_punctuation_and_whitespace_cleanup_does_not_add_record_separators(qapp, remove_newlines):
    texts = ['甲\u3000，\n乙', '\u00a0丙（丁）\t', ' ！？ ']
    original = list(texts)
    cfg = Config(copy_remove_punctuation=True, copy_remove_newlines=remove_newlines)
    before = copy.deepcopy(cfg.data)
    assert copy_text_batch(texts, cfg)
    assert qapp.clipboard().text() == '甲乙丙丁'
    assert texts == original and cfg.data == before


@pytest.mark.parametrize('space', [' ', '\u3000', '\u00a0', '\u2000', '\u2003', '\u2009',
                                  '\u202f', '\u205f', '\t', '\n', '\r', '\r\n', '\v', '\f',
                                  '\u0085', '\u2028', '\u2029', '\x1c', '\x1d', '\x1e', '\x1f'])
def test_all_punctuation_removes_unicode_whitespace_including_line_separators(space):
    text = f'甲{space}，{space}乙'
    assert prepare_copy_text(text, Config(copy_remove_punctuation=True)) == '甲乙'
    assert prepare_copy_text(text, Config()) == text


def test_all_punctuation_keeps_zero_width_format_and_combining_characters():
    text = 'e\u0301，\u200b甲\u200d乙\ufeff\u2060 👩\u200d💻'
    assert prepare_copy_text(text, Config(copy_remove_punctuation=True)) == 'e\u0301\u200b甲\u200d乙\ufeff\u2060👩\u200d💻'


def test_individual_newline_cleanup_still_preserves_other_whitespace():
    text = '甲 \u3000\t\u00a0，\n乙'
    assert prepare_copy_text(text, Config(copy_remove_newlines=True)) == '甲 \u3000\t\u00a0，乙'


def test_legacy_enabled_punctuation_key_keeps_working_after_reload(tmp_path, monkeypatch):
    from src.core.config import ConfigManager
    monkeypatch.setenv('DESKTOP_OCR_HOME', str(tmp_path))
    (tmp_path / 'config').mkdir()
    (tmp_path / 'config/settings.json').write_text(json.dumps({
        'clipboard': {'copy_remove_punctuation': True, 'copy_remove_newlines': False,
                      'monitor_clipboard': False}}), encoding='utf-8')
    cfg = ConfigManager()
    assert cfg.get('clipboard', 'copy_remove_punctuation') is True
    assert cfg.get('clipboard', 'copy_remove_newlines') is False
    assert prepare_copy_text('甲\u3000，\n乙', cfg) == '甲乙'
    assert cfg.get('clipboard', 'monitor_clipboard') is False


def test_settings_all_cleanup_label_and_tooltip_disclose_spaces_and_newlines(qtbot):
    from src.ui.settings_dialog import SettingsDialog
    dialog = SettingsDialog(Config())
    qtbot.addWidget(dialog)
    checkbox = dialog._copy_cleanup_checks['copy_remove_punctuation']
    assert checkbox.text() == '所有標點與空白'
    assert all(text in checkbox.toolTip() for text in ('全形', 'Tab', '換行', '原始文字不變'))
    assert not checkbox.isChecked()


def test_punctuation_only_output_keeps_clipboard_and_manual_epoch(qapp):
    cfg = Config(copy_remove_punctuation=True)
    copy_text('保留內容')
    previous = get_last_write_id(), get_clipboard_epoch()
    assert not copy_text('！？「」\u3000\u00a0 \t\r\n', cfg)
    assert qapp.clipboard().text() == '保留內容'
    assert (get_last_write_id(), get_clipboard_epoch()) == previous


@pytest.mark.parametrize('mode', ['all', 'select'])
def test_punctuation_cleanup_at_ocr_copy_boundary_preserves_saved_original(
        qapp, item_repo, make_image_dto, mode):
    text = '甲（A）… -1,234.50％\nＢ：乙'
    item_id = item_repo.insert(make_image_dto())
    dto = persist(item_repo, item_id, text, 'needs_review')
    before = item_repo.get_by_id(item_id)
    attempts = item_repo.get_ocr_attempts(item_id)
    cfg = Config(ocr_copy_mode=mode, copy_remove_punctuation=True)
    owner, _ = pipeline(item_repo, cfg)
    copy_text('原剪貼簿')
    Pipeline.persisted(owner, item_id, dto)
    if mode == 'select':
        assert qapp.clipboard().text() == '原剪貼簿'
        Pipeline.copy_ocr_selection(owner, item_id, dto, text)
    assert qapp.clipboard().text() == '甲A123450Ｂ乙'
    assert item_repo.get_by_id(item_id) == before
    assert item_repo.get_ocr_attempts(item_id) == attempts


@pytest.mark.parametrize('status', ['done', 'needs_review', 'confirmed'])
@pytest.mark.parametrize('setting', ['default', 'missing', 'none', 'all'])
def test_default_full_copy_preserves_epoch_and_saved_review_state(
        qapp, item_repo, make_image_dto, status, setting):
    cfg = Config()
    if setting == 'missing':
        cfg.data['clipboard'].pop('ocr_copy_mode', None)
    elif setting == 'none':
        cfg = None
    elif setting == 'all':
        cfg.set('clipboard', 'ocr_copy_mode', 'all')
    item_id = item_repo.insert(make_image_dto())
    original = '完整𠮷字\n第二行，1,234.50'
    dto = persist(item_repo, item_id, original, status)
    before = item_repo.get_by_id(item_id)
    attempts = item_repo.get_ocr_attempts(item_id)
    owner, statuses = pipeline(item_repo, cfg)
    copy_text('先前手動複製')
    epoch = get_clipboard_epoch()
    Pipeline.persisted(owner, item_id, dto)
    assert qapp.clipboard().text() == original
    assert get_clipboard_epoch() == epoch
    assert owner.selections == []
    assert '已複製全部文字' in statuses[-1]
    if status == 'needs_review':
        assert '待確認' in statuses[-1]
    assert item_repo.get_by_id(item_id) == before
    assert item_repo.get_ocr_attempts(item_id) == attempts


def test_automatic_full_copy_keeps_existing_cleanup_options(qapp, item_repo, make_image_dto):
    cfg = Config(copy_remove_commas=True, copy_remove_apostrophes=True, copy_remove_newlines=True)
    item_id = item_repo.insert(make_image_dto())
    dto = persist(item_repo, item_id, "-1,234.50'\n567", 'needs_review')
    owner, _ = pipeline(item_repo, cfg)
    Pipeline.persisted(owner, item_id, dto)
    assert qapp.clipboard().text() == '-1234.50567'
    assert item_repo.get_by_id(item_id).text_content == "-1,234.50'\n567"
    assert item_repo.get_by_id(item_id).ocr_status == 'needs_review'


def test_cleaned_empty_automatic_result_does_not_claim_copied(qapp, item_repo, make_image_dto):
    cfg = Config(copy_remove_commas=True, copy_remove_apostrophes=True, copy_remove_newlines=True)
    item_id = item_repo.insert(make_image_dto())
    dto = persist(item_repo, item_id, ",'\n", 'needs_review')
    owner, statuses = pipeline(item_repo, cfg)
    copy_text('保留原內容')
    Pipeline.persisted(owner, item_id, dto)
    assert qapp.clipboard().text() == '保留原內容'
    assert owner.selections == []
    assert '已複製' not in statuses[-1]


def test_missing_mode_in_legacy_settings_dialog_defaults_to_all(qtbot):
    from src.ui.settings_dialog import SettingsDialog
    cfg = Config()
    cfg.data['clipboard'].pop('ocr_copy_mode', None)
    dialog = SettingsDialog(cfg)
    qtbot.addWidget(dialog)
    assert dialog._ocr_copy_mode.currentData() == 'all'
    dialog.reject()
    assert 'ocr_copy_mode' not in cfg.data['clipboard']


def test_saved_copy_mode_applies_to_next_result_without_restart(
        qtbot, qapp, monkeypatch, item_repo, make_image_dto):
    from src.ui.settings_dialog import SettingsDialog
    monkeypatch.setattr('src.core.autostart.set_autostart', lambda _: None)
    cfg = Config()
    owner, _ = pipeline(item_repo, cfg)
    item_id = item_repo.insert(make_image_dto())
    first = persist(item_repo, item_id, '第一次完整結果', 'done')
    Pipeline.persisted(owner, item_id, first)
    assert qapp.clipboard().text() == '第一次完整結果'

    settings = SettingsDialog(cfg)
    qtbot.addWidget(settings)
    settings._ocr_copy_mode.setCurrentIndex(settings._ocr_copy_mode.findData('select'))
    settings._save()
    second = persist(item_repo, item_id, '第二次選字結果', 'done')
    Pipeline.persisted(owner, item_id, second)
    assert owner.selections == [(item_id, second)]
    assert qapp.clipboard().text() == '第一次完整結果'
    Pipeline.copy_ocr_selection(owner, item_id, second, '選字')
    assert qapp.clipboard().text() == '選字'

    settings = SettingsDialog(cfg)
    qtbot.addWidget(settings)
    settings._ocr_copy_mode.setCurrentIndex(settings._ocr_copy_mode.findData('all'))
    settings._save()
    third = persist(item_repo, item_id, '第三次又自動複製全部', 'done')
    Pipeline.persisted(owner, item_id, third)
    assert qapp.clipboard().text() == '第三次又自動複製全部'
    assert owner.selections == [(item_id, second)]


def test_copy_settings_small_window_scrolls_without_truncating_help(qtbot):
    from PySide6.QtWidgets import QLabel, QScrollArea, QTabWidget

    from src.ui.settings_dialog import SettingsDialog
    dialog = SettingsDialog(Config())
    qtbot.addWidget(dialog)
    tabs = dialog.findChild(QTabWidget)
    tabs.setCurrentIndex(next(i for i in range(tabs.count()) if tabs.tabText(i) == '剪貼簿'))
    dialog.resize(520, 420)
    dialog.show()
    scroll = tabs.currentWidget()
    assert isinstance(scroll, QScrollArea)
    qtbot.waitUntil(lambda: scroll.verticalScrollBar().maximum() > 0)
    labels = scroll.widget().findChildren(QLabel)
    assert all(label.height() >= label.heightForWidth(label.width()) for label in labels)
    assert dialog._ocr_copy_mode.currentData() == 'all'
    scroll.ensureWidgetVisible(dialog._copy_cleanup_checks['copy_remove_punctuation'])
    assert scroll.verticalScrollBar().value() > 0
