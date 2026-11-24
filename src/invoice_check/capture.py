"""One-shot, memory-only capture of regions from one physical screen frame."""
from __future__ import annotations

import time
from threading import Event

import numpy as np
from PySide6.QtCore import QObject, QThread, Signal, Slot

from ..capture.backend_mss import MssBackend
from ..core.resource_budget import ResourceBudgetExceeded, memory_preflight

MAX_PAIR_PIXELS = 6_000_000


def validate_region(region):
    if (not isinstance(region, (tuple, list)) or len(region) != 5
            or any(type(value) is not int for value in region)):
        raise ValueError('請重新框選有效範圍。')
    x, y, width, height, monitor = region
    if (x < 0 or y < 0 or width < 5 or height < 5 or monitor < 1
            or width > 8192 or height > 8192 or width * height > MAX_PAIR_PIXELS):
        raise ValueError('框選範圍過大或無效，請縮小後重試。')
    return tuple(region)


def capture_regions(regions, *, backend_factory=MssBackend, cancel_event=None,
                    expected_monitor=None):
    """Capture one union rectangle, then detach each requested BGR crop.

    No file manager, history, clipboard or per-side capture is involved. The
    optional cancellation event is checked around the indivisible native grab.
    """
    if not isinstance(regions, dict) or not regions or set(regions) - {'invoice', 'table'}:
        raise ValueError('請選擇發票或資料表範圍。')
    selected = {side: validate_region(region) for side, region in regions.items()}
    monitors = {region[4] for region in selected.values()}
    if len(monitors) != 1:
        raise ValueError('左右範圍請選在同一個螢幕，才能擷取同一時刻畫面。')
    left = min(r[0] for r in selected.values())
    top = min(r[1] for r in selected.values())
    right = max(r[0] + r[2] for r in selected.values())
    bottom = max(r[1] + r[3] for r in selected.values())
    if (right - left) * (bottom - top) > MAX_PAIR_PIXELS:
        raise ValueError('左右範圍合計過大，請縮小範圍後重試。')
    if cancel_event is not None and cancel_event.is_set():
        return None
    # Account for the native BGRA grab, NumPy transfer and detached ROI copies
    # before any pixel buffer is allocated. An earlier pair retained for the
    # final verification is already included in the live process-tree sample.
    pixels = (right-left) * (bottom-top)
    roi_pixels = sum(region[2] * region[3] for region in selected.values())
    memory_preflight(additional_bytes=pixels*8 + roi_pixels*4)
    backend = backend_factory()
    try:
        if expected_monitor is not None:
            info = getattr(backend, 'get_monitor_info', None)
            if info is None:
                raise ValueError('無法確認主要螢幕，請重新框選。')
            actual = info(next(iter(monitors)))
            keys = ('left', 'top', 'width', 'height')
            if (any(type(expected_monitor.get(key)) is not int for key in keys)
                    or any(actual.get(key) != expected_monitor[key] for key in keys)):
                raise ValueError('螢幕配置已變更或索引不同，請將目標程式移至主要螢幕後重新框選。')
        frame = backend.capture_region(left, top, right-left, bottom-top, next(iter(monitors)))
    finally:
        close = getattr(backend, 'close', None)
        if close is not None:
            close()
    if cancel_event is not None and cancel_event.is_set():
        return None
    if (not isinstance(frame, np.ndarray) or frame.dtype != np.uint8
            or frame.shape != (bottom-top, right-left, 3)):
        raise ValueError('未能取得完整畫面，請確認範圍後重試。')
    images = {}
    for side, (x, y, width, height, _) in selected.items():
        images[side] = frame[y-top:y-top+height, x-left:x-left+width].copy()
    return {'regions': selected, 'images': images, 'captured_at_ns': time.time_ns()}


class _CaptureJob(QThread):
    def __init__(self, token, regions, cancel_event, backend_factory, expected_monitor, parent):
        super().__init__(parent)
        self.token = token
        self.regions = regions
        self.cancel_event = cancel_event
        self.backend_factory = backend_factory
        self.expected_monitor = expected_monitor
        self.payload = None

    def run(self):
        try:
            self.payload = capture_regions(self.regions, backend_factory=self.backend_factory,
                                           cancel_event=self.cancel_event,
                                           expected_monitor=self.expected_monitor)
        except ResourceBudgetExceeded as exc:
            self.payload = {'error': str(exc)}
        except ValueError as exc:
            self.payload = {'error': str(exc)}
        except Exception:
            # Backend exception text may contain local details. Only the
            # controlled message crosses into the UI or diagnostics.
            self.payload = {'error': '擷取畫面未完成，請重新辨識。'}


def primary_monitor_geometry():
    """Read on the GUI thread for comparison with the capture backend."""
    from PySide6.QtGui import QGuiApplication
    screen = QGuiApplication.primaryScreen()
    if screen is None:
        return None
    rect, scale = screen.geometry(), screen.devicePixelRatio()
    return {'left': round(rect.x()*scale), 'top': round(rect.y()*scale),
            'width': round(rect.width()*scale), 'height': round(rect.height()*scale)}


class PairCaptureService(QObject):
    """Own at most one finite capture thread; no idle thread or polling timer."""
    finished = Signal(str, object)
    stopped = Signal()

    def __init__(self, parent=None, *, backend_factory=MssBackend, expected_monitor=None):
        super().__init__(parent)
        self._backend_factory = backend_factory
        self._expected_monitor = expected_monitor
        self._job = None
        self._closed = False

    @property
    def busy(self):
        return self._job is not None

    def request(self, token, regions, cancel_event=None):
        if self._closed or self.busy or not isinstance(token, str) or not token:
            return False
        if not isinstance(regions, dict) or not regions:
            return False
        event = cancel_event if cancel_event is not None else Event()
        if not isinstance(event, Event):
            return False
        expected = self._expected_monitor
        if expected is None:
            expected = primary_monitor_geometry()
            if expected is None:
                return False
        job = _CaptureJob(token, dict(regions), event, self._backend_factory, dict(expected), self)
        self._job = job
        job.finished.connect(self._finished)
        job.start()
        return True

    @Slot()
    def _finished(self):
        job = self._job
        if job is None:
            return
        # This slot runs after native completion. Reap ownership before a
        # subscriber is allowed to request another capture synchronously.
        job.wait()
        self._job = None
        token, payload = job.token, job.payload
        job.payload = None
        job.regions = {}
        job.deleteLater()
        self.finished.emit(token, payload)
        if self._closed and not self.busy:
            self.stopped.emit()

    def cancel(self):
        if self._job is not None:
            self._job.cancel_event.set()

    def shutdown(self):
        if self._closed:
            return
        self._closed = True
        self.cancel()
        if not self.busy:
            self.stopped.emit()
