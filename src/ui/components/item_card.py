# -*- coding: utf-8 -*-
import os
from PySide6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QLabel
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap, QCursor

from ..theme import (
    _BG_THUMB, _TEXT_PRI, _TEXT_SEC,
    _ACCENT, _TEAL, _SUCCESS, _ERROR, _WARNING, _INFO,
    _ACCENT_12, _ACCENT_25, _TEAL_12, _TEAL_25,
)


class ItemCard(QWidget):
    clicked = Signal(int)
    double_clicked = Signal(int)

    def __init__(self, item, data_dir: str, thumb_size: tuple = (48, 48), parent=None):
        super().__init__(parent)
        self._item = item
        self._data_dir = data_dir
        self._setup_ui(thumb_size)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

    def _setup_ui(self, thumb_size: tuple):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 5, 8, 5)
        layout.setSpacing(8)

        # Thumbnail / type indicator
        thumb_lbl = QLabel()
        thumb_lbl.setFixedSize(*thumb_size)
        thumb_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        if self._item.thumbnail_path:
            abs_p = os.path.join(self._data_dir, self._item.thumbnail_path)
            if os.path.exists(abs_p):
                px = QPixmap(abs_p).scaled(
                    *thumb_size,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                )
                thumb_lbl.setPixmap(px)
                thumb_lbl.setStyleSheet(
                    f"background: {_BG_THUMB}; border-radius: 4px;"
                )
            else:
                thumb_lbl.setText("IMG")
                thumb_lbl.setStyleSheet(
                    f"background: {_TEAL_12}; border-radius: 4px;"
                    f" font-size: 10px; font-weight: 600; color: {_TEAL};"
                    f" border: 1px solid {_TEAL_25};"
                )
        else:
            thumb_lbl.setText("T")
            thumb_lbl.setStyleSheet(
                f"background: {_ACCENT_12}; border-radius: 4px;"
                f" font-size: 16px; font-weight: 700; color: {_ACCENT};"
                f" border: 1px solid {_ACCENT_25};"
            )
        layout.addWidget(thumb_lbl)

        # Right panel
        right = QVBoxLayout()
        right.setSpacing(2)
        right.setContentsMargins(0, 1, 0, 1)

        text = self._item.get_effective_text() or "(無文字)"
        preview = text[:60].replace('\n', ' ')
        text_lbl = QLabel(preview)
        text_lbl.setStyleSheet(
            f"font-size: 12px; color: {_TEXT_PRI}; background: transparent;"
        )
        right.addWidget(text_lbl)

        source_names = {
            'region_ocr': 'OCR框選', 'region_image': '框選截圖',
            'fullscreen': '全螢幕', 'clipboard_text': '剪貼簿',
            'clipboard_image': '剪貼板圖', 'import': '匯入'
        }
        src = source_names.get(self._item.source_mode, self._item.source_mode)
        meta_lbl = QLabel(f"{self._item.created_at or ''} · {src}")
        meta_lbl.setStyleSheet(
            f"font-size: 10px; color: {_TEXT_SEC}; background: transparent;"
        )
        right.addWidget(meta_lbl)

        # OCR status badge for non-done items
        status_map = {
            'confirmed': ('已確認', _SUCCESS),
            'needs_review': ('待確認', _WARNING),
            'failed': ('失敗', _ERROR),
            'processing': ('處理中', _INFO),
            'pending': ('排隊', _TEXT_SEC),
        }
        if self._item.ocr_status in status_map:
            label, color = status_map[self._item.ocr_status]
            badge = QLabel(label)
            badge.setStyleSheet(
                f"background: transparent; color: {color};"
                f" font-size: 10px; font-weight: 600;"
                f" border: 1px solid {color}; border-radius: 3px;"
                f" padding: 0px 4px;"
            )
            badge.setFixedHeight(16)
            right.addWidget(badge)

        layout.addLayout(right)
        layout.addStretch()

        if self._item.is_pinned:
            pin = QLabel("●")
            pin.setStyleSheet(
                f"font-size: 8px; color: {_ACCENT}; background: transparent;"
            )
            layout.addWidget(pin)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._item.id)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.double_clicked.emit(self._item.id)
        super().mouseDoubleClickEvent(event)
