"""Finite Windows native focus proof using only an owned synthetic source process.

This probe never attaches to user software or loads OCR models. The GUI probe
launches it in a fresh process with QT_SCALE_FACTOR=2 before QApplication.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time


def _command(flag, report):
    return ([sys.executable] if getattr(sys, 'frozen', False) else
            [sys.executable, str(Path(__file__).with_name('main.py'))]) + [flag, str(report)]


def _write(path, payload):
    temporary = Path(str(path) + '.new')
    temporary.write_text(json.dumps(payload), encoding='utf-8')
    temporary.replace(path)


def _read(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def _pump(app, predicate, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(.01)
    return False


def _api():
    import ctypes
    from ctypes import wintypes
    user = ctypes.WinDLL('user32', use_last_error=True)
    user.GetForegroundWindow.restype = wintypes.HWND
    user.SetForegroundWindow.argtypes = [wintypes.HWND]
    user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    user.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
    user.AllowSetForegroundWindow.argtypes = [wintypes.DWORD]
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    return ctypes, wintypes, user


class _ChildJob:
    """A probe-owned kill-on-close job; never enumerates unrelated processes."""
    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self.ctypes = ctypes
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                       ctypes.c_void_p, wintypes.DWORD]
        self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        class Basic(ctypes.Structure):
            _fields_ = [('a', ctypes.c_int64), ('b', ctypes.c_int64), ('flags', wintypes.DWORD),
                        ('minimum', ctypes.c_size_t), ('maximum', ctypes.c_size_t),
                        ('active', wintypes.DWORD), ('affinity', ctypes.c_size_t),
                        ('priority', wintypes.DWORD), ('scheduling', wintypes.DWORD)]
        class Extended(ctypes.Structure):
            _fields_ = [('basic', Basic), ('io', ctypes.c_uint64 * 6),
                        ('process_memory', ctypes.c_size_t), ('job_memory', ctypes.c_size_t),
                        ('peak_process', ctypes.c_size_t), ('peak_job', ctypes.c_size_t)]
        self.handle = self.kernel.CreateJobObjectW(None, None)
        limits = Extended()
        limits.basic.flags = 0x2000 | 0x8  # KILL_ON_JOB_CLOSE | ACTIVE_PROCESS
        limits.basic.active = 4
        if not self.handle or not self.kernel.SetInformationJobObject(
                self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            self.close()
            raise OSError('Native fixture job unavailable')

    def assign(self, child):
        if not self.kernel.AssignProcessToJobObject(self.handle, int(child._handle)):
            raise OSError('Native fixture ownership unavailable')

    def contains_pid(self, pid):
        from ctypes import wintypes
        kernel = self.kernel
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, self.ctypes.POINTER(wintypes.BOOL)]
        process = kernel.OpenProcess(0x1000, False, pid)
        try:
            member = wintypes.BOOL()
            return bool(process and kernel.IsProcessInJob(process, self.handle, self.ctypes.byref(member)) and member.value)
        finally:
            if process:
                kernel.CloseHandle(process)

    def close(self):
        if self.handle:
            self.kernel.TerminateJobObject(self.handle, 0)
            self.kernel.CloseHandle(self.handle)
            self.handle = None



def _resume(child):
    import ctypes
    from ctypes import wintypes
    native = ctypes.WinDLL('ntdll', use_last_error=True)
    native.NtResumeProcess.argtypes = [wintypes.HANDLE]
    native.NtResumeProcess.restype = ctypes.c_long
    if native.NtResumeProcess(int(child._handle)) < 0:
        raise OSError('Owned native fixture resume failed')


def _start_owned(command, job, **kwargs):
    # Suspend the bootloader itself: no frozen descendant can race assignment.
    child = subprocess.Popen(command, creationflags=0x4, **kwargs)
    try:
        job.assign(child)
        _resume(child)
        return child
    except BaseException:
        child.terminate()
        child.wait(timeout=5)
        raise


class _SourceProtocol:
    """Retry transient Windows sharing failures without losing the arm ACK.

    State transitions happen once, publication remains dirty until atomic replace
    succeeds. A failed deactivation publication must likewise remain observable.
    """
    def __init__(self, path):
        self.path = Path(path)
        self.state = {'pid': os.getpid(), 'armed': False, 'deactivations': 0, 'selected': True}
        self.dirty = True

    def flush(self):
        if self.dirty:
            try:
                _write(self.path, self.state)
            except OSError:
                return False
            self.dirty = False
        return True

    def poll(self):
        command = _read(str(self.path) + '.control').get('command')
        if command == 'arm' and not self.state['armed']:
            self.state['armed'] = True
            self.dirty = True
        self.flush()
        return command == 'stop'

    def deactivated(self):
        if self.state['armed']:
            self.state['deactivations'] += 1
            self.state['selected'] = False
            self.dirty = True
            self.flush()


def _fit_probe_button(window, button, user, ctypes, wintypes, available):
    """Move only the proof fixture HWND; keep the real product button/layout.

    A 768px hosted desktop has just 360 logical pixels at 200%; the product's
    480px minimum cannot fit whole. Native positioning exposes its actual button
    without reducing minimum size, substituting widgets or injecting Qt clicks.
    """
    target = wintypes.RECT()
    owner = wintypes.RECT()
    if not (user.GetWindowRect(int(button.winId()), ctypes.byref(target))
            and user.GetWindowRect(int(window.winId()), ctypes.byref(owner))):
        raise RuntimeError('Probe geometry unavailable')
    left, top, width, height = available
    right, bottom = left + width, top + height
    if target.right - target.left > width or target.bottom - target.top > height:
        raise RuntimeError('Probe action exceeds desktop')
    dx = max(left + 8 - target.left, min(0, right - 8 - target.right))
    dy = max(top + 8 - target.top, min(0, bottom - 16 - target.bottom))
    user.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user.SetWindowPos.restype = wintypes.BOOL
    if (dx or dy) and not user.SetWindowPos(int(window.winId()), 0, owner.left + dx,
                                          owner.top + dy, 0, 0, 0x1 | 0x4 | 0x10):
        raise RuntimeError('Probe action reposition unavailable')
    if not user.GetWindowRect(int(button.winId()), ctypes.byref(target)):
        raise RuntimeError('Probe action geometry unavailable')
    if not (left <= target.left < target.right <= right
            and top <= target.top < target.bottom <= bottom):
        raise RuntimeError('Probe action outside desktop')

def run_source(report_path):
    """Internal owned source fixture; activates no user application."""
    if sys.platform != 'win32':
        return 1
    from PySide6.QtCore import QEvent, QTimer, Qt
    from PySide6.QtGui import QColor, QPainter
    from PySide6.QtWidgets import QApplication, QWidget
    app = QApplication([])
    path = Path(report_path)
    protocol = _SourceProtocol(path)
    state = protocol.state
    class Source(QWidget):
        def event(self, event):
            if event.type() == QEvent.Type.WindowDeactivate and state['armed']:
                protocol.deactivated()
                self.update()
            return super().event(event)

        def paintEvent(self, event):
            painter = QPainter(self)
            painter.fillRect(self.rect(), QColor('white'))
            painter.fillRect(15, 50, self.width() - 30, 35,
                             QColor('#3979b8' if state['selected'] else '#eeeeee'))
            painter.setPen(QColor('black'))
            painter.drawText(20, 30, 'SYNTHETIC SOURCE — active row')
    source = Source()
    source.setWindowTitle('Inkpick owned focus fixture')
    source.resize(240, 160)
    source.move(10, 20)
    source.show()
    state['hwnd'] = int(source.winId())
    protocol.flush()
    def control():
        if protocol.poll():
            app.quit()
    timer = QTimer(source)
    timer.timeout.connect(control)
    timer.start(20)
    QTimer.singleShot(45_000, app.quit)
    return app.exec()



def _retire_fixture(app, fixture, child, job):
    """IPC is best effort; an unreadable command must never bypass owned reap."""
    try:
        if child is not None:
            try:
                _write(str(fixture) + '.control', {'command': 'stop'})
            except OSError:
                pass
            _pump(app, lambda: child.poll() is not None, 2)
    finally:
        try:
            job.close()
        finally:
            if child is not None:
                child.wait(timeout=5)
    return child is not None and child.poll() is not None

def _exercise(app, path):
    from PySide6.QtCore import QObject, Signal, QTimer
    from src.ui.invoice_check_window import InvoiceCheckWindow
    class Controller(QObject):
        state_changed = Signal(str, str)
        results_ready = Signal(object)
        preview_ready = Signal(str, object)
        regions_changed = Signal(object)
        failed = Signal(str)
        capture_started = Signal()
        capture_finished = Signal()
        regions = {'invoice': (10, 10, 100, 100, 1), 'table': (120, 10, 100, 100, 1)}
        busy = False
        state = 'idle'
        is_stopped = True
        calls = 0
        def check_once(self):
            self.calls += 1
            self.busy, self.state = True, 'capturing'
            self.capture_started.emit()
            self.state_changed.emit('capturing', '本次截圖')
            QTimer.singleShot(60, self.finish)
            return True
        def finish(self):
            self.busy, self.state = False, 'ready'
            self.capture_finished.emit()
            self.state_changed.emit('ready', '本次核對完成')
        def cancel(self):
            self.busy = False
        def shutdown(self):
            self.cancel()
    ctypes, wintypes, user = _api()
    controller = Controller()
    window = InvoiceCheckWindow(controller)
    window.resize(348, 480)
    screen = app.primaryScreen()
    geometry = screen.availableGeometry()
    window.move(geometry.right() - window.width() - 12, geometry.top() + 5)
    window.open()
    window.set_ocr_ready(True)
    app.processEvents()
    ratio = screen.devicePixelRatio()
    available = (round(geometry.x() * ratio), round(geometry.y() * ratio),
                 round(geometry.width() * ratio), round(geometry.height() * ratio))
    result = {'dpi200_verified': screen.devicePixelRatio() >= 1.9,
              'main_button_preserved_source': False, 'compact_button_preserved_source': False,
              'fixture_reaped': False, 'deactivation_detector_verified': False,
              'noncheck_native_edit_verified': False}
    fixture = Path(path).with_suffix('.fixture.json')
    job, child = _ChildJob(), None
    old_cursor = wintypes.POINT()
    user.GetCursorPos(ctypes.byref(old_cursor))
    try:
        _fit_probe_button(window, window._check_button, user, ctypes, wintypes, available)
        app.processEvents()
        # Source waits for explicit arm; no focus fixture operation occurs before
        # the subprocess is assigned to our kill-on-close job.
        child = _start_owned(_command('--invoice-focus-source', fixture), job,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if not _pump(app, lambda: bool(_read(fixture).get('hwnd')), 15):
            raise RuntimeError('Fixture startup failed')
        source = _read(fixture)
        hwnd = source['hwnd']
        process_id = wintypes.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
        if process_id.value != source['pid'] or not job.contains_pid(process_id.value):
            raise RuntimeError('Fixture window ownership failed')
        user.AllowSetForegroundWindow(process_id.value)
        user.SetForegroundWindow(hwnd)
        if not _pump(app, lambda: user.GetForegroundWindow() == hwnd):
            raise RuntimeError('Fixture foreground unavailable')
        _write(str(fixture) + '.control', {'command': 'arm'})
        if not _pump(app, lambda: _read(fixture).get('armed') is True):
            raise RuntimeError('Fixture arm failed')
        for index, button in enumerate((window._check_button, window.action_bar.check), 1):
            if not _pump(app, lambda: button.isVisible() and button.isEnabled()):
                raise RuntimeError('Check action unavailable')
            rect = wintypes.RECT()
            if not user.GetWindowRect(int(button.winId()), ctypes.byref(rect)):
                raise RuntimeError('Button physical geometry unavailable')
            x, y = (rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2
            user.SetCursorPos(x, y)
            # Real native input traverses WM_MOUSEACTIVATE and Qt's button path.
            user.mouse_event(0x0002, 0, 0, 0, 0)
            try:
                user.mouse_event(0x0004, 0, 0, 0, 0)
                triggered = _pump(app, lambda: controller.calls == index and not controller.busy)
            finally:
                user.mouse_event(0x0004, 0, 0, 0, 0)
            # Give the other process time to record even transient deactivation.
            _pump(app, lambda: False, .15)
            fixture_state = _read(fixture)
            preserved = (triggered and user.GetForegroundWindow() == hwnd
                         and fixture_state.get('selected') is True
                         and fixture_state.get('deactivations') == 0)
            result['main_button_preserved_source' if index == 1 else
                   'compact_button_preserved_source'] = preserved
        # A real non-check editor click must still activate and accept a key.
        # Do not use open()/activateWindow(): that would make this negative
        # control pass before any user-input path was exercised.
        window.show()
        app.processEvents()
        if user.GetForegroundWindow() != hwnd:
            raise RuntimeError('Passive setup reveal changed foreground')
        editor = window._transparency
        _fit_probe_button(window, editor, user, ctypes, wintypes, available)
        rect = wintypes.RECT()
        if not user.GetWindowRect(int(editor.winId()), ctypes.byref(rect)):
            raise RuntimeError('Editor physical geometry unavailable')
        user.SetCursorPos(rect.left + min(20, (rect.right - rect.left) // 3),
                          (rect.top + rect.bottom) // 2)
        user.mouse_event(0x0002, 0, 0, 0, 0)
        try:
            user.mouse_event(0x0004, 0, 0, 0, 0)
            editing = _pump(app, lambda: user.GetForegroundWindow() == int(window.winId())
                            and editor.hasFocus())
            previous_value = editor.value()
            user.keybd_event(0x28, 0, 0, 0)  # Down: actual focused spin editor.
            user.keybd_event(0x28, 0, 0x2, 0)
            changed = _pump(app, lambda: editor.value() == previous_value - 1)
        finally:
            user.mouse_event(0x0004, 0, 0, 0, 0)
            user.keybd_event(0x28, 0, 0x2, 0)
        result['noncheck_native_edit_verified'] = bool(editing and changed)
        result['deactivation_detector_verified'] = _pump(
            app, lambda: _read(fixture).get('deactivations', 0) > 0
            and _read(fixture).get('selected') is False)
    finally:
        try:
            window.shutdown()
            window.deleteLater()
            user.SetCursorPos(old_cursor.x, old_cursor.y)
        finally:
            result['fixture_reaped'] = _retire_fixture(app, fixture, child, job)
    result['passed'] = all(result.values())
    return result


def run_focus_probe(report_path):
    report = {'schema': 1, 'probe': 'invoice_native_focus', 'passed': False,
              'dpi200_verified': False, 'main_button_preserved_source': False,
              'compact_button_preserved_source': False, 'fixture_reaped': False,
              'deactivation_detector_verified': False, 'noncheck_native_edit_verified': False}
    if sys.platform == 'win32':
        try:
            from PySide6.QtWidgets import QApplication
            app = QApplication.instance() or QApplication([])
            app.setQuitOnLastWindowClosed(False)
            report.update(_exercise(app, report_path))
        except Exception:
            report['passed'] = False
    _write(report_path, report)
    return 0 if report['passed'] else 1


def verify_fresh_child(app, directory):
    """Run 200% before QApplication initialization; validate a strict fixed schema."""
    if sys.platform != 'win32':
        return False
    report = Path(directory) / 'invoice-native-focus.json'
    env = os.environ.copy()
    env['QT_SCALE_FACTOR'] = '2'
    env.pop('QT_SCREEN_SCALE_FACTORS', None)
    env.pop('QT_AUTO_SCREEN_SCALE_FACTOR', None)
    child, job = None, _ChildJob()
    try:
        child = _start_owned(_command('--self-test-invoice-focus', report), job, env=env,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if not _pump(app, lambda: child.poll() is not None, 65):
            return False
        payload = _read(report)
        keys = ('passed', 'dpi200_verified', 'main_button_preserved_source',
                'compact_button_preserved_source', 'fixture_reaped', 'deactivation_detector_verified',
                'noncheck_native_edit_verified')
        return (child.returncode == 0 and type(payload.get('schema')) is int
                and payload.get('schema') == 1
                and payload.get('probe') == 'invoice_native_focus'
                and all(payload.get(key) is True for key in keys))
    finally:
        job.close()
        if child is not None:
            child.wait(timeout=5)
