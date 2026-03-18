# -*- coding: utf-8 -*-
import json
import logging
from PySide6.QtCore import QThread, Signal
from ..ocr.engine import OcrEngine
from ..data.models import OcrResultDTO
from ..core.constants import OCR_STATUS_FAILED

logger = logging.getLogger(__name__)


class OcrWorker(QThread):
    engine_loading = Signal()
    engine_ready = Signal()
    engine_failed = Signal(str)
    ocr_done = Signal(int, object)    # item_id, OcrResultDTO
    ocr_failed = Signal(int, str)     # item_id, error

    def __init__(self, engine: OcrEngine, parent=None):
        super().__init__(parent)
        self._engine = engine
        self._queue = []
        self._mode = 'load'

    def start_loading(self):
        self._mode = 'load'
        self.start()

    def queue_ocr(self, item_id: int, image_path: str, mode: str = 'screen'):
        self._queue.append((item_id, image_path, mode))
        if not self.isRunning():
            self._mode = 'ocr'
            self.start()
        elif self._mode == 'ocr':
            pass  # already running, queue will be processed

    def run(self):
        if self._mode == 'load':
            self._run_load()
        else:
            self._run_ocr_queue()

    def _run_load(self):
        self.engine_loading.emit()
        try:
            self._engine.load()
            self.engine_ready.emit()
            logger.info("OCR 引擎就緒")
            # Process any queued items
            if self._queue:
                self._run_ocr_queue()
        except Exception as e:
            logger.error(f"OCR 引擎載入失敗: {e}", exc_info=True)
            self.engine_failed.emit(str(e))

    def _run_ocr_queue(self):
        while self._queue:
            item_id, image_path, mode = self._queue.pop(0)
            try:
                if not self._engine.is_ready():
                    self.ocr_failed.emit(item_id, "OCR 引擎未就緒")
                    continue
                result = self._engine.run_ocr_from_path(image_path, mode)
                dto = OcrResultDTO(
                    text=result.get('text', ''),
                    confidence=result.get('confidence', 0.0),
                    status=result.get('status', OCR_STATUS_FAILED),
                    detail_json=json.dumps(result.get('detail', []),
                                           ensure_ascii=False),
                    elapsed_ms=result.get('elapsed_ms', 0),
                    error_message=result.get('error'),
                )
                self.ocr_done.emit(item_id, dto)
            except Exception as e:
                logger.error(f"OCR 執行失敗 (item {item_id}): {e}", exc_info=True)
                self.ocr_failed.emit(item_id, str(e))
