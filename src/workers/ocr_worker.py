import json
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Lock

import numpy as np
from PySide6.QtCore import Signal

from ..data.models import OcrResultDTO
from .queue_worker import QueueWorker

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class DocumentOcrTask:
    """Transient, worker-owned raster; it never creates a history record."""
    token: str
    page_index: int
    field_name: str
    image: np.ndarray | None
    cancel_event: Event


class OcrWorker(QueueWorker):
    engine_loading = Signal()
    engine_progress = Signal(int, str)
    engine_ready = Signal()
    engine_failed = Signal(str)
    ocr_done = Signal(int, object)
    ocr_failed = Signal(int, str)
    ocr_progress = Signal(int, str)
    submission_rejected = Signal(int, str)
    document_ocr_done = Signal(str, int, str, object)

    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self._engine = engine
        self._repo = None
        self._files = None
        self._document_lock = Lock()
        self._document_pending = False
        self._engine.set_progress_callback(self.ocr_progress.emit)

    def start_loading(self):
        with self._submission_lock:
            if self._accepting:
                self._ensure_started()

    def set_repository(self, repo, files=None):
        """Configure before UI actions queue work; Qt worker owns inference only."""
        self._repo, self._files = repo, files

    def queue_ocr(self, item_id, image_path, mode='screen', clipboard_epoch=None):
        if clipboard_epoch is None:
            from ..clipboard.writer import get_clipboard_epoch
            clipboard_epoch = get_clipboard_epoch()
        def reserve(task):
            def source_check(relative):
                if not relative:
                    return False
                current = Path(self._files.get_abs_path(relative))
                return current == Path(image_path).resolve() and current.is_file()
            check = source_check if self._files else None
            job_id, revision = self._repo.begin_ocr_attempt(item_id, source_check=check) if self._repo else (None, None)
            return (*task, job_id, revision)
        try:
            self.submit((item_id, image_path, mode, clipboard_epoch), prepare=reserve)
            return True
        except (RuntimeError, ValueError, sqlite3.Error) as exc:
            # Rejected work was never reserved, so it must not fail/supersede
            # an already accepted OCR attempt for this item.
            self.submission_rejected.emit(item_id, str(exc))
            self.ocr_failed.emit(item_id, str(exc))  # compatibility notification
            return False

    @property
    def accepts_document_work(self):
        with self._submission_lock:
            return self._accepting

    def queue_document_ocr(self, token, page_index, field_name, image, cancel_event=None):
        """Transfer a single bounded BGR crop to the same serialized engine."""
        try:
            if (not isinstance(token, str) or not token or
                    not isinstance(field_name, str) or not field_name or
                    isinstance(page_index, bool) or not isinstance(page_index, int) or page_index < 0 or
                    not isinstance(image, np.ndarray) or image.dtype != np.uint8 or
                    image.ndim != 3 or image.shape[2] != 3 or
                    not image.shape[0] or not image.shape[1] or
                    image.shape[0] * image.shape[1] > 16_000_000):
                return False
            cancel_event = cancel_event if cancel_event is not None else Event()
            if not isinstance(cancel_event, Event):
                return False
            # This is a global raster slot, across document controllers and
            # cancelled generations. Repeated cancel/restart while a screen OCR
            # runs must not accumulate a full-size crop per accepted token.
            with self._document_lock:
                if self._document_pending:
                    return False
                self._document_pending = True
            try:
                self.submit(DocumentOcrTask(token, page_index, field_name, image, cancel_event))
            except Exception:
                with self._document_lock:
                    self._document_pending = False
                raise
            return True
        except (RuntimeError, ValueError):
            return False

    def _recognize_document(self, task):
        result = {'text': '', 'status': 'cancelled'}
        try:
            if not task.cancel_event.is_set():
                if not self._engine.is_ready():
                    raise RuntimeError('辨識引擎尚未就緒，請稍後重試。')
                cancellable = getattr(self._engine, 'run_cancellable_ocr', None)
                result = dict(cancellable(task.image, 'screen', task.cancel_event)
                              if cancellable is not None else self._engine.run_ocr(task.image, 'screen'))
                if task.cancel_event.is_set():
                    result = {'text': '', 'status': 'cancelled'}
        except Exception as exc:  # noqa: BLE001 - native inference errors become controlled transient results
            # No source paths or recognized text are written to the log here.
            logger.warning('文件欄位辨識未完成 (%s)', type(exc).__name__)
            result = {'text': '', 'status': 'failed', 'error': '此欄位未能辨識，請重新辨識或手動修正。'}
        finally:
            # QueueWorker's generator retains the last task while idle. Release
            # the only transferred crop explicitly instead of retaining its pixels.
            task.image = None
            with self._document_lock:
                self._document_pending = False
        self.document_ocr_done.emit(task.token, task.page_index, task.field_name, result)
        self.ocr_progress.emit(100, '文件欄位辨識完成')

    def run(self):
        self.engine_loading.emit()
        try:
            if not self._engine.is_ready():
                self._engine.load(progress_cb=self.engine_progress.emit)
            self.engine_ready.emit()
        except Exception as exc:
            logger.exception('OCR 引擎載入失敗')
            self.engine_failed.emit(str(exc))
        for task in self.tasks():
            if isinstance(task, DocumentOcrTask):
                self._recognize_document(task)
                continue
            item_id, image_path, mode, clipboard_epoch, job_id, revision = task
            try:
                if not self._engine.is_ready():
                    raise RuntimeError('OCR 引擎未就緒')
                result = self._engine.run_ocr_from_path(image_path, mode)
                dto = OcrResultDTO(
                    text=result.get('text', ''),
                    confidence=result.get('confidence', 0.0),
                    status=result.get('status', 'failed'),
                    detail_json=json.dumps(result.get('detail', []), ensure_ascii=False),
                    elapsed_ms=result.get('elapsed_ms', 0),
                    error_message=result.get('error'),
                    engine=result.get('engine', 'unknown'),
                    model_version=result.get('model_version', 'unknown'),
                    job_id=job_id, base_edit_revision=revision,
                    provenance_json=json.dumps(result, ensure_ascii=False),
                    clipboard_epoch=clipboard_epoch,
                )
                self.ocr_done.emit(item_id, dto)
            except Exception as exc:
                logger.exception('OCR 執行失敗 (item %s)', item_id)
                self.ocr_done.emit(item_id, OcrResultDTO(
                    text='', confidence=0.0, status='failed', error_message=str(exc),
                    engine=getattr(self._engine, 'name', 'rapidocr_onnxruntime'),
                    model_version=getattr(self._engine, '_model_version', 'unknown'),
                    job_id=job_id, base_edit_revision=revision,
                    clipboard_epoch=clipboard_epoch,
                    provenance_json=json.dumps({'error': str(exc), 'stage': 'worker'}, ensure_ascii=False)))
                self.ocr_failed.emit(item_id, str(exc))
            finally:
                self.ocr_progress.emit(100, 'OCR 工作完成')
