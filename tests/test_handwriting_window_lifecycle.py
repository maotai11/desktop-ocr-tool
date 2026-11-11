"""Hidden handwriting UI owns no pending recognition or active gesture."""
import pytest
from PySide6.QtCore import QObject, QPoint, Qt, Signal

from src.ui.handwriting_window import HandwritingWindow


class _Controller(QObject):
    candidates = Signal(int, object)
    request_failed = Signal(int, str)
    stopped = Signal()
    busy_changed = Signal(bool)

    def __init__(self):
        super().__init__()
        self.is_stopped = True
        self.closed = False
        self.submissions = []
        self.resumes = 0

    def submit(self, strokes):
        if self.closed:
            return None
        self.submissions.append(strokes)
        self.is_stopped = False
        return len(self.submissions)

    def cancel_current(self):
        self.is_stopped = True

    def stop(self):
        self.closed = True
        self.is_stopped = True
        # An idle real controller emits this before stop() returns.
        self.stopped.emit()

    def resume(self):
        self.closed = False
        self.resumes += 1


@pytest.fixture
def window(qtbot):
    ink, image = _Controller(), _Controller()
    widget = HandwritingWindow(controller=ink, image_controller=image)
    widget._test_controllers = ink, image
    qtbot.addWidget(widget)
    widget.show()
    qtbot.waitUntil(widget.isVisible)
    yield widget
    widget.shutdown()


def _stroke(qtbot, window):
    point = window.pad.canvas_rect().center().toPoint()
    qtbot.mousePress(window.pad, Qt.MouseButton.LeftButton, pos=point)
    qtbot.mouseRelease(window.pad, Qt.MouseButton.LeftButton, pos=point + QPoint(20, 20))


def test_hide_before_debounce_does_not_start_background_recognition(qtbot, window):
    _stroke(qtbot, window)
    assert window._debounce.isActive()
    window.hide()
    qtbot.wait(420)
    assert not window.controller.submissions
    assert window.controller.closed and window.image_controller.closed
    assert not window._candidate_scroll.isActive()


@pytest.mark.parametrize('retire', ['hide', 'close'])
def test_hidden_window_rejects_late_recognition_and_reopens_explicitly(qtbot, window, retire):
    _stroke(qtbot, window)
    window._debounce.stop()
    resumes = window.controller.resumes
    getattr(window, retire)()
    assert window.controller.closed and window.image_controller.closed
    assert window.controller.resumes == resumes
    window.recognize()
    window.recognize_image()
    assert not window.controller.submissions and not window.image_controller.submissions

    window.show()
    window.recognize()
    assert len(window.controller.submissions) == 1


def test_hide_during_stroke_retires_pointer_before_reopen(qtbot, window):
    point = window.pad.canvas_rect().center().toPoint()
    qtbot.mousePress(window.pad, Qt.MouseButton.LeftButton, pos=point)
    window.hide()
    assert window.pad.buffer.active is None
    assert window.pad._stroke_rect is None
    window.show()
    qtbot.mouseRelease(window.pad, Qt.MouseButton.LeftButton, pos=point + QPoint(20, 20))
    assert not window.pad.buffer.strokes
    assert not window._debounce.isActive()
    _stroke(qtbot, window)
    assert len(window.pad.buffer.strokes) == 1


@pytest.mark.parametrize('recognize', ['recognize', 'recognize_image'])
def test_hide_retires_running_results_even_after_reopen(qtbot, window, recognize):
    _stroke(qtbot, window)
    getattr(window, recognize)()
    window.hide()
    assert window.is_stopped
    assert window.controller.closed and window.image_controller.closed
    window.show()
    if recognize == 'recognize':
        window.controller.candidates.emit(1, ['舊'])
    else:
        from src.handwriting.candidates import Candidate
        window.image_controller.candidates.emit(1, [Candidate('舊', image_rank=1)])
    assert not window._buttons


def test_shutdown_then_late_show_never_restarts_controllers(qtbot, window):
    _stroke(qtbot, window)
    window.shutdown()
    resumes = window.controller.resumes
    window.show()
    window.recognize()
    window.recognize_image()
    assert window.controller.closed and window.image_controller.closed
    assert window.controller.resumes == resumes
    assert not window.controller.submissions and not window.image_controller.submissions
