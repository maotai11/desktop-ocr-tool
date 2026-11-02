"""Bounded document rendering and serial, transient OCR orchestration.

The GUI owns the controller; one renderer owns its single page cache. Crops are
sent one at a time to the existing OcrWorker, so screen captures and documents
share one model and one inference thread. Inputs are read-only and session-only.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from threading import Event
from uuid import uuid4

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal, Slot

from ..documents.loader import (
    DocumentCancelled,
    DocumentLoadError,
    enumerate_pages,
    load_sources,
    render_page,
)
from ..documents.template import (
    MAX_OUTPUT_CHARS,
    TemplateError,
    TemplateLayout,
    build_output,
    validate_template,
)
from .queue_worker import QueueWorker

logger = logging.getLogger(__name__)
MAX_FIELDS = 32


@dataclass(frozen=True)
class _RenderTask:
    kind: str
    token: object
    cancel_event: Event | None
    payload: object = None


class DocumentRenderWorker(QueueWorker):
    loaded = Signal(int, object)
    preview_ready = Signal(int, int, object)
    crop_ready = Signal(str, int, str, object)
    task_failed = Signal(str, object, int, str, str)

    def __init__(self, parent=None):
        super().__init__(parent, capacity=64)
        self._cached_page = None
        self._cached_image = None

    def _clear_cache(self):
        if self._cached_image is not None:
            self._cached_image.close()
        self._cached_image = self._cached_page = None

    def clear_cache(self):
        try:
            self.submit(_RenderTask('clear', None, None))
            return True
        except RuntimeError:
            return False

    def import_paths(self, token, paths, max_file_size_mb, event):
        self.submit(_RenderTask('import', token, event, (tuple(paths), max_file_size_mb)))

    def preview(self, token, page_index, page, event):
        self.submit(_RenderTask('preview', token, event, (page_index, page)))

    def crop(self, token, page_index, page, field_name, region, event):
        self.submit(_RenderTask('crop', token, event, (page_index, page, field_name, region)))

    def run(self):
        try:
            for task in self.tasks():
                if task.kind == 'clear':
                    self._clear_cache()
                    continue
                if task.cancel_event.is_set():
                    continue
                page_index, field_name = -1, ''
                try:
                    if task.kind == 'import':
                        paths, limit = task.payload
                        sources = load_sources(paths, max_file_size_mb=limit,
                                               cancelled=task.cancel_event.is_set)
                        pages = enumerate_pages(sources)
                        if not task.cancel_event.is_set():
                            self.loaded.emit(task.token, pages)
                    elif task.kind == 'preview':
                        page_index, page = task.payload
                        image = render_page(page, preview=True)
                        if task.cancel_event.is_set():
                            image.close()
                        else:
                            self.preview_ready.emit(task.token, page_index, image)
                        del image
                    elif task.kind == 'crop':
                        page_index, page, field_name, region = task.payload
                        if page != self._cached_page:
                            self._clear_cache()
                            image = render_page(page)
                            if task.cancel_event.is_set():
                                image.close()
                                continue
                            self._cached_image, self._cached_page = image, page
                        bounds = region.pixel_bounds(*self._cached_image.size)
                        with self._cached_image.crop(bounds) as crop:
                            # OcrEngine expects OpenCV's BGR order. The copy owns
                            # its pixels independently of Pillow and the page cache.
                            array = np.asarray(crop)[:, :, ::-1].copy()
                        if not task.cancel_event.is_set():
                            self.crop_ready.emit(task.token, page_index, field_name, array)
                        del array
                except DocumentCancelled:
                    pass
                except Exception as exc:  # noqa: BLE001 - contain native/file failures without exposing diagnostics
                    self._clear_cache()
                    message = (str(exc) if isinstance(exc, (DocumentLoadError, TemplateError))
                               else '無法讀取這一頁，請重新匯入或改用其他檔案。')
                    logger.warning('文件背景工作未完成 (%s)', type(exc).__name__)
                    if not task.cancel_event.is_set():
                        self.task_failed.emit(task.kind, task.token, page_index, field_name, message)
        finally:
            self._clear_cache()


class DocumentController(QObject):
    document_loaded = Signal(object)
    page_ready = Signal(int, object)
    results_ready = Signal(object)
    results_detailed = Signal(object, object, bool)
    progress = Signal(int, int, str)
    busy_changed = Signal(bool, str)
    failed = Signal(str)
    stopped = Signal()

    def __init__(self, ocr_worker, parent=None):
        super().__init__(parent)
        self._ocr = ocr_worker
        self._renderer = DocumentRenderWorker(self)
        self._renderer.loaded.connect(self._loaded)
        self._renderer.preview_ready.connect(self._preview_ready)
        self._renderer.crop_ready.connect(self._crop_ready)
        self._renderer.task_failed.connect(self._render_failed)
        self._renderer.finished.connect(self.stopped)
        self._ocr.document_ocr_done.connect(self._ocr_done)
        self._generation = 0
        self._preview_serial = 0
        self._import_event = Event()
        self._preview_event = Event()
        self._batch_event = Event()
        self._pages = ()
        self._state = 'idle'
        self._closing = False
        self._token = None
        self._layout = None
        self._template = ''
        self._names = ()
        self._results = []
        self._statuses = []
        self._position = (0, 0)
        self._inflight = None
        self._pending_crop = None
        self._done_count = 0
        self._text_chars = 0
        self._retry_count = 0
        self._queue_retry_limit = 600
        self._retry = QTimer(self)
        self._retry.setInterval(50)
        self._retry.timeout.connect(self._submit_pending_crop)

    @property
    def pages(self):
        return self._pages

    @property
    def is_stopped(self):
        return not self._renderer.isRunning()

    @Slot(object, int)
    def import_paths(self, paths, max_file_size_mb=100):
        if self._closing:
            return
        self._cancel(emit_results=False)
        self._generation += 1
        self._import_event = Event()
        self._state = 'import'
        self.busy_changed.emit(True, '讀取文件…')
        try:
            self._renderer.import_paths(self._generation, paths, max_file_size_mb, self._import_event)
        except (RuntimeError, TypeError):
            self._state = 'idle'
            self.busy_changed.emit(False, '')
            self.failed.emit('無法加入匯入工作，請稍後重試。')

    @Slot(int, object)
    def _loaded(self, generation, pages):
        if self._closing or generation != self._generation or self._import_event.is_set():
            return
        self._pages = tuple(pages)
        self._state = 'idle'
        self.document_loaded.emit(self._pages)
        self.busy_changed.emit(False, f'已匯入 {len(self._pages)} 頁')

    @Slot(int)
    def request_page(self, page_index):
        if self._closing or not 0 <= page_index < len(self._pages):
            return
        self._preview_event.set()
        self._preview_event = Event()
        self._preview_serial += 1
        try:
            self._renderer.preview(self._preview_serial, page_index, self._pages[page_index],
                                   self._preview_event)
        except RuntimeError:
            self.failed.emit('正在讀取文件，請稍後再切換頁面。')

    @Slot(int, int, object)
    def _preview_ready(self, serial, page_index, image):
        if self._closing or serial != self._preview_serial or self._preview_event.is_set():
            image.close()
            return
        self.page_ready.emit(page_index, image)

    @Slot(object)
    def start_recognition(self, snapshot):
        if self._closing or self._state != 'idle':
            return
        try:
            if not self._pages:
                raise TemplateError('請先匯入 PDF 或圖片。')
            if not isinstance(snapshot, dict) or not isinstance(snapshot.get('layout'), TemplateLayout):
                raise TemplateError('欄位設定無效，請重新框選。')
            if snapshot.get('page_count') != len(self._pages):
                raise TemplateError('頁面已更新，請重新開始辨識。')
            layout = snapshot['layout'].copy()
            if len(layout.fields) > MAX_FIELDS:
                raise TemplateError(f'每次最多使用 {MAX_FIELDS} 個欄位。')
            template = snapshot.get('template')
            names = validate_template(template, layout.field_names)
            for page_index in range(len(self._pages)):
                regions = layout.effective_regions(page_index)
                missing = [name for name in names if regions[name] is None]
                if missing:
                    raise TemplateError(f'第 {page_index + 1} 頁尚未框選：' + '、'.join(missing))
        except TemplateError as exc:
            self.failed.emit(str(exc))
            return
        except (TypeError, ValueError, KeyError):
            self.failed.emit('模板或欄位設定無效，請檢查後再試。')
            return
        self._cancel(emit_results=False)
        self._state = 'recognize'
        self._token = uuid4().hex
        self._batch_event = Event()
        self._layout, self._template, self._names = layout, template, names
        self._results = [{name: '' for name in names} for _ in self._pages]
        self._statuses = [{name: 'pending' for name in names} for _ in self._pages]
        self._position = (0, 0)
        self._done_count = self._text_chars = 0
        self.busy_changed.emit(True, '開始辨識…')
        self._renderer.clear_cache()
        if not names:
            # Literal-only templates are legitimate and need no model work.
            self._complete()
        else:
            self._advance()

    def _advance(self):
        if self._closing or self._state != 'recognize' or self._batch_event.is_set():
            return
        page_index, field_index = self._position
        if page_index >= len(self._pages):
            self._complete()
            return
        name = self._names[field_index]
        self._inflight = page_index, name
        self.progress.emit(self._done_count, len(self._pages) * len(self._names),
                           f'第 {page_index + 1} 頁 · {name}')
        try:
            self._renderer.crop(self._token, page_index, self._pages[page_index], name,
                                self._layout.region_for(name, page_index), self._batch_event)
        except RuntimeError:
            self._cancel(emit_results=True)
            self.failed.emit('無法加入文件辨識工作，請稍後重試。')

    @Slot(str, int, str, object)
    def _crop_ready(self, token, page_index, name, array):
        if (self._closing or self._state != 'recognize' or token != self._token or
                self._batch_event.is_set() or self._inflight != (page_index, name)):
            return
        self._pending_crop = token, page_index, name, array
        self._retry_count = 0
        self._submit_pending_crop()

    @Slot()
    def _submit_pending_crop(self):
        if self._pending_crop is None or self._closing or self._batch_event.is_set():
            self._retry.stop()
            self._pending_crop = None
            return
        token, page, name, array = self._pending_crop
        if self._ocr.queue_document_ocr(token, page, name, array, self._batch_event):
            self._pending_crop = None
            self._retry.stop()
            return
        self._retry_count += 1
        if (not getattr(self._ocr, 'accepts_document_work', True) or
                self._retry_count >= self._queue_retry_limit):
            self._retry.stop()
            self._pending_crop = None
            self._cancel(emit_results=True)
            self.failed.emit('辨識工作尚未能加入佇列，請稍後重新開始。')
        elif not self._retry.isActive():
            self.progress.emit(self._done_count, len(self._pages) * len(self._names), '等待目前的辨識工作…')
            self._retry.start()

    @Slot(str, int, str, object)
    def _ocr_done(self, token, page_index, name, result):
        if (self._closing or self._state != 'recognize' or token != self._token or
                self._batch_event.is_set() or self._inflight != (page_index, name)):
            return
        result = result if isinstance(result, dict) else {}
        text = result.get('text', '')
        text = text if isinstance(text, str) else ''
        status = result.get('status')
        status = ('needs_review' if status == 'needs_review' and text.strip() else
                  'done' if status in ('done', 'confirmed') and text.strip() else 'failed')
        if self._text_chars + len(text) > MAX_OUTPUT_CHARS:
            self._cancel(emit_results=True)
            self.failed.emit('辨識結果過大，請減少頁數或縮小欄位範圍。')
            return
        self._results[page_index][name] = text
        self._statuses[page_index][name] = status
        self._text_chars += len(text)
        self._next_position()

    def _next_position(self):
        self._done_count += 1
        page, field = self._position
        field += 1
        if field == len(self._names):
            page, field = page + 1, 0
        self._position = page, field
        self._inflight = None
        self._advance()

    @Slot(str, object, int, str, str)
    def _render_failed(self, kind, token, page_index, name, message):
        if self._closing:
            return
        if kind == 'import':
            if token == self._generation and not self._import_event.is_set():
                self._state = 'idle'
                self.busy_changed.emit(False, '')
                self.failed.emit(message)
        elif kind == 'preview':
            if token == self._preview_serial and not self._preview_event.is_set():
                self.failed.emit(message)
        elif (kind == 'crop' and self._state == 'recognize' and token == self._token and
              self._inflight == (page_index, name) and not self._batch_event.is_set()):
            self._results[page_index][name] = ''
            self._statuses[page_index][name] = 'failed'
            self._next_position()

    def _emit_results(self, cancelled):
        results = [dict(row) for row in self._results]
        statuses = [dict(row) for row in self._statuses]
        self.results_ready.emit(results)
        self.results_detailed.emit(results, statuses, cancelled)

    def _complete(self):
        try:
            build_output(self._template, self._results)
        except TemplateError as exc:
            self._cancel(emit_results=True)
            self.failed.emit(str(exc))
            return
        self._state = 'idle'
        self._inflight = None
        self._renderer.clear_cache()
        self.progress.emit(self._done_count, len(self._pages) * len(self._names), '辨識完成')
        # Busy state is cleared before results so the UI's failed/review warning
        # remains the final message instead of being replaced by generic success.
        self.busy_changed.emit(False, '')
        self._emit_results(False)

    @Slot()
    def cancel(self):
        self._cancel(emit_results=True)

    def _cancel(self, emit_results):
        was_recognizing = self._state == 'recognize'
        was_busy = self._state != 'idle'
        self._generation += 1
        self._preview_serial += 1
        self._import_event.set()
        self._preview_event.set()
        self._batch_event.set()
        self._retry.stop()
        self._pending_crop = self._inflight = None
        self._token = None
        self._state = 'idle'
        if was_busy:
            self.busy_changed.emit(False, '已取消')
        if was_recognizing and emit_results:
            for row in self._statuses:
                for name, status in row.items():
                    if status == 'pending':
                        row[name] = 'cancelled'
            self._emit_results(True)
        if self._renderer._started_once:
            self._renderer.clear_cache()

    @Slot()
    def shutdown(self):
        if self._closing:
            return
        self._closing = True
        self._cancel(emit_results=False)
        self._renderer.stop()
