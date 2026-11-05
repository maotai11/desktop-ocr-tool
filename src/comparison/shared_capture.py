"""Bounded same-frame capture for multiple source fields and one search area."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import blake2b
import sys
from threading import Event

import mss
import numpy as np
from PySide6.QtCore import Signal

from .capture import ComparisonCaptureError, MAX_CAPTURE_PIXELS, validate_region
from ..workers.queue_worker import QueueWorker

MAX_SHARED_REGIONS = 22  # 20 A fields, B and optional table tracking.
MAX_SHARED_PIXELS = 36_000_000


def validate_regions(regions):
    if not isinstance(regions, dict) or not 1 <= len(regions) <= MAX_SHARED_REGIONS:
        raise ComparisonCaptureError('請設定辨識範圍，最多可使用 20 個 A 欄位。')
    result = {}
    for name, region in regions.items():
        if not isinstance(name, str) or not name or len(name) > 80:
            raise ComparisonCaptureError('辨識範圍名稱無效。')
        result[name] = validate_region(region)
    if len({region[4] for region in result.values()}) != 1:
        raise ComparisonCaptureError('所有範圍請選在同一個螢幕。')
    if sum(region[2] * region[3] for region in result.values()) > MAX_SHARED_PIXELS:
        raise ComparisonCaptureError('辨識範圍總面積過大，請縮小範圍。')
    return result


def fingerprint_image(image):
    pixels = np.ascontiguousarray(image)
    digest = blake2b(digest_size=32)
    digest.update(str((pixels.shape, pixels.dtype.str)).encode('ascii'))
    digest.update(memoryview(pixels).cast('B'))
    return digest.digest()


def capture_shared(regions, expected_layout=None):
    """One union grab; independent BGR crops, with no content written to disk."""
    regions = validate_regions(regions)
    if sys.platform == 'win32':
        # Synchronize the same production capture path with the desktop
        # compositor after the owner-thread hide/settle step. Never grab the
        # previous overlay frame merely because the machine is under load.
        try:
            import ctypes
            flush = ctypes.WinDLL('dwmapi').DwmFlush
            flush.argtypes = []
            flush.restype = ctypes.c_long
            if flush() < 0:
                raise OSError('Desktop compositor did not synchronize')
        except (OSError, AttributeError):
            raise ComparisonCaptureError('畫面尚未完成更新，請稍後再試。') from None
    with mss.mss() as screen:
        monitors = screen.monitors
        layout = tuple(tuple(int(item[key]) for key in ('left', 'top', 'width', 'height'))
                       for item in monitors[1:])
        if expected_layout is not None and layout != expected_layout:
            raise ComparisonCaptureError('螢幕配置已變更，請重新設定範圍。', layout_changed=True)
        monitor_index = next(iter(regions.values()))[4]
        if not 1 <= monitor_index < len(monitors):
            raise ComparisonCaptureError('原本的螢幕已無法使用，請重新設定範圍。', layout_changed=True)
        monitor = monitors[monitor_index]
        for x, y, width, height, _ in regions.values():
            if x + width > monitor['width'] or y + height > monitor['height']:
                raise ComparisonCaptureError('辨識範圍超出螢幕，請重新設定。', layout_changed=True)
        left = min(region[0] for region in regions.values())
        top = min(region[1] for region in regions.values())
        right = max(region[0] + region[2] for region in regions.values())
        bottom = max(region[1] + region[3] for region in regions.values())
        if (right - left) * (bottom - top) > MAX_CAPTURE_PIXELS:
            raise ComparisonCaptureError('範圍相距過遠，請縮小或拉近選取範圍。')
        shot = screen.grab({'left': monitor['left'] + left, 'top': monitor['top'] + top,
                            'width': right - left, 'height': bottom - top})
        pixels = np.asarray(shot)
        if pixels.dtype != np.uint8 or pixels.shape != (bottom - top, right - left, 4):
            raise ComparisonCaptureError('未能取得完整畫面，請稍後再試。')
        images = {name: pixels[y - top:y - top + height, x - left:x - left + width, :3].copy()
                  for name, (x, y, width, height, _) in regions.items()}
        return images, layout


@dataclass(frozen=True)
class SharedCaptureTask:
    token: str
    regions: dict
    layout: tuple | None
    cancelled: Event
    processor: object = None


class SharedCaptureWorker(QueueWorker):
    completed = Signal(str, object, object, object)
    failed = Signal(str, str, bool)
    analyzed = Signal(str, object)
    supports_analysis = True

    def __init__(self, parent=None, *, capture=capture_shared):
        super().__init__(parent, capacity=1)
        self._capture = capture

    def capture(self, token, regions, layout, cancelled, *, processor=None):
        self.submit(SharedCaptureTask(token, validate_regions(regions), layout, cancelled, processor))

    def run(self):
        for task in self.tasks():
            images = layout = hashes = analysis = None
            try:
                if not task.cancelled.is_set():
                    images, layout = self._capture(task.regions, task.layout)
                    if not task.cancelled.is_set():
                        hashes = {name: fingerprint_image(image) for name, image in images.items()}
                    if not task.cancelled.is_set() and task.processor is not None:
                        analysis = task.processor(images)
                if task.cancelled.is_set():
                    images = layout = hashes = None
                elif task.processor is not None:
                    self.analyzed.emit(task.token, analysis)
                self.completed.emit(task.token, images, layout, hashes)
            except Exception as exc:  # noqa: BLE001 - never expose pixels/native paths
                if task.cancelled.is_set():
                    self.completed.emit(task.token, None, None, None)
                else:
                    message = str(exc) if isinstance(exc, ComparisonCaptureError) else '未能擷取畫面，請稍後再試。'
                    self.failed.emit(task.token, message,
                                     isinstance(exc, ComparisonCaptureError) and exc.layout_changed)
            finally:
                images = layout = hashes = analysis = None
