"""Native Windows overlay proof on an owned synthetic surface, never user content."""
from __future__ import annotations

import sys
from threading import Event


def _wait(milliseconds):
    from PySide6.QtCore import QEventLoop, QTimer
    loop = QEventLoop()
    QTimer.singleShot(milliseconds, loop.quit)
    loop.exec()


def _until(predicate, *, timeout=8000):
    from PySide6.QtCore import QEventLoop, QTimer
    if predicate():
        return
    loop, poll, deadline = QEventLoop(), QTimer(), QTimer()
    poll.setInterval(10)
    deadline.setSingleShot(True)
    poll.timeout.connect(lambda: loop.quit() if predicate() else None)
    deadline.timeout.connect(loop.quit)
    poll.start()
    deadline.start(timeout)
    loop.exec()
    poll.stop()
    deadline.stop()
    if not predicate():
        raise ValueError('Windows overlay proof deadline')


def _require(condition):
    if not condition:
        raise ValueError('Windows overlay pixel proof failed')


def verify_overlay_capture(app) -> bool:
    """Prove real displayed marks are excluded by the production capture path.

    False means unsupported (not Windows or not the native Windows Qt plugin).
    A supported but failed proof raises a fixed error; callers must not turn that
    into a successful native result. No screenshots, crops or OCR text are saved.
    """
    if sys.platform != 'win32' or app.platformName().lower() != 'windows':
        return False

    import ctypes
    from ctypes import wintypes

    import numpy as np
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QPainter
    from PySide6.QtWidgets import QWidget

    from .comparison.shared_capture import SharedCaptureWorker, capture_shared
    from .comparison.shared_controller import SharedSearchController
    from .ui.shared_search_window import SharedSearchWindow
    from .workers.ocr_worker import OcrWorker

    class Underlay(QWidget):
        def __init__(self, *, interactive=False):
            flags = Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint
            if not interactive:
                flags |= Qt.WindowType.WindowDoesNotAcceptFocus
            super().__init__(None, flags)
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, not interactive)
            self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)

        def paintEvent(self, event):
            painter = QPainter(self)
            painter.fillRect(self.rect(), QColor(27, 57, 89))

    class Oracle:
        """A fixture result for a uniform crop, not a recognition accuracy test."""
        def set_progress_callback(self, callback):
            pass

        def is_ready(self):
            return True

        def run_ocr(self, image, mode):
            _require(bool(np.all(image == (89, 57, 27))))
            height, width = image.shape[:2]
            return {'text': 'PROBE42', 'status': 'done', 'detail': [
                {'text': 'PROBE42', 'confidence': 1.0,
                 'box': [[8, 8], [width - 8, 8], [width - 8, min(height - 8, 28)],
                         [8, min(height - 8, 28)]]}]}

    screen = app.primaryScreen()
    _require(screen is not None)
    available = screen.availableGeometry()
    _require(available.width() >= 400 and available.height() >= 260)
    underlay = Underlay()
    foreground = None
    window = controller = worker = capture_worker = None
    hidden = Event()
    captures, snapshots, failures, restore_observations = [], [], [], []
    quit_on_last_window = app.quitOnLastWindowClosed()
    app.setQuitOnLastWindowClosed(False)
    try:
        underlay.setGeometry(available.left() + 24, available.top() + 24, 352, 220)
        underlay.show()
        app.processEvents()
        _wait(80)
        scale = screen.devicePixelRatio()
        origin = screen.geometry().topLeft()

        def region(x, y, width, height):
            return (round((underlay.x() - origin.x() + x) * scale),
                    round((underlay.y() - origin.y() + y) * scale),
                    round(width * scale), round(height * scale), 1)

        a_region, b_region = region(20, 70, 80, 40), region(150, 50, 180, 130)
        baseline, layout = capture_shared({'A': a_region, 'B': b_region})
        _require(all(bool(np.all(image == (89, 57, 27))) for image in baseline.values()))

        def real_capture(regions, expected_layout):
            # This provider runs in the real capture worker. UI state is recorded
            # in the owner-thread hook; no QWidget methods run on this thread.
            _require(hidden.is_set())
            images, current_layout = capture_shared(regions, expected_layout)
            _require(current_layout == layout)
            _require(all(np.array_equal(image, baseline['B' if name == 'B' else 'A'])
                         for name, image in images.items()))
            captures.append(True)
            return images, current_layout

        worker = OcrWorker(Oracle())
        capture_worker = SharedCaptureWorker(capture=real_capture)
        controller = SharedSearchController(worker, capture_worker=capture_worker)
        window = SharedSearchWindow(controller)
        overlay = window.highlight_overlay
        user32 = ctypes.WinDLL('user32', use_last_error=True)
        get_style = getattr(user32, 'GetWindowLongPtrW', user32.GetWindowLongW)
        get_style.argtypes = [wintypes.HWND, ctypes.c_int]
        get_style.restype = ctypes.c_ssize_t
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        user32.SetForegroundWindow.restype = wintypes.BOOL
        user32.GetForegroundWindow.restype = wintypes.HWND

        def native_pinned(widget):
            return bool(get_style(int(widget.winId()), -20) & 0x00000008)  # WS_EX_TOPMOST

        controller.updated.connect(snapshots.append)
        controller.failed.connect(failures.append)
        field_id = controller.fields[0]['id']
        _require(controller.set_a_region(field_id, a_region))
        _require(controller.set_b_region(b_region))
        _require(controller.set_interval(6))

        def before_capture():
            accepted = window.before_capture()
            if accepted and not overlay.isVisible():
                hidden.set()
            else:
                hidden.clear()
            return accepted

        def after_capture():
            window.after_capture()
            restore_observations.append(not overlay.isVisible())
            hidden.clear()

        controller.before_capture = before_capture
        controller.after_capture = after_capture

        def wait_result(count):
            _until(lambda: len(snapshots) >= count or bool(failures))
            _require(not failures and len(snapshots) >= count)
            _require(snapshots[-1]['fields'][0]['result']['status'] == 'matched')
            _require(overlay.isVisible())

        def pixels():
            return capture_shared({'B': b_region}, layout)[0]['B']

        _require(controller.start())
        wait_result(1)
        _wait(50)
        displayed = pixels()
        _require(not np.array_equal(displayed, baseline['B']))
        cards = overlay.callouts
        _require(len(cards) == 1 and cards[0]['name'] == controller.fields[0]['name']
                 and cards[0]['a_text'] == cards[0]['b_text'] == 'PROBE42'
                 and cards[0]['status'] == 'matched')
        x, y, width, height = cards[0]['rect']
        x, y = round(x * scale) - b_region[0], round(y * scale) - b_region[1]
        width, height = round(width * scale), round(height * scale)
        card_pixels = displayed[y + 3:y + height - 3, x + 3:x + width - 3]
        # White A/B glyph pixels on the dark card prove text is actually painted,
        # while the semantic payload above verifies the corresponding A and B.
        _require(card_pixels.size > 0 and np.count_nonzero(np.all(card_pixels > 140, axis=2)) >= 5)
        _require(native_pinned(overlay) and not native_pinned(window))
        window.set_pinned(False)
        _require(not native_pinned(overlay) and not native_pinned(window))
        window.set_pinned(True)
        _require(native_pinned(overlay) and not native_pinned(window))

        # Another owned normal application window takes the native foreground.
        # It paints the same fixture surface so every OCR pixel remains bounded
        # to our synthetic content; no external application or input hook is used.
        foreground = Underlay(interactive=True)
        foreground.setGeometry(underlay.geometry())
        foreground.show()
        foreground.raise_()
        foreground.activateWindow()
        user32.SetForegroundWindow(int(foreground.winId()))
        _until(lambda: user32.GetForegroundWindow() == int(foreground.winId()), timeout=2000)
        window.showMinimized()
        user32.SetForegroundWindow(int(foreground.winId()))
        _wait(50)
        _require(window.isMinimized() and not native_pinned(window) and native_pinned(overlay)
                 and foreground.isVisible() and user32.GetForegroundWindow() == int(foreground.winId())
                 and overlay.isVisible() and not np.array_equal(pixels(), baseline['B']))

        # Same-generation hide/grab/restore proves the visible native window
        # actually disappears from pixels, rather than only flipping a Qt flag.
        _require(window.before_capture())
        _wait(50)
        _require(np.array_equal(pixels(), baseline['B']))
        window.after_capture()
        _wait(50)
        _require(overlay.isVisible() and not np.array_equal(pixels(), baseline['B']))

        # Let the real controller's next timer perform its own 50 ms settle and
        # actual worker capture. Beginning a new cycle already invalidates old
        # marks, so after_capture must not revive them before the fresh result.
        first_capture_count = len(captures)
        wait_result(2)
        _require(len(captures) > first_capture_count and all(restore_observations))
        _require(window.before_capture())
        window.pause()
        window.after_capture()
        _wait(50)
        _require(not overlay.isVisible() and not overlay.marks
                 and np.array_equal(pixels(), baseline['B']))

        _require(controller.start())
        wait_result(3)
        _require(window.before_capture())
        window.close()
        window.after_capture()
        _wait(50)
        _require(not controller.running and not overlay.isVisible() and not overlay.marks
                 and np.array_equal(pixels(), baseline['B']))
        return True
    finally:
        if window is not None:
            window.shutdown()
        elif controller is not None:
            controller.shutdown()
        if capture_worker is not None:
            capture_worker.stop()
        if worker is not None:
            worker.stop()
        capture_stopped = capture_worker is None or capture_worker.wait(5000)
        worker_stopped = worker is None or worker.wait(5000)
        if window is not None:
            window.highlight_overlay.deleteLater()
            window.deleteLater()
        if foreground is not None:
            foreground.close()
            foreground.deleteLater()
        underlay.close()
        underlay.deleteLater()
        app.setQuitOnLastWindowClosed(quit_on_last_window)
        _require(capture_stopped and worker_stopped)
