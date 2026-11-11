"""Interrupted native gestures and editor sessions must not leak into the next one."""
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPointF, Qt
from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox

from src.ui.main_window import MainWindow
from test_document_template_window import imported, window  # noqa: F401


@pytest.fixture
def history(qtbot, tmp_path, item_repo, tag_repo, file_mgr, db_worker):
    widget = MainWindow(item_repo, tag_repo, file_mgr, db_worker, MagicMock(),
                        MagicMock(), str(tmp_path))
    qtbot.addWidget(widget)
    widget.show()
    return widget


@pytest.mark.parametrize('finish', ['reject', 'accept', 'close'])
def test_finished_history_editor_reopens_with_current_saved_text(
        history, item_repo, make_text_dto, finish):
    item_id = item_repo.insert(make_text_dto('original'))
    history._open_editor(item_repo.get_by_id(item_id))
    old = history._editor_windows[item_id]
    old._text_edit.setPlainText('unsaved draft')
    getattr(old, finish)()
    item_repo.update_edited_text(item_id, 'new saved text')
    history._open_editor(item_repo.get_by_id(item_id))
    reopened = history._editor_windows[item_id]
    assert reopened.isVisible()
    assert reopened._text_edit.toPlainText() == 'new saved text'
    assert reopened is not old
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert history._editor_windows[item_id] is reopened


def test_minimized_history_editor_restores_without_discarding_draft(
        history, item_repo, make_text_dto):
    item_id = item_repo.insert(make_text_dto('original'))
    history._open_editor(item_repo.get_by_id(item_id))
    editor = history._editor_windows[item_id]
    editor._text_edit.setPlainText('draft stays')
    editor.showMinimized()
    history._open_editor(item_repo.get_by_id(item_id))
    assert history._editor_windows[item_id] is editor
    assert not editor.isMinimized()
    assert editor._text_edit.toPlainText() == 'draft stays'


def test_history_shutdown_ignores_queued_db_responses_and_keeps_editor_draft(
        history, item_repo, make_text_dto, monkeypatch):
    item_id = item_repo.insert(make_text_dto('saved'))
    history._open_editor(item_repo.get_by_id(item_id))
    editor = history._editor_windows[item_id]
    editor._text_edit.setPlainText('unsaved draft')
    editor._note_edit.setPlainText('unsaved note')
    history.shutdown()
    reads, notices = [], []
    monkeypatch.setattr(item_repo, 'list_recent', lambda **kwargs: reads.append(kwargs))
    monkeypatch.setattr(QMessageBox, 'information', lambda *args: notices.append(args))
    history._db_worker.items_soft_deleted.emit([item_id])
    history._db_worker.item_pinned.emit(item_id, True)
    history._db_worker.purge_done.emit(1)
    history.refresh()
    history._open_editor(item_repo.get_by_id(item_id))
    editor._save()
    editor._confirm_ocr()
    assert not history.isVisible() and not editor.isVisible()
    assert not reads and not notices
    assert editor._text_edit.toPlainText() == 'unsaved draft'
    assert item_repo.get_by_id(item_id).get_effective_text() == 'saved'
    assert not item_repo.get_by_id(item_id).note_plaintext


@pytest.mark.parametrize('action', ['delete', 'purge', 'export'])
def test_history_modal_return_after_shutdown_cannot_write_or_export(
        history, item_repo, make_text_dto, monkeypatch, action):
    from src.data.exporter import Exporter
    item_id = item_repo.insert(make_text_dto('saved'))
    history.refresh()
    history._table.selectRow(0)
    writes = []
    history.request_soft_delete.connect(lambda ids: writes.append(('delete', ids)))
    history.request_purge.connect(lambda: writes.append(('purge',)))
    monkeypatch.setattr(Exporter, 'export_txt', lambda *args: writes.append(('export',)))
    monkeypatch.setattr(QMessageBox, 'information', lambda *args: writes.append(('notice',)))

    def finish_after_shutdown(*_):
        history.shutdown()
        return QDialog.DialogCode.Accepted if action == 'export' else QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, 'question', finish_after_shutdown)
    monkeypatch.setattr(QDialog, 'exec', finish_after_shutdown)
    if action == 'delete':
        history._delete_selected()
    elif action == 'purge':
        history._clear_deleted()
    else:
        history._export_with_dialog('txt')
    assert writes == []
    assert not item_repo.get_by_id(item_id).is_deleted


def test_editor_shutdown_blocks_rerun_and_file_dialog_completion(
        qtbot, tmp_path, item_repo, file_mgr, make_image_dto, monkeypatch):
    from src.ui.editor_window import EditorWindow
    source = Path(file_mgr.get_abs_path('captures/source.png'))
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b'original bytes')
    destination = tmp_path / 'export.png'
    item_id = item_repo.insert(make_image_dto(rel_path='captures/source.png'))
    worker = MagicMock()
    editor = EditorWindow(item_repo.get_by_id(item_id), item_repo, file_mgr, worker)
    qtbot.addWidget(editor)

    def finish_after_shutdown(*_):
        editor.shutdown()
        return str(destination), ''

    monkeypatch.setattr(QFileDialog, 'getSaveFileName', finish_after_shutdown)
    editor._save_image_as()
    editor._rerun_ocr()
    worker.queue_ocr.assert_not_called()
    assert not destination.exists()


@pytest.mark.parametrize('interrupt', ['hide', 'disable', 'zoom', 'pan', 'middle_pan'])
def test_interrupted_document_drag_does_not_commit_old_coordinates(window, qtbot, interrupt):
    imported(window, 1)
    canvas = window._canvas
    start = canvas.mapFromScene(QPointF(150, 250))
    end = canvas.mapFromScene(QPointF(350, 450))
    qtbot.mousePress(canvas.viewport(), Qt.MouseButton.LeftButton, pos=start)
    qtbot.mouseMove(canvas.viewport(), pos=end)
    assert canvas._start is not None
    if interrupt == 'hide':
        window.hide()
        window.show()
    elif interrupt == 'disable':
        window.set_busy(True)
        window.set_busy(False)
    elif interrupt == 'zoom':
        canvas.zoom_in()
    elif interrupt == 'pan':
        canvas.set_pan_mode(True)
        canvas.set_pan_mode(False)
    else:
        qtbot.mousePress(canvas.viewport(), Qt.MouseButton.MiddleButton, pos=end)
        qtbot.mouseRelease(canvas.viewport(), Qt.MouseButton.MiddleButton, pos=end)
    qtbot.mouseRelease(canvas.viewport(), Qt.MouseButton.LeftButton, pos=end)
    assert window._layout.region_for('辨識1', 0) is None
    assert canvas._start is None and canvas._rubber is None
    # Cancellation must not make the next deliberate selection unusable.
    start = canvas.mapFromScene(QPointF(150, 250))
    end = canvas.mapFromScene(QPointF(250, 350))
    qtbot.mousePress(canvas.viewport(), Qt.MouseButton.LeftButton, pos=start)
    qtbot.mouseMove(canvas.viewport(), pos=end)
    qtbot.mouseRelease(canvas.viewport(), Qt.MouseButton.LeftButton, pos=end)
    region = window._layout.region_for('辨識1', 0)
    assert region.pixel_bounds(600, 800) == pytest.approx((150, 250, 250, 350), abs=3)
