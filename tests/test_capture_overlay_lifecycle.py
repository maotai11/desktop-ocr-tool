"""A cancelled or hidden capture cannot complete in a later UI session."""
import pytest
from PySide6.QtCore import QPoint, Qt

from src.ui.capture_overlay import CaptureOverlay


@pytest.fixture
def overlay(qtbot):
    widget = CaptureOverlay()
    qtbot.addWidget(widget)
    yield widget
    widget._cancel()


def _select(qtbot, overlay):
    qtbot.mousePress(overlay, Qt.MouseButton.LeftButton, pos=QPoint(20, 20))
    qtbot.mouseRelease(overlay, Qt.MouseButton.LeftButton, pos=QPoint(70, 60))


@pytest.mark.parametrize('retire', ['escape', 'hide', 'close', 'restart'])
def test_interrupted_drag_requires_fresh_press_after_restart(qtbot, overlay, retire):
    old, current = [], []
    overlay.start_capture(lambda *rect: old.append(rect))
    qtbot.mousePress(overlay, Qt.MouseButton.LeftButton, pos=QPoint(20, 20))
    if retire == 'escape':
        qtbot.keyClick(overlay, Qt.Key.Key_Escape)
    elif retire != 'restart':
        getattr(overlay, retire)()
    overlay.start_capture(lambda *rect: current.append(rect))
    qtbot.mouseRelease(overlay, Qt.MouseButton.LeftButton, pos=QPoint(70, 60))
    qtbot.wait(190)
    assert not old and not current
    assert overlay.isVisible()

    _select(qtbot, overlay)
    qtbot.waitUntil(lambda: len(current) == 1)
    assert not old


@pytest.mark.parametrize('retire', ['close', '_cancel'])
def test_close_or_cancel_retires_pending_capture_callback(qtbot, overlay, retire):
    captured, cancelled = [], []
    overlay.cancelled.connect(lambda: cancelled.append(True))
    overlay.start_capture(lambda *rect: captured.append(rect))
    _select(qtbot, overlay)
    assert not overlay.isVisible()
    getattr(overlay, retire)()
    qtbot.wait(190)
    assert not captured
    overlay._cancel()  # Shutdown/repeated close must not re-open the owner's UI.
    assert cancelled == [True]
    assert overlay._callback is None


def test_hide_cancels_once_and_late_release_cannot_capture(qtbot, overlay):
    captured, cancelled = [], []
    overlay.cancelled.connect(lambda: cancelled.append(True))
    overlay.start_capture(lambda *rect: captured.append(rect))
    qtbot.mousePress(overlay, Qt.MouseButton.LeftButton, pos=QPoint(20, 20))
    overlay.hide()
    overlay._cancel()
    qtbot.mouseRelease(overlay, Qt.MouseButton.LeftButton, pos=QPoint(70, 60))
    qtbot.wait(190)
    assert not captured
    assert cancelled == [True]
    assert overlay._callback is None


def test_delivered_callback_is_consumed_before_reentrant_capture(qtbot, overlay):
    captured, next_captured, cancelled = [], [], []
    overlay.cancelled.connect(lambda: cancelled.append(True))

    def first(*rect):
        captured.append(rect)
        overlay.start_capture(lambda *next_rect: next_captured.append(next_rect))

    overlay.start_capture(first)
    _select(qtbot, overlay)
    qtbot.waitUntil(lambda: len(captured) == 1)
    assert overlay.isVisible()
    _select(qtbot, overlay)
    qtbot.waitUntil(lambda: len(next_captured) == 1)
    overlay._cancel()
    assert cancelled == []
    assert overlay._callback is None


def test_tiny_selection_consumes_callback_and_cancels_once(qtbot, overlay):
    captured, cancelled = [], []
    overlay.cancelled.connect(lambda: cancelled.append(True))
    overlay.start_capture(lambda *rect: captured.append(rect))
    qtbot.mouseClick(overlay, Qt.MouseButton.LeftButton, pos=QPoint(20, 20))
    overlay._cancel()
    qtbot.wait(190)
    assert not captured
    assert cancelled == [True]
    assert overlay._callback is None
