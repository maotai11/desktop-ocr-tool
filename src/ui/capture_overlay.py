# -*- coding: utf-8 -*-
"""
框選覆蓋層：全螢幕覆蓋，讓使用者拖曳框選區域
"""
import logging
from typing import Callable, Optional
from PySide6.QtWidgets import QWidget, QApplication, QRubberBand
from PySide6.QtCore import Qt, QRect, QPoint, QSize, Signal, QTimer
from PySide6.QtGui import QPainter, QColor, QCursor

logger = logging.getLogger(__name__)


class CaptureOverlay(QWidget):
    cancelled = Signal()
    region_selected = Signal(int, int, int, int, int)  # x, y, w, h, monitor_idx

    def __init__(self, parent=None):
        super().__init__(parent)
        self._callback: Optional[Callable] = None
        self._capture_generation = 0
        self._capture_active = False
        self._hiding_for_delivery = False
        self._origin = QPoint()
        self._rubber_band: Optional[QRubberBand] = None
        self._is_drawing = False
        self._monitor_idx = 1
        self._monitor_offset_x = 0
        self._monitor_offset_y = 0
        self.selection_prompt = ''

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor))

    def start_capture(self, callback: Callable):
        self._capture_generation += 1
        self._reset_drag()
        self._capture_active = True
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
        if self.selection_prompt:
            painter.fillRect(20, 20, 300, 44, QColor('#17202b'))
            painter.setPen(QColor('#ffffff'))
            painter.drawText(QRect(32, 20, 276, 44), Qt.AlignmentFlag.AlignVCenter,
                             self.selection_prompt)

    def mousePressEvent(self, event):
        if (self._capture_active and self.isVisible()
                and event.button() == Qt.MouseButton.LeftButton):
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
        if not self._capture_active:
            return
        self.releaseKeyboard()
        self._reset_drag()
        if rect.width() < 5 or rect.height() < 5:
            logger.debug("框選區域太小，取消")
            self._cancel()
            return

        # Only this hide keeps a pending selection alive while the compositor
        # removes our mask. User/owner hide and close retire the whole session.
        self._hiding_for_delivery = True
        try:
            self.hide()
        finally:
            self._hiding_for_delivery = False

        dpr = QApplication.primaryScreen().devicePixelRatio()
        x = int(rect.x() * dpr)
        y = int(rect.y() * dpr)
        w = int(rect.width() * dpr)
        h = int(rect.height() * dpr)
        x = max(0, x)
        y = max(0, y)

        logger.info(f"框選區域: x={x} y={y} w={w} h={h} monitor={self._monitor_idx}")
        if self._callback:
            # 延遲 150ms：讓 OS 重繪（移除半透明遮罩），避免截圖包含 overlay 圖層
            monitor, generation = self._monitor_idx, self._capture_generation

            def deliver():
                if not self._capture_active or generation != self._capture_generation:
                    return
                callback = self._callback
                self._callback = None
                self._capture_active = False
                # Consume the session before calling owner code, which may
                # immediately start another selection or shut down the app.
                if callback is not None:
                    callback(x, y, w, h, monitor)

            QTimer.singleShot(150, self, deliver)

    def _reset_drag(self):
        self._is_drawing = False
        self._origin = QPoint()
        if self._rubber_band:
            self._rubber_band.hide()

    def _cancel(self):
        was_active = self._capture_active
        self._capture_active = False
        self._capture_generation += 1
        self._callback = None
        self.releaseKeyboard()
        self._reset_drag()
        self.hide()
        if was_active:
            self.cancelled.emit()
            logger.debug("框選已取消")

    def hideEvent(self, event):
        if not self._hiding_for_delivery:
            self._cancel()
        super().hideEvent(event)

    def closeEvent(self, event):
        self._cancel()
        super().closeEvent(event)
