"""The new manual tool captures a single bounded frame and reaps its owner."""
from threading import Event
import time

import numpy as np
import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QThread

from src.invoice_check.capture import PairCaptureService, capture_regions
from src.core.resource_budget import ResourceBudgetExceeded


@pytest.fixture(autouse=True)
def isolated_capture_budget(monkeypatch):
    # These are geometry/ownership tests inside a retained pytest host. Actual
    # process-tree budget and frozen-payload gates are tested separately.
    monkeypatch.setattr('src.invoice_check.capture.memory_preflight', lambda **_: None)


def test_both_regions_are_crops_of_exactly_one_native_frame():
    calls = []

    class Backend:
        def capture_region(self, x, y, width, height, monitor):
            calls.append((x, y, width, height, monitor))
            image = np.zeros((height, width, 3), dtype=np.uint8)
            image[:, :, 0] = np.arange(width, dtype=np.uint8)
            image[:, :, 1] = np.arange(height, dtype=np.uint8)[:, None]
            return image

    regions = {'invoice': (10, 20, 20, 30, 1), 'table': (50, 25, 30, 15, 1)}
    before = time.time_ns()
    snapshot = capture_regions(regions, backend_factory=Backend)
    assert before <= snapshot['captured_at_ns'] <= time.time_ns()
    assert calls == [(10, 20, 70, 30, 1)]
    assert snapshot['regions'] == regions
    assert snapshot['images']['invoice'].shape == (30, 20, 3)
    assert snapshot['images']['table'][0, 0].tolist() == [40, 5, 0]
    assert snapshot['images']['invoice'].base is None
    assert snapshot['images']['table'].base is None


@pytest.mark.parametrize('regions', [
    {'invoice': (0, 0, 20, 20, 1), 'table': (20, 0, 20, 20, 2)},
    {'invoice': (0, 0, 20, 20, 1), 'table': (7000, 0, 20, 1000, 1)},
    {'invoice': (False, 0, 20, 20, 1)},
    {'invoice': (0, 0, 9000, 20, 1)},
    {'unknown': (0, 0, 20, 20, 1)},
])
def test_invalid_capture_rejected_before_backend_access(regions):
    calls = []
    with pytest.raises(ValueError):
        capture_regions(regions, backend_factory=lambda: calls.append(True))
    assert calls == []


def test_cancelled_capture_releases_thread_and_never_keeps_pixels(qtbot):
    entered, release = Event(), Event()

    class Backend:
        def get_monitor_info(self, monitor):
            return {'left': 0, 'top': 0, 'width': 800, 'height': 600}

        def capture_region(self, x, y, width, height, monitor):
            entered.set()
            release.wait(2)
            return np.zeros((height, width, 3), dtype=np.uint8)

    service = PairCaptureService(backend_factory=Backend,
                                 expected_monitor={'left': 0, 'top': 0, 'width': 800, 'height': 600})
    results = []
    service.finished.connect(lambda *args: results.append(args))
    try:
        assert service.request('one', {'invoice': (0, 0, 20, 20, 1)})
        qtbot.waitUntil(entered.is_set)
        assert not service.request('two', {'invoice': (0, 0, 20, 20, 1)})
        service.shutdown()
        assert service.busy
        release.set()
        qtbot.waitUntil(lambda: not service.busy)
        assert results == [('one', None)]
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert service.findChildren(QThread) == []
        assert not service.request('late', {'invoice': (0, 0, 20, 20, 1)})
    finally:
        release.set()
        service.shutdown()
        qtbot.waitUntil(lambda: not service.busy)


def test_invalid_service_request_creates_no_thread(qapp):
    service = PairCaptureService()
    assert not service.request('bad', None)
    assert not service.request('bad-event', {'invoice': (0, 0, 20, 20, 1)}, object())
    assert not service.busy and service.findChildren(QThread) == []
    service.shutdown()


def test_memory_rejection_happens_before_native_capture(monkeypatch):
    calls = []
    def exhausted(**_):
        raise ResourceBudgetExceeded('memory')
    monkeypatch.setattr('src.invoice_check.capture.memory_preflight', exhausted)
    with pytest.raises(ResourceBudgetExceeded):
        capture_regions({'invoice': (0, 0, 20, 20, 1)}, backend_factory=lambda: calls.append(True))
    assert calls == []


@pytest.mark.parametrize('actual', [
    {'left': -800, 'top': 0, 'width': 800, 'height': 600},
    {'left': 0, 'top': 0, 'width': 1600, 'height': 1200},
])
def test_native_monitor_mismatch_is_rejected_before_grab(actual):
    calls = []
    class Backend:
        def get_monitor_info(self, monitor):
            return actual
        def capture_region(self, *args):
            calls.append(args)
    with pytest.raises(ValueError, match='螢幕配置'):
        capture_regions({'invoice': (0, 0, 20, 20, 1)}, backend_factory=Backend,
                        expected_monitor={'left': 0, 'top': 0, 'width': 800, 'height': 600})
    assert calls == []
