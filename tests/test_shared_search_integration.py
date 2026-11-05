"""Launcher and application shutdown exercise the new tool's actual entry points."""
from PySide6.QtCore import QObject, QRect
from PySide6.QtWidgets import QApplication, QWidget
import pytest

from test_widget_interactions import _make_widget


def test_launcher_selection_and_late_callback_cannot_reopen_after_shutdown(qtbot, tmp_path, monkeypatch):
    widget = _make_widget(tmp_path)
    qtbot.addWidget(widget)
    QApplication.clipboard().setText('原本的內容')
    widget.open_shared_search()
    window, controller = widget._shared_search, widget._shared_search_controller
    qtbot.addWidget(window)
    assert not controller.running and widget.shared_search_is_stopped()
    first = controller.fields[0]['id']
    callbacks = []
    monkeypatch.setattr(widget._shared_search_selector, 'start_capture', callbacks.append)
    widget._select_shared_search_region(first)
    assert window._selection_active and not window.isVisible()
    callbacks.pop()(10, 10, 80, 24, 1)
    widget._select_shared_search_region('B')
    callbacks.pop()(220, 10, 100, 120, 1)
    assert controller.fields[0]['region'] == (10, 10, 80, 24, 1)
    assert controller.b_region == (220, 10, 100, 120, 1)
    widget._select_shared_search_region(first)
    delayed = callbacks.pop()
    widget.shutdown_shared_search()
    delayed(20, 20, 80, 24, 1)
    assert not window.isVisible() and widget.shared_search_is_stopped()
    assert controller.fields[0]['region'] == (10, 10, 80, 24, 1)
    assert QApplication.clipboard().text() == '原本的內容'
    assert not list(tmp_path.rglob('*.png'))


def test_field_edit_cancels_selector_and_rejects_its_old_callback(qtbot, tmp_path, monkeypatch):
    widget = _make_widget(tmp_path)
    qtbot.addWidget(widget)
    widget.open_shared_search()
    window, controller = widget._shared_search, widget._shared_search_controller
    qtbot.addWidget(window)
    field_id = controller.fields[0]['id']
    callbacks = []
    monkeypatch.setattr(widget._shared_search_selector, 'start_capture', callbacks.append)
    widget._select_shared_search_region(field_id)
    delayed = callbacks.pop()
    controller.add_field('另一欄')
    assert not window._selection_active
    delayed(10, 10, 80, 24, 1)
    assert controller.fields[0]['region'] is None
    widget.shutdown_shared_search()


def test_saved_settings_cancel_search_cache_and_pending_calibration(qtbot, tmp_path):
    widget = _make_widget(tmp_path)
    qtbot.addWidget(widget)
    widget.open_shared_search()
    window, controller = widget._shared_search, widget._shared_search_controller
    qtbot.addWidget(window)
    controller._b_cache = ('old profile and frame', {'text': '先前內容'})
    window._calibrate.click()
    assert window._calibration_timer.isActive()
    widget.settings_saved.emit()
    assert controller._b_cache is None and not controller.running
    assert not window._calibration_timer.isActive() and not window.highlight_overlay.marks
    widget.shutdown_shared_search()


def test_switching_modes_reuses_windows_and_does_not_start_ocr(qtbot, tmp_path):
    widget = _make_widget(tmp_path)
    qtbot.addWidget(widget)
    widget.open_comparison()
    paired = widget._comparison
    qtbot.addWidget(paired)
    widget.open_shared_search()
    search = widget._shared_search
    qtbot.addWidget(search)
    assert not paired.isVisible() and not widget._comparison_controller.running
    widget.open_comparison()
    assert widget._comparison is paired
    assert not search.isVisible() and not widget._shared_search_controller.running
    widget.open_shared_search()
    assert widget._shared_search is search and not widget._shared_search_controller.running
    widget.shutdown_comparison()
    widget.shutdown_shared_search()


@pytest.mark.parametrize('initial', ['paired', 'search'])
def test_mode_switch_cancels_the_other_selector_and_ignores_late_selection(
        qtbot, tmp_path, monkeypatch, initial):
    widget = _make_widget(tmp_path)
    qtbot.addWidget(widget)
    callbacks = []
    if initial == 'paired':
        widget.open_comparison()
        old_window = widget._comparison
        monkeypatch.setattr(widget._comparison_overlay, 'start_capture', callbacks.append)
        widget._select_comparison_region('A')
        widget.open_shared_search()
    else:
        widget.open_shared_search()
        old_window = widget._shared_search
        monkeypatch.setattr(widget._shared_search_selector, 'start_capture', callbacks.append)
        widget._select_shared_search_region(widget._shared_search_controller.fields[0]['id'])
        widget.open_comparison()
    qtbot.addWidget(widget._comparison)
    qtbot.addWidget(widget._shared_search)
    callbacks.pop()(10, 10, 80, 24, 1)
    assert not old_window.isVisible() and not old_window._selection_active
    if initial == 'paired':
        assert not widget._comparison_controller.regions
    else:
        assert widget._shared_search_controller.fields[0]['region'] is None
    widget.shutdown_comparison()
    widget.shutdown_shared_search()


def test_other_inkpick_window_prevents_capture_before_hiding_overlay(qtbot, tmp_path, monkeypatch):
    widget = _make_widget(tmp_path)
    qtbot.addWidget(widget)
    widget.open_shared_search()
    window, controller = widget._shared_search, widget._shared_search_controller
    qtbot.addWidget(window)
    blocker = QWidget()
    qtbot.addWidget(blocker)
    # Native title bars extend above the client area on Windows. Keep the
    # whole frame on-screen so the valid target reaches the overlap guard.
    blocker.setGeometry(QRect(120, 140, 100, 50))
    blocker.show()
    QApplication.processEvents()
    screen = QApplication.primaryScreen()
    origin, dpr = screen.geometry().topLeft(), screen.devicePixelRatio()
    frame = blocker.frameGeometry()
    assert controller.set_a_region(controller.fields[0]['id'],
                                   (round((frame.x() - origin.x()) * dpr),
                                    round((frame.y() - origin.y()) * dpr), 30, 20, 1))
    calls = []
    monkeypatch.setattr(window, 'can_capture', lambda: True)
    monkeypatch.setattr(window, 'before_capture', lambda: calls.append('hide') or True)
    assert not widget._shared_search_can_capture() and not calls
    blocker.hide()
    assert widget._shared_search_can_capture() and calls == ['hide']
    widget.shutdown_shared_search()


def test_tray_search_status_and_pause_entry_without_notifications(qtbot, tmp_path, monkeypatch):
    from src.ui.tray_manager import TrayManager
    widget = _make_widget(tmp_path)
    qtbot.addWidget(widget)
    tray = TrayManager(widget)
    notices = []
    monkeypatch.setattr(tray, 'show_notification', lambda *args: notices.append(args))
    widget.shared_search_status.emit('matched')
    assert '整列搜尋：已找到' in tray._tray.toolTip()
    widget.shared_search_status.emit('uncertain')
    assert '整列搜尋：待確認' in tray._tray.toolTip()
    assert '暫停整列搜尋' in [action.text() for action in tray._tray.contextMenu().actions()]
    assert not notices


@pytest.mark.parametrize('action', ['tray_pause', 'switch_mode'])
def test_calibration_countdown_is_cancelled_by_all_external_pause_entries(
        qtbot, tmp_path, monkeypatch, action):
    from src.ui.tray_manager import TrayManager
    widget = _make_widget(tmp_path)
    qtbot.addWidget(widget)
    widget.open_shared_search()
    window = widget._shared_search
    qtbot.addWidget(window)
    calibrated = []
    monkeypatch.setattr(widget._shared_search_controller, 'calibrate_tracking',
                        lambda: calibrated.append(True))
    window._calibration_timer.setInterval(5)
    window._calibrate.click()
    assert window._calibration_timer.isActive()
    if action == 'tray_pause':
        tray = TrayManager(widget)
        next(item for item in tray._tray.contextMenu().actions()
             if item.text() == '暫停整列搜尋').trigger()
    else:
        widget.open_comparison()
        qtbot.addWidget(widget._comparison)
    assert not window._calibration_timer.isActive()
    qtbot.wait(75)
    assert not calibrated and not widget._shared_search_controller.running
    widget.shutdown_comparison()
    widget.shutdown_shared_search()


def test_application_waits_for_shared_search_worker_before_shutdown_complete(
        qtbot, tmp_db, item_repo, file_mgr):
    from src.workers.capture_worker import CaptureWorker
    from src.workers.db_worker import create_db_worker_in_thread
    from src.workers.ocr_worker import OcrWorker
    from src.workers.pipeline import Pipeline
    from test_pipeline_shutdown import SlowEngine

    class ShutdownWidget(QObject):
        stopped = False
        shutdown_calls = 0

        def setEnabled(self, value):
            pass

        def set_ocr_status(self, text):
            pass

        def refresh_list(self):
            pass

        def shutdown_shared_search(self):
            self.shutdown_calls += 1

        def shared_search_is_stopped(self):
            return self.stopped

    widget = ShutdownWidget()
    capture, ocr = CaptureWorker(file_mgr), OcrWorker(SlowEngine())
    worker, thread = create_db_worker_in_thread(item_repo, file_mgr)
    pipeline = Pipeline(capture, ocr, worker, thread, tmp_db, item_repo, file_mgr, widget, None)
    pipeline.shutdown()
    qtbot.waitUntil(lambda: pipeline._database_closed, timeout=5000)
    assert widget.shutdown_calls == 1 and not pipeline.closed
    widget.stopped = True
    qtbot.waitUntil(lambda: pipeline.closed, timeout=5000)
    assert not capture.isRunning() and not ocr.isRunning() and not thread.isRunning()
