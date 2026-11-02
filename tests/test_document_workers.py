"""Real Qt threads and files at the transient document OCR boundaries."""
import gc
import hashlib
import threading
import time
import weakref

import numpy as np
import pytest
from PIL import Image
from PySide6.QtCore import QObject, Signal

from src.clipboard.writer import copy_text
from src.documents.template import Field, Region, TemplateLayout
from src.workers.document_worker import DocumentController
from src.workers.ocr_worker import OcrWorker


class Engine:
    def __init__(self):
        self.ready = False
        self.loads = 0
        self.calls = []
        self.owner = None
        self.entered = threading.Event()
        self.release = threading.Event()
        self.block = None

    def set_progress_callback(self, callback):
        self.callback = callback

    def is_ready(self):
        return self.ready

    def load(self, progress_cb):
        self.owner = threading.get_ident()
        self.loads += 1
        self.ready = True
        progress_cb(100, 'ready')

    def _call(self, kind, value):
        assert threading.get_ident() == self.owner
        self.calls.append((kind, value, threading.get_ident()))
        if self.block == kind:
            self.entered.set()
            assert self.release.wait(5)
        return {'text': value, 'status': 'done', 'confidence': 1.0}

    def run_ocr_from_path(self, path, mode):
        return self._call('capture', path)

    def run_ocr(self, array, mode):
        b, g, r = array[0, 0]
        return self._call('document', f'{r},{g},{b}')


@pytest.fixture
def workers(qtbot):
    owned = []

    def create(engine=None, ocr=None):
        engine = engine or Engine()
        ocr = ocr or OcrWorker(engine)
        controller = DocumentController(ocr)
        owned.append((engine, ocr, controller))
        return engine, ocr, controller

    yield create
    for engine, ocr, controller in owned:
        engine.release.set()
        controller.shutdown()
        assert controller._renderer.wait(5000)
        if hasattr(ocr, 'stop'):
            ocr.stop()
            assert ocr.wait(5000)


def image_file(tmp_path, name, colors=((10, 20, 30), (40, 50, 60)), size=(100, 120)):
    path = tmp_path / name
    with Image.new('RGB', size, colors[0]) as image:
        with Image.new('RGB', (size[0], size[1] // 2), colors[1]) as lower:
            image.paste(lower, (0, size[1] // 2))
        image.save(path)
    return path


def imported(qtbot, controller, paths):
    results = []
    controller.document_loaded.connect(results.append)
    controller.import_paths(paths, 100)
    qtbot.waitUntil(lambda: bool(results))
    return results[-1]


def snapshot(controller, layout=None, template='{上}\n{下}'):
    layout = layout or TemplateLayout([
        Field('上', Region(0, 0, 1, .5)), Field('下', Region(0, .5, 1, .5))])
    return {'layout': layout, 'template': template, 'page_count': len(controller.pages)}


def test_document_and_capture_ocr_share_one_model_thread_and_leave_inputs_clipboard(
        qapp, qtbot, tmp_path, workers):
    first = image_file(tmp_path, '第一張.png')
    second = image_file(tmp_path, '第二張.png', ((70, 80, 90), (100, 110, 120)), (200, 240))
    hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in (first, second)]
    engine = Engine()
    engine.block = 'capture'
    engine, ocr, controller = workers(engine)
    captures, batches = [], []
    ocr.ocr_done.connect(lambda *args: captures.append(args))
    controller.results_detailed.connect(lambda *args: batches.append(args))
    copy_text('先前內容')
    assert ocr.queue_ocr(1, 'screen-one')
    qtbot.waitUntil(engine.entered.is_set)
    imported(qtbot, controller, [first, second])
    controller.start_recognition(snapshot(controller))
    qtbot.waitUntil(lambda: ocr._queue.qsize() >= 1)
    assert ocr.queue_ocr(2, 'screen-two')
    engine.release.set()
    qtbot.waitUntil(lambda: bool(batches), timeout=5000)
    assert batches[-1] == ([{'上': '10,20,30', '下': '40,50,60'},
                            {'上': '70,80,90', '下': '100,110,120'}],
                           [{'上': 'done', '下': 'done'}] * 2, False)
    assert [kind for kind, _, _ in engine.calls][:3] == ['capture', 'document', 'capture']
    assert len(captures) == 2
    assert engine.loads == 1
    assert {thread for _, _, thread in engine.calls} == {engine.owner}
    assert engine.owner != threading.get_ident()
    assert qapp.clipboard().text() == '先前內容'
    assert hashes == [hashlib.sha256(path.read_bytes()).hexdigest() for path in (first, second)]


def test_normalized_regions_inherit_and_later_page_override_is_independent(qtbot, tmp_path, workers):
    first = image_file(tmp_path, 'first.png')
    second = image_file(tmp_path, 'second.png', ((70, 80, 90), (100, 110, 120)), (200, 240))
    engine, _, controller = workers()
    imported(qtbot, controller, [first, second])
    layout = TemplateLayout([Field('姓名', Region(0, 0, 1, .5))])
    layout.set_region('姓名', Region(0, .5, 1, .5), 1)
    values = []
    controller.results_ready.connect(values.append)
    controller.start_recognition(snapshot(controller, layout, '姓名：｛姓名}'))
    # A caller changing its layout while work runs cannot move the accepted crop.
    layout.set_region('姓名', Region(0, 0, 1, .5), 1)
    qtbot.waitUntil(lambda: bool(values))
    assert values == [[{'姓名': '10,20,30'}, {'姓名': '100,110,120'}]]
    assert len(engine.calls) == 2


def test_unused_unselected_fields_are_not_recognized_and_literal_template_needs_no_model(
        qtbot, tmp_path, workers):
    engine, _, controller = workers()
    imported(qtbot, controller, [image_file(tmp_path, 'one.png')])
    layout = TemplateLayout([Field('使用', Region(0, 0, 1, .5)), Field('未使用')])
    values = []
    controller.results_ready.connect(values.append)
    controller.start_recognition(snapshot(controller, layout, '{使用}'))
    qtbot.waitUntil(lambda: len(values) == 1)
    assert values == [[{'使用': '10,20,30'}]]
    before = len(engine.calls)
    controller.start_recognition(snapshot(controller, layout, '固定文字'))
    assert values[-1] == [{}]
    assert len(engine.calls) == before


@pytest.mark.parametrize('template,layout', [('{未知}', TemplateLayout()),
    ('{未框選}', TemplateLayout([Field('未框選')])), ('{未關閉', TemplateLayout())])
def test_invalid_template_does_not_queue_any_inference(qtbot, tmp_path, workers, template, layout):
    engine, _, controller = workers()
    imported(qtbot, controller, [image_file(tmp_path, 'one.png')])
    errors = []
    controller.failed.connect(errors.append)
    controller.start_recognition(snapshot(controller, layout, template))
    assert errors and engine.calls == []
    assert controller._state == 'idle'


def test_cancelled_queued_document_never_runs_and_crop_memory_is_released(qtbot):
    engine = Engine()
    engine.block = 'capture'
    ocr = OcrWorker(engine)
    documents, captures = [], []
    ocr.document_ocr_done.connect(lambda *args: documents.append(args))
    ocr.ocr_done.connect(lambda *args: captures.append(args))
    try:
        ocr.queue_ocr(1, 'blocking')
        qtbot.waitUntil(engine.entered.is_set)
        array = np.zeros((40, 80, 3), np.uint8)
        reference = weakref.ref(array)
        event = threading.Event()
        assert ocr.queue_document_ocr('job', 0, '欄位', array, event)
        del array
        event.set()
        engine.release.set()
        qtbot.waitUntil(lambda: bool(documents))
        gc.collect()
        assert documents[0][3]['status'] == 'cancelled'
        assert len(captures) == 1
        assert [kind for kind, _, _ in engine.calls] == ['capture']
        assert reference() is None
    finally:
        engine.release.set()
        ocr.stop()
        assert ocr.wait(5000)


def test_running_cancel_then_reimport_discards_old_result(qapp, qtbot, tmp_path, workers):
    engine = Engine()
    engine.block = 'document'
    _, _, controller = workers(engine)
    one = image_file(tmp_path, 'old.png')
    two = image_file(tmp_path, 'new.png', ((90, 80, 70), (60, 50, 40)))
    imported(qtbot, controller, [one])
    values = []
    controller.results_detailed.connect(lambda *args: values.append(args))
    layout = TemplateLayout([Field('字', Region(0, 0, 1, .5))])
    controller.start_recognition(snapshot(controller, layout, '{字}'))
    qtbot.waitUntil(engine.entered.is_set)
    controller.cancel()
    assert values[-1] == ([{'字': ''}], [{'字': 'cancelled'}], True)
    imported(qtbot, controller, [two])
    controller.start_recognition(snapshot(controller, layout, '{字}'))
    engine.release.set()
    qtbot.waitUntil(lambda: len(values) == 2)
    assert values[-1] == ([{'字': '90,80,70'}], [{'字': 'done'}], False)


def test_cancel_restart_cannot_accumulate_rasters_behind_a_blocked_capture(qtbot):
    engine = Engine()
    engine.block = 'capture'
    ocr = OcrWorker(engine)
    results = []
    ocr.document_ocr_done.connect(lambda *args: results.append(args))
    references, accepted = [], []
    try:
        ocr.queue_ocr(1, 'blocking')
        qtbot.waitUntil(engine.entered.is_set)
        for index in range(30):
            image = np.zeros((400, 800, 3), np.uint8)
            references.append(weakref.ref(image))
            event = threading.Event()
            accepted.append(ocr.queue_document_ocr(str(index), 0, '欄位', image, event))
            event.set()
            del image
        gc.collect()
        assert accepted == [True] + [False] * 29
        assert sum(reference() is not None for reference in references) == 1
        engine.release.set()
        qtbot.waitUntil(lambda: bool(results))
        gc.collect()
        assert all(reference() is None for reference in references)
        assert ocr.queue_document_ocr('fresh', 0, '欄位', np.zeros((4, 4, 3), np.uint8))
        qtbot.waitUntil(lambda: len(results) == 2)
        assert results[-1][3]['status'] == 'done'
    finally:
        engine.release.set()
        ocr.stop()
        assert ocr.wait(5000)


def test_failed_import_preserves_previous_session_and_reports_error(qtbot, tmp_path, workers):
    _, _, controller = workers()
    imported(qtbot, controller, [image_file(tmp_path, 'valid.png')])
    previous = controller.pages
    errors = []
    controller.failed.connect(errors.append)
    controller.import_paths([tmp_path / 'missing.pdf'])
    qtbot.waitUntil(lambda: bool(errors))
    assert controller.pages == previous
    assert controller._state == 'idle'


def test_shutdown_during_render_returns_without_gui_wait_and_rejects_late_preview(
        qtbot, tmp_path, workers, monkeypatch):
    from src.workers import document_worker
    _, _, controller = workers()
    imported(qtbot, controller, [image_file(tmp_path, 'one.png')])
    entered, release = threading.Event(), threading.Event()
    real_render = document_worker.render_page

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return real_render(*args, **kwargs)

    monkeypatch.setattr(document_worker, 'render_page', blocked)
    previews = []
    controller.page_ready.connect(lambda *args: previews.append(args))
    controller.request_page(0)
    qtbot.waitUntil(entered.is_set)
    started = time.monotonic()
    controller.shutdown()
    assert time.monotonic() - started < .25
    assert not controller.is_stopped
    release.set()
    qtbot.waitUntil(lambda: controller.is_stopped)
    assert previews == []
    controller.import_paths([tmp_path / 'new.png'])
    assert controller._state == 'idle'


def test_full_shared_queue_retries_then_marks_partial_results_cancelled(qtbot, tmp_path, workers):
    class FullQueue(QObject):
        document_ocr_done = Signal(str, int, str, object)
        accepts_document_work = True
        calls = 0

        def queue_document_ocr(self, *_):
            self.calls += 1
            return False

    full = FullQueue()
    _, _, controller = workers(ocr=full)
    controller._queue_retry_limit = 2
    controller._retry.setInterval(1)
    imported(qtbot, controller, [image_file(tmp_path, 'one.png')])
    results, errors, events = [], [], []
    controller.results_detailed.connect(lambda *args: results.append(args))
    controller.failed.connect(errors.append)
    controller.failed.connect(lambda _: events.append('failed'))
    controller.results_detailed.connect(lambda *_: events.append('results'))
    controller.busy_changed.connect(lambda *_: events.append('busy'))
    controller.start_recognition(snapshot(controller))
    qtbot.waitUntil(lambda: bool(results))
    assert full.calls == 2
    assert errors
    assert results[-1][2] is True
    assert set(results[-1][1][0].values()) == {'cancelled'}
    assert controller._pending_crop is None
    assert events[-1] == 'failed'


def test_failed_ocr_and_empty_text_are_visible_statuses_without_history_signal(qtbot, tmp_path, workers):
    engine = Engine()
    engine.run_ocr = lambda *_: {'text': '', 'status': 'done'}
    _, ocr, controller = workers(engine)
    imported(qtbot, controller, [image_file(tmp_path, 'one.png')])
    results, history, events = [], [], []
    controller.results_detailed.connect(lambda *args: results.append(args))
    controller.results_detailed.connect(lambda *_: events.append('results'))
    controller.busy_changed.connect(lambda *_: events.append('busy'))
    ocr.ocr_done.connect(lambda *args: history.append(args))
    controller.start_recognition(snapshot(controller))
    qtbot.waitUntil(lambda: bool(results))
    assert results[-1] == ([{'上': '', '下': ''}], [{'上': 'failed', '下': 'failed'}], False)
    assert history == []
    assert events[-1] == 'results'


@pytest.mark.parametrize('phase', ['import', 'preview', 'ocr'])
def test_clear_document_discards_late_work_then_can_import_and_recognize_again(
        qapp, qtbot, tmp_path, workers, monkeypatch, phase):
    from src.workers import document_worker
    old = image_file(tmp_path, 'old.png')
    new = image_file(tmp_path, 'new.png', ((90, 80, 70), (60, 50, 40)))
    original_hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in (old, new)]
    engine, _, controller = workers()
    entered, release = threading.Event(), threading.Event()
    loaded, previews, batches = [], [], []
    controller.document_loaded.connect(loaded.append)
    controller.page_ready.connect(lambda *args: previews.append(args))
    controller.results_detailed.connect(lambda *args: batches.append(args))
    copy_text('保留的剪貼簿')
    if phase == 'import':
        real_load = document_worker.load_sources

        def blocked_load(*args, **kwargs):
            entered.set()
            assert release.wait(5)
            return real_load(*args, **kwargs)

        monkeypatch.setattr(document_worker, 'load_sources', blocked_load)
        controller.import_paths([old])
    else:
        imported(qtbot, controller, [old])
        if phase == 'preview':
            real_render = document_worker.render_page

            def blocked_render(*args, **kwargs):
                if kwargs.get('preview'):
                    entered.set()
                    assert release.wait(5)
                return real_render(*args, **kwargs)

            monkeypatch.setattr(document_worker, 'render_page', blocked_render)
            controller.request_page(0)
        else:
            engine.block = 'document'
            entered, release = engine.entered, engine.release
            controller.start_recognition(snapshot(controller))
    try:
        qtbot.waitUntil(entered.is_set)
        cached = controller._renderer._cached_image
        controller.clear_document()
        assert controller.pages == ()
        assert loaded[-1] == ()
        assert controller._results == controller._statuses == []
        assert controller._state == 'idle'
        assert batches == []
        assert controller._renderer.isRunning()  # Cancellation does not terminate a native thread.
        release.set()
        qtbot.waitUntil(lambda: controller._renderer._cached_image is None)
        if cached is not None:
            with pytest.raises(ValueError):
                cached.getpixel((0, 0))
        imported(qtbot, controller, [new])
        controller.start_recognition(snapshot(controller))
        qtbot.waitUntil(lambda: bool(batches))
        assert previews == []
        assert batches == [([{'上': '90,80,70', '下': '60,50,40'}],
                            [{'上': 'done', '下': 'done'}], False)]
        assert qapp.clipboard().text() == '保留的剪貼簿'
        assert original_hashes == [hashlib.sha256(path.read_bytes()).hexdigest() for path in (old, new)]
    finally:
        release.set()


def test_clear_document_is_idempotent_and_rejected_after_shutdown(qtbot, tmp_path, workers):
    _, _, controller = workers()
    imported(qtbot, controller, [image_file(tmp_path, 'one.png')])
    loaded = []
    controller.document_loaded.connect(loaded.append)
    controller.clear_document()
    controller.clear_document()
    assert controller.pages == () and loaded == [(), ()]
    controller.shutdown()
    controller.clear_document()
    assert loaded == [(), ()]


def test_clear_document_releases_cache_even_when_cancelled_preview_queue_is_full(
        qtbot, tmp_path, workers, monkeypatch):
    from src.workers import document_worker
    engine, _, controller = workers()
    engine.block = 'document'
    imported(qtbot, controller, [image_file(tmp_path, 'one.png')])
    controller.start_recognition(snapshot(controller))
    qtbot.waitUntil(engine.entered.is_set)
    cached = controller._renderer._cached_image
    preview_entered, release_preview = threading.Event(), threading.Event()
    real_render = document_worker.render_page

    def blocked_preview(*args, **kwargs):
        if kwargs.get('preview'):
            preview_entered.set()
            assert release_preview.wait(5)
        return real_render(*args, **kwargs)

    monkeypatch.setattr(document_worker, 'render_page', blocked_preview)
    try:
        controller.request_page(0)
        qtbot.waitUntil(preview_entered.is_set)
        cancelled = threading.Event()
        cancelled.set()
        for index in range(controller._renderer._capacity):
            controller._renderer.preview(index + 100, 0, controller.pages[0], cancelled)
        assert controller._renderer._queue.qsize() == controller._renderer._capacity
        controller.clear_document()
        release_preview.set()
        qtbot.waitUntil(lambda: controller._renderer._cached_image is None)
        with pytest.raises(ValueError):
            cached.getpixel((0, 0))
        assert controller.pages == ()
    finally:
        release_preview.set()
        engine.release.set()
