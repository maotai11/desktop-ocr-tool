# -*- coding: utf-8 -*-
import logging
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QClipboard
from PySide6.QtWidgets import QApplication
from ..core.constants import CUSTOM_MIME_TYPE

logger = logging.getLogger(__name__)


class ClipboardWatcher(QObject):
    text_captured = Signal(str)
    image_captured = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._paused = False
        self._clipboard = QApplication.clipboard()
        self._clipboard.changed.connect(self._on_changed)
        logger.info("剪貼簿監聽已啟動（QClipboard.changed）")

    def pause(self, paused: bool):
        self._paused = paused
        logger.info(f"剪貼簿監聽 {'暫停' if paused else '恢復'}")

    def _on_changed(self, mode: QClipboard.Mode):
        if self._paused:
            return
        if mode != QClipboard.Mode.Clipboard:
            return
        mime = self._clipboard.mimeData()
        if mime is None:
            return

        # Check ignore_self via custom MIME
        if mime.hasFormat(CUSTOM_MIME_TYPE):
            data = mime.data(CUSTOM_MIME_TYPE)
            write_id = bytes(data).decode('utf-8', errors='ignore')
            from .writer import get_last_write_id
            if write_id == get_last_write_id():
                logger.debug("忽略自身剪貼簿事件")
                return

        if mime.hasText():
            text = mime.text()
            if text:
                logger.debug(f"剪貼簿文字擷取: {len(text)} 字元")
                self.text_captured.emit(text)
        elif mime.hasImage():
            logger.debug("剪貼簿圖片擷取")
            self.image_captured.emit('')
