# -*- coding: utf-8 -*-
import json
import logging

from PIL import Image
from PySide6.QtCore import Signal

from ..capture.factory import get_capture_backend
from ..core.constants import (
    ITEM_TYPE_IMAGE,
    SOURCE_MODE_FULLSCREEN,
)
from ..data.file_manager import FileManager
from ..data.hasher import sha256_image
from ..data.models import (
    ItemCreateDTO,
    issue_capture_file_ownership,
    remove_owned_capture_file,
)
from .queue_worker import QueueWorker

logger = logging.getLogger(__name__)


class CaptureWorker(QueueWorker):
    capture_done = Signal(str, object)   # image_path, ItemCreateDTO
    capture_failed = Signal(str)
    invoice_frame_ready = Signal(object)
    invoice_items_ready = Signal(object, object, str)

    def __init__(self, file_manager: FileManager, parent=None):
        super().__init__(parent)
        self._file_manager = file_manager

    def capture_region(self, x: int, y: int, w: int, h: int,
                       monitor_idx: int, source_mode: str, clipboard_epoch=None):
        if clipboard_epoch is None:
            from ..clipboard.writer import get_clipboard_epoch
            clipboard_epoch = get_clipboard_epoch()
        self._submit_capture(('region', x, y, w, h, monitor_idx, source_mode, clipboard_epoch))

    def capture_fullscreen(self, monitor_idx: int = 1, clipboard_epoch=None):
        if clipboard_epoch is None:
            from ..clipboard.writer import get_clipboard_epoch
            clipboard_epoch = get_clipboard_epoch()
        self._submit_capture(('fullscreen', monitor_idx, clipboard_epoch))

    def _submit_capture(self, task):
        try:
            self.submit(task)
        except RuntimeError as exc:
            self.capture_failed.emit(str(exc))

    def capture_invoice_items(self, region, expected_monitor, cancel):
        self.submit(('invoice_items', region, expected_monitor, cancel))

    def _read_invoice_items(self, region, expected_monitor, cancel):
        from ..core.resource_budget import ResourceBudgetExceeded, memory_preflight
        try:
            from ..invoice_check.capture import capture_regions
            from ..invoice_check.qr_items import (
                MAX_QR_PIXELS, MAX_QR_RECOVERY_PIXELS, InvoiceQrError, read_invoice_items,
            )
        except (ImportError, OSError):
            if not cancel.is_set():
                self.invoice_items_ready.emit(cancel, None, '無法載入 QR 解碼元件，請重新開啟拾字筆。')
            return

        result, error = None, ''
        try:
            pixels = region[2] * region[3]
            if pixels > MAX_QR_PIXELS:
                raise InvoiceQrError('框選範圍太大，請只框住同張發票的左右兩碼。')
            # The recovery workspace is capped even for a large source crop;
            # a tiny crop may still grow to three times its side plus a quiet zone.
            memory_preflight(additional_bytes=pixels * 32 + MAX_QR_RECOVERY_PIXELS * 12 + 32_000_000)
            snapshot = capture_regions({'invoice': region}, expected_monitor=expected_monitor,
                                       cancel_event=cancel)
            if snapshot is None or cancel.is_set():
                return
            self.invoice_frame_ready.emit(cancel)
            result = read_invoice_items(snapshot['images']['invoice'], cancel_event=cancel)
        except (InvoiceQrError, ResourceBudgetExceeded) as exc:
            error = str(exc)
        except Exception:
            error = '無法讀取框選畫面，請重新框選發票的 QR Code。'
        if not cancel.is_set():
            self.invoice_items_ready.emit(cancel, result, error)

    def run(self):
        backend = None
        for task in self.tasks():
            if task[0] == 'invoice_items':
                _, region, expected_monitor, cancel = task
                if not cancel.is_set():
                    self._read_invoice_items(region, expected_monitor, cancel)
                continue
            abs_path = thumb_abs = None
            owned_files = []
            handed_off = False
            try:
                if backend is None:
                    backend = get_capture_backend()
                if task[0] == 'region':
                    _, x, y, w, h, monitor_idx, source_mode, clipboard_epoch = task
                    img = backend.capture_region(x, y, w, h, monitor_idx)
                    region_json = json.dumps({'x': x, 'y': y, 'w': w, 'h': h})
                    monitor = monitor_idx
                elif task[0] == 'fullscreen':
                    _, monitor_idx, clipboard_epoch = task
                    img = backend.capture_monitor(monitor_idx)
                    region_json = None
                    monitor = monitor_idx
                    source_mode = SOURCE_MODE_FULLSCREEN
                else:
                    logger.warning(f"未知 capture task 類型: {task[0]}")
                    continue

                if img is None:
                    self.capture_failed.emit("截圖失敗：無法擷取畫面")
                    continue

                # Convert BGR→RGB for PIL
                pil_img = Image.fromarray(img[:, :, ::-1])
                abs_path, rel_path = self._file_manager.save_capture(pil_img)
                owned_files.append(issue_capture_file_ownership(self._file_manager, rel_path))
                thumb_abs, thumb_rel = self._file_manager.create_thumbnail(abs_path)

                if thumb_abs and thumb_rel:
                    owned_files.append(issue_capture_file_ownership(self._file_manager, thumb_rel))

                h_img, w_img = img.shape[:2]
                img_hash = sha256_image(abs_path)

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
                    clipboard_epoch=clipboard_epoch,
                    transient_capture_files=tuple(owned_files),
                )
                handed_off = True
                self.capture_done.emit(abs_path, dto)
            except Exception as e:
                logger.error(f"CaptureWorker 錯誤: {e}", exc_info=True)
                if not handed_off:
                    for proof in owned_files:
                        try:
                            remove_owned_capture_file(self._file_manager, proof)
                        except (OSError, ValueError):
                            logger.warning('未完成截圖的所有權無法確認，保留檔案')
                self.capture_failed.emit(str(e))
