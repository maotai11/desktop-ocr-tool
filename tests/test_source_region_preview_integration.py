"""Real launcher preview routes never resume OCR or revive closed tools."""
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QApplication

from src.ui.region_preview_dialog import RegionPreviewDialog
from test_widget_interactions import _make_widget


def prepare(qtbot, tmp_path, monkeypatch):
    widget = _make_widget(tmp_path)
    qtbot.addWidget(widget)
    widget.open_shared_search()
    window = widget._shared_search
    qtbot.addWidget(window)
    controller = widget._shared_search_controller
    assert controller.set_source_region((10, 10, 80, 60, 1))
    assert controller.set_b_region((200, 10, 100, 100, 1))
    screen = QApplication.primaryScreen()
    picture = QImage(round(screen.geometry().width() * screen.devicePixelRatio()),
                     round(screen.geometry().height() * screen.devicePixelRatio()), QImage.Format.Format_RGB32)
    picture.fill(0xffffffff)
    calls = []

    def grab(*args):
        assert not window.isVisible() and window._selection_active
        assert not controller.running
        calls.append(True)
        return QPixmap.fromImage(picture)

    monkeypatch.setattr(type(screen), 'grabWindow', grab)
    return widget, window, controller, calls


def test_preview_accept_adjusts_only_source_and_keeps_tool_paused(qtbot, tmp_path, monkeypatch):
    widget, window, controller, calls = prepare(qtbot, tmp_path, monkeypatch)
    QApplication.clipboard().setText('keep clipboard')

    def confirm(dialog):
        assert dialog.nudge(1, 0)
        dialog.accept()
        return dialog.result()

    monkeypatch.setattr(RegionPreviewDialog, 'exec', confirm)
    widget._preview_shared_search_region('SOURCE')
    qtbot.waitUntil(lambda: not window._selection_active)
    assert len(calls) == 1 and controller.source_region == (11, 10, 80, 60, 1)
    assert controller.b_region == (200, 10, 100, 100, 1)
    assert not controller.running and widget._shared_search_preview is None
    assert QApplication.clipboard().text() == 'keep clipboard'
    assert not list(tmp_path.rglob('*.png'))
    widget.shutdown_shared_search()


def test_preview_shutdown_during_modal_ignores_accepted_late_rectangle(qtbot, tmp_path, monkeypatch):
    widget, window, controller, calls = prepare(qtbot, tmp_path, monkeypatch)

    def shutdown(dialog):
        dialog.nudge(1, 0)
        widget.shutdown_shared_search()
        return 1  # A late modal result cannot save or reopen anything.

    monkeypatch.setattr(RegionPreviewDialog, 'exec', shutdown)
    widget._preview_shared_search_region('SOURCE')
    qtbot.waitUntil(lambda: len(calls) == 1)
    assert controller.source_region == (10, 10, 80, 60, 1)
    assert not window.isVisible() and not controller.running
    assert widget._shared_search_preview is None and not window.highlight_overlay.marks


def test_cancel_pending_preview_never_takes_screen_snapshot(qtbot, tmp_path, monkeypatch):
    widget, window, controller, calls = prepare(qtbot, tmp_path, monkeypatch)
    widget._preview_shared_search_region('SOURCE')
    widget._cancel_shared_search_selection()
    qtbot.wait(150)
    assert not calls and not window._selection_active and not controller.running
    widget.shutdown_shared_search()


def test_applying_preview_during_synchronous_close_cannot_save_late_region(qtbot, tmp_path, monkeypatch):
    widget, window, controller, calls = prepare(qtbot, tmp_path, monkeypatch)

    def confirm(dialog):
        dialog.nudge(1, 0)
        dialog.accept()
        # Closing synchronously from the apply operation's pause signal must
        # cancel the rest of that operation, even though exec returned OK.
        controller.state_changed.connect(lambda state: window.close() if state == 'paused' and not window._closed_by_user else None)
        return dialog.result()

    monkeypatch.setattr(RegionPreviewDialog, 'exec', confirm)
    widget._preview_shared_search_region('SOURCE')
    qtbot.waitUntil(lambda: len(calls) == 1)
    assert controller.source_region == (10, 10, 80, 60, 1)
    assert not window.isVisible() and not controller.running
    widget.shutdown_shared_search()
