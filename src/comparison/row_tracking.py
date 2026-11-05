"""Pure row geometry and calibrated, conservative highlight tracking.

All regions are physical monitor-relative ``(x, y, width, height, monitor)``.
The tracker observes only caller-provided table pixels; it cannot capture,
click, scroll, start timers, or interact with another application.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from hashlib import blake2b

import cv2
import numpy as np

MAX_PIXELS = 16_000_000
MAX_FIELDS = 20


def _region(value, *, pixel_bound=True):
    if (not isinstance(value, (tuple, list)) or len(value) != 5
            or any(type(item) is not int for item in value)):
        raise ValueError('框選位置無效。')
    x, y, width, height, monitor = value
    if (x < 0 or y < 0 or width < 5 or height < 5 or monitor < 1
            or (pixel_bound and width * height > MAX_PIXELS)):
        raise ValueError('框選範圍太小、太大或超出螢幕。')
    return tuple(value)


def _contains(outer, inner):
    return (outer[4] == inner[4] and outer[0] <= inner[0] and outer[1] <= inner[1]
            and inner[0] + inner[2] <= outer[0] + outer[2]
            and inner[1] + inner[3] <= outer[1] + outer[3])


def _overlap(first, second):
    return (first[4] == second[4] and first[0] < second[0] + second[2]
            and second[0] < first[0] + first[2] and first[1] < second[1] + second[3]
            and second[1] < first[1] + first[3])


def translate_regions(regions, delta_y, *, table_region, b_region, monitor_bounds=None):
    """Translate every A by the same delta, or raise without changing inputs.

    ``monitor_bounds`` optionally gives ``(width, height)`` for this monitor,
    or a five-tuple rectangle in the same coordinate system.
    """
    if type(delta_y) is not int:
        raise ValueError('列位移必須是整數像素。')
    if not isinstance(regions, dict) or not 1 <= len(regions) <= MAX_FIELDS:
        raise ValueError('請設定 1 至 20 個 A 欄位。')
    table, target = _region(table_region, pixel_bound=False), _region(b_region)
    if table[4] != target[4]:
        raise ValueError('表格與 B 必須位於同一螢幕。')
    bounds = None
    if monitor_bounds is not None:
        if isinstance(monitor_bounds, (tuple, list)) and len(monitor_bounds) == 2:
            bounds = _region((0, 0, *monitor_bounds, table[4]), pixel_bound=False)
        else:
            bounds = _region(monitor_bounds, pixel_bound=False)
        if not _contains(bounds, table) or not _contains(bounds, target):
            raise ValueError('表格或 B 超出螢幕。')
    output = {}
    for name, value in regions.items():
        old = _region(value)
        moved = _region((old[0], old[1] + delta_y, old[2], old[3], old[4]))
        if not _contains(table, moved):
            raise ValueError('下一列超出表格可見範圍，請捲動後重新定位。')
        if _overlap(moved, target):
            raise ValueError('A 與 B 的辨識範圍不可重疊。')
        output[name] = moved
    return output


@dataclass(frozen=True)
class RowTemplate:
    """Whole-row template with explicit pitch and per-field relative offsets."""

    table_region: tuple
    baseline_row_region: tuple
    regions: dict
    b_region: tuple
    row_pitch: int

    def __post_init__(self):
        table = _region(self.table_region, pixel_bound=False)
        baseline = _region(self.baseline_row_region)
        if type(self.row_pitch) is not int or not 1 <= self.row_pitch <= 2000:
            raise ValueError('列間距必須介於 1 與 2000 像素。')
        if not _contains(table, baseline):
            raise ValueError('基準列必須位於表格內。')
        copied = translate_regions(self.regions, 0, table_region=table, b_region=self.b_region)
        if any(not _contains(baseline, region) for region in copied.values()):
            raise ValueError('所有 A 欄位必須位於同一個基準列。')
        object.__setattr__(self, 'table_region', table)
        object.__setattr__(self, 'baseline_row_region', baseline)
        object.__setattr__(self, 'b_region', _region(self.b_region))
        object.__setattr__(self, 'regions', copied)

    @property
    def offsets(self):
        x, y = self.baseline_row_region[:2]
        return {name: (region[0] - x, region[1] - y, region[2], region[3])
                for name, region in self.regions.items()}

    def at_offset(self, delta_y, *, monitor_bounds=None):
        return translate_regions(self.regions, delta_y, table_region=self.table_region,
                                 b_region=self.b_region, monitor_bounds=monitor_bounds)

    def at_row(self, row_index, *, monitor_bounds=None):
        if type(row_index) is not int:
            raise ValueError('列索引必須是整數。')
        return self.at_offset(row_index * self.row_pitch, monitor_bounds=monitor_bounds)


class RowTracker:
    """Follow one calibrated colored row; ambiguous/lost frames clear trust."""

    def __init__(self, table_region, baseline_row_region, *, min_width_ratio=0.55, stable_frames=2):
        self.table_region = _region(table_region)
        self.baseline_row_region = _region(baseline_row_region)
        if not _contains(self.table_region, self.baseline_row_region):
            raise ValueError('基準列必須位於表格內。')
        if (isinstance(min_width_ratio, bool) or not isinstance(min_width_ratio, (int, float))
                or not math.isfinite(min_width_ratio) or not 0.5 <= min_width_ratio <= 1):
            raise ValueError('最小色帶寬度比例必須介於 0.5 與 1。')
        if type(stable_frames) is not int or not 2 <= stable_frames <= 10:
            raise ValueError('至少需要兩個穩定畫面。')
        self.min_width_ratio = float(min_width_ratio)
        self.stable_frames = stable_frames
        self._color = None
        self._baseline_top = None
        self._height = None
        self._pending_row = None
        self._count = 0
        self._last_fingerprint = None

    def _pixels(self, image):
        expected = (self.table_region[3], self.table_region[2], 3)
        if not isinstance(image, np.ndarray) or image.dtype != np.uint8 or image.shape != expected:
            raise ValueError('表格影像與框選範圍不符，請重新校準。')
        return image

    def _result(self, status, row=None, fingerprint=None, reason=''):
        return {'status': status, 'row': row,
                'delta_y': row[1] - self._baseline_top if row is not None and self._baseline_top is not None else None,
                'fingerprint': fingerprint, 'content_changed': False, 'reason': reason}

    def _reset_pending(self):
        self._pending_row = None
        self._count = 0

    def _mask(self, hsv):
        hue, saturation, value = self._color
        difference = np.abs(hsv[:, :, 0].astype(np.int16) - hue)
        difference = np.minimum(difference, 180 - difference)
        return ((difference <= 10) & (hsv[:, :, 1] >= 45)
                & (np.abs(hsv[:, :, 1].astype(np.int16) - saturation) <= 70)
                & (np.abs(hsv[:, :, 2].astype(np.int16) - value) <= 75))

    def _bands(self, mask):
        width = mask.shape[1]
        qualifying = mask.sum(axis=1) >= math.ceil(width * self.min_width_ratio)
        # Fill at most one scanline obscured by a border or text. Broad gaps
        # must remain separate candidates instead of merging selected rows.
        if len(qualifying) > 2:
            qualifying[1:-1] |= qualifying[:-2] & qualifying[2:]
        changes = np.diff(np.concatenate(([False], qualifying, [False])).astype(np.int8))
        starts, ends = np.flatnonzero(changes == 1), np.flatnonzero(changes == -1)
        bands = []
        for top, bottom in zip(starts, ends):
            height = int(bottom - top)
            if height < 5:
                continue
            horizontal = mask[top:bottom].sum(axis=0) >= max(2, height * .35)
            positions = np.flatnonzero(horizontal)
            if len(positions) < width * self.min_width_ratio:
                continue
            # Require broad support across the table, not one colored icon.
            if positions[-1] - positions[0] + 1 < width * self.min_width_ratio:
                continue
            bands.append((int(top), height))
        return bands

    def calibrate(self, table_bgr):
        """Learn the user's chosen row color, rejecting gray or other rows."""
        pixels = self._pixels(table_bgr)
        self._color = self._baseline_top = self._height = None
        self._last_fingerprint = None
        self._reset_pending()
        hsv = cv2.cvtColor(pixels, cv2.COLOR_BGR2HSV)
        top = self.baseline_row_region[1] - self.table_region[1]
        height = self.baseline_row_region[3]
        selected = hsv[top:top + height]
        colored = selected[(selected[:, :, 1] >= 45) & (selected[:, :, 2] >= 45)]
        if len(colored) < selected.shape[0] * selected.shape[1] * .35:
            return self._result('lost', reason='基準列沒有足夠清楚的彩色背景，請選取該列後重新校準。')
        # Quantized hue histogram prevents unrelated colored text from
        # averaging into a color absent from the actual selected background.
        histogram = np.bincount((colored[:, 0] // 6).astype(np.int32), minlength=30)
        dominant = int(np.argmax(histogram))
        sample = colored[colored[:, 0] // 6 == dominant]
        self._color = tuple(int(value) for value in np.median(sample, axis=0))
        bands = self._bands(self._mask(hsv))
        center = top + height / 2
        selected_bands = [(y, h) for y, h in bands if y <= center < y + h]
        if len(bands) != 1 or len(selected_bands) != 1:
            self._color = None
            return self._result('ambiguous' if len(bands) > 1 else 'lost',
                                reason='無法唯一辨認基準色帶，請縮小表格範圍或重新選取一列。')
        y, self._height = selected_bands[0]
        if self._height > max(height * 4, 100):
            self._color = self._height = None
            return self._result('lost', reason='色帶範圍過高，無法確認是單一選取列，請重新校準。')
        self._baseline_top = self.table_region[1] + y
        row = (self.table_region[0], self._baseline_top, self.table_region[2], self._height, self.table_region[4])
        return self._result('pending', row, reason='已校準，等待兩個穩定畫面。')

    def update(self, table_bgr):
        pixels = self._pixels(table_bgr)
        if self._color is None:
            return self._result('lost', reason='請先校準目前選取列的彩色背景。')
        bands = self._bands(self._mask(cv2.cvtColor(pixels, cv2.COLOR_BGR2HSV)))
        if len(bands) != 1:
            self._reset_pending()
            return self._result('ambiguous' if len(bands) > 1 else 'lost',
                                reason='找到多個相似色帶。' if len(bands) > 1 else '目前選取列已無法辨認。')
        top, height = bands[0]
        if height != self._height:
            self._reset_pending()
            return self._result('lost', reason='選取列高度已改變，請重新校準。')
        # Partial rows at viewport edges cannot establish full A coordinates.
        if (top == 0 or top + height == pixels.shape[0]) and height < self._height:
            self._reset_pending()
            return self._result('lost', reason='選取列超出可見表格，請捲動後重新校準。')
        row = (self.table_region[0], self.table_region[1] + top,
               self.table_region[2], height, self.table_region[4])
        fingerprint = blake2b(np.ascontiguousarray(pixels[top:top + height]).tobytes(), digest_size=16).hexdigest()
        if row != self._pending_row:
            self._pending_row = row
            self._count = 1
        else:
            self._count += 1
        if self._count < self.stable_frames:
            return self._result('pending', row, fingerprint, '選取列移動中，等待穩定。')
        result = self._result('stable', row, fingerprint)
        result['content_changed'] = self._last_fingerprint != fingerprint
        self._last_fingerprint = fingerprint
        return result
