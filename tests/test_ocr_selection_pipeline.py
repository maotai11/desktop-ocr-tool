"""Real SQLite and Qt checks for the OCR selection/copy publication boundary."""
import json
from dataclasses import replace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from test_data_integrity_regressions import make_pipeline, reserve

from src.clipboard.writer import copy_text, get_clipboard_epoch


@pytest.fixture
def selection_pipeline(qtbot, tmp_db, item_repo, file_mgr):
    pipeline, worker, _thread, _ocr, _capture, _widget = make_pipeline(
        tmp_db, item_repo, file_mgr, copy_mode='select')
    pipeline.selection_requested.connect(pipeline.select_ocr_text)
    yield pipeline, worker
    for dialog in list(pipeline._ocr_selections.values()):
        dialog.reject()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def deliver(pipeline, worker, item_repo, make_image_dto, *, status='done'):
    item_id = item_repo.insert(make_image_dto())
    result = replace(reserve(item_repo, item_id, '姓名：臺𠮷\n金額：1,234.50', status),
                     detail_json=json.dumps([
                         {'box': [10, 10, 180, 40], 'text': '姓名：臺𠮷'},
                         {'box': [10, 50, 180, 80], 'text': '金額：1,234.50'},
                     ], ensure_ascii=False), clipboard_epoch=get_clipboard_epoch())
    worker.update_ocr(item_id, result)
    return item_id, result


@pytest.mark.parametrize('status', ['done', 'needs_review', 'confirmed'])
def test_only_explicit_selected_copy_changes_clipboard(
        qapp, qtbot, selection_pipeline, item_repo, make_image_dto, status):
    pipeline, worker = selection_pipeline
    copy_text('先前複製的內容')
    item_id, _result = deliver(pipeline, worker, item_repo, make_image_dto, status=status)
    dialog = pipeline._ocr_selections[item_id]
    before = item_repo.get_by_id(item_id)
    attempts = item_repo.get_ocr_attempts(item_id)
    assert qapp.clipboard().text() == '先前複製的內容'
    assert dialog.selected_indexes == set()
    assert not dialog._copy.isEnabled()
    dialog.select_indexes([1])
    # A newer manual copy must not block a subsequent explicit user selection.
    copy_text('稍後的手寫選字')
    epoch = get_clipboard_epoch()
    qtbot.mouseClick(dialog._copy, Qt.MouseButton.LeftButton)
    assert qapp.clipboard().text() == '金額：1,234.50'
    assert get_clipboard_epoch() == epoch + 1
    assert item_id not in pipeline._ocr_selections
    assert item_repo.get_by_id(item_id) == before
    assert item_repo.get_ocr_attempts(item_id) == attempts


def test_cancel_and_missing_image_keep_clipboard_and_full_history(
        qapp, selection_pipeline, item_repo, make_image_dto):
    pipeline, worker = selection_pipeline
    copy_text('保留')
    item_id, _ = deliver(pipeline, worker, item_repo, make_image_dto)
    dialog = pipeline._ocr_selections[item_id]
    assert not dialog._image.has_image
    dialog.select_indexes([0])
    dialog.reject()
    assert qapp.clipboard().text() == '保留'
    assert item_id not in pipeline._ocr_selections
    assert item_repo.get_by_id(item_id).text_content == '姓名：臺𠮷\n金額：1,234.50'


@pytest.mark.parametrize('change', ['delete', 'delete_restore', 'edit', 'empty_edit', 'rerun', 'shutdown'])
def test_open_dialog_cannot_copy_after_item_changes(
        qapp, selection_pipeline, item_repo, make_image_dto, change):
    pipeline, worker = selection_pipeline
    copy_text('保留')
    item_id, _ = deliver(pipeline, worker, item_repo, make_image_dto)
    dialog = pipeline._ocr_selections[item_id]
    dialog.select_indexes([0])
    if change in ('delete', 'delete_restore'):
        item_repo.soft_delete(item_id)
        if change == 'delete_restore':
            item_repo.restore(item_id)
    elif change in ('edit', 'empty_edit'):
        item_repo.update_edited_text(item_id, '修正' if change == 'edit' else '')
    elif change == 'rerun':
        item_repo.begin_ocr_attempt(item_id)
    else:
        pipeline.closing = True
    dialog._request_copy()
    assert qapp.clipboard().text() == '保留'


def test_new_result_replaces_old_dialog_without_leaking_reference(
        qapp, selection_pipeline, item_repo, make_image_dto):
    pipeline, worker = selection_pipeline
    item_id, _ = deliver(pipeline, worker, item_repo, make_image_dto)
    old = pipeline._ocr_selections[item_id]
    result = reserve(item_repo, item_id, '重新辨識')
    worker.update_ocr(item_id, result)
    current = pipeline._ocr_selections[item_id]
    assert current is not old and not old.isVisible()
    current.select_indexes([0])
    current._request_copy()
    assert qapp.clipboard().text() == '重新辨識'
    assert not pipeline._ocr_selections


def test_shutdown_rejects_selections_and_no_late_result_prompts(
        qapp, selection_pipeline, item_repo, make_image_dto, monkeypatch):
    pipeline, worker = selection_pipeline
    copy_text('保留')
    item_id, _ = deliver(pipeline, worker, item_repo, make_image_dto)
    dialog = pipeline._ocr_selections[item_id]
    # Exercise shutdown's GUI boundary without starting the fixture's threads.
    monkeypatch.setattr(pipeline, 'capture_stopped', lambda: None)
    pipeline.shutdown()
    assert not dialog.isVisible() and not pipeline._ocr_selections
    other = item_repo.insert(make_image_dto())
    worker.update_ocr(other, reserve(item_repo, other))
    assert not pipeline._ocr_selections
    assert qapp.clipboard().text() == '保留'


def test_removed_original_image_still_offers_text_selection(
        qapp, selection_pipeline, item_repo, file_mgr, make_image_dto):
    from pathlib import Path

    from PIL import Image
    pipeline, _worker = selection_pipeline
    # Use the public worker option on a fresh instance; preserve cleanup policy.
    from src.workers.db_worker import DbWorker
    cleanup_worker = DbWorker(item_repo, file_mgr, save_raw_image=False)
    cleanup_worker.ocr_persisted.connect(pipeline.persisted)
    absolute, relative = file_mgr.save_capture(Image.new('RGB', (200, 100), 'white'))
    item_id = item_repo.insert(make_image_dto(rel_path=relative))
    cleanup_worker.update_ocr(item_id, reserve(item_repo, item_id, '留下文字'))
    assert not Path(absolute).exists()
    dialog = pipeline._ocr_selections[item_id]
    assert not dialog._image.has_image
    dialog.select_indexes([0])
    dialog._request_copy()
    assert qapp.clipboard().text() == '留下文字'
