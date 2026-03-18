# -*- coding: utf-8 -*-
import uuid
import logging
from PySide6.QtCore import QMimeData, QByteArray
from PySide6.QtWidgets import QApplication
from ..core.constants import CUSTOM_MIME_TYPE

logger = logging.getLogger(__name__)
_last_write_id: str = ''


def write_text_to_clipboard(text: str) -> str:
    global _last_write_id
    write_id = str(uuid.uuid4())
    _last_write_id = write_id
    mime = QMimeData()
    mime.setText(text)
    mime.setData(CUSTOM_MIME_TYPE, QByteArray(write_id.encode('utf-8')))
    QApplication.clipboard().setMimeData(mime)
    logger.debug(f"已寫入文字到剪貼簿 (id={write_id[:8]})")
    return write_id


def write_image_to_clipboard(image_path: str) -> str:
    global _last_write_id
    from PySide6.QtGui import QImage
    write_id = str(uuid.uuid4())
    _last_write_id = write_id
    mime = QMimeData()
    img = QImage(image_path)
    if not img.isNull():
        mime.setImageData(img)
    mime.setData(CUSTOM_MIME_TYPE, QByteArray(write_id.encode('utf-8')))
    QApplication.clipboard().setMimeData(mime)
    logger.debug(f"已寫入圖片到剪貼簿 (id={write_id[:8]})")
    return write_id


def get_last_write_id() -> str:
    return _last_write_id
