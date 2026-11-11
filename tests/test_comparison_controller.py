"""Paired snapshots, serial transient OCR, and stale-result cancellation."""
from threading import Event

import numpy as np
import pytest
from PySide6.QtCore import QObject, Signal

from src.comparison.capture import (
    ComparisonCaptureError,
    ComparisonCaptureWorker,
    capture_pair,
    fingerprint_pair,
)
from src.comparison.controller import (
    ComparisonController,
    compare_results,
    normalize_text,
)
from src.workers.ocr_worker import OcrWorker


@pytest.mark.parametrize(('first', 'second', 'mode', 'expected'), [
    ('甲， 123！', '甲123', 'normalized', 'match'),
    ('甲\n123', '甲 123', 'exact', 'mismatch'),
    ('AB', 'ab', 'normalized', 'mismatch'),
    ('１２', '12', 'normalized', 'mismatch'),
    ('-1.23元', '123元', 'normalized', 'match'),
    ('-1.23元', '123元', 'exact', 'mismatch'),
    ('é', 'e\u0301', 'normalized', 'match'),
    ('é', 'e', 'normalized', 'mismatch'),
    ('क', 'कि', 'normalized', 'mismatch'),
    ('，！', '', 'normalized', 'uncertain'),
    (' ', '\n', 'exact', 'uncertain'),
])
def test_comparison_rules(first, second, mode, expected):
    assert compare_results({'text': first, 'status': 'done'},
                           {'text': second, 'status': 'done'}, mode) == expected


@pytest.mark.parametrize('status', ['needs_review', 'failed', 'cancelled', None])
def test_rejected_or_uncertain_ocr_never_proves_match(status):
    assert compare_results({'text': '123', 'status': status},
                           {'text': '123', 'status': 'done'}) == 'uncertain'
    assert compare_results({'text': '123', 'status': 'done'},
                           {'text': '456', 'status': status}) == 'uncertain'


def test_normalized_keeps_words_numbers_and_combining_marks_only():
    assert normalize_text('A+甲１٢Ⅲ🏠$€\tक\u093f\n') == 'A甲１٢Ⅲकि'


class Screen:
    def __init__(self):
        self.monitors = [{'left': -200, 'top': 30, 'width': 200, 'height': 120},
                         {'left': -200, 'top': 30, 'width': 200, 'height': 120}]
        self.grabs = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def grab(self, region):
        self.grabs.append(region)
        pixels = np.zeros((region['height'], region['width'], 4), dtype=np.uint8)
        pixels[:, :, 0] = np.arange(region['width'])
        pixels[:, :, 1] = np.arange(region['height'])[:, None]
        pixels[:, :, 2] = len(self.grabs)
        return pixels


def test_both_crops_share_one_snapshot_and_keep_monitor_relative_coordinates(monkeypatch):
    screen = Screen()
    monkeypatch.setattr('src.comparison.capture.mss.mss', lambda: screen)
    regions = {'A': (10, 20, 10, 10, 1), 'B': (60, 30, 20, 10, 1)}
    images, layout = capture_pair(regions)
    assert screen.grabs == [{'left': -190, 'top': 50, 'width': 70, 'height': 20}]
    assert tuple(images['A'][0, 0]) == (0, 0, 1)
    assert tuple(images['B'][0, 0]) == (50, 10, 1)
    assert not np.shares_memory(images['A'], images['B'])
    assert images['A'].flags.c_contiguous and images['B'].flags.c_contiguous
    screen.monitors[1]['width'] = 199
    with pytest.raises(ComparisonCaptureError, match='配置已變更'):
        capture_pair(regions, layout)
    assert len(screen.grabs) == 1


@pytest.mark.parametrize('a,b', [
    ((0, 0, 10, 10, 1), (5, 5, 10, 10, 1)),
    ((0, 0, 10, 10, 1), (20, 20, 10, 10, 2)),
    ((0, 0, 10, 10, 0), (20, 20, 10, 10, 0)),
    ((0, 0, 10, 10, 1), (195, 20, 10, 10, 1)),
    ((0, 0, 10000, 10000, 1), (20, 20, 10, 10, 1)),
])
def test_invalid_capture_positions_never_grab(monkeypatch, a, b):
    screen = Screen()
    monkeypatch.setattr('src.comparison.capture.mss.mss', lambda: screen)
    with pytest.raises(ComparisonCaptureError):
        capture_pair({'A': a, 'B': b})
    assert screen.grabs == []


class FakeCapture(QObject):
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

    def finish(self, index=-1, values=(1, 2)):
        token = self.requests[index][0]
        images = {name: np.full((5, 5, 3), value, dtype=np.uint8)
                  for name, value in zip(('A', 'B'), values)}
        self.completed.emit(token, images, ((0, 0, 100, 100),), fingerprint_pair(images))


class FakeOcr(QObject):
    document_ocr_done = Signal(str, int, str, object)

    def __init__(self):
        super().__init__()
        self.requests = []
        self.accepts_document_work = True
        self.busy = False

    def queue_document_ocr(self, *args):
        if self.busy:
            return False
        self.requests.append(args)
        return True

    def finish(self, text, status='done', index=-1):
        token, page, name, *_ = self.requests[index]
        self.document_ocr_done.emit(token, page, name, {'text': text, 'status': status})


@pytest.fixture
def controlled(qapp):
    ocr, capture = FakeOcr(), FakeCapture()
    controller = ComparisonController(ocr, capture_worker=capture)
    controller.set_region('A', (10, 20, 10, 10, 1))
    controller.set_region('B', (60, 30, 20, 10, 1))
    yield controller, ocr, capture
    controller.shutdown()


def test_single_pair_serial_ocr_repeats_unchanged_positions(qtbot, controlled):
    controller, ocr, capture = controlled
    updates = []
    controller.updated.connect(updates.append)
    assert controller.start()
    assert len(capture.requests) == 1 and not ocr.requests
    capture.finish()
    assert [r[2] for r in ocr.requests] == ['A']
    assert not controller._cycle_timer.isActive()
    ocr.finish('甲，123')
    assert [r[2] for r in ocr.requests] == ['A', 'B']
    ocr.finish('甲123')
    assert updates[-1]['verdict'] == 'match' and updates[-1]['cycle'] == 1
    assert controller._images == {} and controller._cycle_timer.isActive()
    controller._cycle_timer.start(0)
    qtbot.waitUntil(lambda: len(capture.requests) == 2)
    assert capture.requests[0][1] == capture.requests[1][1]
    assert capture.requests[0][0] != capture.requests[1][0]


def test_pause_discards_late_ocr_and_reuses_positions(qtbot, controlled):
    controller, ocr, capture = controlled
    updates = []
    controller.updated.connect(updates.append)
    controller.start()
    capture.finish()
    old_request = ocr.requests[0]
    controller.pause()
    assert old_request[-1].is_set()
    assert not controller._retry.isActive() and not controller._cycle_timer.isActive()
    ocr.finish('舊結果')
    assert not updates and len(ocr.requests) == 1
    assert controller.start()
    capture.finish()
    ocr.finish('新資料')
    ocr.finish('新資料')
    assert updates[-1]['a'] == '新資料'


def test_cancelled_capture_cannot_create_a_backlog(qtbot, controlled):
    controller, ocr, capture = controlled
    controller.start()
    for _ in range(10):
        controller.pause()
        controller.start()
    assert len(capture.requests) == 1 and capture.requests[0][-1].is_set()
    capture.finish(0)
    assert not ocr.requests
    qtbot.waitUntil(lambda: len(capture.requests) == 2)
    capture.finish()
    assert len(ocr.requests) == 1


def test_occupied_ocr_slot_retries_without_accepting_extra_frames(qtbot, controlled):
    controller, ocr, capture = controlled
    ocr.busy = True
    controller.start()
    capture.finish()
    qtbot.wait(150)
    assert len(capture.requests) == 1 and not ocr.requests
    assert controller._retry.isActive()
    ocr.busy = False
    qtbot.waitUntil(lambda: len(ocr.requests) == 1)
    assert not controller._retry.isActive()


@pytest.mark.parametrize('restart', [False, True])
def test_waiting_callback_cancellation_cannot_rearm_retry(controlled, restart):
    controller, ocr, capture = controlled
    ocr.busy = True
    callbacks = []

    def cancel(state):
        if state != 'waiting' or callbacks:
            return
        callbacks.append(state)
        controller.pause()
        if restart:
            assert controller.start()

    controller.state_changed.connect(cancel)
    controller.start()
    capture.finish()
    assert callbacks and not controller._retry.isActive()
    assert controller.running == restart


def test_checking_callback_restart_releases_unsubmitted_capture(controlled):
    controller, ocr, capture = controlled
    callbacks = []

    def restart(state):
        if state != 'checking' or callbacks:
            return
        callbacks.append(state)
        controller.pause()
        assert controller.start()

    controller.state_changed.connect(restart)
    controller.start()
    assert not capture.requests and controller._capture_token is None
    controller._cycle_timer.stop()
    controller._begin_cycle()
    assert len(capture.requests) == 1 and capture.requests[0][0] == controller._token
    assert controller._token is not None
    capture.finish()
    assert len(ocr.requests) == 1


def test_display_guard_blocks_capture_and_layout_change_requires_reselection(controlled):
    controller, _, capture = controlled
    controller.before_capture = lambda: False
    assert not controller.start() and not capture.requests
    controller.before_capture = lambda: True
    assert controller.start()
    token = capture.requests[0][0]
    capture.failed.emit(token, '螢幕配置已變更', True)
    assert not controller.running and not controller.regions
    assert not controller.start()


class Engine:
    def __init__(self):
        self.ready = False
        self.calls = []

    def set_progress_callback(self, _):
        pass

    def is_ready(self):
        return self.ready

    def load(self, progress_cb):
        self.ready = True

    def run_ocr(self, array, _):
        self.calls.append(int(array[0, 0, 0]))
        return {'text': '測試123', 'status': 'done'}


def test_real_workers_leave_clipboard_and_history_channel_untouched(qapp, qtbot):
    def snapshot(regions, layout):
        return {'A': np.full((5, 5, 3), 1, np.uint8),
                'B': np.full((5, 5, 3), 2, np.uint8)}, ((0, 0, 100, 100),)
    engine = Engine()
    ocr = OcrWorker(engine)
    capture = ComparisonCaptureWorker(capture=snapshot)
    controller = ComparisonController(ocr, capture_worker=capture)
    updates, history = [], []
    controller.updated.connect(updates.append)
    ocr.ocr_done.connect(lambda *args: history.append(args))
    qapp.clipboard().setText('原本剪貼簿')
    try:
        controller.set_region('A', (0, 0, 5, 5, 1))
        controller.set_region('B', (10, 0, 5, 5, 1))
        controller.start()
        qtbot.waitUntil(lambda: bool(updates))
        controller.pause()
        assert updates[0]['verdict'] == 'match' and engine.calls == [1, 2]
        assert not history and qapp.clipboard().text() == '原本剪貼簿'
        assert controller._images == {} and controller._results == {}
    finally:
        controller.shutdown()
        assert capture.wait(5000)
        ocr.stop()
        assert ocr.wait(5000)


def test_shutdown_cancels_owned_inflight_capture(qtbot):
    entered, release = Event(), Event()
    def snapshot(regions, layout):
        entered.set()
        assert release.wait(5)
        return {'A': np.zeros((5, 5, 3), np.uint8), 'B': np.zeros((5, 5, 3), np.uint8)}, ()
    capture = ComparisonCaptureWorker(capture=snapshot)
    ocr = FakeOcr()
    controller = ComparisonController(ocr, capture_worker=capture)
    try:
        controller.set_region('A', (0, 0, 5, 5, 1))
        controller.set_region('B', (10, 0, 5, 5, 1))
        controller.start()
        qtbot.waitUntil(entered.is_set)
        controller.shutdown()
        assert not controller.running and controller._event.is_set()
        release.set()
        qtbot.waitUntil(lambda: controller.is_stopped)
        assert not ocr.requests
    finally:
        release.set()
        controller.shutdown()
        assert capture.wait(5000)


@pytest.mark.parametrize('mark', ['\u0301', '\u093f', '\ufe0f'])
def test_isolated_marks_never_count_as_readable_text(mark):
    assert compare_results({'text': mark, 'status': 'done'},
                           {'text': mark, 'status': 'done'}) == 'uncertain'


@pytest.mark.parametrize('stage', ['capture', 'queued', 'inference'])
def test_cycle_deadline_cancels_queue_and_ignores_late_results(qtbot, stage):
    ocr, capture = FakeOcr(), FakeCapture()
    controller = ComparisonController(ocr, capture_worker=capture, cycle_timeout_ms=30)
    failures, updates = [], []
    controller.failed.connect(failures.append)
    controller.updated.connect(updates.append)
    try:
        controller.set_region('A', (0, 0, 5, 5, 1))
        controller.set_region('B', (10, 0, 5, 5, 1))
        controller.start()
        event = controller._event
        if stage != 'capture':
            ocr.busy = stage == 'queued'
            capture.finish()
        qtbot.waitUntil(lambda: bool(failures))
        assert failures == ['核對逾時，請稍後重新開始。']
        assert not controller.running and event.is_set()
        assert controller._images == {} and controller._results == {}
        assert not controller._retry.isActive() and not controller._cycle_timer.isActive()
        assert not controller._deadline.isActive()
        if stage == 'capture':
            capture.finish()
            assert not ocr.requests
        elif stage == 'inference':
            ocr.finish('123')
            assert len(ocr.requests) == 1  # Late A must not schedule B.
        else:
            ocr.busy = False
            controller._submit_crop()
            assert not ocr.requests
        assert not updates
    finally:
        controller.shutdown()


def test_result_after_elapsed_deadline_is_rejected_even_before_timer_delivery(monkeypatch, controlled):
    controller, ocr, capture = controlled
    clock = [100.0]
    monkeypatch.setattr('src.comparison.controller.monotonic', lambda: clock[0])
    failures, updates = [], []
    controller.failed.connect(failures.append)
    controller.updated.connect(updates.append)
    controller.start()
    capture.finish()
    ocr.finish('123')
    clock[0] = 131.0
    ocr.finish('123')
    assert not updates and failures == ['核對逾時，請稍後重新開始。']
    assert not controller.running and not controller._deadline.isActive()


def test_result_timestamp_is_capture_start_and_success_stops_deadline(monkeypatch, controlled):
    from datetime import datetime, timezone
    class Clock:
        value = datetime(2026, 11, 3, 1, 2, 3, tzinfo=timezone.utc)
        @classmethod
        def now(cls, tz):
            return cls.value
    monkeypatch.setattr('src.comparison.controller.datetime', Clock)
    controller, ocr, capture = controlled
    updates = []
    controller.updated.connect(updates.append)
    controller.start()
    expected = Clock.value.astimezone().isoformat(timespec='seconds')
    Clock.value = datetime(2026, 11, 3, 1, 2, 10, tzinfo=timezone.utc)
    capture.finish()
    ocr.finish('123')
    ocr.finish('123')
    assert updates[-1]['timestamp'] == expected
    assert not controller._deadline.isActive() and controller._cycle_started is None
    controller.pause()
    assert controller._cycle_timestamp is None


def test_restart_waiting_for_cancelled_grab_keeps_original_deadline(monkeypatch, controlled):
    controller, ocr, capture = controlled
    clock = [100.0]
    monkeypatch.setattr('src.comparison.controller.monotonic', lambda: clock[0])
    failures = []
    controller.failed.connect(failures.append)
    controller.start()
    controller.pause()
    assert capture.requests[0][-1].is_set()
    clock[0] = 101.0
    assert controller.start()
    assert controller._deadline.isActive()
    started = controller._cycle_started
    for elapsed in (1, 10, 29):
        clock[0] = started + elapsed
        controller._begin_cycle()
        assert controller._cycle_started == started
        assert controller.running and len(capture.requests) == 1
    clock[0] = started + 31
    controller._begin_cycle()
    assert failures == ['核對逾時，請稍後重新開始。']
    assert not controller.running and not controller._deadline.isActive()
    assert not controller._cycle_timer.isActive()
    capture.finish(0)
    assert not ocr.requests and len(capture.requests) == 1


@pytest.mark.parametrize('changed', [(3, 2), (1, 3), (3, 4)])
def test_unchanged_pixels_skip_ocr_and_either_region_change_resumes(controlled, changed):
    controller, ocr, capture = controlled
    updates, states = [], []
    controller.updated.connect(updates.append)
    controller.state_changed.connect(states.append)
    controller.start()
    capture.finish()
    ocr.finish('甲123')
    ocr.finish('甲123')
    first = dict(updates[0])
    for _ in range(10):
        controller._cycle_timer.stop()
        controller._begin_cycle()
        capture.finish()
        assert states[-1] == 'unchanged'
        assert len(ocr.requests) == 2 and updates == [first]
        assert not controller._images and not controller._results
        assert not controller._deadline.isActive() and not controller._retry.isActive()
    controller._cycle_timer.stop()
    controller._begin_cycle()
    capture.finish(values=changed)
    assert len(ocr.requests) == 3
    ocr.finish('新文字')
    ocr.finish('另一段')
    assert updates[-1]['cycle'] == 2 and updates[-1]['verdict'] == 'mismatch'
    controller.pause()
    assert controller._last_fingerprint is None and controller._pending_fingerprint is None
    # Explicit resume forces a fresh reading even on the same image.
    controller.start()
    capture.finish(values=changed)
    assert len(ocr.requests) == 5


def test_exact_fingerprint_detects_one_pixel_and_region_geometry():
    images = {'A': np.zeros((5, 10, 3), np.uint8), 'B': np.zeros((5, 5, 3), np.uint8)}
    original = fingerprint_pair(images)
    images['B'][2, 2, 0] = 1
    assert fingerprint_pair(images) != original
    images['B'][2, 2, 0] = 0
    images['A'] = images['A'].reshape(10, 5, 3)
    assert fingerprint_pair(images) != original


def test_close_during_real_worker_ocr_stops_followups_and_releases_pixels(qapp, qtbot):
    import gc
    import weakref
    from src.ui.comparison_window import ComparisonWindow
    entered, release = Event(), Event()
    pixels = []
    cancellation_events = []
    class SlowEngine(Engine):
        def run_cancellable_ocr(self, array, mode, event):
            cancellation_events.append(event)
            return self.run_ocr(array, mode)

        def run_ocr(self, array, mode):
            self.calls.append(int(array[0, 0, 0]))
            entered.set()
            assert release.wait(5)
            return {'text': '舊結果', 'status': 'done'}
    def snapshot(regions, layout):
        images = {'A': np.ones((5, 5, 3), np.uint8), 'B': np.ones((5, 5, 3), np.uint8)}
        pixels.extend(weakref.ref(image) for image in images.values())
        return images, ((0, 0, 100, 100),)
    engine = SlowEngine()
    ocr = OcrWorker(engine)
    capture = ComparisonCaptureWorker(capture=snapshot)
    controller = ComparisonController(ocr, capture_worker=capture)
    window = ComparisonWindow(controller)
    qtbot.addWidget(window)
    updates = []
    controller.updated.connect(updates.append)
    try:
        window.set_region('A', (0, 0, 5, 5, 1))
        window.set_region('B', (10, 0, 5, 5, 1))
        window.show()
        controller.before_capture = lambda: True
        assert controller.start()
        qtbot.waitUntil(entered.is_set)
        # UI closure must not wait for the native inference to finish.
        window._close_button.click()
        assert not window.isVisible() and not controller.running
        assert controller._event.is_set()
        assert len(cancellation_events) == 1 and cancellation_events[0].is_set()
        assert not any(timer.isActive() for timer in
                       (controller._cycle_timer, controller._retry, controller._deadline))
        release.set()
        qtbot.waitUntil(lambda: not ocr._document_pending)
        qtbot.wait(100)
        gc.collect()
        assert all(reference() is None for reference in pixels)
        assert engine.calls == [1] and not updates
        assert not window.isVisible() and not controller._images
    finally:
        release.set()
        window.shutdown()
        assert capture.wait(5000)
        ocr.stop()
        assert ocr.wait(5000)
