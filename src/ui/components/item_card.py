# -*- coding: utf-8 -*-
import os
from PySide6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QLabel
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap, QCursor


class ItemCard(QWidget):
    clicked = Signal(int)
    double_clicked = Signal(int)

    def __init__(self, item, data_dir: str, thumb_size: tuple = (60, 60), parent=None):
        super().__init__(parent)
        self._item = item
        self._data_dir = data_dir
        self._setup_ui(thumb_size)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

    def _setup_ui(self, thumb_size: tuple):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

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
            else:
                thumb_lbl.setText("IMG")
                thumb_lbl.setStyleSheet(
                    "background:#e8f4fd; border-radius:4px; font-size:10px; color:#4A90D9;"
                )
        else:
            thumb_lbl.setText("T")
            thumb_lbl.setStyleSheet(
                "background:#e8f8e8; border-radius:4px; font-size:18px; color:#27ae60;"
            )
        layout.addWidget(thumb_lbl)

        # Right panel
        right = QVBoxLayout()
        right.setSpacing(2)
        right.setContentsMargins(0, 0, 0, 0)

        text = self._item.get_effective_text() or "(無文字)"
        preview = text[:60].replace('\n', ' ')
        text_lbl = QLabel(preview)
        text_lbl.setStyleSheet("font-size:12px; color:#333;")
        right.addWidget(text_lbl)

        source_names = {
            'region_ocr': 'OCR框選', 'region_image': '框選截圖',
            'fullscreen': '全螢幕', 'clipboard_text': '剪貼簿',
            'clipboard_image': '剪貼板圖', 'import': '匯入'
        }
        src = source_names.get(self._item.source_mode, self._item.source_mode)
        meta_lbl = QLabel(f"{self._item.created_at or ''} | {src}")
        meta_lbl.setStyleSheet("font-size:10px; color:#888;")
        right.addWidget(meta_lbl)

        # OCR status badge for non-done items
        status_map = {
            'confirmed': ('已確認', '#27ae60'),
            'needs_review': ('待確認', '#f39c12'),
            'failed': ('失敗', '#e74c3c'),
            'processing': ('處理中', '#3498db'),
            'pending': ('排隊', '#95a5a6'),
        }
        if self._item.ocr_status in status_map:
            label, color = status_map[self._item.ocr_status]
            badge = QLabel(label)
            badge.setStyleSheet(
                f"background:{color}; color:white; border-radius:3px; "
                f"padding:1px 4px; font-size:10px;"
            )
            right.addWidget(badge)

        layout.addLayout(right)
        layout.addStretch()

        if self._item.is_pinned:
            pin = QLabel("[釘]")
            pin.setStyleSheet("font-size:10px; color:#4A90D9;")
            layout.addWidget(pin)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._item.id)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.double_clicked.emit(self._item.id)
        super().mouseDoubleClickEvent(event)
