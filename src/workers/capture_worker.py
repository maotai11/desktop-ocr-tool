# -*- coding: utf-8 -*-
import json
import logging
import queue as _queue_mod
from typing import Optional
import numpy as np
from PIL import Image
from PySide6.QtCore import QThread, Signal
from ..capture.factory import get_capture_backend
from ..data.file_manager import FileManager
from ..data.hasher import phash_image
from ..data.models import ItemCreateDTO
from ..core.constants import (
    SOURCE_MODE_REGION_IMAGE, SOURCE_MODE_REGION_OCR,
    SOURCE_MODE_FULLSCREEN, ITEM_TYPE_IMAGE
)

logger = logging.getLogger(__name__)


class CaptureWorker(QThread):
    capture_done = Signal(str, object)   # image_path, ItemCreateDTO
    capture_failed = Signal(str)

    def __init__(self, file_manager: FileManager, parent=None):
        super().__init__(parent)
        self._file_manager = file_manager
        self._queue = _queue_mod.Queue()

    def capture_region(self, x: int, y: int, w: int, h: int,
                       monitor_idx: int, source_mode: str):
        self._queue.put(('region', x, y, w, h, monitor_idx, source_mode))
        if not self.isRunning():
            self.start()

    def capture_fullscreen(self, monitor_idx: int = 1):
        self._queue.put(('fullscreen', monitor_idx))
        if not self.isRunning():
            self.start()

    def run(self):
        try:
            task = self._queue.get_nowait()
        except _queue_mod.Empty:
            return
        try:
            backend = get_capture_backend()
            if task[0] == 'region':
                _, x, y, w, h, monitor_idx, source_mode = task
                img = backend.capture_region(x, y, w, h, monitor_idx)
                region_json = json.dumps({'x': x, 'y': y, 'w': w, 'h': h})
                monitor = monitor_idx
            elif task[0] == 'fullscreen':
                _, monitor_idx = task
                img = backend.capture_monitor(monitor_idx)
                region_json = None
                monitor = monitor_idx
                source_mode = SOURCE_MODE_FULLSCREEN
            else:
                return

            if img is None:
                self.capture_failed.emit("截圖失敗：無法擷取畫面")
                return

            # Convert BGR→RGB for PIL
            pil_img = Image.fromarray(img[:, :, ::-1])
            abs_path, rel_path = self._file_manager.save_capture(pil_img)
            thumb_abs, thumb_rel = self._file_manager.create_thumbnail(abs_path)

            h_img, w_img = img.shape[:2]
            img_hash = phash_image(abs_path)

            dto = ItemCreateDTO(
                item_type=ITEM_TYPE_IMAGE,
                source_mode=source_mode,
                raw_image_path=rel_path,
                thumbnail_path=thumb_rel if thumb_rel else None,
                image_width=w_img,
                image_height=h_img,
                image_hash=img_hash,
                ocr_status='none',
                capture_region=region_json,
                capture_monitor=monitor,
            )
            self.capture_done.emit(abs_path, dto)
        except Exception as e:
            logger.error(f"CaptureWorker 錯誤: {e}", exc_info=True)
            self.capture_failed.emit(str(e))
