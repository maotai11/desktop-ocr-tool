"""Native Windows overlay proof on an owned synthetic surface, never user content."""
from __future__ import annotations

import sys
from contextlib import contextmanager
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



def _surface_script(parent_pid):
    """An owned, plain white external foreground; fixed script, no user input."""
    if type(parent_pid) is not int or parent_pid <= 0:
        raise ValueError('Invalid owned probe parent')
    return r'''$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$form = New-Object System.Windows.Forms.Form
$form.FormBorderStyle = [System.Windows.Forms.FormBorderStyle]::None
$form.StartPosition = [System.Windows.Forms.FormStartPosition]::Manual
$form.AutoScaleMode = [System.Windows.Forms.AutoScaleMode]::None
$form.BackColor = [System.Drawing.Color]::FromArgb(245, 245, 245)
$form.Bounds = New-Object System.Drawing.Rectangle(0, 0, 352, 220)
$form.ShowInTaskbar = $false
$deadline = [DateTime]::UtcNow.AddSeconds(45)
$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 250
$timer.Add_Tick({
    $alive = $true
    try { $alive = -not [System.Diagnostics.Process]::GetProcessById(PARENT_PID).HasExited }
    catch { $alive = $false }
    if (-not $alive -or [DateTime]::UtcNow -ge $deadline) { $form.Close() }
})
try { $timer.Start(); [System.Windows.Forms.Application]::Run($form) }
finally { $timer.Stop(); $timer.Dispose(); $form.Dispose() }
'''.replace('PARENT_PID', str(parent_pid))


@contextmanager
def _owned_foreground_surface(user32, physical_rect):
    """Separate PID exercises actual application deactivation; always reap it."""
    import base64
    import ctypes
    import os
    import subprocess
    from ctypes import wintypes
    from pathlib import Path

    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.GetSystemDirectoryW.argtypes = [wintypes.LPWSTR, wintypes.UINT]
    kernel32.GetSystemDirectoryW.restype = wintypes.UINT
    system = ctypes.create_unicode_buffer(32768)
    length = kernel32.GetSystemDirectoryW(system, len(system))
    _require(0 < length < len(system))
    executable = Path(system.value) / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe'
    _require(executable.is_file())
    encoded = base64.b64encode(_surface_script(os.getpid()).encode('utf-16-le')).decode('ascii')
    child = subprocess.Popen([str(executable), '-NoLogo', '-NoProfile', '-NonInteractive',
                              '-EncodedCommand', encoded], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.IsWindowVisible.restype = wintypes.BOOL
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
        user32.EnumWindows.restype = wintypes.BOOL
        found = []

        @callback_type
        def visit(hwnd, context):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == child.pid and user32.IsWindowVisible(hwnd):
                found.append(hwnd)
                return False
            return True

        def located():
            found.clear()
            user32.EnumWindows(visit, 0)
            return bool(found) or child.poll() is not None

        _until(located)
        _require(bool(found) and child.poll() is None and child.pid != os.getpid())
        hwnd = found[0]
        user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_int, wintypes.UINT]
        user32.SetWindowPos.restype = wintypes.BOOL
        _require(user32.SetWindowPos(hwnd, None, *physical_rect, 0x0014))  # NOZORDER | NOACTIVATE
        yield hwnd
    finally:
        _reap_surface(child)


def _reap_surface(child):
    """The probe owns its process even when foreground/capture validation fails."""
    import subprocess
    if child.poll() is None:
        child.terminate()
    try:
        child.wait(timeout=5)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait(timeout=5)
    _require(child.poll() is not None)



def _click_owned_resume(user32, button):
    """Grant real last-input focus only at this probe's own top-level button."""
    import ctypes
    from ctypes import wintypes
    hwnd = int(button.winId())
    rectangle, original = wintypes.RECT(), wintypes.POINT()
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    user32.GetCursorPos.restype = wintypes.BOOL
    user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
    user32.SetCursorPos.restype = wintypes.BOOL
    user32.WindowFromPoint.argtypes = [wintypes.POINT]
    user32.WindowFromPoint.restype = wintypes.HWND
    user32.mouse_event.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                   wintypes.DWORD, ctypes.c_size_t]
    user32.mouse_event.restype = None
    _require(button.isVisible() and user32.GetWindowRect(hwnd, ctypes.byref(rectangle))
             and user32.GetCursorPos(ctypes.byref(original)))
    center = wintypes.POINT((rectangle.left + rectangle.right) // 2,
                           (rectangle.top + rectangle.bottom) // 2)
    _require(user32.WindowFromPoint(center) == hwnd)
    try:
        _require(user32.SetCursorPos(center.x, center.y))
        _require(user32.WindowFromPoint(center) == hwnd)
        try:
            user32.mouse_event(0x0002, 0, 0, 0, 0)  # LEFTDOWN, only our verified HWND.
        finally:
            user32.mouse_event(0x0004, 0, 0, 0, 0)  # Always release at the same owned point.
        _wait(40)
    finally:
        _require(user32.SetCursorPos(original.x, original.y))

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
            painter.fillRect(self.rect(), QColor(245, 245, 245))

    class Oracle:
        """A fixture result for a uniform crop, not a recognition accuracy test."""
        def set_progress_callback(self, callback):
            pass

        def is_ready(self):
            return True

        def run_ocr(self, image, mode):
            _require(bool(np.all(image == (245, 245, 245))))
            inference_calls.append(True)
            height, width = image.shape[:2]
            return {'text': 'PROBE42', 'status': 'done', 'detail': [
                {'text': 'PROBE42', 'confidence': 1.0,
                 'box': [[8, 8], [width - 8, 8], [width - 8, min(height - 8, 28)],
                         [8, min(height - 8, 28)]]}]}

    screen = app.primaryScreen()
    _require(screen is not None)
    available = screen.availableGeometry()
    _require(available.width() >= 400 and available.height() >= 260)
    underlay = Underlay(interactive=True)
    window = controller = worker = capture_worker = None
    hidden = Event()
    captures, snapshots, failures, restore_observations, inference_calls = [], [], [], [], []
    quit_on_last_window = app.quitOnLastWindowClosed()
    app.setQuitOnLastWindowClosed(False)
    try:
        underlay.setGeometry(available.left() + 24, available.top() + 24, 352, 220)
        underlay.show()
        underlay.raise_()
        underlay.activateWindow()
        user32 = ctypes.WinDLL('user32', use_last_error=True)
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        user32.SetForegroundWindow.restype = wintypes.BOOL
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.SetForegroundWindow(int(underlay.winId()))
        _until(lambda: user32.GetForegroundWindow() == int(underlay.winId()))
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
        _require(all(bool(np.all(image == (245, 245, 245))) for image in baseline.values()))

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
        get_style = getattr(user32, 'GetWindowLongPtrW', user32.GetWindowLongW)
        get_style.argtypes = [wintypes.HWND, ctypes.c_int]
        get_style.restype = ctypes.c_ssize_t

        def native_pinned(widget):
            return bool(get_style(int(widget.winId()), -20) & 0x00000008)  # WS_EX_TOPMOST

        controller.updated.connect(snapshots.append)
        controller.failed.connect(failures.append)
        field_id = controller.fields[0]['id']
        _require(controller.set_a_region(field_id, a_region))
        _require(controller.set_b_region(b_region))
        _require(controller.set_interval(60))

        def before_capture():
            accepted = window.before_capture()
            if accepted and not overlay.isVisible() and not window.resume_button.isVisible():
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
            return capture_shared({'LABEL': label_region}, layout)[0]['LABEL']

        def no_ink():
            return bool(np.all(pixels() == (245, 245, 245)))

        window.open()
        window._start_button.click()
        _require(not window.isVisible() and controller.running)
        wait_result(1)
        _require(window.resume_button.isVisible())
        _wait(50)
        cards = overlay.callouts
        _require(len(cards) == 1 and cards[0]['a_text'] == 'PROBE42'
                 and cards[0]['status'] == 'matched' and len(cards[0]['display_lines']) == 1)
        _require(tuple(round(value * scale) for value in cards[0]['anchor']) == a_region[:4])
        x, y, width, height = cards[0]['rect']
        _require(y >= cards[0]['anchor'][1] + cards[0]['anchor'][3])
        label_region = (round(x * scale), round(y * scale), round(width * scale), round(height * scale), 1)
        displayed = pixels()
        # The rect immediately below A contains real ink on our owned white paper.
        # Metadata binds it to the A value; no B outline can satisfy this sample.
        _require(displayed.size > 0 and np.count_nonzero(np.any(displayed < 230, axis=2)) >= 5)
        _require(native_pinned(overlay) and native_pinned(window.resume_button) and not native_pinned(window))
        for pinned in (False, True):
            window.set_pinned(pinned)
            _require(native_pinned(overlay) == pinned and native_pinned(window.resume_button) == pinned
                     and not native_pinned(window))

        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.GetWindowRect.restype = wintypes.BOOL
        native_rect = wintypes.RECT()
        _require(user32.GetWindowRect(int(underlay.winId()), ctypes.byref(native_rect)))
        physical_rect = (native_rect.left, native_rect.top, native_rect.right - native_rect.left,
                         native_rect.bottom - native_rect.top)
        # A separate owned process, not a second QWidget in this application,
        # takes focus and covers the same plain paper. This really deactivates Qt.
        with _owned_foreground_surface(user32, physical_rect) as foreground:
            for _ in range(3):
                _click_owned_resume(user32, window.resume_button)
                _until(lambda: window.isVisible() and not window.isMinimized()
                       and app.applicationState() == Qt.ApplicationState.ApplicationActive, timeout=2000)
                _require(not native_pinned(window))
                window.showMinimized()
                user32.SetForegroundWindow(foreground)
                _until(lambda: user32.GetForegroundWindow() == foreground
                       and app.applicationState() == Qt.ApplicationState.ApplicationInactive, timeout=2000)
                _wait(50)
                _require(native_pinned(overlay) and overlay.isVisible() and not no_ink()
                         and not native_pinned(window) and window.isMinimized())
                _require(window.before_capture())
                _wait(50)
                _require(no_ink() and not window.resume_button.isVisible())
                window.after_capture()
                _wait(50)
                _require(overlay.isVisible() and window.resume_button.isVisible() and not no_ink()
                         and user32.GetForegroundWindow() == foreground)

        # Let the real controller's next timer perform its own 50 ms settle and
        # actual worker capture. Beginning a new cycle already invalidates old
        # marks, so after_capture must not revive them before the fresh result.
        first_capture_count = len(captures)
        first_inferences = len(inference_calls)
        _require(controller.set_interval(1) and controller._poll.isActive())
        controller._poll.start(100)  # Shorten only this fixture's next real timer.
        wait_result(2)
        _require(len(captures) > first_capture_count and all(restore_observations)
                 and len(inference_calls) == first_inferences and overlay.isVisible())
        window.resume_button.click()
        _require(window.isVisible() and not native_pinned(window))
        _require(window.before_capture())
        window._pause_button.click()
        window.after_capture()
        _wait(50)
        _require(not overlay.isVisible() and not overlay.marks and not window.resume_button.isVisible()
                 and no_ink())

        window._start_button.click()
        _require(controller.running and not window.isVisible())
        wait_result(3)
        _require(window.before_capture())
        window.close()
        window.after_capture()
        _wait(50)
        _require(not controller.running and not overlay.isVisible() and not overlay.marks and not window.resume_button.isVisible()
                 and no_ink())
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
            window.resume_button.deleteLater()
            window.deleteLater()
        underlay.close()
        underlay.deleteLater()
        app.setQuitOnLastWindowClosed(quit_on_last_window)
        _require(capture_stopped and worker_stopped)
