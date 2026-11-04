"""One paired snapshot and at most one transient OCR crop at a time."""
from __future__ import annotations

import unicodedata
from datetime import datetime, timezone
from threading import Event
from time import monotonic
from uuid import uuid4

from PySide6.QtCore import QObject, QTimer, Signal

from .capture import (
    ComparisonCaptureError,
    ComparisonCaptureWorker,
    regions_overlap,
    validate_region,
)


def normalize_text(text, mode='normalized'):
    if mode == 'exact':
        return text
    return ''.join(character for character in unicodedata.normalize('NFC', text)
                   if unicodedata.category(character)[0] in 'LNM')


def compare_results(first, second, mode='normalized'):
    text_a, text_b = first.get('text', ''), second.get('text', '')
    a, b = normalize_text(text_a, mode), normalize_text(text_b, mode)
    # Empty, rejected and low-confidence OCR are never proof of agreement.
    if (not a.strip() or not b.strip()
            or first.get('status') not in ('done', 'confirmed')
            or second.get('status') not in ('done', 'confirmed')):
        return 'uncertain'
    if mode == 'normalized' and any(
            not any(unicodedata.category(character)[0] in 'LN' for character in text)
            for text in (a, b)):
        return 'uncertain'
    return 'match' if a == b else 'mismatch'


class ComparisonController(QObject):
    updated = Signal(object)
    state_changed = Signal(str)
    failed = Signal(str)
    stopped = Signal()
    groups_changed = Signal()
    group_changed = Signal()
    MAX_GROUPS = 20

    def __init__(self, ocr_worker, parent=None, *, capture_worker=None, cycle_timeout_ms=30_000):
        super().__init__(parent)
        if type(cycle_timeout_ms) is not int or cycle_timeout_ms < 1:
            raise ValueError('Cycle timeout must be a positive integer')
        self._cycle_timeout_ms = cycle_timeout_ms
        self._ocr = ocr_worker
        self._capture = capture_worker or ComparisonCaptureWorker(self)
        self._capture.completed.connect(self._captured)
        self._capture.failed.connect(self._capture_failed)
        self._capture.finished.connect(self.stopped)
        self._ocr.document_ocr_done.connect(self._recognized)
        first_id = uuid4().hex
        self._groups = {first_id: self._new_group(first_id, '第 1 組')}
        self.active_group_id = first_id
        self._run_group_ids = ()
        self._run_index = 0
        self._scan_group_id = None
        self._waiting_between_rounds = False
        self.running = False
        self.mode = 'normalized'
        self.interval = 2
        self.before_capture = None
        self._closing = False
        self._layout = None
        self._token = None
        self._capture_token = None
        self._event = Event()
        self._images = {}
        self._results = {}
        self._last_fingerprint = None
        self._pending_fingerprint = None
        self._pending_name = None
        self._inflight_name = None
        self._cycle = 0
        self._cycle_started = None
        self._cycle_timestamp = None
        self._retry_count = 0
        self._cycle_timer = QTimer(self)
        self._cycle_timer.setSingleShot(True)
        self._cycle_timer.timeout.connect(self._begin_cycle)
        self._retry = QTimer(self)
        self._retry.setInterval(100)
        self._retry.timeout.connect(self._submit_crop)
        self._deadline = QTimer(self)
        self._deadline.setSingleShot(True)
        self._deadline.setInterval(cycle_timeout_ms)
        self._deadline.timeout.connect(self._timed_out)

    @property
    def is_stopped(self):
        return not self._capture.isRunning()

    @staticmethod
    def _new_group(group_id, name):
        return {'id': group_id, 'name': name, 'regions': {}, 'result': None,
                'fingerprint': None, 'cycle': 0}

    @property
    def groups(self):
        """Bounded UI snapshots; callers cannot mutate scheduling or positions."""
        return [{'id': group['id'], 'name': group['name'], 'regions': dict(group['regions']),
                 'result': dict(group['result']) if group['result'] is not None else None}
                for group in self._groups.values()]

    @property
    def regions(self):
        return self._groups[self.active_group_id]['regions']

    @regions.setter
    def regions(self, value):
        # Kept for the original launcher integration; regular edits use
        # set_region so cancellation and bounds checking remain explicit.
        self._groups[self.active_group_id]['regions'] = value

    @property
    def target_regions(self):
        """All configured targets, including groups not selected in the UI."""
        return tuple(region for group in self._groups.values() for region in group['regions'].values())

    @property
    def scanning_group_id(self):
        return self._scan_group_id if self.running else None

    def _valid_group_name(self, value, *, replacing=None):
        if not isinstance(value, str):
            return None
        value = value.strip()
        if (not value or len(value) > 32
                or any(unicodedata.category(character)[0] == 'C'
                       or unicodedata.category(character) in ('Zl', 'Zp') for character in value)
                or any(group['name'].casefold() == value.casefold()
                       for key, group in self._groups.items() if key != replacing)):
            return None
        return value

    def add_group(self, name=None):
        if self._closing:
            return None
        if len(self._groups) >= self.MAX_GROUPS:
            self.failed.emit('最多可新增 20 組核對。')
            return None
        if name is None:
            names = {group['name'] for group in self._groups.values()}
            name = next(f'第 {number} 組' for number in range(1, self.MAX_GROUPS + 2)
                        if f'第 {number} 組' not in names)
        name = self._valid_group_name(name)
        if name is None:
            self.failed.emit('組別名稱請填 1–32 字，且不可重複或換行。')
            return None
        self.pause()
        group_id = uuid4().hex
        self._groups[group_id] = self._new_group(group_id, name)
        self.active_group_id = group_id
        self.groups_changed.emit()
        self.group_changed.emit()
        return group_id

    def select_group(self, group_id):
        if self._closing or group_id not in self._groups:
            return False
        if self.active_group_id != group_id:
            self.active_group_id = group_id
            self.group_changed.emit()
        return True

    def rename_group(self, group_id, name):
        if self._closing or group_id not in self._groups:
            return False
        name = self._valid_group_name(name, replacing=group_id)
        if name is None:
            self.failed.emit('組別名稱請填 1–32 字，且不可重複或換行。')
            return False
        self.pause()
        group = self._groups[group_id]
        group['name'] = name
        if group['result'] is not None:
            group['result']['group_name'] = name
        self.groups_changed.emit()
        return True

    def remove_group(self, group_id):
        if self._closing or group_id not in self._groups:
            return False
        if len(self._groups) == 1:
            self.failed.emit('請至少保留一組核對。')
            return False
        self.pause()
        del self._groups[group_id]
        if self.active_group_id == group_id:
            self.active_group_id = next(iter(self._groups))
        self.groups_changed.emit()
        self.group_changed.emit()
        return True

    def set_region(self, name, region):
        if self._closing:
            return False
        self.pause()
        try:
            if name not in ('A', 'B'):
                raise ComparisonCaptureError('請選擇 A 或 B。')
            region = validate_region(region)
            other = self.regions.get('B' if name == 'A' else 'A')
            if other and (other[4] != region[4] or regions_overlap(region, other)):
                raise ComparisonCaptureError('A、B 請框選在同一個螢幕，且範圍不可重疊。')
        except ComparisonCaptureError as exc:
            self.failed.emit(str(exc))
            return False
        self.regions[name] = region
        group = self._groups[self.active_group_id]
        group['result'], group['cycle'] = None, 0
        self._layout = None
        self.groups_changed.emit()
        self.state_changed.emit('paused')
        return True

    def set_interval(self, seconds):
        if type(seconds) is not int or not 1 <= seconds <= 60:
            return False
        self.interval = seconds
        if self._cycle_timer.isActive() and self._waiting_between_rounds:
            self._cycle_timer.start(seconds * 1000)
        return True

    def set_mode(self, mode):
        if mode not in ('normalized', 'exact'):
            return False
        if self.mode != mode:
            self.pause()
            self.mode = mode
            for group in self._groups.values():
                group['result'], group['cycle'] = None, 0
            self.groups_changed.emit()
        return True

    def invalidate_regions(self, message='螢幕配置已變更，請重新框選 A 與 B。'):
        self.pause()
        for group in self._groups.values():
            group['regions'].clear()
            group['result'], group['cycle'] = None, 0
        self._layout = None
        self.groups_changed.emit()
        self.state_changed.emit('paused')
        if message:
            self.failed.emit(message)

    def start(self):
        if self._closing or self.running:
            return False
        for group in self._groups.values():
            if set(group['regions']) != {'A', 'B'}:
                self.failed.emit('請先框選 A 與 B。' if len(self._groups) == 1
                                 else f'「{group["name"]}」尚未選好 A、B 範圍。')
                return False
        self._run_group_ids = tuple(self._groups)
        self._run_index = 0
        self.running = True
        self.state_changed.emit('running')
        self._begin_cycle()
        return self.running

    def pause(self):
        self.running = False
        self._cycle_timer.stop()
        self._retry.stop()
        self._deadline.stop()
        self._cycle_started = self._cycle_timestamp = None
        self._event.set()
        self._token = None
        self._images.clear()
        self._results.clear()
        self._last_fingerprint = self._pending_fingerprint = None
        for group in self._groups.values():
            group['fingerprint'] = None
        self._run_group_ids = ()
        self._run_index = 0
        self._scan_group_id = None
        self._waiting_between_rounds = False
        self._pending_name = self._inflight_name = None
        self.state_changed.emit('paused')

    def _begin_cycle(self):
        if not self.running or self._closing:
            return
        if self._cycle_started is None:
            # Waiting for a cancelled native grab is part of this start
            # attempt too. A retry must not refresh the timeout indefinitely.
            self._cycle_started = monotonic()
            self._deadline.start()
        if self._expired():
            return
        if self._capture_token is not None:
            # A cancelled grab still owns its worker until its completion is
            # delivered. Never accumulate another capture behind it.
            self._cycle_timer.start(100)
            return
        self._waiting_between_rounds = False
        self._scan_group_id = self._run_group_ids[self._run_index]
        group = self._groups[self._scan_group_id]
        self._last_fingerprint = group['fingerprint']
        self._cycle = group['cycle']
        try:
            available = self.before_capture is None or self.before_capture()
        except Exception:  # noqa: BLE001 - a display guard must fail closed
            available = False
        if not available:
            self.pause()
            self.failed.emit('核對視窗遮住辨識位置，請移開後再開始。')
            return
        self._token = self._capture_token = uuid4().hex
        self._event = Event()
        self._results = {}
        self._cycle_timestamp = datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')
        self.state_changed.emit('checking')
        if not self.running or self._closing:
            self._capture_token = None
            return
        try:
            self._capture.capture(self._token, group['regions'], self._layout, self._event)
        except RuntimeError:
            self._capture_token = None
            self.pause()
            self.failed.emit('畫面擷取尚未就緒，請稍後再開始。')

    def _timed_out(self):
        if self.running and self._cycle_started is not None:
            self.pause()
            self.failed.emit('核對逾時，請稍後重新開始。')

    def _expired(self):
        # A busy GUI may deliver an OCR result before the queued timer event.
        # Check the actual elapsed time at every asynchronous result boundary.
        if (self._cycle_started is not None
                and (monotonic() - self._cycle_started) * 1000 >= self._cycle_timeout_ms):
            self._timed_out()
            return True
        return False

    def _captured(self, token, images, layout, fingerprint):
        if token != self._capture_token:
            return
        self._capture_token = None
        if self._closing or not self.running or token != self._token or self._event.is_set():
            return
        if self._expired():
            return
        if images is None:
            self.pause()
            return
        self._layout = layout
        if fingerprint is not None and fingerprint == self._last_fingerprint:
            # Only the two selected regions matter. Changes elsewhere on the
            # desktop must not trigger inference, replace a result or advance
            # its timestamp. No raster is kept between polling intervals.
            self._deadline.stop()
            self._cycle_started = self._cycle_timestamp = None
            self.state_changed.emit('unchanged')
            self._schedule_next_group(token)
            return
        self._pending_fingerprint = fingerprint
        self._images = images
        self._pending_name = 'A'
        self._retry_count = 0
        self.state_changed.emit('running')
        self._submit_crop()

    def _capture_failed(self, token, message, layout_changed):
        if token != self._capture_token:
            return
        self._capture_token = None
        if self._closing or not self.running or token != self._token:
            return
        if layout_changed:
            for group in self._groups.values():
                group['regions'].clear()
                group['result'], group['cycle'] = None, 0
            self._layout = None
            self.groups_changed.emit()
        self.pause()
        self.failed.emit(message)

    def _submit_crop(self):
        if self._closing or not self.running or self._pending_name is None:
            self._retry.stop()
            return
        if self._expired():
            return
        name = self._pending_name
        if self._ocr.queue_document_ocr(self._token, 0, name, self._images[name], self._event):
            self._inflight_name, self._pending_name = name, None
            self._images.pop(name)
            self._retry.stop()
            return
        self._retry_count += 1
        if not getattr(self._ocr, 'accepts_document_work', True) or self._retry_count >= 600:
            self.pause()
            self.failed.emit('目前的辨識工作尚未結束，請稍後重新開始核對。')
        else:
            self.state_changed.emit('waiting')
            self._retry.start()

    def _recognized(self, token, page, name, result):
        if (self._closing or not self.running or token != self._token or page != 0
                or name != self._inflight_name or self._event.is_set()):
            return
        if self._expired():
            return
        result = result if isinstance(result, dict) else {}
        text = result.get('text')
        text = text if isinstance(text, str) else ''
        # Keep visible text bounded even when a model returns malformed output.
        status = result.get('status')
        if len(text) > 100_000:
            text, status = '', 'failed'
        self._results[name] = {'text': text, 'status': status}
        self._inflight_name = None
        if name == 'A':
            self._pending_name = 'B'
            self._retry_count = 0
            self._submit_crop()
            return
        self._cycle += 1
        self._deadline.stop()
        timestamp = self._cycle_timestamp
        self._cycle_started = self._cycle_timestamp = None
        self._last_fingerprint = self._pending_fingerprint
        self._pending_fingerprint = None
        first, second = self._results['A'], self._results['B']
        group = self._groups[self._scan_group_id]
        result = {'a': first['text'], 'b': second['text'],
                  'verdict': compare_results(first, second, self.mode),
                  'cycle': self._cycle, 'timestamp': timestamp,
                  'group_id': group['id'], 'group_name': group['name']}
        group['fingerprint'], group['cycle'], group['result'] = self._last_fingerprint, self._cycle, result
        self._images.clear()
        self._results.clear()
        self.groups_changed.emit()
        if not self.running or self._closing or token != self._token:
            return
        self.updated.emit(dict(result))
        if self.running and not self._closing and token == self._token:
            self.state_changed.emit('watching')
            self._schedule_next_group(token)

    def _schedule_next_group(self, token):
        if not self.running or self._closing or token != self._token:
            return
        self._run_index = (self._run_index + 1) % len(self._run_group_ids)
        self._waiting_between_rounds = self._run_index == 0
        self._cycle_timer.start(self.interval * 1000 if self._waiting_between_rounds else 0)

    def shutdown(self):
        if self._closing:
            return
        self._closing = True
        self.pause()
        self._capture.stop()
        if not self._capture.isRunning():
            self.stopped.emit()
