# -*- coding: utf-8 -*-
import logging
import sys
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QClipboard
from PySide6.QtWidgets import QApplication
from ..core.constants import CUSTOM_MIME_TYPE

logger = logging.getLogger(__name__)


def _clipboard_sequence():
    """Windows sequence identity, without opening or reading clipboard content."""
    if sys.platform != 'win32':
        return None
    try:
        import ctypes
        user32 = ctypes.WinDLL('user32', use_last_error=True)
        user32.GetClipboardSequenceNumber.argtypes = []
        user32.GetClipboardSequenceNumber.restype = ctypes.c_uint32
        return user32.GetClipboardSequenceNumber() or None
    except (AttributeError, OSError):
        return None


class ClipboardWatcher(QObject):
    text_captured = Signal(str)
    image_captured = Signal(str)

    def __init__(self, parent=None, ignore_self=True):
        super().__init__(parent)
        self._paused = False
        self._closed = False
        self._ignore_self = ignore_self
        self._clipboard = QApplication.clipboard()
        self._last_sequence = _clipboard_sequence()
        self._clipboard.changed.connect(self._on_changed)
        logger.info("剪貼簿監聽已啟動（QClipboard.changed）")

    def pause(self, paused: bool):
        self._paused = paused
        logger.info(f"剪貼簿監聽 {'暫停' if paused else '恢復'}")

    def close(self):
        """Detach only this listener; never modify the system clipboard."""
        if self._closed:
            return
        self._closed = self._paused = True
        try:
            self._clipboard.changed.disconnect(self._on_changed)
        except (RuntimeError, TypeError):
            pass

    def _on_changed(self, mode: QClipboard.Mode):
        if self._paused or self._closed:
            return
        if mode != QClipboard.Mode.Clipboard:
            return
        sequence = _clipboard_sequence()
        if sequence is not None:
            if sequence == self._last_sequence:
                return  # A queued notification predating enable/resume.
            self._last_sequence = sequence
        mime = self._clipboard.mimeData()
        if mime is None:
            return

        # Check ignore_self via custom MIME
        if self._ignore_self and mime.hasFormat(CUSTOM_MIME_TYPE):
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
