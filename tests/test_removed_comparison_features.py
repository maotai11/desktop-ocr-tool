"""Removed tools leave no launcher, tray, controller hooks or shutdown work."""
import pytest
from PySide6.QtWidgets import QPushButton

from src.ui.tray_manager import TrayManager
from src.workers.capture_worker import CaptureWorker
from src.workers.db_worker import create_db_worker_in_thread
from src.workers.ocr_worker import OcrWorker
from src.workers.pipeline import Pipeline
from test_pipeline_shutdown import SlowEngine
from test_widget_interactions import _make_widget


def test_launcher_and_tray_have_no_comparison_entry_or_hidden_tool_state(qtbot, tmp_path):
    widget = _make_widget(tmp_path)
    qtbot.addWidget(widget)
    assert not any('comparison' in name or 'shared_search' in name for name in dir(widget))
    buttons = {button.text() for button in widget.findChildren(QPushButton)}
    assert {'擷取文字', '截圖', '手寫', '文件套版', '歷史紀錄', '檔案位置'} <= buttons
    assert not any('核對' in text or '整列搜尋' in text for text in buttons)
    tray = TrayManager(widget)
    actions = {action.text() for action in tray._tray.contextMenu().actions() if not action.isSeparator()}
    assert {'手寫', '文件套版（PDF／圖片）', '設定', '退出', '暫停剪貼簿監聽'} <= actions
    assert not any('核對' in text or '整列搜尋' in text for text in actions)
    assert not hasattr(tray, 'set_comparison_status')
    assert not hasattr(tray, 'set_shared_search_status')


@pytest.mark.parametrize('mode', ['region_ocr', 'region_image'])
def test_ordinary_capture_buttons_still_route_after_tool_removal(qtbot, tmp_path, mode):
    widget = _make_widget(tmp_path)
    qtbot.addWidget(widget)
    calls = []
    # The production launcher callback remains the public ordinary-capture route.
    widget.set_capture_callbacks(lambda: calls.append('region_ocr'), lambda: calls.append('region_image'))
    widget.on_ocr_engine_ready()
    button = widget._btn_ocr if mode == 'region_ocr' else widget._btn_screenshot
    button.click()
    assert calls == [mode]


def test_shutdown_never_queries_removed_tool_workers(qtbot, tmp_path, tmp_db, item_repo, file_mgr, monkeypatch):
    widget = _make_widget(tmp_path)
    qtbot.addWidget(widget)

    def forbidden(instance):
        raise AssertionError('Removed comparison worker was queried during shutdown')

    for name in ('shutdown_comparison', 'shutdown_shared_search', 'comparison_is_stopped', 'shared_search_is_stopped'):
        monkeypatch.setattr(type(widget), name, property(forbidden), raising=False)
    capture, ocr = CaptureWorker(file_mgr), OcrWorker(SlowEngine())
    worker, thread = create_db_worker_in_thread(item_repo, file_mgr)
    pipeline = Pipeline(capture, ocr, worker, thread, tmp_db, item_repo, file_mgr, widget, None)
    pipeline.shutdown()
    qtbot.waitUntil(lambda: pipeline.closed, timeout=5000)
    assert not capture.isRunning() and not ocr.isRunning() and not thread.isRunning()
