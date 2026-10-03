# -*- coding: utf-8 -*-
import json
import logging
import sqlite3
from pathlib import Path
from PySide6.QtCore import Signal
from .queue_worker import QueueWorker
from ..data.models import OcrResultDTO

logger = logging.getLogger(__name__)


class OcrWorker(QueueWorker):
    engine_loading = Signal()
    engine_progress = Signal(int, str)
    engine_ready = Signal()
    engine_failed = Signal(str)
    ocr_done = Signal(int, object)
    ocr_failed = Signal(int, str)
    ocr_progress = Signal(int, str)
    submission_rejected = Signal(int, str)

    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self._engine = engine
        self._repo = None
        self._files = None
        self._engine.set_progress_callback(self.ocr_progress.emit)

    def start_loading(self):
        with self._submission_lock:
            if self._accepting:
                self._ensure_started()

    def set_repository(self, repo, files=None):
        """Configure before UI actions queue work; Qt worker owns inference only."""
        self._repo, self._files = repo, files

    def queue_ocr(self, item_id, image_path, mode='screen'):
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
            self.submit((item_id, image_path, mode), prepare=reserve)
            return True
        except (RuntimeError, ValueError, sqlite3.Error) as exc:
            # Rejected work was never reserved, so it must not fail/supersede
            # an already accepted OCR attempt for this item.
            self.submission_rejected.emit(item_id, str(exc))
            self.ocr_failed.emit(item_id, str(exc))  # compatibility notification
            return False

    def run(self):
        self.engine_loading.emit()
        try:
            if not self._engine.is_ready():
                self._engine.load(progress_cb=self.engine_progress.emit)
            self.engine_ready.emit()
        except Exception as exc:
            logger.exception('OCR 引擎載入失敗')
            self.engine_failed.emit(str(exc))
        for item_id, image_path, mode, job_id, revision in self.tasks():
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
                )
                self.ocr_done.emit(item_id, dto)
            except Exception as exc:
                logger.exception('OCR 執行失敗 (item %s)', item_id)
                self.ocr_done.emit(item_id, OcrResultDTO(
                    text='', confidence=0.0, status='failed', error_message=str(exc),
                    engine=getattr(self._engine, 'name', 'rapidocr_onnxruntime'),
                    model_version=getattr(self._engine, '_model_version', 'unknown'),
                    job_id=job_id, base_edit_revision=revision,
                    provenance_json=json.dumps({'error': str(exc), 'stage': 'worker'}, ensure_ascii=False)))
                self.ocr_failed.emit(item_id, str(exc))
            finally:
                self.ocr_progress.emit(100, 'OCR 工作完成')
