"""Candidate updates cannot move the writing surface under a held pointer."""
import pytest
from PySide6.QtCore import (
    QCoreApplication,
    QEvent,
    QObject,
    QPoint,
    QPointF,
    Qt,
    Signal,
)

from src.ui.handwriting_pad import HandwritingPad
from src.ui.handwriting_window import HandwritingWindow


class _Controller(QObject):
    candidates = Signal(int, object)
    request_failed = Signal(int, str)
    stopped = Signal()
    busy_changed = Signal(bool)
    is_stopped = True

    def __init__(self):
        super().__init__()
        self.generation = 0

    def submit(self, _strokes):
        self.generation += 1
        return self.generation

    def stop(self):
        pass

    def resume(self):
        pass


def _settle_layout(qapp):
    QCoreApplication.sendPostedEvents(None, QEvent.Type.LayoutRequest)
    qapp.processEvents()


def _geometry(window):
    pad = window.pad
    rect = pad.canvas_rect()
    return (window.geometry(), pad.size(),
            _global_rect(pad, rect), window.workspace_scroll.verticalScrollBar().value(),
            window.workspace_scroll.verticalScrollBar().maximum())


def _global_rect(pad, rect):
    return rect.translated(QPointF(pad.mapToGlobal(QPoint())))


@pytest.fixture
def handwriting_window(qtbot):
    ink, image = _Controller(), _Controller()
    window = HandwritingWindow(controller=ink, image_controller=image)
    # Retain the signal owners alongside the real native window/pad.
    window._test_controllers = ink, image
    qtbot.addWidget(window)
    yield window
    window.shutdown()


def _draw_then_candidates(window, qtbot, qapp, count):
    rect = window.pad.canvas_rect()
    qtbot.mousePress(window.pad, Qt.MouseButton.LeftButton, pos=rect.center().toPoint())
    qtbot.mouseRelease(window.pad, Qt.MouseButton.LeftButton,
                       pos=(rect.center() + QPointF(20, 20)).toPoint())
    window._debounce.stop()
    window.recognize()
    controller = window._test_controllers[0]
    controller.candidates.emit(controller.generation, [chr(0x4e00 + index) for index in range(count)])
    _settle_layout(qapp)


@pytest.mark.parametrize('size,expanded,count', [
    ((460, 640), False, 1), ((460, 640), False, 30),
    ((460, 640), True, 30), ((360, 360), True, 30),
])
@pytest.mark.parametrize('scroll_offset', [0, 50])
def test_copy_then_write_keeps_pad_geometry_and_stationary_point(
        qtbot, qapp, handwriting_window, size, expanded, count, scroll_offset):
    window = handwriting_window
    window.resize(*size)
    window.show()
    _settle_layout(qapp)
    _draw_then_candidates(window, qtbot, qapp, count)
    if expanded:
        qtbot.mouseClick(window.candidate_panel.expand_button, Qt.MouseButton.LeftButton)
        _settle_layout(qapp)
    qtbot.mouseClick(window._buttons[0], Qt.MouseButton.LeftButton)
    assert qapp.clipboard().text() == chr(0x4e00)
    window.workspace_scroll.verticalScrollBar().setValue(scroll_offset)
    _settle_layout(qapp)
    before = _geometry(window)
    old_strokes = window.pad.buffer.snapshot()
    point = window.pad.canvas_rect().center().toPoint()

    qtbot.mousePress(window.pad, Qt.MouseButton.LeftButton, pos=point)
    _settle_layout(qapp)
    assert _geometry(window) == before
    qtbot.mouseRelease(window.pad, Qt.MouseButton.LeftButton, pos=point)
    window._debounce.stop()
    _settle_layout(qapp)

    assert _geometry(window) == before
    assert window.pad.buffer.snapshot()[:-1] == old_strokes
    # No mouse movement must never manufacture a segment when candidates clear.
    assert len(window.pad.buffer.strokes[-1]) == 1
    assert window.pad.buffer.strokes[-1][0] == pytest.approx((500, 500), abs=2)
    assert not window._buttons
    assert window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint


def test_pending_candidate_autoscroll_cannot_move_workspace_after_new_press(
        qtbot, qapp, handwriting_window):
    window = handwriting_window
    window.resize(360, 360)
    window.show()
    _settle_layout(qapp)
    _draw_then_candidates(window, qtbot, qapp, 30)
    window.candidate_panel.expand_button.click()
    _settle_layout(qapp)
    window.workspace_scroll.verticalScrollBar().setValue(0)
    # This queued scroll used to run after the next mousePress, moving the
    # entire pad even on small/200% screens where its size stayed at the minimum.
    window._schedule_candidates_visible()
    before = _geometry(window)
    point = window.pad.canvas_rect().center().toPoint()
    qtbot.mousePress(window.pad, Qt.MouseButton.LeftButton, pos=point)
    _settle_layout(qapp)
    assert _geometry(window) == before
    qtbot.mouseRelease(window.pad, Qt.MouseButton.LeftButton, pos=point)
    window._debounce.stop()
    assert len(window.pad.buffer.strokes[-1]) == 1


def test_one_stroke_keeps_screen_coordinates_when_layout_changes(qtbot, qapp):
    pad = HandwritingPad()
    qtbot.addWidget(pad)
    pad.resize(400, 300)
    pad.move(100, 100)
    pad.show()
    _settle_layout(qapp)
    before = _global_rect(pad, pad.canvas_rect())
    origin = before.center().toPoint()
    start = pad.mapFromGlobal(origin)
    qtbot.mousePress(pad, Qt.MouseButton.LeftButton, pos=start)

    # Simulate a native layout/monitor change while the pointer remains held.
    pad.resize(500, 400)
    pad.move(120, 110)
    _settle_layout(qapp)
    assert _global_rect(pad, pad.canvas_rect()) == before
    endpoint = origin + QPoint(29, 29)
    expected = tuple(round(value * 1000) for value in (
        (endpoint.x() - before.left()) / before.width(),
        (endpoint.y() - before.top()) / before.height()))
    qtbot.mouseRelease(pad, Qt.MouseButton.LeftButton, pos=pad.mapFromGlobal(endpoint))
    assert pad.buffer.strokes[-1][0] == pytest.approx((500, 500), abs=2)
    assert pad.buffer.strokes[-1][-1] == expected
    assert pad._stroke_rect is None


@pytest.mark.parametrize('action', ['clear', 'undo', 'cancel_active_stroke'])
def test_interrupted_stroke_releases_frozen_frame(qtbot, action):
    pad = HandwritingPad()
    qtbot.addWidget(pad)
    pad.resize(300, 300)
    pad.show()
    qtbot.mousePress(pad, Qt.MouseButton.LeftButton, pos=pad.canvas_rect().center().toPoint())
    assert pad._stroke_rect is not None
    getattr(pad, action)()
    assert pad.buffer.active is None and pad._stroke_rect is None
    assert not pad._mouse_active and pad._touch_id is None


def test_close_cancels_active_frame_and_reopens_normally(qtbot, qapp, handwriting_window):
    window = handwriting_window
    window.show()
    _settle_layout(qapp)
    qtbot.mousePress(window.pad, Qt.MouseButton.LeftButton,
                     pos=window.pad.canvas_rect().center().toPoint())
    window.close()
    assert not window.isVisible()
    assert window.pad.buffer.active is None and window.pad._stroke_rect is None
    window.show()
    _settle_layout(qapp)
    point = window.pad.canvas_rect().center().toPoint()
    qtbot.mouseClick(window.pad, Qt.MouseButton.LeftButton, pos=point)
    window._debounce.stop()
    assert len(window.pad.buffer.strokes[-1]) == 1
