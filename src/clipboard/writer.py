# -*- coding: utf-8 -*-
import uuid
import logging
from PySide6.QtCore import QMimeData, QByteArray
from PySide6.QtWidgets import QApplication
from ..core.constants import CUSTOM_MIME_TYPE
from .text_cleanup import prepare_copy_text

logger = logging.getLogger(__name__)
_last_write_id: str = ''


def copy_text(text: str, cfg=None) -> str:
    """Copy formatted output without altering the source or clearing on empty."""
    prepared = prepare_copy_text(text, cfg)
    if not prepared or not prepared.strip():
        return ''
    return write_text_to_clipboard(prepared)


def copy_text_batch(texts, cfg=None) -> str:
    """Format each record separately, retaining boundaries between records."""
    prepared = [prepare_copy_text(text, cfg) for text in texts]
    prepared = [text for text in prepared if text and text.strip()]
    return write_text_to_clipboard('\n\n'.join(prepared)) if prepared else ''


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
