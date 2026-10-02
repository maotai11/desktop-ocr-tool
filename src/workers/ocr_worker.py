# -*- coding: utf-8 -*-
import json
import logging
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

    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self._engine = engine
        self._engine.set_progress_callback(self.ocr_progress.emit)

    def start_loading(self):
        with self._submission_lock:
            if self._accepting:
                self._ensure_started()

    def queue_ocr(self, item_id, image_path, mode='screen'):
        try:
            self.submit((item_id, image_path, mode))
        except RuntimeError as exc:
            self.ocr_failed.emit(item_id, str(exc))

    def run(self):
        self.engine_loading.emit()
        try:
            if not self._engine.is_ready():
                self._engine.load(progress_cb=self.engine_progress.emit)
            self.engine_ready.emit()
        except Exception as exc:
            logger.exception('OCR 引擎載入失敗')
            self.engine_failed.emit(str(exc))
        for item_id, image_path, mode in self.tasks():
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
                )
                self.ocr_done.emit(item_id, dto)
            except Exception as exc:
                logger.exception('OCR 執行失敗 (item %s)', item_id)
                self.ocr_failed.emit(item_id, str(exc))
