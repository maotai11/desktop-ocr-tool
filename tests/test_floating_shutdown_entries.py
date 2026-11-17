"""Late tray/modal callbacks cannot reopen tools while the pipeline drains."""
import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QDialog

from src.ui.tray_manager import TrayManager
from src.workers.capture_worker import CaptureWorker
from src.workers.db_worker import create_db_worker_in_thread
from src.workers.ocr_worker import OcrWorker
from src.workers.pipeline import Pipeline
from test_pipeline_shutdown import SlowEngine
from test_widget_interactions import _make_widget


@pytest.fixture
def running_pipeline(qtbot, tmp_path, tmp_db, item_repo, file_mgr):
    from src.ui.capture_overlay import CaptureOverlay
    widget = _make_widget(tmp_path)
    overlay = CaptureOverlay()
    capture, ocr = CaptureWorker(file_mgr), OcrWorker(SlowEngine())
    worker, thread = create_db_worker_in_thread(item_repo, file_mgr)
    pipeline = Pipeline(capture, ocr, worker, thread, tmp_db, item_repo, file_mgr, widget, None)
    pipeline.set_capture_overlay(overlay)

    def shutdown_before_widget_deletion(_):
        pipeline.shutdown()
        qtbot.waitUntil(lambda: pipeline.closed, timeout=5000)

    qtbot.addWidget(widget, before_close_func=shutdown_before_widget_deletion)
    qtbot.addWidget(overlay)
    yield pipeline, widget, overlay
    pipeline.shutdown()
    qtbot.waitUntil(lambda: pipeline.closed, timeout=5000)


def _select(qtbot, overlay):
    qtbot.mousePress(overlay, Qt.MouseButton.LeftButton, pos=QPoint(20, 20))
    qtbot.mouseRelease(overlay, Qt.MouseButton.LeftButton, pos=QPoint(70, 60))


@pytest.mark.parametrize('completion', ['cancel', 'failure'])
def test_capture_recovers_a_previously_minimized_launcher(qtbot, running_pipeline, completion):
    pipeline, widget, overlay = running_pipeline
    widget.showMinimized()
    pipeline.start_region_capture('region_ocr')
    if completion == 'cancel':
        qtbot.keyClick(overlay, Qt.Key.Key_Escape)
    else:
        pipeline.cancel_region_capture()
        pipeline.capture_failed('test capture failure')
    assert widget.isVisible() and not widget.isMinimized()


@pytest.mark.parametrize('phase', ['dragging', 'pending'])
def test_shutdown_retires_normal_selector_without_reveal_or_late_submission(
        qtbot, running_pipeline, monkeypatch, phase):
    pipeline, widget, overlay = running_pipeline
    submissions = []
    monkeypatch.setattr(pipeline.capture, 'capture_region', lambda *args: submissions.append(args))
    pipeline.start_region_capture('region_ocr')
    if phase == 'pending':
        _select(qtbot, overlay)
    else:
        qtbot.mousePress(overlay, Qt.MouseButton.LeftButton, pos=QPoint(20, 20))
    pipeline.shutdown()
    qtbot.waitUntil(lambda: pipeline.closed)
    qtbot.wait(180)
    assert not overlay.isVisible() and not widget.isVisible()
    assert not submissions and not overlay._capture_active
    # These callbacks can already be queued when the DB is closed.
    pipeline.start_region_capture('region_image')
    assert not overlay.isVisible()
    assert not pipeline._selection_is_current(123, object())


@pytest.mark.parametrize('next_mode', ['region_ocr', 'region_image'])
def test_restarting_normal_selector_retires_pending_old_mode(
        qtbot, running_pipeline, monkeypatch, next_mode):
    pipeline, widget, overlay = running_pipeline
    submissions = []
    monkeypatch.setattr(pipeline.capture, 'capture_region', lambda *args: submissions.append(args))
    pipeline.start_region_capture('region_ocr')
    _select(qtbot, overlay)  # The first selector has a delayed accepted drag.
    pipeline.start_region_capture(next_mode)
    qtbot.wait(180)
    assert overlay.isVisible() and not widget.isVisible()
    assert not submissions
    _select(qtbot, overlay)
    qtbot.waitUntil(lambda: len(submissions) == 1)
    assert submissions[0][-2] == next_mode


def test_tray_cannot_create_handwriting_after_shutdown_begins(
        qtbot, tmp_path, tmp_db, item_repo, file_mgr):
    widget = _make_widget(tmp_path)
    qtbot.addWidget(widget)
    capture, ocr = CaptureWorker(file_mgr), OcrWorker(SlowEngine())
    worker, thread = create_db_worker_in_thread(item_repo, file_mgr)
    pipeline = Pipeline(capture, ocr, worker, thread, tmp_db, item_repo, file_mgr, widget, None)
    tray = TrayManager(widget)
    try:
        pipeline.shutdown()
        next(action for action in tray._tray.contextMenu().actions()
             if action.text() == '手寫').trigger()
        assert widget._handwriting is None
    finally:
        widget.shutdown_handwriting()
        qtbot.waitUntil(lambda: pipeline.closed, timeout=5000)


def test_minimized_launcher_reopens_in_one_tray_action(qtbot, tmp_path):
    widget = _make_widget(tmp_path)
    qtbot.addWidget(widget)
    widget.showMinimized()
    assert widget.isMinimized()
    widget.toggle_visibility()
    assert widget.isVisible() and not widget.isMinimized()


@pytest.mark.parametrize('closing', [False, True])
def test_single_instance_wakeup_restores_minimized_but_respects_shutdown(qtbot, tmp_path, closing):
    import ctypes
    from ctypes import wintypes

    class Message(ctypes.Structure):
        _fields_ = [('hwnd', wintypes.HWND), ('message', wintypes.UINT),
                    ('wParam', wintypes.WPARAM), ('lParam', wintypes.LPARAM),
                    ('time', wintypes.DWORD), ('pt', wintypes.POINT)]

    widget = _make_widget(tmp_path)
    qtbot.addWidget(widget)
    widget.showMinimized()
    widget.hide()
    if closing:
        widget.begin_shutdown()
    message = Message(message=0x0401)
    assert widget.nativeEvent(b'windows_generic_MSG', ctypes.addressof(message)) == (True, 0)
    assert widget.isVisible() is (not closing)
    if not closing:
        assert not widget.isMinimized()


def test_finished_editor_is_not_reused_before_deferred_deletion(qtbot, tmp_path, item_repo, make_text_dto):
    widget = _make_widget(tmp_path)
    qtbot.addWidget(widget)
    widget._item_repo = item_repo
    item = item_repo.get_by_id(item_repo.insert(make_text_dto("draft")))
    widget._open_editor(item)
    first = widget._editor_windows[item.id]
    first.reject()
    # No processEvents: this is the interval before WA_DeleteOnClose deletion.
    widget._open_editor(item)
    second = widget._editor_windows[item.id]
    try:
        assert second is not first and second.isVisible()
    finally:
        second.reject()


def test_modal_return_after_shutdown_cannot_publish_settings(qtbot, tmp_path, monkeypatch):
    from src.ui.settings_dialog import SettingsDialog
    widget = _make_widget(tmp_path)
    qtbot.addWidget(widget)
    updates = []
    widget.settings_saved.connect(lambda: updates.append(True))

    def finish_during_shutdown(dialog):
        widget.begin_shutdown()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(SettingsDialog, 'exec', finish_during_shutdown)
    widget.open_settings()
    assert not updates
