"""Capture both comparison crops from one bounded, thread-owned MSS grab."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import blake2b
from threading import Event

import mss
import numpy as np
from PySide6.QtCore import Signal

from ..workers.queue_worker import QueueWorker

MAX_REGION_PIXELS = 16_000_000
MAX_CAPTURE_PIXELS = 36_000_000


def fingerprint_pair(images):
    """Hash exact target pixels off the UI thread, retaining no image cache."""
    digest = blake2b(digest_size=32)
    for name in ('A', 'B'):
        pixels = np.ascontiguousarray(images[name])
        digest.update(str((name, pixels.shape, pixels.dtype.str)).encode('ascii'))
        digest.update(memoryview(pixels).cast('B'))
    return digest.digest()


class ComparisonCaptureError(ValueError):
    def __init__(self, message, *, layout_changed=False):
        super().__init__(message)
        self.layout_changed = layout_changed


def validate_region(value):
    if (not isinstance(value, (tuple, list)) or len(value) != 5
            or any(type(number) is not int for number in value)):
        raise ComparisonCaptureError('框選位置無效，請重新框選。')
    x, y, width, height, monitor = value
    if (x < 0 or y < 0 or width < 5 or height < 5 or monitor < 1
            or width * height > MAX_REGION_PIXELS):
        raise ComparisonCaptureError('框選範圍太小、太大或超出螢幕，請重新框選。')
    return tuple(value)


def regions_overlap(first, second):
    ax, ay, aw, ah, am = first
    bx, by, bw, bh, bm = second
    return am == bm and ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


def capture_pair(regions, expected_layout=None):
    """Return independent BGR rasters and a display-layout fingerprint.

    The union rectangle is captured once, then cropped in memory. Neither
    screen images nor recognized text are written to disk.
    """
    first, second = (validate_region(regions[name]) for name in ('A', 'B'))
    if first[4] != second[4]:
        raise ComparisonCaptureError('A、B 請框選在同一個螢幕。')
    if regions_overlap(first, second):
        raise ComparisonCaptureError('A、B 的辨識範圍不可重疊，請重新框選。')
    with mss.mss() as screen:
        monitors = screen.monitors
        layout = tuple(tuple(int(item[key]) for key in ('left', 'top', 'width', 'height'))
                       for item in monitors[1:])
        if expected_layout is not None and layout != expected_layout:
            raise ComparisonCaptureError('螢幕配置已變更，請重新框選 A 與 B。', layout_changed=True)
        monitor_index = first[4]
        if not 1 <= monitor_index < len(monitors):
            raise ComparisonCaptureError('原本的螢幕已無法使用，請重新框選 A 與 B。', layout_changed=True)
        monitor = monitors[monitor_index]
        for x, y, width, height, _ in (first, second):
            if x + width > monitor['width'] or y + height > monitor['height']:
                raise ComparisonCaptureError('辨識範圍超出螢幕，請重新框選 A 與 B。', layout_changed=True)
        left, top = min(first[0], second[0]), min(first[1], second[1])
        right = max(first[0] + first[2], second[0] + second[2])
        bottom = max(first[1] + first[3], second[1] + second[3])
        if (right - left) * (bottom - top) > MAX_CAPTURE_PIXELS:
            raise ComparisonCaptureError('A、B 合併範圍過大，請縮小框選或拉近位置。')
        screenshot = screen.grab({'left': monitor['left'] + left, 'top': monitor['top'] + top,
                                  'width': right - left, 'height': bottom - top})
        pixels = np.asarray(screenshot)
        if pixels.shape != (bottom - top, right - left, 4) or pixels.dtype != np.uint8:
            raise ComparisonCaptureError('未能取得完整畫面，請稍後再試。')
        images = {}
        for name, (x, y, width, height, _) in zip(('A', 'B'), (first, second)):
            images[name] = pixels[y - top:y - top + height, x - left:x - left + width, :3].copy()
        return images, layout


@dataclass(frozen=True)
class CaptureTask:
    token: str
    regions: dict
    layout: tuple | None
    cancelled: Event


class ComparisonCaptureWorker(QueueWorker):
    completed = Signal(str, object, object, object)
    failed = Signal(str, str, bool)

    def __init__(self, parent=None, *, capture=capture_pair):
        super().__init__(parent, capacity=1)
        self._capture = capture

    def capture(self, token, regions, layout, cancelled):
        self.submit(CaptureTask(token, dict(regions), layout, cancelled))

    def run(self):
        for task in self.tasks():
            images = layout = fingerprint = None
            try:
                if not task.cancelled.is_set():
                    images, layout = self._capture(task.regions, task.layout)
                    if not task.cancelled.is_set():
                        fingerprint = fingerprint_pair(images)
                if task.cancelled.is_set():
                    images = layout = fingerprint = None
                self.completed.emit(task.token, images, layout, fingerprint)
            except Exception as exc:  # noqa: BLE001 - native capture errors must not leak paths or pixels
                if task.cancelled.is_set():
                    self.completed.emit(task.token, None, None, None)
                else:
                    message = str(exc) if isinstance(exc, ComparisonCaptureError) else '未能擷取畫面，請重新框選後再試。'
                    changed = isinstance(exc, ComparisonCaptureError) and exc.layout_changed
                    self.failed.emit(task.token, message, changed)
            finally:
                images = layout = fingerprint = None
