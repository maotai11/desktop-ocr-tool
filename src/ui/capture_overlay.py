# -*- coding: utf-8 -*-
"""
框選覆蓋層：全螢幕覆蓋，讓使用者拖曳框選區域
"""
import logging
from typing import Callable, Optional
from PySide6.QtWidgets import QWidget, QApplication, QRubberBand
from PySide6.QtCore import Qt, QRect, QPoint, QSize, Signal
from PySide6.QtGui import QPainter, QColor, QCursor

logger = logging.getLogger(__name__)


class CaptureOverlay(QWidget):
    region_selected = Signal(int, int, int, int, int)  # x, y, w, h, monitor_idx

    def __init__(self, parent=None):
        super().__init__(parent)
        self._callback: Optional[Callable] = None
        self._origin = QPoint()
        self._rubber_band: Optional[QRubberBand] = None
        self._is_drawing = False
        self._monitor_idx = 1
        self._monitor_offset_x = 0
        self._monitor_offset_y = 0

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor))

    def start_capture(self, callback: Callable):
        self._callback = callback
        self._setup_fullscreen()
        self.show()
        self.raise_()
        self.activateWindow()
        self.grabKeyboard()

    def _setup_fullscreen(self):
        screen = QApplication.primaryScreen()
        geo = screen.geometry()
        self._monitor_offset_x = geo.x()
        self._monitor_offset_y = geo.y()
        self.setGeometry(geo)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 60))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._origin = event.position().toPoint()
            self._is_drawing = True
            if self._rubber_band is None:
                self._rubber_band = QRubberBand(QRubberBand.Shape.Rectangle, self)
            self._rubber_band.setGeometry(QRect(self._origin, QSize()))
            self._rubber_band.show()

    def mouseMoveEvent(self, event):
        if self._is_drawing and self._rubber_band:
            self._rubber_band.setGeometry(
                QRect(self._origin, event.position().toPoint()).normalized()
            )

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._is_drawing:
            self._is_drawing = False
            if self._rubber_band:
                self._rubber_band.hide()
            rect = QRect(self._origin, event.position().toPoint()).normalized()
            self._finish_capture(rect)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self._cancel()

    def _finish_capture(self, rect: QRect):
        self.releaseKeyboard()
        self.hide()
        if rect.width() < 5 or rect.height() < 5:
            logger.debug("框選區域太小，取消")
            return

        dpr = QApplication.primaryScreen().devicePixelRatio()
        x = int(rect.x() * dpr)
        y = int(rect.y() * dpr)
        w = int(rect.width() * dpr)
        h = int(rect.height() * dpr)
        x = max(0, x)
        y = max(0, y)

        logger.info(f"框選區域: x={x} y={y} w={w} h={h} monitor={self._monitor_idx}")
        if self._callback:
            self._callback(x, y, w, h, self._monitor_idx)

    def _cancel(self):
        self.releaseKeyboard()
        if self._rubber_band:
            self._rubber_band.hide()
        self.hide()
        logger.debug("框選已取消")
