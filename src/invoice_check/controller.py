"""A finite manual snapshot transaction, sharing the serialized OCR worker."""
from __future__ import annotations

import math
from copy import deepcopy
from threading import Event
from uuid import uuid4

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal, Slot

from ..core.resource_budget import ResourceBudgetExceeded, memory_preflight
from .capture import PairCaptureService, validate_region
from .fields import (
    FIELD_KEYS,
    compare_fields,
    extract_invoice_fields,
    extract_row_fields,
    header_columns,
)
from .rows import detect_active_row


class InvoiceCheckController(QObject):
    state_changed = Signal(str, str)
    results_ready = Signal(object)
    preview_ready = Signal(str, object)
    regions_changed = Signal(object)
    failed = Signal(str)
    capture_started = Signal()
    capture_finished = Signal()
    stopped = Signal()

    def __init__(self, ocr_worker, parent=None, capture_service=None, *, settle_ms=150):
        super().__init__(parent)
        self._ocr = ocr_worker
        self._capture = capture_service if capture_service is not None else PairCaptureService(self)
        self._capture.finished.connect(self._captured)
        self._capture.stopped.connect(self._maybe_stopped)
        self._ocr.document_ocr_done.connect(self._recognized)
        self._regions = {}
        self._overrides = {'invoice': {}, 'table': {}}
        self._manual_row = None
        self._header_row = None
        self._amount_key = 'net_amount'
        self._tax_id_key = 'seller_tax_id'
        self._generation = 0
        self._closed = False
        self._stopped_announced = False
        self._operation = None
        self._capture_intent = None
        self._ocr_pending = None
        self._cancel_event = Event()
        self._visibility_generation = None
        self._revoking = False
        self._state = 'idle'
        self.last_result = None
        self._settle = QTimer(self)
        self._settle.setSingleShot(True)
        self._settle.setInterval(max(0, int(settle_ms)))
        self._settle.timeout.connect(self._start_capture)

    @property
    def regions(self):
        return dict(self._regions)

    @property
    def generation(self):
        return self._generation

    @property
    def state(self):
        return self._state

    @property
    def amount_key(self):
        return self._amount_key

    @property
    def tax_id_key(self):
        return self._tax_id_key

    @property
    def busy(self):
        return (self._revoking or self._operation is not None or self._ocr_pending is not None
                or self._capture.busy or self._settle.isActive())

    @property
    def running(self):
        return self.busy

    @property
    def is_stopped(self):
        return self._closed and not self.busy

    def _state_change(self, state, message):
        self._state = state
        self.state_changed.emit(state, message)

    def _current(self, generation):
        return (not self._closed and generation == self._generation
                and self._operation is not None and not self._cancel_event.is_set())

    def set_region(self, side, region):
        if self._closed:
            return False
        if side not in ('invoice', 'table'):
            raise ValueError('Unknown invoice region')
        chosen = validate_region(region)
        if chosen[4] != 1:
            # The shared selector and current annotation window are tied to
            # Qt's primary screen. Never accept a different MSS screen and
            # silently draw a correct result at another monitor's origin.
            raise ValueError('請將目標程式移至主要螢幕後框選。')
        other = self._regions.get('table' if side == 'invoice' else 'invoice')
        if other is not None:
            if chosen[4] != other[4]:
                raise ValueError('左右範圍請選在同一個螢幕。')
            if (chosen[0] < other[0]+other[2] and other[0] < chosen[0]+chosen[2]
                    and chosen[1] < other[1]+other[3] and other[1] < chosen[1]+chosen[3]):
                raise ValueError('發票與資料表範圍請分開框選。')
        self.cancel()
        if self._closed or self._operation is not None:
            return False
        self._regions[side] = chosen
        self._overrides[side] = {}
        if side == 'table':
            self._manual_row = self._header_row = None
        self.regions_changed.emit(self.regions)
        return True

    def clear_region(self, side):
        if self._closed:
            return False
        if side not in ('invoice', 'table'):
            raise ValueError('Unknown invoice region')
        self.cancel()
        if self._closed or self._operation is not None:
            return False
        self._regions.pop(side, None)
        self._overrides[side] = {}
        if side == 'table':
            self._manual_row = self._header_row = None
        self.regions_changed.emit(self.regions)
        return True

    def set_overrides(self, side, mapping):
        if self._closed:
            return False
        if side not in self._overrides or not isinstance(mapping, dict) or set(mapping)-set(FIELD_KEYS):
            raise ValueError('Invalid field positions')
        values = {}
        for key, rect in mapping.items():
            if not isinstance(rect, (tuple, list)) or len(rect) != 4:
                raise ValueError('Invalid field rectangle')
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in rect):
                raise ValueError('Invalid field rectangle')
            x, y, width, height = map(float, rect)
            if x < 0 or y < 0 or width <= 0 or height <= 0 or x+width > 1.000001 or y+height > 1.000001:
                raise ValueError('Field rectangle is outside the preview')
            values[key] = (x, y, width, height)
        self.cancel()
        if self._closed or self._operation is not None:
            return False
        self._overrides[side] = values
        return True

    @staticmethod
    def _band(value):
        if value is None:
            return None
        if (not isinstance(value, (tuple, list)) or len(value) != 2
                or any(type(v) is not int for v in value) or not 0 <= value[0] < value[1]):
            raise ValueError('Invalid row range')
        return tuple(value)

    def set_manual_row(self, band):
        if self._closed:
            return False
        chosen = self._band(band)
        if chosen is not None and 'table' in self._regions and chosen[1] > self._regions['table'][3]:
            raise ValueError('資料列超出預覽範圍。')
        self.cancel()
        if self._closed or self._operation is not None:
            return False
        self._manual_row = chosen
        return True

    def set_header_row(self, band):
        if self._closed:
            return False
        chosen = self._band(band)
        if chosen is not None and 'table' in self._regions and chosen[1] > self._regions['table'][3]:
            raise ValueError('欄名列超出預覽範圍。')
        self.cancel()
        if self._closed or self._operation is not None:
            return False
        self._header_row = chosen
        return True

    def set_field_options(self, amount_key='net_amount', tax_id_key='seller_tax_id'):
        if self._closed:
            return False
        if amount_key not in ('net_amount', 'total_amount') or tax_id_key not in ('seller_tax_id', 'buyer_tax_id'):
            raise ValueError('Invalid field meanings')
        self.cancel()
        if self._closed or self._operation is not None:
            return False
        self._amount_key, self._tax_id_key = amount_key, tax_id_key
        return True

    def preview_once(self, side):
        if self._closed or self.busy:
            return False
        if side not in self._regions:
            self.failed.emit('請先框選要預覽的範圍。')
            return False
        return self._begin('preview', {side: self._regions[side]})

    def check_once(self):
        if self._closed or self.busy:
            return False
        if set(self._regions) != {'invoice', 'table'}:
            self.failed.emit('請先框選左側發票與右側資料表。')
            return False
        return self._begin('check', self.regions)

    def _begin(self, kind, regions):
        self._generation += 1
        self._cancel_event = Event()
        generation = self._generation
        self.last_result = None
        self._operation = {'generation': generation, 'kind': kind, 'regions': dict(regions),
                           'results': {}, 'snapshot_id': uuid4().hex,
                           'overrides': deepcopy(self._overrides), 'manual_row': self._manual_row,
                           'header_row': self._header_row, 'amount_key': self._amount_key,
                           'tax_id_key': self._tax_id_key}
        self._state_change('capturing', '正在擷取本次畫面…')
        if not self._current(generation):
            return False
        return self._request_capture('initial', regions)

    def _request_capture(self, purpose, regions):
        generation = self._generation
        if not self._current(generation):
            return False
        token = 'invoice-capture-' + uuid4().hex
        self._capture_intent = (token, generation, purpose, dict(regions))
        self._visibility_generation = generation
        self.capture_started.emit()
        if not self._current(generation):
            self._restore_visibility(generation)
            return False
        self._settle.start()
        return True

    @Slot()
    def _start_capture(self):
        intent = self._capture_intent
        if intent is None:
            return
        token, generation, _, regions = intent
        if not self._current(generation):
            self._restore_visibility(generation)
            return
        try:
            accepted = self._capture.request(token, regions, self._cancel_event)
        except Exception:
            accepted = False
        if not accepted:
            self._fail('擷取工作尚未結束，請稍後再按辨識。')

    def _restore_visibility(self, generation=None):
        if (self._visibility_generation is not None
                and (generation is None or generation == self._visibility_generation)):
            self._visibility_generation = None
            self.capture_finished.emit()

    @Slot(str, object)
    def _captured(self, token, payload):
        intent = self._capture_intent
        if intent is None or token != intent[0]:
            self._after_acknowledgement()
            return
        _, generation, purpose, _ = intent
        self._capture_intent = None
        self._restore_visibility(generation)
        if not self._current(generation):
            self._after_acknowledgement()
            return
        if not isinstance(payload, dict) or 'error' in payload:
            self._fail(payload.get('error', '擷取已取消，請重新辨識。') if isinstance(payload, dict)
                       else '擷取已取消，請重新辨識。')
            return
        try:
            if purpose == 'verify':
                self._verify_and_publish(payload)
                return
            images = payload['images']
            operation = self._operation
            if operation['kind'] == 'preview':
                for side, image in images.items():
                    self.preview_ready.emit(side, image)
                    if not self._current(generation):
                        return
                self._operation = None
                self._state_change('idle', '範圍預覽已更新，按辨識才會讀取文字。')
                return
            # Keep just the two detached crops until the single verification
            # capture. No full monitor image survives the capture worker.
            operation['snapshot'] = payload
            table = images['table']
            band = self._selected_row(table, operation['manual_row'])
            header = operation['header_row'] if operation['header_row'] is not None else band.get('header')
            y0, _ = band['row']
            if header is not None and not 0 <= header[0] < header[1] <= y0:
                raise ValueError('欄名範圍須位於目前資料列上方，請重新調整。')
            operation['band'] = band
            operation['header'] = header
            operation['stages'] = ['invoice'] + (['header'] if header is not None else []) + ['row']
            operation['stage_index'] = 0
            for side, image in images.items():
                self.preview_ready.emit(side, image)
                if not self._current(generation):
                    return
            self._state_change('reading', '正在辨識發票與目前同行…')
            if self._current(generation):
                self._queue_next()
        except MemoryError:
            self._fail(str(ResourceBudgetExceeded('memory')))
        except (ValueError, KeyError, TypeError, IndexError):
            self._fail('無法確定目前同行，請重新框選或在預覽中指定資料列。')

    def _queue_next(self):
        operation = self._operation
        if operation is None or not self._current(operation['generation']):
            return
        index = operation['stage_index']
        if index >= len(operation['stages']):
            self._state_change('verifying', '正在確認本次畫面未變更…')
            if self._current(operation['generation']):
                self._request_capture('verify', operation['regions'])
            return
        stage = operation['stages'][index]
        images = operation['snapshot']['images']
        if stage == 'invoice':
            source_image = images['invoice']
        else:
            y0, y1 = operation['header'] if stage == 'header' else operation['band']['row']
            source_image = images['table'][y0:y1]
        try:
            memory_preflight(additional_bytes=128_000_000 + source_image.nbytes)
        except ResourceBudgetExceeded as exc:
            self._fail(str(exc))
            return
        try:
            image = source_image.copy()
        except MemoryError:
            self._fail(str(ResourceBudgetExceeded('memory')))
            return
        token = 'invoice-ocr-' + operation['snapshot_id']
        pending = (token, index, stage, operation['generation'])
        self._ocr_pending = pending
        try:
            accepted = self._ocr.queue_document_ocr(token, index, stage, image,
                                                    cancel_event=self._cancel_event)
        except Exception:
            accepted = False
        if not accepted and self._ocr_pending == pending:
            self._ocr_pending = None
            self._fail('辨識引擎正在處理其他工作，請稍後再按辨識。')

    @Slot(str, int, str, object)
    def _recognized(self, token, page, stage, result):
        pending = self._ocr_pending
        if pending is None or pending[:3] != (token, page, stage):
            return
        self._ocr_pending = None
        if not self._current(pending[3]):
            self._after_acknowledgement()
            return
        if not isinstance(result, dict) or result.get('status') not in ('done', 'needs_review', 'confirmed'):
            self._fail('本次辨識未完成，請確認畫面後重新辨識。')
            return
        self._operation['results'][stage] = result
        self._operation['stage_index'] += 1
        self._queue_next()

    def _verify_and_publish(self, payload):
        operation = self._operation
        generation = operation['generation']
        original = operation['snapshot']['images']
        current = payload['images']
        band = self._selected_row(current['table'], operation['manual_row'])
        old_band = operation['band']
        y0, y1 = old_band['row']
        stable = (payload['regions'] == operation['regions'] and band['row'] == old_band['row']
                  and np.array_equal(original['invoice'], current['invoice'])
                  and np.array_equal(original['table'][y0:y1], current['table'][y0:y1]))
        if operation['header'] is not None:
            h0, h1 = operation['header']
            stable = stable and np.array_equal(original['table'][h0:h1], current['table'][h0:h1])
        if not stable:
            self._fail('畫面已變更，本次結果已作廢；請停留在欲核對資料後再按辨識。')
            return
        source_results = operation['results']
        height, width = original['invoice'].shape[:2]
        invoice = extract_invoice_fields(source_results['invoice'].get('detail', []), (width, height),
                                         overrides=operation['overrides']['invoice'])
        row_width = original['table'].shape[1]
        columns = {}
        if operation['header'] is not None:
            h0, h1 = operation['header']
            columns = header_columns(source_results['header'].get('detail', []), (row_width, h1-h0),
                                     amount_key=operation['amount_key'], tax_id_key=operation['tax_id_key'])
        # A table calibration defines columns. The selected row may move down
        # without pinning its values to the previous row's vertical location.
        row_overrides = {key: (rect[0], 0., rect[2], 1.)
                         for key, rect in operation['overrides']['table'].items()}
        row = extract_row_fields(source_results['row'].get('detail', []), (row_width, y1-y0),
                                 headers=columns, overrides=row_overrides)
        for stage, fields in (('invoice', invoice), ('row', row)):
            if source_results[stage].get('status') == 'needs_review':
                for field in fields.values():
                    if field.get('value') is not None:
                        field['ambiguous'] = True
        checks = compare_fields(invoice, row, operation['amount_key'], operation['tax_id_key'])
        status = ('different' if any(check['status'] == 'different' for check in checks)
                  else 'matched' if all(check['status'] == 'matched' for check in checks) else 'review')
        snapshot = {'kind': 'manual_snapshot', 'snapshot_id': operation['snapshot_id'],
                    'generation': generation, 'captured_at_ns': operation['snapshot']['captured_at_ns'],
                    'regions': dict(operation['regions']), 'active_row': list(old_band['box']),
                    'invoice_fields': invoice, 'row_fields': row, 'checks': checks, 'status': status,
                    'message': '本次截圖核對結果；換列後請再按辨識。'}
        self._operation = None
        self.last_result = snapshot
        self._state_change('ready', snapshot['message'])
        if not self._closed and generation == self._generation:
            self.results_ready.emit(snapshot)

    @staticmethod
    def _selected_row(image, manual_row):
        if manual_row is None:
            return detect_active_row(image)
        try:
            # Calibration is a fallback, never a lock to an old Y coordinate
            # once the application clearly selects another row.
            return detect_active_row(image)
        except ValueError:
            selected = detect_active_row(image, manual_row=manual_row)
            b, g, r = (image[:, :, index].astype(np.int16) for index in range(3))
            blue = (b > 110) & (b-r > 45) & (b-g > 15)
            active = blue.sum(axis=1) >= max(3, image.shape[1]*.008)
            y0, y1 = selected['row']
            if np.any(active[:y0]) or np.any(active[y1:]):
                raise ValueError('目前停留位置已離開指定資料列，請重新指定。')
            return selected

    def _fail(self, message):
        if self._closed:
            return
        self._invalidate()
        if self._closed:
            return
        generation = self._generation
        self._state_change('error', str(message))
        if not self._closed and generation == self._generation:
            self.failed.emit(str(message))

    def _invalidate(self):
        self._revoking = True
        try:
            self._generation += 1
            self._cancel_event.set()
            self._settle.stop()
            self._capture_intent = None
            self._operation = None
            self.last_result = None
            self._capture.cancel()
            self._restore_visibility()
        finally:
            self._revoking = False

    def cancel(self):
        if self._closed:
            return
        self._invalidate()
        self._state_change('cancelling' if self.busy else 'idle',
                           '正在取消本次工作…' if self.busy else '已停止，按辨識才會重新讀取。')

    def shutdown(self):
        if self._closed:
            return
        self._closed = True
        self._invalidate()
        self._state_change('closed', '已關閉發票核對。')
        self._capture.shutdown()
        self._maybe_stopped()

    def _after_acknowledgement(self):
        if self._closed:
            self._maybe_stopped()
        elif self._state == 'cancelling' and not self.busy:
            self._state_change('idle', '已停止，按辨識才會重新讀取。')

    @Slot()
    def _maybe_stopped(self):
        if self.is_stopped and not self._stopped_announced:
            self._stopped_announced = True
            self._ocr.document_ocr_done.disconnect(self._recognized)
            self.stopped.emit()
