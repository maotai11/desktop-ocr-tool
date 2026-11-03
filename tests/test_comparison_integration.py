"""Exercise the real launcher, selector and tray without capturing the desktop."""
from PySide6.QtCore import QRect
from PySide6.QtWidgets import QApplication
from test_widget_interactions import _make_widget


def test_launcher_selection_keeps_regions_and_clipboard(qtbot, tmp_path, monkeypatch):
    widget = _make_widget(tmp_path)
    qtbot.addWidget(widget)
    widget.show()
    clipboard = QApplication.clipboard()
    clipboard.setText('保留原本的剪貼簿')
    widget.open_comparison()
    window = widget._comparison
    qtbot.addWidget(window)
    selected = []
    monkeypatch.setattr(widget._comparison_overlay, 'start_capture', selected.append)
    widget._select_comparison_region('A')
    assert not widget.isVisible() and not window.isVisible()
    selected.pop()(10, 10, 80, 30, 1)
    widget._select_comparison_region('B')
    selected.pop()(110, 10, 80, 30, 1)
    expected = {'A': (10, 10, 80, 30, 1), 'B': (110, 10, 80, 30, 1)}
    assert widget._comparison_controller.regions == expected
    window.close()
    widget.open_comparison()
    assert widget._comparison is window
    assert widget._comparison_controller.regions == expected
    assert not widget._comparison_controller.running
    assert clipboard.text() == '保留原本的剪貼簿'
    widget._select_comparison_region('A')
    generation = widget._comparison_overlay._capture_generation
    window._screen_changed()
    assert widget._comparison_overlay._capture_generation > generation
    assert not window._selection_active and not widget._comparison_controller.regions
    window.set_region('A', expected['A'])
    window.set_region('B', expected['B'])
    selected.clear()
    # Cancelling or a delayed selection after application shutdown cannot reopen it.
    widget._select_comparison_region('A')
    delayed = selected.pop()
    widget.shutdown_comparison()
    delayed(20, 20, 80, 30, 1)
    assert not window.isVisible() and widget.comparison_is_stopped()
    assert widget._comparison_controller.regions == expected


def test_launcher_guards_other_visible_tool_windows(qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QWidget
    widget = _make_widget(tmp_path)
    qtbot.addWidget(widget)
    widget.open_comparison()
    qtbot.addWidget(widget._comparison)
    monkeypatch.setattr(widget._comparison, 'can_capture', lambda: True)
    blocker = QWidget()
    qtbot.addWidget(blocker)
    blocker.setGeometry(QRect(20, 20, 100, 50))
    blocker.show()
    screen = QApplication.primaryScreen()
    scale = screen.devicePixelRatio()
    frame = blocker.frameGeometry()
    origin = screen.geometry().topLeft()
    widget._comparison_controller.regions = {
        'A': (round((frame.x() - origin.x()) * scale),
              round((frame.y() - origin.y()) * scale), 30, 20, 1)}
    assert not widget._comparison_can_capture()
    blocker.showMinimized()
    QApplication.processEvents()
    assert widget._comparison_can_capture()
    blocker.hide()
    assert widget._comparison_can_capture()
    widget.shutdown_comparison()


def test_tray_status_is_visible_without_copy_or_notifications(qtbot, tmp_path, monkeypatch):
    from src.ui.tray_manager import TrayManager
    widget = _make_widget(tmp_path)
    qtbot.addWidget(widget)
    tray = TrayManager(widget)
    notices = []
    monkeypatch.setattr(tray, 'show_notification', lambda *args: notices.append(args))
    QApplication.clipboard().setText('unchanged')
    widget.comparison_status.emit('mismatch')
    assert '文字不同' in tray._tray.toolTip()
    widget.comparison_status.emit('match')
    assert '文字一致' in tray._tray.toolTip()
    widget.comparison_status.emit('paused')
    assert '已暫停' in tray._tray.toolTip()
    assert QApplication.clipboard().text() == 'unchanged' and not notices
