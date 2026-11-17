"""Manual snapshots keep same-row data and retire every asynchronous generation."""
from copy import deepcopy
from threading import Event, get_ident

import numpy as np
import pytest
from PySide6.QtCore import QObject, Signal

from src.invoice_check.controller import InvoiceCheckController
from src.workers.ocr_worker import OcrWorker
from src.core.resource_budget import ResourceBudgetExceeded


REGIONS = {'invoice': (0, 0, 320, 200, 1), 'table': (350, 0, 500, 125, 1)}


@pytest.fixture(autouse=True)
def isolated_controller_budget(monkeypatch):
    # Functional Qt tests share pytest's retained models; separate resource
    # tests and the Windows child-process gate exercise live accounting.
    monkeypatch.setattr('src.invoice_check.controller.memory_preflight', lambda **_: None)


def pair():
    invoice = np.full((200, 320, 3), 255, dtype=np.uint8)
    invoice[10:20, 10:20] = 0
    table = np.full((125, 500, 3), 255, dtype=np.uint8)
    table[[0, 30, 60, 90, 120]] = 0
    table[32:59, 5:75] = (240, 60, 0)
    table[67:75, 300:320] = (0, 0, 200)  # An adjacent row must never enter OCR.
    return {'regions': dict(REGIONS), 'images': {'invoice': invoice, 'table': table}, 'captured_at_ns': 42}


def item(text, x, y, width=60):
    return {'text': text, 'raw_text': text, 'box': [x, y, width, 16], 'confidence': .99}


def invoice_detail():
    values = [('日期', '1150108'), ('發票號碼', 'AB12345678'), ('銷售額', '1,000'),
              ('營業稅', '50'), ('賣方統編', '04595257')]
    result = []
    for index, (label, value) in enumerate(values):
        result.extend([item(label, 5, index*35+5, 85), item(value, 100, index*35+5, 120)])
    return result


def stage_result(stage, *, needs_review=False):
    if stage == 'invoice':
        detail = invoice_detail()
    else:
        strings = (['銷售額', '營業稅', '日期', '發票號碼', '賣方統編'] if stage == 'header'
                   else ['1000', '50', '2026-01-08', 'AB12345678', '04595257'])
        detail = [item(text, index*100+10, 5, 80) for index, text in enumerate(strings)]
    return {'status': 'needs_review' if needs_review else 'done', 'text': 'synthetic', 'detail': detail}


class Capture(QObject):
    finished = Signal(str, object)
    stopped = Signal()

    def __init__(self):
        super().__init__()
        self.requests = []
        self.busy = False
        self.closed = False

    def request(self, token, regions, cancel_event):
        if self.closed or self.busy:
            return False
        self.requests.append((token, dict(regions), cancel_event))
        self.busy = True
        return True

    def finish(self, payload):
        token = self.requests[-1][0]
        self.busy = False
        self.finished.emit(token, payload)
        if self.closed:
            self.stopped.emit()

    def cancel(self):
        if self.requests and self.busy:
            self.requests[-1][2].set()

    def shutdown(self):
        self.closed = True
        self.cancel()
        if not self.busy:
            self.stopped.emit()


class Ocr(QObject):
    document_ocr_done = Signal(str, int, str, object)

    def __init__(self):
        super().__init__()
        self.requests = []
        self.reject = False

    def queue_document_ocr(self, token, index, stage, image, cancel_event=None):
        if self.reject:
            return False
        self.requests.append((token, index, stage, image, cancel_event))
        return True

    def finish(self, result=None):
        token, index, stage, _, _ = self.requests[-1]
        self.document_ocr_done.emit(token, index, stage, result or stage_result(stage))


@pytest.fixture
def owner(qapp):
    capture, ocr = Capture(), Ocr()
    controller = InvoiceCheckController(ocr, capture_service=capture, settle_ms=0)
    for side, region in REGIONS.items():
        controller.set_region(side, region)
    yield controller, capture, ocr
    controller.shutdown()
    if capture.busy:
        capture.finish(None)
    if controller._ocr_pending is not None:
        ocr.finish({'status': 'cancelled'})
    assert controller.is_stopped


def begin(owner, qtbot, snapshot=None):
    controller, capture, _ = owner
    count = len(capture.requests)
    assert controller.check_once()
    qtbot.waitUntil(lambda: len(capture.requests) == count+1)
    capture.finish(snapshot if snapshot is not None else pair())


def read_all(owner, qtbot, review_stage=None):
    controller, capture, ocr = owner
    count = len(capture.requests)
    while controller._ocr_pending is not None:
        stage = ocr.requests[-1][2]
        ocr.finish(stage_result(stage, needs_review=stage == review_stage))
    qtbot.waitUntil(lambda: len(capture.requests) == count+1)


def test_region_configuration_never_starts_capture_or_ocr(owner, qtbot):
    controller, capture, ocr = owner
    controller.set_field_options('total_amount', 'buyer_tax_id')
    controller.set_overrides('invoice', {'invoice_number': (0, 0, .5, .2)})
    qtbot.wait(20)
    assert not controller.busy and capture.requests == ocr.requests == []


def test_one_manual_round_reads_header_and_only_current_row_then_stops(owner, qtbot, qapp):
    controller, capture, ocr = owner
    results, previews = [], []
    controller.results_ready.connect(results.append)
    controller.preview_ready.connect(lambda side, image: previews.append((side, image.shape)))
    qapp.clipboard().setText('untouched')
    begin(owner, qtbot)
    assert not controller.check_once()  # No second work item can accumulate.
    read_all(owner, qtbot)
    assert [request[2] for request in ocr.requests] == ['invoice', 'header', 'row']
    row = ocr.requests[-1][3]
    assert row.shape == (29, 500, 3)
    assert not np.any(np.all(row == (0, 0, 200), axis=2))
    capture.finish(pair())
    assert results[0]['kind'] == 'manual_snapshot' and results[0]['status'] == 'matched'
    assert results[0]['active_row'] == [0, 31, 500, 29]
    assert len(results[0]['checks']) == 5 and not controller.busy
    assert previews == [('invoice', (200, 320, 3)), ('table', (125, 500, 3))]
    assert qapp.clipboard().text() == 'untouched'
    qtbot.wait(30)
    assert len(capture.requests) == 2 and len(ocr.requests) == 3


@pytest.mark.parametrize('change', ['invoice', 'unselected_column', 'selected_row'])
def test_changed_pair_is_rejected_before_any_result_is_published(owner, qtbot, change):
    controller, capture, _ = owner
    results = []
    controller.results_ready.connect(results.append)
    begin(owner, qtbot)
    read_all(owner, qtbot)
    newer = pair()
    if change == 'invoice':
        newer['images']['invoice'][15, 15] = 100
    elif change == 'unselected_column':
        newer['images']['table'][45, 450] = 0
    else:
        newer['images']['table'][32:59, 5:75] = 255
        newer['images']['table'][62:89, 5:75] = (240, 60, 0)
    capture.finish(newer)
    assert results == [] and controller.last_result is None and not controller.busy
    assert controller._state == 'error'


def test_neighbour_row_changes_do_not_enter_current_row_comparison(owner, qtbot):
    controller, capture, _ = owner
    begin(owner, qtbot)
    read_all(owner, qtbot)
    newer = pair()
    newer['images']['table'][100, 450] = 0
    capture.finish(newer)
    assert controller.last_result['status'] == 'matched'


def test_review_status_cannot_become_green_even_when_fields_match(owner, qtbot):
    controller, capture, _ = owner
    begin(owner, qtbot)
    read_all(owner, qtbot, review_stage='invoice')
    capture.finish(pair())
    assert controller.last_result['status'] == 'review'
    assert all(check['status'] == 'review' for check in controller.last_result['checks'])


def test_cancel_pending_shared_ocr_drains_without_starting_next_stage(owner, qtbot):
    controller, capture, ocr = owner
    begin(owner, qtbot)
    event = ocr.requests[0][4]
    controller.cancel()
    assert event.is_set() and controller.busy and not controller.check_once()
    ocr.finish()
    assert not controller.busy and controller.last_result is None
    qtbot.wait(20)
    assert len(ocr.requests) == 1 and len(capture.requests) == 1
    assert controller.check_once()


def test_cancel_during_settle_retires_timer_and_balances_capture_visibility(owner, qtbot):
    controller, capture, ocr = owner
    visibility = []
    controller.capture_started.connect(lambda: visibility.append('hide'))
    controller.capture_finished.connect(lambda: visibility.append('restore'))
    controller._settle.setInterval(40)
    assert controller.check_once()
    controller.cancel()
    qtbot.wait(60)
    assert capture.requests == ocr.requests == [] and visibility == ['hide', 'restore']


def test_shutdown_waits_for_worker_ack_and_late_result_cannot_restart(owner, qtbot):
    controller, capture, ocr = owner
    results = []
    controller.results_ready.connect(results.append)
    begin(owner, qtbot)
    controller.shutdown()
    assert not controller.is_stopped
    ocr.finish()
    assert controller.is_stopped and results == [] and not controller.check_once()
    assert len(capture.requests) == len(ocr.requests) == 1


def test_capture_started_close_cannot_schedule_a_late_capture(owner, qtbot):
    controller, capture, ocr = owner
    controller.capture_started.connect(controller.shutdown)
    assert not controller.check_once()
    qtbot.wait(20)
    assert controller.is_stopped and capture.requests == ocr.requests == []


def test_close_from_ready_notification_prevents_late_result_publication(owner, qtbot):
    controller, capture, _ = owner
    results = []
    controller.results_ready.connect(results.append)
    controller.state_changed.connect(lambda state, message: controller.shutdown() if state == 'ready' else None)
    begin(owner, qtbot)
    read_all(owner, qtbot)
    capture.finish(pair())
    assert controller.is_stopped and results == [] and controller.last_result is None


def test_synchronous_capture_failure_releases_busy_and_visibility(owner, qtbot, monkeypatch):
    controller, capture, _ = owner
    restored = []
    controller.capture_finished.connect(lambda: restored.append(True))
    monkeypatch.setattr(capture, 'request', lambda *args: (_ for _ in ()).throw(RuntimeError('fixture')))
    assert controller.check_once()
    qtbot.waitUntil(lambda: controller._state == 'error')
    assert restored == [True] and not controller.busy


def test_ambiguous_blue_rows_fail_without_ocr_or_table_fallback(owner, qtbot):
    controller, capture, ocr = owner
    original = pair()
    original['images']['table'][62:89, 5:75] = (240, 60, 0)
    begin(owner, qtbot, original)
    assert ocr.requests == [] and controller._state == 'error' and not controller.busy


def test_configuration_save_after_reentrant_shutdown_is_ignored(owner):
    controller, _, _ = owner
    previous = deepcopy(controller.regions)
    controller.state_changed.connect(lambda *_: controller.shutdown() if not controller._closed else None)
    assert not controller.set_region('invoice', (0, 0, 250, 150, 1))
    assert controller.regions == previous and controller.is_stopped


def test_other_monitor_rejected_without_replacing_existing_range(owner):
    controller, _, _ = owner
    previous = controller.regions
    with pytest.raises(ValueError, match='主要螢幕'):
        controller.set_region('invoice', (0, 0, 320, 200, 2))
    assert controller.regions == previous and not controller.busy


def test_manual_row_calibration_does_not_lock_to_old_row_when_blue_moves(owner, qtbot):
    controller, capture, ocr = owner
    controller.set_manual_row((31, 60))
    moved = pair()
    moved['images']['table'][32:59, 5:75] = 255
    moved['images']['table'][62:89, 5:75] = (240, 60, 0)
    begin(owner, qtbot, moved)
    read_all(owner, qtbot)
    row_crop = ocr.requests[-1][3]
    assert np.any(np.all(row_crop == (0, 0, 200), axis=2))
    capture.finish(moved)
    assert controller.last_result['active_row'] == [0, 61, 500, 29]


def test_manual_fallback_refuses_blue_outside_band_when_grid_is_ambiguous(owner, qtbot):
    controller, _, ocr = owner
    controller.set_manual_row((31, 60))
    moved = pair()
    moved['images']['table'][32:59, 5:75] = 255
    moved['images']['table'][62:89, 5:75] = (240, 60, 0)
    moved['images']['table'][60] = 255  # The selected row's top boundary is absent.
    begin(owner, qtbot, moved)
    assert ocr.requests == [] and controller.state == 'error'


def manual_gray_table(width=400):
    image = np.full((125, width, 3), 255, np.uint8)
    image[[0, 30, 60, 90, 120]] = 0
    image[32:59, 5:75] = (190, 190, 190)
    # Native ClearType can produce blue glyph edges away from a gray row.
    for x in (40, 80, 120, 160, 200):
        image[10:22, x] = (240, 60, 0)
        image[10:22, x+1:x+4] = 0
    return image


@pytest.mark.parametrize('scale', [1, 2])
def test_manual_gray_row_uses_shared_absent_selection_analysis_with_cleartype(scale):
    image = np.repeat(np.repeat(manual_gray_table(), scale, axis=0), scale, axis=1)
    selected = InvoiceCheckController._selected_row(image, (31*scale, 60*scale))
    assert selected['source'] == 'manual'
    assert selected['row'] == [31*scale, 60*scale]


@pytest.mark.parametrize('scale', [1, 2])
@pytest.mark.parametrize('evidence', ['outside_missing_grid', 'multiple', 'gradient', 'thin_unknown'])
def test_manual_row_cannot_override_true_or_ambiguous_blue_evidence(scale, evidence):
    image = manual_gray_table()
    if evidence == 'outside_missing_grid':
        image[62:89, 5:75] = (240, 60, 0)
        image[60] = 255
    elif evidence == 'multiple':
        image[32:59, 5:75] = (240, 60, 0)
        image[62:89, 5:75] = (240, 60, 0)
    elif evidence == 'gradient':
        for y in range(63, 88):
            image[y, 250:254] = (215+y-63, 60, 0)
        image[63:88, 254:256] = (0, 60, 220)
    else:
        image[10:22, 250:254] = (240, 60, 0)
        image[10:22, 254:256] = 0
    image = np.repeat(np.repeat(image, scale, axis=0), scale, axis=1)
    with pytest.raises(ValueError):
        InvoiceCheckController._selected_row(image, (31*scale, 60*scale))


@pytest.mark.parametrize('scale', [1, 2])
def test_old_manual_row_refuses_singleton_gradient_selection_elsewhere(scale):
    image = np.full((140, 400, 3), 255, np.uint8)
    image[[0, 30, 60, 90, 120, 139]] = 0
    # The only blue evidence is a different row's narrow gradient selection.
    # Its adjacent red ink must not turn positive selection into typed absence.
    for y in range(63, 88):
        image[y, 200:204] = (215+y-63, 60, 0)
    image[63:88, 204:206] = (0, 60, 220)
    image = np.repeat(np.repeat(image, scale, axis=0), scale, axis=1)
    with pytest.raises(ValueError):
        InvoiceCheckController._selected_row(image, (31*scale, 60*scale))


@pytest.mark.parametrize('changed', [False, True])
def test_manual_gray_row_checks_snapshot_and_rejects_later_selected_row(owner, qtbot, changed):
    controller, capture, ocr = owner
    controller.set_manual_row((31, 60))
    controller.set_header_row((1, 30))
    original = pair()
    original['images']['table'] = manual_gray_table(width=500)
    begin(owner, qtbot, original)
    read_all(owner, qtbot)
    assert [request[2] for request in ocr.requests] == ['invoice', 'header', 'row']
    assert ocr.requests[-1][3].shape == (29, 500, 3)
    verification = deepcopy(original)
    if changed:
        verification['images']['table'][62:89, 5:75] = (240, 60, 0)
    capture.finish(verification)
    if changed:
        assert controller.state == 'error' and controller.last_result is None
    else:
        assert controller.state == 'ready'
        assert controller.last_result['active_row'] == [0, 31, 500, 29]
        assert controller.last_result['status'] == 'matched'
    assert not controller.busy
    qtbot.wait(20)
    assert len(capture.requests) == 2 and len(ocr.requests) == 3


def test_memory_exhaustion_does_not_submit_ocr_or_leave_a_busy_lease(owner, qtbot, monkeypatch):
    controller, capture, ocr = owner
    def exhausted(**_):
        raise ResourceBudgetExceeded('memory')
    monkeypatch.setattr('src.invoice_check.controller.memory_preflight', exhausted)
    begin(owner, qtbot)
    assert controller.state == 'error' and not controller.busy
    assert len(capture.requests) == 1 and ocr.requests == []


def initial_calibration_read(owner, qtbot, *, invoice=None, row=None):
    begin(owner, qtbot)
    ocr = owner[2]
    ocr.finish(invoice or stage_result('invoice'))
    ocr.finish(stage_result('header'))
    ocr.finish(row or stage_result('row'))


def missing_amount_result():
    result = stage_result('invoice')
    result['detail'] = [observation for observation in result['detail']
                        if observation['raw_text'] != '1,000']
    result['detail'].append(item('AMOUNT1000', 0, 75, 220))
    return result


def amount_crop_result(value='1000', status='done'):
    return {'status': status, 'detail': [{'text': value, 'raw_text': value,
             'box': [2, 2, 80, 12], 'confidence': .99}]}


@pytest.mark.parametrize('value,status,expected', [('1000', 'done', 'matched'),
                                                 ('2000', 'done', 'different'),
                                                 ('1000', 'needs_review', 'review')])
def test_explicit_roi_recovers_joined_field_without_guessing_or_forcing_green(owner, qtbot, value, status, expected):
    controller, capture, ocr = owner
    controller.set_overrides('invoice', {'net_amount': (100/320, 70/200, 120/320, 16/200)})
    initial_calibration_read(owner, qtbot, invoice=missing_amount_result())
    assert ocr.requests[-1][2] == 'calibration_invoice_net_amount'
    assert ocr.requests[-1][3].shape == (16, 120, 3)
    ocr.finish(amount_crop_result(value, status))
    qtbot.waitUntil(lambda: len(capture.requests) == 2)
    capture.finish(pair())
    result = controller.last_result
    assert result['status'] == expected
    assert result['invoice_fields']['net_amount']['box'] == [102., 72., 80., 12.]
    assert len(ocr.requests) == 4 and not controller.busy


def test_existing_uncertain_value_is_retained_on_calibrated_crop_conflict(owner, qtbot):
    controller, capture, ocr = owner
    controller.set_overrides('invoice', {'net_amount': (100/320, 70/200, 120/320, 25/200)})
    initial_calibration_read(owner, qtbot, invoice=stage_result('invoice', needs_review=True))
    ocr.finish(amount_crop_result('9000'))
    qtbot.waitUntil(lambda: len(capture.requests) == 2)
    capture.finish(pair())
    field = controller.last_result['invoice_fields']['net_amount']
    assert field['value'] == '1000' and field['ambiguous']
    assert {candidate['value'] for candidate in field['candidates']} == {'1000', '9000'}
    assert controller.last_result['status'] == 'review'


def test_calibration_is_once_bounded_to_compared_five_fields_and_keeps_only_descriptors(owner, qtbot):
    from src.invoice_check.fields import FIELD_KEYS
    controller, capture, ocr = owner
    mapping = dict.fromkeys(FIELD_KEYS, (0., 0., 1., 1.))
    controller.set_overrides('invoice', mapping)
    controller.set_overrides('table', mapping)
    initial_calibration_read(owner, qtbot, invoice=stage_result('invoice', needs_review=True),
                             row=stage_result('row', needs_review=True))
    tasks = controller._operation['calibration_tasks']
    assert len(tasks) == 10 and len(ocr.requests) == 4
    assert all(set(task) == {'side', 'key', 'rect', 'region'} for task in tasks.values())
    assert all(task['key'] not in ('total_amount', 'buyer_tax_id') for task in tasks.values())
    for _ in range(10):
        ocr.finish({'status': 'done', 'detail': []})
    qtbot.waitUntil(lambda: len(capture.requests) == 2)
    capture.finish(pair())
    assert len(ocr.requests) == 13
    assert len({request[2] for request in ocr.requests[3:]}) == 10
    assert controller.last_result['status'] == 'review' and not controller.busy


@pytest.mark.parametrize('stop', ['cancel', 'shutdown'])
def test_calibrated_crop_late_ack_cannot_publish_or_queue_after_stop(owner, qtbot, stop):
    controller, capture, ocr = owner
    controller.set_overrides('invoice', {'net_amount': (100/320, 70/200, 120/320, 16/200)})
    initial_calibration_read(owner, qtbot, invoice=missing_amount_result())
    getattr(controller, stop)()
    assert ocr.requests[-1][4].is_set() and controller.busy
    ocr.finish(amount_crop_result())
    qtbot.wait(20)
    assert controller.last_result is None and len(ocr.requests) == 4 and len(capture.requests) == 1
    assert not controller.busy


@pytest.mark.parametrize('reject', ['budget', 'cancel', 'cancel_then_budget'])
def test_crop_budget_boundary_never_submits_after_denial_or_reentrant_cancel(owner, qtbot, monkeypatch, reject):
    controller, capture, ocr = owner
    controller.set_overrides('invoice', {'net_amount': (100/320, 70/200, 120/320, 16/200)})
    begin(owner, qtbot)
    ocr.finish(missing_amount_result())
    ocr.finish(stage_result('header'))
    def boundary(**_):
        if reject == 'budget':
            raise ResourceBudgetExceeded('memory')
        controller.cancel()
        if reject == 'cancel_then_budget':
            raise ResourceBudgetExceeded('memory')
    monkeypatch.setattr('src.invoice_check.controller.memory_preflight', boundary)
    ocr.finish(stage_result('row'))
    assert len(ocr.requests) == 3 and len(capture.requests) == 1
    assert not controller.busy and controller.last_result is None
    assert controller.state == ('error' if reject == 'budget' else 'idle')


def test_calibrated_read_keeps_final_same_row_verification(owner, qtbot):
    controller, capture, ocr = owner
    controller.set_overrides('invoice', {'net_amount': (100/320, 70/200, 120/320, 16/200)})
    initial_calibration_read(owner, qtbot, invoice=missing_amount_result())
    ocr.finish(amount_crop_result())
    qtbot.waitUntil(lambda: len(capture.requests) == 2)
    changed = pair()
    changed['images']['table'][45, 450] = 0
    capture.finish(changed)
    assert controller.state == 'error' and controller.last_result is None


def test_table_calibration_rereads_only_active_row_and_keeps_row_local_coordinates(owner, qtbot):
    controller, capture, ocr = owner
    controller.set_overrides('table', {'net_amount': (.02, .8, .16, .1)})
    row = stage_result('row')
    row['detail'].pop(0)
    initial_calibration_read(owner, qtbot, row=row)
    assert ocr.requests[-1][2] == 'calibration_row_net_amount'
    assert ocr.requests[-1][3].shape == (29, 80, 3)
    ocr.finish({'status': 'done', 'detail': [item('1000', 2, 3, 60)]})
    qtbot.waitUntil(lambda: len(capture.requests) == 2)
    capture.finish(pair())
    assert controller.last_result['row_fields']['net_amount']['box'] == [12., 3., 60., 16.]
    assert controller.last_result['active_row'] == [0, 31, 500, 29]


def test_calibrated_quad_and_alternative_boxes_translate_without_mutating_raw_result():
    detail = [{'box': [[1, 2], [9, 2], [9, 8], [1, 8]], 'raw_text': '1000',
               'alternatives': [{'box': [2, 3, 6, 4], 'raw_text': '1001'}]}]
    original = deepcopy(detail)
    translated = InvoiceCheckController._translated_detail(detail, 100, 70)
    assert translated[0]['box'] == [[101, 72], [109, 72], [109, 78], [101, 78]]
    assert translated[0]['alternatives'][0]['box'] == [102, 73, 6, 4]
    assert detail == original


class VisualEngine:
    """A finite image oracle to exercise actual Qt worker ownership."""
    def __init__(self, block=False):
        self.calls = []
        self.entered, self.release = Event(), Event()
        if not block:
            self.release.set()

    def set_progress_callback(self, callback):
        self.progress = callback

    def is_ready(self):
        return True

    def run_cancellable_ocr(self, image, mode, event):
        self.calls.append((get_ident(), image.shape))
        self.entered.set()
        self.release.wait(3)
        if event.is_set():
            return {'status': 'cancelled'}
        stage = ('invoice' if image.shape[0] == 200 else
                 'row' if np.any(image[:, :, 0] > image[:, :, 2]) else 'header')
        return stage_result(stage)

    def run_invoice_ocr(self, image, mode='screen', *, cancel_event=None):
        return self.run_cancellable_ocr(image, mode, cancel_event)


def test_real_shared_ocr_worker_serializes_all_three_crops(qtbot):
    engine = VisualEngine()
    ocr, capture = OcrWorker(engine), Capture()
    controller = InvoiceCheckController(ocr, capture_service=capture, settle_ms=0)
    for side, region in REGIONS.items():
        controller.set_region(side, region)
    try:
        begin((controller, capture, ocr), qtbot)
        qtbot.waitUntil(lambda: len(capture.requests) == 2, timeout=5000)
        capture.finish(pair())
        assert controller.last_result['status'] == 'matched'
        assert len(engine.calls) == 3
        assert len({thread for thread, _ in engine.calls}) == 1
        assert engine.calls[0][0] != get_ident()
        assert not ocr._document_pending
    finally:
        controller.shutdown()
        engine.release.set()
        ocr.stop()
        qtbot.waitUntil(lambda: controller.is_stopped and not ocr.isRunning(), timeout=5000)
        ocr.wait()


def test_shutdown_during_real_inference_drains_one_cancelled_task(qtbot):
    engine = VisualEngine(block=True)
    ocr, capture = OcrWorker(engine), Capture()
    controller = InvoiceCheckController(ocr, capture_service=capture, settle_ms=0)
    for side, region in REGIONS.items():
        controller.set_region(side, region)
    stopped, results = [], []
    controller.stopped.connect(lambda: stopped.append(True))
    controller.results_ready.connect(results.append)
    try:
        begin((controller, capture, ocr), qtbot)
        qtbot.waitUntil(engine.entered.is_set)
        controller.shutdown()
        assert not controller.is_stopped and not controller.check_once()
        engine.release.set()
        qtbot.waitUntil(lambda: controller.is_stopped)
        assert len(engine.calls) == 1 and stopped == [True] and results == []
    finally:
        engine.release.set()
        controller.shutdown()
        ocr.stop()
        qtbot.waitUntil(lambda: controller.is_stopped and not ocr.isRunning(), timeout=5000)
        ocr.wait()


def test_busy_document_raster_slot_is_not_cancelled_or_retried_by_invoice(qtbot):
    engine = VisualEngine(block=True)
    ocr, capture = OcrWorker(engine), Capture()
    controller = InvoiceCheckController(ocr, capture_service=capture, settle_ms=0)
    for side, region in REGIONS.items():
        controller.set_region(side, region)
    other_event = Event()
    try:
        assert ocr.queue_document_ocr('other-document', 0, 'other', np.zeros((20, 20, 3), dtype=np.uint8), other_event)
        qtbot.waitUntil(engine.entered.is_set)
        begin((controller, capture, ocr), qtbot)
        assert controller.state == 'error' and not controller.busy
        assert not other_event.is_set()
        engine.release.set()
        qtbot.waitUntil(lambda: not ocr._document_pending)
        assert len(engine.calls) == 1 and len(capture.requests) == 1
    finally:
        engine.release.set()
        controller.shutdown()
        ocr.stop()
        qtbot.waitUntil(lambda: controller.is_stopped and not ocr.isRunning(), timeout=5000)
        ocr.wait()
