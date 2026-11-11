"""Real controller boundaries: same-frame search, cache and stale-row rejection."""
from threading import Event
from types import SimpleNamespace

import numpy as np
import pytest
from PySide6.QtCore import QObject, Signal

from src.comparison.shared_capture import SharedCaptureWorker, capture_shared, fingerprint_image, validate_regions
from src.comparison.shared_controller import SharedSearchController


class Capture(QObject):
    completed = Signal(str, object, object, object)
    failed = Signal(str, str, bool)
    finished = Signal()

    def __init__(self):
        super().__init__()
        self.requests = []

    def capture(self, *args):
        self.requests.append(args)

    def isRunning(self):
        return False

    def stop(self):
        pass

    def finish(self, values=None, *, row=8, band=True, index=-1):
        values = values or {}
        token, regions, *_ = self.requests[index]
        images = {}
        for name, (x, y, width, height, _) in regions.items():
            image = np.full((height, width, 3), values.get(name, 1 if name != 'B' else 2), np.uint8)
            if name == 'TRACK':
                image[:] = 245
                if band:
                    image[max(0, row - y):max(0, row - y) + 16] = (210, 100, 20)
                    image[max(0, row - y) + 4:max(0, row - y) + 7, 10:20] = values.get('row_text', 15)
            images[name] = image
        self.completed.emit(token, images, ((0, 0, 300, 200),),
                            {name: fingerprint_image(image) for name, image in images.items()})


class Ocr(QObject):
    document_ocr_done = Signal(str, int, str, object)

    def __init__(self):
        super().__init__()
        self.requests = []
        self.busy = False
        self.accepts_document_work = True
        self._engine = SimpleNamespace(model_profile='small', _max_ocr_passes=2)

    def queue_document_ocr(self, *args):
        if self.busy:
            return False
        self.requests.append(args)
        return True

    def finish(self, text='甲123', *, status='done', duplicate=False, index=-1):
        token, page, name, *_ = self.requests[index]
        detail = []
        if name == 'B':
            detail = [{'text': text, 'confidence': .99, 'box': [[0, 0], [60, 0], [60, 15], [0, 15]]}]
            if duplicate:
                detail.append({'text': text, 'confidence': .99, 'box': [[0, 30], [60, 30], [60, 45], [0, 45]]})
        self.document_ocr_done.emit(token, page, name, {'text': text, 'status': status, 'detail': detail})


@pytest.fixture
def controlled(qapp):
    ocr, capture = Ocr(), Capture()
    controller = SharedSearchController(ocr, capture_worker=capture, settle_ms=0)
    first = controller.fields[0]['id']
    assert controller.set_a_region(first, (5, 10, 15, 10, 1))
    assert controller.set_b_region((100, 0, 80, 80, 1))
    yield controller, ocr, capture, first
    controller.shutdown()


def take(controller, capture, values=None, **kwargs):
    assert controller._settle.isActive()
    controller._settle.stop()
    controller._take_capture()
    capture.finish(values, **kwargs)


def next_cycle(controller):
    controller._poll.stop()
    controller._begin_cycle()


def complete_search(controller, ocr, capture, values=None, *, duplicate=False):
    while controller._inflight is not None:
        ocr.finish(duplicate=duplicate)
    take(controller, capture, values)


def test_one_b_ocr_for_all_as_and_no_publish_before_revalidation(controlled):
    controller, ocr, capture, first = controlled
    second = controller.add_field('第二欄')
    assert controller.set_a_region(second, (30, 10, 15, 10, 1))
    updates = []
    controller.updated.connect(updates.append)
    assert controller.start()
    take(controller, capture)
    assert [request[2] for request in ocr.requests] == ['B']
    ocr.finish()
    ocr.finish()
    ocr.finish()
    assert not updates and all(field['result'] is None for field in controller.fields)
    assert [request[2] for request in ocr.requests] == ['B', first, second]
    take(controller, capture)
    assert len(updates) == 1
    assert all(field['result']['status'] == 'matched' for field in updates[0]['fields'])
    assert len(capture.requests) == 2 and not controller._images
    assert not controller._deadline.isActive()


@pytest.mark.parametrize('changed', ['A', 'B', 'settings'])
def test_changed_source_or_settings_during_ocr_cannot_publish_old_result(controlled, changed):
    controller, ocr, capture, first = controlled
    updates = []
    controller.updated.connect(updates.append)
    controller.start()
    take(controller, capture)
    ocr.finish()
    ocr.finish()
    values = {first: 9} if changed == 'A' else {'B': 9} if changed == 'B' else {}
    if changed == 'settings':
        ocr._engine._max_ocr_passes = 3
    take(controller, capture, values)
    assert not updates and controller.fields[0]['result'] is None
    assert controller.state == 'changed' and controller._poll.isActive()


def test_unchanged_skips_all_ocr_and_a_changes_reuse_only_b_cache(controlled):
    controller, ocr, capture, first = controlled
    updates = []
    controller.updated.connect(updates.append)
    controller.start()
    take(controller, capture)
    complete_search(controller, ocr, capture)
    original = updates[0]
    next_cycle(controller)
    take(controller, capture)
    assert len(ocr.requests) == 2 and updates[-1]['cycle'] == original['cycle']
    assert updates[-1]['timestamp'] == original['timestamp'] and updates[-1]['state'] == 'unchanged'
    next_cycle(controller)
    take(controller, capture, {first: 8})
    assert [request[2] for request in ocr.requests] == ['B', first, first]
    ocr.finish()
    take(controller, capture, {first: 8})
    assert updates[-1]['cycle'] == 2
    next_cycle(controller)
    take(controller, capture, {first: 8, 'B': 7})
    assert ocr.requests[-1][2] == 'B'


@pytest.mark.parametrize('signal_name', ['fields_changed', 'state_changed', 'updated'])
@pytest.mark.parametrize('cached', [False, True])
def test_result_callback_restart_cannot_cancel_new_capture_generation(controlled, signal_name, cached):
    controller, ocr, capture, first = controlled
    controller.start()
    take(controller, capture)
    if cached:
        complete_search(controller, ocr, capture)
        next_cycle(controller)
    else:
        ocr.finish()
        ocr.finish()
    new_tokens, updates = [], []

    def restart(*args):
        if new_tokens:
            return
        if signal_name == 'state_changed' and args[0] not in ('ready', 'unchanged'):
            return
        new_tokens.append(None)
        controller.pause()
        assert controller.start()
        new_tokens[0] = controller._token

    getattr(controller, signal_name).connect(restart)
    controller.updated.connect(updates.append)
    take(controller, capture)
    assert new_tokens and controller._token == new_tokens[0]
    assert controller._capture_generation == new_tokens[0]
    assert controller._settle.isActive() and controller._deadline.isActive()
    assert not controller._poll.isActive()
    # Listeners that invalidated the old result before publication must not receive it.
    if signal_name != 'updated':
        assert not updates
    take(controller, capture)
    complete_search(controller, ocr, capture)
    assert updates[-1]['fields'][0]['result']['status'] == 'matched'


def test_ocr_settings_change_invalidates_b_cache(controlled):
    controller, ocr, capture, first = controlled
    controller.start()
    take(controller, capture)
    complete_search(controller, ocr, capture)
    ocr._engine._max_ocr_passes = 3
    next_cycle(controller)
    take(controller, capture)
    assert ocr.requests[-1][2] == 'B' and len(ocr.requests) == 3


@pytest.mark.parametrize('stage', ['settle', 'capture', 'B', 'A', 'verify', 'poll'])
def test_pause_cancels_all_boundaries_and_restore_hook_runs_once(controlled, stage):
    controller, ocr, capture, first = controlled
    hooks, updates = [], []
    controller.before_capture = lambda: hooks.append('hide') or True
    controller.after_capture = lambda: hooks.append('restore')
    controller.updated.connect(updates.append)
    controller.start()
    if stage != 'settle':
        controller._settle.stop()
        controller._take_capture()
    if stage not in ('settle', 'capture'):
        capture.finish()
    if stage in ('A', 'verify', 'poll'):
        ocr.finish()
    if stage in ('verify', 'poll'):
        ocr.finish()
    if stage == 'poll':
        take(controller, capture)
    requests_before, captures_before = len(ocr.requests), len(capture.requests)
    controller.pause()
    assert not controller.running and not controller._images
    assert all(field['result'] is None for field in controller.fields)
    assert not any(timer.isActive() for timer in (controller._poll, controller._retry, controller._settle, controller._deadline))
    assert hooks.count('hide') == hooks.count('restore')
    if stage == 'capture':
        capture.finish()
    elif stage in ('B', 'A'):
        ocr.finish()
    controller._begin_cycle()
    assert len(ocr.requests) == requests_before and len(capture.requests) == captures_before
    assert hooks.count('hide') == hooks.count('restore')
    assert len(updates) == int(stage == 'poll')


@pytest.mark.parametrize('state', ['checking', 'reading', 'waiting', 'changed', 'tracking_pending', 'tracking_lost'])
@pytest.mark.parametrize('restart', [False, True])
def test_state_callback_cancellation_never_rearms_old_work(controlled, state, restart):
    controller, ocr, capture, first = controlled
    if state.startswith('tracking_'):
        calibrate(controller, capture)
    if state == 'waiting':
        ocr.busy = True
    callbacks = []

    def cancel(observed):
        if observed != state or callbacks:
            return
        callbacks.append(None)
        controller.pause()
        if restart:
            assert controller.start()
            callbacks[0] = controller._token

    controller.state_changed.connect(cancel)
    controller.start()
    if state != 'checking':
        take(controller, capture, band=state != 'tracking_lost')
    if state == 'changed':
        ocr.finish()
        ocr.finish()
        take(controller, capture, {first: 9})
    assert callbacks
    if restart:
        assert controller.running and controller._token == callbacks[0]
        assert controller._capture_generation == callbacks[0]
        assert controller._capture_phase == 'source'
        assert controller._settle.isActive() and controller._deadline.isActive()
        assert not controller._poll.isActive() and not controller._retry.isActive()
    else:
        assert not controller.running and controller.state == 'paused'
        assert not any(timer.isActive() for timer in (controller._poll, controller._retry,
                                                       controller._settle, controller._deadline))


def test_poll_clear_results_callback_restart_keeps_only_new_cycle(controlled):
    controller, ocr, capture, first = controlled
    controller.start()
    take(controller, capture)
    complete_search(controller, ocr, capture)
    tokens = []

    def restart():
        if tokens:
            return
        tokens.append(None)
        controller.pause()
        assert controller.start()
        tokens[0] = controller._token

    controller.fields_changed.connect(restart)
    next_cycle(controller)
    assert controller._token == tokens[0]
    assert controller._settle.isActive() and not controller._poll.isActive()


def test_candidate_choice_is_one_per_a_and_requires_current_frame(controlled):
    controller, ocr, capture, first = controlled
    updates = []
    controller.updated.connect(updates.append)
    controller.start()
    take(controller, capture)
    complete_search(controller, ocr, capture, duplicate=True)
    result = controller.fields[0]['result']
    assert result['status'] == 'ambiguous' and result['selected_id'] is None
    choice = result['candidates'][1]['id']
    assert controller.select_candidate(first, choice)
    assert controller.fields[0]['result']['selected_id'] is None
    take(controller, capture)
    assert controller.fields[0]['result']['selected_id'] == choice
    assert controller.fields[0]['result']['status'] == 'matched'
    assert len(ocr.requests) == 2
    assert controller.select_candidate(first, result['candidates'][0]['id'])
    take(controller, capture, {'B': 3})
    assert controller.fields[0]['result'] is None
    assert len(updates) == 2


def test_manual_rows_move_all_fields_and_reuse_b_without_auto_follow(controlled):
    controller, ocr, capture, first = controlled
    second = controller.add_field()
    controller.set_a_region(second, (30, 10, 15, 10, 1))
    controller.start()
    take(controller, capture)
    complete_search(controller, ocr, capture)
    assert controller.set_row_pitch(20)
    assert controller.move_rows(1)
    assert [field['region'][1] for field in controller.fields] == [30, 30]
    assert not controller.auto_follow
    take(controller, capture)
    assert ocr.requests[-1][2] == first
    assert sum(request[2] == 'B' for request in ocr.requests) == 1
    controller.pause()
    assert not controller.move_rows(-100) and [field['region'][1] for field in controller.fields] == [30, 30]


def calibrate(controller, capture):
    assert controller.set_tracking_region((0, 0, 80, 90, 1))
    assert controller.set_row_pitch(20)
    assert controller.calibrate_tracking()
    take(controller, capture)
    assert controller.calibrated and not controller.running
    assert controller.set_auto_follow(True)


def test_auto_follow_needs_stable_pair_then_moves_all_a_and_same_y_content_retriggers(controlled):
    controller, ocr, capture, first = controlled
    calibrate(controller, capture)
    controller.start()
    take(controller, capture)
    assert controller.state == 'tracking_pending' and not ocr.requests
    next_cycle(controller)
    take(controller, capture)
    complete_search(controller, ocr, capture)
    assert controller.fields[0]['result']['status'] == 'matched'
    next_cycle(controller)
    take(controller, capture, row=28)
    assert not controller.fields[0]['result'] and controller.state == 'tracking_pending'
    next_cycle(controller)
    take(controller, capture, row=28)
    assert controller.fields[0]['region'][1] == 30
    assert len(ocr.requests) == 2  # Moved coordinates require a new capture first.
    next_cycle(controller)
    take(controller, capture, {first: 4}, row=28)
    assert ocr.requests[-1][2] == first  # B cache survives selected-row changes.
    ocr.finish()
    take(controller, capture, {first: 4}, row=28)
    next_cycle(controller)
    take(controller, capture, {first: 5, 'row_text': 40}, row=28)
    assert len(ocr.requests) == 4
    controller.pause()
    assert controller.fields[0]['result'] is None


def test_gray_calibration_rejected_and_lost_tracking_backs_off(controlled):
    controller, ocr, capture, first = controlled
    controller.set_tracking_region((0, 0, 80, 90, 1))
    failures = []
    controller.failed.connect(failures.append)
    controller.calibrate_tracking()
    take(controller, capture, band=False)
    assert not controller.calibrated and failures and not controller.auto_follow
    calibrate(controller, capture)
    controller.start()
    take(controller, capture, band=False)
    assert controller.state == 'tracking_lost'
    assert controller._poll.interval() == controller.interval * 1000 and not ocr.requests


def test_calibration_rejects_fields_spanning_different_rows_even_with_dominant_blue(controlled):
    controller, ocr, capture, first = controlled
    second = controller.add_field('下一列欄位')
    assert controller.set_a_region(second, (30, 25, 15, 5, 1))
    assert controller.set_tracking_region((0, 0, 80, 90, 1))
    failures = []
    controller.failed.connect(failures.append)
    assert controller.calibrate_tracking()
    take(controller, capture)
    assert not controller.calibrated and not controller.auto_follow
    assert failures and '同一列' in failures[-1]
    assert all(field['result'] is None for field in controller.fields)


def test_primary_only_b_overlap_bounds_and_field_limits(controlled):
    controller, ocr, capture, first = controlled
    assert not controller.set_a_region(first, (5, 10, 15, 10, 2))
    assert not controller.set_a_region(first, (105, 10, 15, 10, 1))
    assert controller.fields[0]['region'] == (5, 10, 15, 10, 1)
    for i in range(19):
        assert controller.add_field() is not None
    assert controller.add_field() is None
    assert not controller.start()
    assert len(controller.fields) == 20
    assert not controller.rename_field(first, 'bad\nname')
    with pytest.raises(ValueError):
        validate_regions({str(i): (0, 0, 2000, 2000, 1) for i in range(10)})


def test_stale_cancelled_capture_does_not_accumulate_new_work(controlled):
    controller, ocr, capture, first = controlled
    controller.start()
    controller._settle.stop()
    controller._take_capture()
    for _ in range(8):
        controller.pause()
        assert controller.start()
    assert len(capture.requests) == 1
    capture.finish()
    assert not ocr.requests
    next_cycle(controller)
    take(controller, capture)
    assert len(capture.requests) == 2 and len(ocr.requests) == 1


def test_deadline_cancels_busy_queue_and_native_ocr_result(qtbot, qapp):
    ocr, capture = Ocr(), Capture()
    controller = SharedSearchController(ocr, capture_worker=capture, cycle_timeout_ms=40, settle_ms=0)
    first = controller.fields[0]['id']
    controller.set_a_region(first, (5, 10, 15, 10, 1))
    controller.set_b_region((100, 0, 80, 80, 1))
    failures = []
    controller.failed.connect(failures.append)
    try:
        ocr.busy = True
        controller.start()
        take(controller, capture)
        qtbot.waitUntil(lambda: bool(failures))
        assert not controller.running and controller._event.is_set()
        assert not controller._retry.isActive() and not controller._images
        ocr.busy = False
        controller._submit_next()
        assert not ocr.requests
    finally:
        controller.shutdown()


def test_capture_shared_uses_one_union_snapshot_and_independent_crops(monkeypatch):
    class Screen:
        monitors = [{'left': 10, 'top': 20, 'width': 200, 'height': 100}] * 2
        grabs = []
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def grab(self, region):
            self.grabs.append(region)
            pixels = np.zeros((region['height'], region['width'], 4), np.uint8)
            pixels[:, :, 0] = np.arange(region['width'])
            pixels[:, :, 1] = np.arange(region['height'])[:, None]
            return pixels
    screen = Screen()
    monkeypatch.setattr('src.comparison.shared_capture.mss.mss', lambda: screen)
    images, layout = capture_shared({'a': (5, 10, 10, 10, 1), 'B': (50, 20, 20, 10, 1), 'TRACK': (0, 0, 40, 50, 1)})
    assert screen.grabs == [{'left': 10, 'top': 20, 'width': 70, 'height': 50}]
    assert tuple(images['a'][0, 0]) == (5, 10, 0)
    assert tuple(images['B'][0, 0]) == (50, 20, 0)
    assert not np.shares_memory(images['a'], images['TRACK'])


def test_capture_worker_analysis_runs_off_gui_and_cancelled_results_are_empty(qapp, qtbot):
    from threading import get_ident
    owner = get_ident()
    threads, completed = [], []
    def snapshot(regions, layout):
        return {'B': np.ones((5, 5, 3), np.uint8)}, ((0, 0, 100, 100),)
    def analyze(images):
        threads.append(get_ident())
        return {'status': 'pending'}
    worker = SharedCaptureWorker(capture=snapshot)
    worker.completed.connect(lambda *args: completed.append(args))
    try:
        worker.capture('first', {'B': (0, 0, 5, 5, 1)}, None, Event(), processor=analyze)
        qtbot.waitUntil(lambda: bool(completed))
        assert threads and threads[0] != owner
        event = Event()
        event.set()
        worker.capture('cancelled', {'B': (0, 0, 5, 5, 1)}, None, event)
        qtbot.waitUntil(lambda: len(completed) == 2)
        assert completed[-1] == ('cancelled', None, None, None)
    finally:
        worker.stop()
        assert worker.wait(5000)


def test_real_controller_cancelled_ocr_releases_all_rasters_without_history_or_clipboard(qapp, qtbot):
    import gc
    import weakref
    from src.workers.ocr_worker import OcrWorker
    entered, release = Event(), Event()
    references, calls, history = [], [], []
    class Engine:
        def set_progress_callback(self, callback): pass
        def is_ready(self): return True
        def run_ocr(self, image, mode):
            calls.append(int(image[0, 0, 0]))
            entered.set()
            assert release.wait(5)
            return {'text': '甲123', 'status': 'done', 'detail': []}
    def snapshot(regions, layout):
        images = {name: np.full((region[3], region[2], 3), 1 if name == 'B' else 2, np.uint8)
                  for name, region in regions.items()}
        references.extend(weakref.ref(image) for image in images.values())
        return images, ((0, 0, 300, 200),)
    ocr = OcrWorker(Engine())
    capture = SharedCaptureWorker(capture=snapshot)
    controller = SharedSearchController(ocr, capture_worker=capture, settle_ms=0)
    first = controller.fields[0]['id']
    controller.set_a_region(first, (5, 10, 15, 10, 1))
    controller.set_b_region((100, 0, 80, 80, 1))
    updates = []
    controller.updated.connect(updates.append)
    ocr.ocr_done.connect(lambda *args: history.append(args))
    qapp.clipboard().setText('保留既有剪貼簿')
    try:
        controller.start()
        qtbot.waitUntil(entered.is_set)
        controller.pause()
        release.set()
        qtbot.waitUntil(lambda: not ocr._document_pending)
        qtbot.wait(50)
        gc.collect()
        assert calls == [1] and not history and not updates
        assert all(reference() is None for reference in references)
        assert not controller._images and controller.fields[0]['result'] is None
        assert qapp.clipboard().text() == '保留既有剪貼簿'
    finally:
        release.set()
        controller.shutdown()
        assert capture.wait(5000)
        ocr.stop()
        assert ocr.wait(5000)


def test_real_controller_calibration_and_tracking_analysis_never_run_in_gui(qapp, qtbot, monkeypatch):
    from threading import get_ident
    from src.comparison.row_tracking import RowTracker
    owner = get_ident()
    observed = []
    for method_name in ('calibrate', 'update'):
        original = getattr(RowTracker, method_name)
        def record(self, pixels, function=original, name=method_name):
            observed.append((name, get_ident()))
            return function(self, pixels)
        monkeypatch.setattr(RowTracker, method_name, record)
    def snapshot(regions, layout):
        images = {name: np.ones((region[3], region[2], 3), np.uint8) for name, region in regions.items()}
        if 'TRACK' in images:
            images['TRACK'][:] = 245
            images['TRACK'][8:24] = (210, 100, 20)
        return images, ((0, 0, 300, 200),)
    capture = SharedCaptureWorker(capture=snapshot)
    controller = SharedSearchController(Ocr(), capture_worker=capture, settle_ms=0)
    first = controller.fields[0]['id']
    controller.set_a_region(first, (5, 10, 15, 10, 1))
    controller.set_b_region((100, 0, 80, 80, 1))
    controller.set_tracking_region((0, 0, 80, 90, 1))
    try:
        assert controller.calibrate_tracking()
        qtbot.waitUntil(lambda: controller.calibrated)
        assert controller.set_auto_follow(True) and controller.start()
        qtbot.waitUntil(lambda: any(name == 'update' for name, _ in observed))
        assert all(thread != owner for _, thread in observed)
    finally:
        controller.shutdown()
        assert capture.wait(5000)


def test_out_of_bounds_ocr_box_is_uncertain_and_never_published_as_green(controlled):
    controller, ocr, capture, first = controlled
    controller.start()
    take(controller, capture)
    token, page, name, *_ = ocr.requests[-1]
    ocr.document_ocr_done.emit(token, page, name, {'text': '甲123', 'status': 'done',
        'detail': [{'text': '甲123', 'confidence': .99,
                    'box': [[0, 0], [500, 0], [500, 15], [0, 15]]}]})
    ocr.finish()
    take(controller, capture)
    assert controller.fields[0]['result']['status'] == 'uncertain'
    assert controller.fields[0]['result']['candidates'] == []
