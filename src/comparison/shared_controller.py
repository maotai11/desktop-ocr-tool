"""Session-only multi-field search with verified, same-frame publication."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from math import isfinite
from threading import Event
from time import monotonic
import unicodedata
from uuid import uuid4

import numpy as np

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QGuiApplication

from .capture import ComparisonCaptureError, regions_overlap, validate_region
from .shared_capture import SharedCaptureWorker, validate_regions
from .search_matching import match_field
from .row_tracking import RowTracker, translate_regions


class SharedSearchController(QObject):
    updated = Signal(object)
    fields_changed = Signal()
    state_changed = Signal(str)
    failed = Signal(str)
    stopped = Signal()
    MAX_FIELDS = 20

    def __init__(self, ocr_worker, parent=None, *, capture_worker=None,
                 cycle_timeout_ms=30_000, settle_ms=50):
        super().__init__(parent)
        if type(cycle_timeout_ms) is not int or cycle_timeout_ms < 1:
            raise ValueError('Cycle timeout must be positive')
        if type(settle_ms) is not int or not 0 <= settle_ms <= 1000:
            raise ValueError('Capture settle delay must be 0–1000 ms')
        self._ocr = ocr_worker
        self._capture = capture_worker or SharedCaptureWorker(self)
        self._capture.completed.connect(self._captured)
        self._capture.failed.connect(self._capture_failed)
        self._capture.finished.connect(self.stopped)
        if getattr(self._capture, 'supports_analysis', False):
            self._capture.analyzed.connect(self._analyzed)
        self._ocr.document_ocr_done.connect(self._recognized)
        field_id = uuid4().hex
        self._fields = {field_id: {'id': field_id, 'name': '欄位 1', 'region': None, 'result': None}}
        self.b_region = None
        self.tracking_region = None
        self.row_pitch = 30
        self.auto_follow = False
        self.mode = 'normalized'
        self.date_equivalence = True
        self.interval = 2
        self.running = False
        self.state = 'paused'
        self.before_capture = None
        self.after_capture = None
        self._closing = False
        self._calibrating = False
        self._tracker = None
        self._baseline_a = None
        self._layout = None
        self._token = None
        self._event = Event()
        self._capture_token = None
        self._capture_generation = None
        self._capture_phase = None
        self._capture_regions = None
        self._capture_analysis = None
        self._calibration_tracker_candidate = None
        self._grab_submitted = False
        self._restore_token = None
        self._images = {}
        self._work = []
        self._inflight = None
        self._a_results = {}
        self._b_result = None
        self._b_cache = None
        self._source_key = None
        self._source_regions = None
        self._source_settings = None
        self._timestamp = None
        self._cycle = 0
        self._started = None
        self._last_key = None
        self._last_payload = None
        self._last_a = None
        self._last_b = None
        self._selected_ids = {}
        self._cycle_timeout_ms = cycle_timeout_ms
        self._poll = QTimer(self)
        self._poll.setSingleShot(True)
        self._poll.timeout.connect(self._begin_cycle)
        self._settle = QTimer(self)
        self._settle.setSingleShot(True)
        self._settle.setInterval(settle_ms)
        self._settle.timeout.connect(self._take_capture)
        self._retry = QTimer(self)
        self._retry.setSingleShot(True)
        self._retry.setInterval(100)
        self._retry.timeout.connect(self._submit_next)
        self._deadline = QTimer(self)
        self._deadline.setSingleShot(True)
        self._deadline.setInterval(cycle_timeout_ms)
        self._deadline.timeout.connect(lambda: self._fail('搜尋逾時，請縮小範圍後重試。'))

    @property
    def fields(self):
        return deepcopy(list(self._fields.values()))

    @property
    def calibrated(self):
        return self._tracker is not None and self._baseline_a is not None

    @property
    def is_stopped(self):
        return not self._capture.isRunning()

    @property
    def calibrating(self):
        return self._calibrating

    @property
    def target_regions(self):
        return tuple(self._regions().values())

    def _regions(self):
        regions = {key: field['region'] for key, field in self._fields.items() if field['region'] is not None}
        if self.b_region is not None:
            regions['B'] = self.b_region
        if self.tracking_region is not None:
            regions['TRACK'] = self.tracking_region
        return regions

    def _set_state(self, state):
        self.state = state
        self.state_changed.emit(state)

    def _clear_results(self):
        changed = False
        for field in self._fields.values():
            changed = changed or field['result'] is not None
            field['result'] = None
        if changed:
            self.fields_changed.emit()

    def clear_results(self):
        self.pause()

    def _name(self, name, replacing=None):
        if not isinstance(name, str):
            return None
        name = name.strip()
        if (not 1 <= len(name) <= 32
                or any(unicodedata.category(char)[0] == 'C'
                       or unicodedata.category(char) in ('Zl', 'Zp') for char in name)
                or any(field['name'].casefold() == name.casefold()
                       for key, field in self._fields.items() if key != replacing)):
            return None
        return name

    def add_field(self, name=None):
        if self._closing or len(self._fields) >= self.MAX_FIELDS:
            if not self._closing:
                self.failed.emit('最多可新增 20 個欄位。')
            return None
        if name is None:
            names = {field['name'] for field in self._fields.values()}
            name = next(f'欄位 {i}' for i in range(1, 22) if f'欄位 {i}' not in names)
        name = self._name(name)
        if name is None:
            self.failed.emit('欄位名稱請填 1–32 字，且不可重複或換行。')
            return None
        self.pause()
        field_id = uuid4().hex
        self._fields[field_id] = {'id': field_id, 'name': name, 'region': None, 'result': None}
        self._reset_tracking()
        self.fields_changed.emit()
        return field_id

    def remove_field(self, field_id):
        if self._closing or field_id not in self._fields or len(self._fields) == 1:
            return False
        self.pause()
        del self._fields[field_id]
        self._reset_tracking()
        self.fields_changed.emit()
        return True

    def rename_field(self, field_id, name):
        name = self._name(name, field_id)
        if self._closing or field_id not in self._fields or name is None:
            return False
        self.pause()
        self._fields[field_id]['name'] = name
        self.fields_changed.emit()
        return True

    def _validate_configuration(self, regions):
        validate_regions(regions)
        b = regions.get('B')
        if b is not None and any(regions_overlap(region, b)
                                 for name, region in regions.items() if name not in ('B', 'TRACK')):
            raise ComparisonCaptureError('輸入數值欄位與發票位置不可重疊，請分別框選。')

    def set_a_region(self, field_id, region):
        if self._closing or field_id not in self._fields:
            return False
        return self._set_region(field_id, region)

    def set_b_region(self, region):
        return self._set_region('B', region)

    def set_tracking_region(self, region):
        return self._set_region('TRACK', region)

    def _set_region(self, name, region):
        if self._closing:
            return False
        self.pause()
        try:
            region = validate_region(region)
            if region[4] != 1:
                raise ComparisonCaptureError('目前只支援主螢幕，請在主螢幕重新框選。')
            regions = self._regions()
            regions[name] = region
            self._validate_configuration(regions)
        except ComparisonCaptureError as exc:
            self.failed.emit(str(exc))
            return False
        if name == 'B':
            self.b_region = region
            self._b_cache = None
        elif name == 'TRACK':
            self.tracking_region = region
            self._reset_tracking()
        else:
            self._fields[name]['region'] = region
            self._reset_tracking()
        self._layout = None
        self.fields_changed.emit()
        return True

    def _reset_tracking(self):
        self._tracker = self._baseline_a = None
        self.auto_follow = False

    def set_row_pitch(self, value):
        if self._closing or type(value) is not int or not 1 <= value <= 500:
            return False
        self.pause()
        self.row_pitch = value
        self._reset_tracking()
        self.fields_changed.emit()
        return True

    def set_interval(self, seconds):
        if type(seconds) is not int or not 1 <= seconds <= 60:
            return False
        self.interval = seconds
        return True

    def set_mode(self, mode):
        if self._closing or mode not in ('normalized', 'exact'):
            return False
        if self.mode != mode:
            self.pause()
            self.mode = mode
            self._b_cache = None
        return True

    def set_auto_follow(self, value):
        if self._closing or type(value) is not bool:
            return False
        if value and not self.calibrated:
            self.failed.emit('請先設定表格範圍並校準目前選取列。')
            return False
        self.pause()
        self.auto_follow = value
        self.fields_changed.emit()
        return True

    def invalidate_cache(self):
        self.pause()
        self._b_cache = None

    def set_date_equivalence(self, enabled):
        if self._closing or type(enabled) is not bool:
            return False
        if enabled != self.date_equivalence:
            self.pause()
            self.date_equivalence = enabled
            self._b_cache = None
        return True

    def refresh_now(self):
        """Retire this cycle and request fresh invoice/source OCR explicitly."""
        if self._closing or not self.running or self._calibrating:
            return False
        self._cancel_cycle()
        # Restoring the overlay may synchronously pause, close, or restart.
        # Never let this older entry alter a replacement generation.
        if self._closing or not self.running or self._token is not None:
            return False
        self._b_cache = None
        self._last_key = self._last_payload = self._last_a = self._last_b = None
        self._selected_ids.clear()
        self._begin_cycle()
        return self.running and not self._closing

    def invalidate_regions(self, message='螢幕配置已變更，請重新設定範圍。'):
        self.pause()
        for field in self._fields.values():
            field['region'] = None
        self.b_region = self.tracking_region = self._layout = None
        self._b_cache = None
        self._reset_tracking()
        self.fields_changed.emit()
        if message:
            self.failed.emit(message)

    def _ready(self):
        if self.b_region is None or any(field['region'] is None for field in self._fields.values()):
            self.failed.emit('請先框選發票位置，以及每個輸入數值欄位。')
            return False
        return True

    def start(self):
        if self._closing or self.running or self._calibrating or not self._ready():
            return False
        self.running = True
        self._begin_cycle()
        return self.running

    def pause(self):
        self.running = self._calibrating = False
        self._cancel_cycle()
        self._last_key = self._last_payload = self._last_a = self._last_b = None
        self._selected_ids.clear()
        self._clear_results()
        self._set_state('paused')

    def _cancel_cycle(self):
        self._event.set()
        self._token = None
        for timer in (self._poll, self._settle, self._retry, self._deadline):
            timer.stop()
        self._started = None
        self._images.clear()
        self._work.clear()
        self._a_results.clear()
        self._b_result = None
        self._source_key = self._source_regions = self._source_settings = None
        self._capture_analysis = None
        self._calibration_tracker_candidate = None
        self._inflight = None
        if not self._grab_submitted:
            self._capture_token = None
        self._restore_capture()

    def _expired(self):
        if self._started is not None and (monotonic() - self._started) * 1000 >= self._cycle_timeout_ms:
            self._fail('搜尋逾時，請縮小範圍後重試。')
            return True
        return False

    def _fail(self, message):
        self.pause()
        if not self._closing:
            self.failed.emit(message)

    def _begin_cycle(self):
        if not self.running or self._closing:
            return
        if self._token is None:
            self._token, self._event = uuid4().hex, Event()
            token = self._token
            self._started = monotonic()
            self._deadline.start()
            self._clear_results()
            if not self._active_generation(token):
                return
        if self._expired():
            return
        if self._capture_token is not None:
            self._poll.start(100)
            return
        self._request_capture('source')

    def _request_capture(self, phase, regions=None):
        if self._closing or self._token is None or self._event.is_set() or self._expired():
            return
        self._capture_token = uuid4().hex
        self._capture_generation = self._token
        self._capture_phase = phase
        self._capture_analysis = None
        if phase == 'calibrate':
            self._calibration_tracker_candidate = None
        self._capture_regions = dict(regions if regions is not None else self._regions())
        self._grab_submitted = False
        self._restore_token = self._capture_token
        try:
            self._validate_configuration(self._capture_regions)
            if self.before_capture is not None and not self.before_capture():
                self._fail('核對視窗遮住辨識範圍，請移開後再開始。')
                return
        except Exception:  # noqa: BLE001 - capture guards fail closed without paths
            self._fail('畫面尚未就緒，請移開核對視窗後再試。')
            return
        if self._token != self._capture_generation or self._event.is_set():
            self._restore_capture()
            return
        token, capture_token = self._token, self._capture_token
        self._set_state('calibrating' if phase == 'calibrate' else 'checking')
        if (token == self._token and capture_token == self._capture_token
                and not self._closing and not self._event.is_set()):
            self._settle.start()

    def _take_capture(self):
        if (self._token is None or self._closing or self._event.is_set()
                or self._token != self._capture_generation or self._expired()):
            self._restore_capture()
            return
        try:
            self._grab_submitted = True
            processor = self._analysis_processor() if getattr(self._capture, 'supports_analysis', False) else None
            options = {'processor': processor} if processor is not None else {}
            self._capture.capture(self._capture_token, self._capture_regions, self._layout, self._event, **options)
        except (RuntimeError, ValueError):
            self._grab_submitted = False
            self._capture_token = None
            self._fail('畫面擷取尚未就緒，請稍後再試。')

    def _analysis_processor(self):
        """HSV/row analysis belongs to the capture thread, never the GUI."""
        if self._capture_phase == 'calibrate':
            tracker = self._make_tracker()
            self._calibration_tracker_candidate = tracker
            return lambda images: tracker.calibrate(images['TRACK'])
        if self.auto_follow and self.calibrated and 'TRACK' in self._capture_regions:
            tracker = self._tracker
            return lambda images: tracker.update(images['TRACK'])
        return None

    def _analyzed(self, token, result):
        if token == self._capture_token and self._capture_generation == self._token and not self._event.is_set():
            self._capture_analysis = result

    def _restore_capture(self, token=None):
        if self._restore_token is None or (token is not None and token != self._restore_token):
            return
        self._restore_token = None
        if self.after_capture is not None:
            try:
                self.after_capture()
            except Exception:  # noqa: BLE001 - never restore by a private traceback
                pass

    def _capture_failed(self, token, message, changed):
        if token != self._capture_token:
            return
        generation = self._capture_generation
        self._capture_token = None
        self._grab_submitted = False
        self._restore_capture(token)
        if self._closing or generation != self._token or self._event.is_set():
            return
        if changed:
            self.invalidate_regions(message)
        else:
            self._fail(message)

    def _captured(self, token, images, layout, hashes):
        if token != self._capture_token:
            return
        generation, phase, regions = self._capture_generation, self._capture_phase, self._capture_regions
        self._capture_token = None
        self._grab_submitted = False
        self._restore_capture(token)
        if self._closing or generation != self._token or self._event.is_set() or self._expired():
            return
        if images is None:
            self._fail('未能取得畫面，請重新開始。')
            return
        if (not isinstance(images, dict) or set(images) != set(regions) or not isinstance(hashes, dict)
                or set(hashes) != set(regions)
                or any(not isinstance(image, np.ndarray) or image.dtype != np.uint8
                       or image.shape != (regions[name][3], regions[name][2], 3)
                       for name, image in images.items())
                or any(not isinstance(value, (bytes, str)) or not 1 <= len(value) <= 128
                       for value in hashes.values())):
            self._fail('畫面資料不完整，請重新開始。')
            return
        self._layout = layout
        try:
            if phase == 'calibrate':
                self._calibrated_capture(images)
            elif phase in ('verify', 'choice'):
                self._verify_capture(images, hashes, phase)
            else:
                self._source_capture(images, hashes, regions)
        except Exception:  # noqa: BLE001 - malformed native/OCR state cannot publish a result
            self._fail('畫面或辨識資料已失效，請重新開始搜尋。')
        finally:
            self._capture_analysis = None
            self._calibration_tracker_candidate = None

    def _settings_signature(self):
        engine = getattr(self._ocr, '_engine', None)
        names = ('model_profile', '_model_version', '_confidence_accept', '_confidence_review',
                 '_max_short_side', '_enable_second_pass', '_max_ocr_passes', '_enable_handwriting',
                 '_enable_secondary_engine', '_secondary_for_handwriting', '_secondary_for_low_confidence',
                 '_secondary_confidence_threshold')
        values = tuple(getattr(engine, name, None) for name in names)
        return (id(engine), id(getattr(engine, '_secondary', None)), self.mode, self.date_equivalence, values)

    def _tracking_key(self, images, *, move):
        if not self.auto_follow:
            return ()
        if not self.calibrated or 'TRACK' not in images:
            return None
        if getattr(self._capture, 'supports_analysis', False) and self._capture_analysis is None:
            self._set_state('tracking_lost')
            return None
        result = (self._capture_analysis if self._capture_analysis is not None
                  else self._tracker.update(images['TRACK']))
        if result.get('status') != 'stable':
            self._set_state('tracking_pending' if result.get('status') == 'pending' else 'tracking_lost')
            return None
        row, delta = result.get('row'), result.get('delta_y')
        if row is None or type(delta) is not int:
            return None
        if move:
            try:
                positions = translate_regions(self._baseline_a, delta,
                                              table_region=self.tracking_region, b_region=self.b_region,
                                              monitor_bounds=self._monitor_bounds())
                if not self._inside_row(positions.values(), row):
                    raise ValueError('Source fields extend outside the selected row')
            except (ValueError, TypeError):
                self._set_state('tracking_lost')
                return None
            if any(self._fields[key]['region'] != region for key, region in positions.items()):
                for key, region in positions.items():
                    self._fields[key]['region'] = region
                self._last_key = None
                self._selected_ids.clear()
                self.fields_changed.emit()
                return 'moved'
        return (tuple(row), result.get('fingerprint'))

    def _key(self, hashes, tracking):
        keys = tuple(self._fields) + ('B',)
        if any(key not in hashes or not isinstance(hashes[key], (bytes, str)) for key in keys):
            raise ValueError('Missing frame fingerprints')
        return (tuple((key, self._fields[key]['region'], hashes[key]) for key in self._fields),
                self.b_region, hashes['B'], tracking, self._settings_signature())

    def _source_capture(self, images, hashes, regions):
        token = self._token
        tracking = self._tracking_key(images, move=True)
        if not self._active_generation(token):
            return
        if tracking is None:
            self._wait_next(200 if self.state == 'tracking_pending' else self.interval * 1000)
            return
        if tracking == 'moved':
            self._wait_next(0)
            return
        try:
            key = self._key(hashes, tracking)
        except ValueError:
            self._fail('畫面資料不完整，請重新開始。')
            return
        if key == self._last_key and self._last_payload is not None:
            for field_id, result in self._last_payload.items():
                self._fields[field_id]['result'] = deepcopy(result)
            self.fields_changed.emit()
            if self._emit_snapshot('unchanged', token):
                self._wait_next(self.interval * 1000)
            return
        self._selected_ids.clear()
        self._source_key = key
        self._source_regions = dict(regions)
        self._source_settings = self._settings_signature()
        self._timestamp = datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')
        self._images = {name: image for name, image in images.items() if name != 'TRACK'}
        self._a_results = {}
        cache_key = (self.b_region, hashes['B'], self._source_settings)
        if self._b_cache is not None and self._b_cache[0] == cache_key:
            self._b_result = deepcopy(self._b_cache[1])
            self._images.pop('B')
            self._work = list(self._fields)
        else:
            self._b_result = None
            self._work = ['B', *self._fields]
        self._cache_key = cache_key
        self._set_state('reading')
        if self._active_generation(token):
            self._submit_next()

    def _submit_next(self):
        if self._closing or not self.running or self._token is None or self._event.is_set() or self._expired():
            return
        if not self._work:
            self._request_capture('verify')
            return
        key = self._work[0]
        if self._ocr.queue_document_ocr(self._token, 0, key, self._images[key], self._event):
            self._inflight = key
            self._work.pop(0)
            self._images.pop(key)
            return
        if not getattr(self._ocr, 'accepts_document_work', True):
            self._fail('辨識工作已停止，請重新開啟程式。')
            return
        token = self._token
        self._set_state('waiting')
        if self._active_generation(token):
            self._retry.start()

    @staticmethod
    def _safe_result(result, region):
        if not isinstance(result, dict):
            return {'text': '', 'status': 'failed', 'detail': []}
        text, detail = result.get('text'), result.get('detail', [])
        if not isinstance(text, str) or len(text) > 100_000 or not isinstance(detail, list) or len(detail) > 5000:
            return {'text': '', 'status': 'failed', 'detail': []}
        status = result.get('status')
        status = status if status in ('done', 'confirmed', 'needs_review', 'failed', 'cancelled') else 'failed'
        clean, total = [], 0
        width, height = region[2:4]
        for item in detail:
            try:
                box, row_text, confidence = item['box'], item['text'], item['confidence']
                if (not isinstance(row_text, str) or len(row_text) > 1000 or not isinstance(box, (tuple, list))
                        or len(box) != 4 or type(confidence) not in (int, float)
                        or not isfinite(confidence) or not 0 <= confidence <= 1):
                    raise ValueError()
                points = []
                for point in box:
                    if (not isinstance(point, (tuple, list)) or len(point) != 2
                            or any(type(value) not in (int, float) or not isfinite(value) for value in point)
                            or not 0 <= point[0] <= width or not 0 <= point[1] <= height):
                        raise ValueError()
                    points.append([float(point[0]), float(point[1])])
                total += len(row_text)
                if total > 100_000:
                    return {'text': '', 'status': 'failed', 'detail': []}
                clean.append({'text': row_text, 'confidence': float(confidence), 'box': points})
            except (TypeError, KeyError, ValueError):
                status = 'needs_review' if status in ('done', 'confirmed') else status
        return {'text': text, 'status': status, 'detail': clean}

    def _recognized(self, token, page, name, result):
        if (self._closing or not self.running or token != self._token or page != 0
                or name != self._inflight or self._event.is_set() or self._expired()):
            return
        result = self._safe_result(result, self._source_regions[name])
        self._inflight = None
        if name == 'B':
            self._b_result = result
            if result['status'] in ('done', 'confirmed', 'needs_review'):
                self._b_cache = (self._cache_key, deepcopy(result))
        else:
            self._a_results[name] = result
        self._submit_next()

    def _verify_capture(self, images, hashes, phase):
        token = self._token
        tracking = self._tracking_key(images, move=False)
        if not self._active_generation(token):
            return
        try:
            key = self._key(hashes, tracking) if tracking is not None else None
        except ValueError:
            key = None
        expected = self._last_key if phase == 'choice' else self._source_key
        if key is None or key != expected:
            self._selected_ids.clear()
            self._last_key = self._last_payload = self._last_a = self._last_b = None
            self._clear_results()
            if not self._active_generation(token):
                return
            self._set_state('changed')
            if self._active_generation(token):
                self._wait_next(200)
            return
        if phase == 'choice':
            self._a_results = deepcopy(self._last_a)
            self._b_result = deepcopy(self._last_b)
        results = {field_id: match_field(self._a_results[field_id], self._b_result,
                                        mode=self.mode, date_equivalence=self.date_equivalence,
                                        selected_id=self._selected_ids.get(field_id))
                   for field_id in self._fields}
        self._last_key = key
        self._last_payload = deepcopy(results)
        self._last_a, self._last_b = deepcopy(self._a_results), deepcopy(self._b_result)
        for field_id, result in results.items():
            self._fields[field_id]['result'] = result
        if phase != 'choice':
            self._cycle += 1
        self.fields_changed.emit()
        if self._emit_snapshot('ready', token):
            self._wait_next(self.interval * 1000)

    def _active_generation(self, token):
        return (token is not None and token == self._token and self.running
                and not self._closing and not self._event.is_set())

    def _emit_snapshot(self, state, token):
        # Qt slots may pause/restart synchronously; only this generation may
        # publish its result or release the cycle's timers and capture buffers.
        if not self._active_generation(token):
            return False
        self._set_state(state)
        if not self._active_generation(token):
            return False
        self.updated.emit({'fields': self.fields, 'b_region': self.b_region,
                           'tracking_region': self.tracking_region, 'row_pitch': self.row_pitch,
                           'auto_follow': self.auto_follow, 'calibrated': self.calibrated,
                           'timestamp': self._timestamp, 'cycle': self._cycle, 'state': state})
        return self._active_generation(token)

    def _wait_next(self, milliseconds):
        self._deadline.stop()
        self._started = None
        self._token = None
        self._images.clear()
        self._work.clear()
        self._inflight = None
        self._a_results.clear()
        self._b_result = None
        self._source_key = self._source_regions = self._source_settings = None
        if self.running and not self._closing:
            self._poll.start(milliseconds)

    def select_candidate(self, field_id, candidate_id):
        field = self._fields.get(field_id)
        if (self._closing or not self.running or self._token is not None or self._capture_token is not None
                or field is None or not isinstance(candidate_id, str) or self._last_key is None
                or field['result'] is None
                or not any(candidate['id'] == candidate_id for candidate in field['result']['candidates'])):
            return False
        self._poll.stop()
        self._selected_ids[field_id] = candidate_id
        self._token, self._event = uuid4().hex, Event()
        self._started = monotonic()
        self._deadline.start()
        self._request_capture('choice')
        return True

    def _monitor_bounds(self):
        regions = [field['region'] for field in self._fields.values() if field['region'] is not None]
        if not regions:
            raise ValueError('No source regions')
        monitor = regions[0][4]
        if self._layout is not None and 1 <= monitor <= len(self._layout):
            return (0, 0, self._layout[monitor - 1][2], self._layout[monitor - 1][3], monitor)
        screen = QGuiApplication.primaryScreen()
        if monitor != 1 or screen is None:
            raise ValueError('No monitor dimensions')
        size, ratio = screen.geometry().size(), screen.devicePixelRatio()
        return (0, 0, round(size.width() * ratio), round(size.height() * ratio), monitor)

    def move_rows(self, delta):
        if self._closing or type(delta) is not int or not delta or abs(delta) > 1000:
            return False
        self.pause()
        self.auto_follow = False
        if not self._ready() or not 1 <= self.row_pitch <= 500:
            self.failed.emit('請先設定每列高度。')
            return False
        try:
            bounds = self._monitor_bounds()
            positions = translate_regions({key: field['region'] for key, field in self._fields.items()},
                                          delta * self.row_pitch,
                                          table_region=self.tracking_region or bounds,
                                          b_region=self.b_region, monitor_bounds=bounds)
            self._validate_configuration({**positions, 'B': self.b_region})
        except (ValueError, TypeError):
            self.failed.emit('移動後的欄位超出表格或螢幕，請重新框選。')
            return False
        for field_id, region in positions.items():
            self._fields[field_id]['region'] = region
        self._reset_tracking()
        self.fields_changed.emit()
        return self.start()

    def calibrate_tracking(self):
        if self._closing or self._capture_token is not None:
            return False
        self.pause()
        if self.tracking_region is None or not self._ready() or not 1 <= self.row_pitch <= 500:
            self.failed.emit('請先框選輸入表格、數值欄位，再設定每列高度。')
            return False
        self._calibrating = True
        self._token, self._event = uuid4().hex, Event()
        self._started = monotonic()
        self._deadline.start()
        self._request_capture('calibrate', {'TRACK': self.tracking_region})
        return self._calibrating

    def _calibrated_capture(self, images):
        try:
            positions = {key: field['region'] for key, field in self._fields.items()}
            if getattr(self._capture, 'supports_analysis', False) and self._capture_analysis is None:
                raise ValueError('Missing worker analysis')
            tracker = self._calibration_tracker_candidate or self._make_tracker()
            result = (self._capture_analysis if self._capture_analysis is not None
                      else tracker.calibrate(images['TRACK']))
            if result.get('status') not in ('pending', 'stable') or result.get('row') is None:
                raise ValueError('No distinctive selected row')
            row = validate_region(result['row'])
            if not self._inside_row(positions.values(), row):
                raise ValueError('All source fields must be inside the actual selected row')
            self._tracker, self._baseline_a = tracker, positions
        except (TypeError, ValueError):
            self._reset_tracking()
            self._fail('未找到完整的彩色選取列；請確認輸入數值欄位都在同一列後再校準。')
            return
        self._calibrating = False
        self._deadline.stop()
        self._started = self._token = None
        self.fields_changed.emit()
        self._set_state('calibrated')

    def _make_tracker(self):
        positions = [field['region'] for field in self._fields.values()]
        top = min(region[1] for region in positions)
        bottom = max(region[1] + region[3] for region in positions)
        x, _y, width, _height, monitor = self.tracking_region
        return RowTracker(self.tracking_region, (x, top, width, bottom - top, monitor))

    @staticmethod
    def _inside_row(regions, row):
        return all(region[4] == row[4] and region[0] >= row[0] and region[1] >= row[1]
                   and region[0] + region[2] <= row[0] + row[2]
                   and region[1] + region[3] <= row[1] + row[3] for region in regions)

    def shutdown(self):
        if self._closing:
            return
        self._closing = True
        self.pause()
        self._b_cache = None
        self._reset_tracking()
        self._capture.stop()
        if not self._capture.isRunning():
            self.stopped.emit()
