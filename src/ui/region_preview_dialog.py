"""Explicit, memory-only screen selection preview and adjustment."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QGraphicsPixmapItem,
                              QGraphicsScene, QGraphicsView, QHBoxLayout, QLabel,
                              QPushButton, QVBoxLayout)

from ..comparison.capture import validate_region


class _SelectionView(QGraphicsView):
    changed = Signal(object)

    def __init__(self, image, region, parent=None):
        super().__init__(parent)
        self._image_bounds = QRectF(0, 0, image.width(), image.height())
        scene = QGraphicsScene(self)
        pixmap = QPixmap.fromImage(image)
        pixmap.setDevicePixelRatio(1)
        scene.addItem(QGraphicsPixmapItem(pixmap))
        self.setScene(scene)
        self._outline = scene.addRect(QRectF(*region[:4]), QPen(QColor('#2563eb'), 3))
        self._outline.setZValue(1)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setMinimumHeight(180)
        self._gesture = None

    @property
    def rectangle(self):
        return self._outline.rect()

    def set_rectangle(self, rectangle):
        rectangle = QRectF(QRectF(rectangle).toAlignedRect())
        if rectangle.width() < 5 or rectangle.height() < 5:
            return False
        if not self._image_bounds.contains(rectangle):
            return False
        if rectangle.width() * rectangle.height() > 16_000_000:
            return False
        self._outline.setRect(rectangle)
        self.changed.emit(rectangle)
        return True

    def fit(self):
        self.fitInView(self._image_bounds, Qt.AspectRatioMode.KeepAspectRatio)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            point = self.mapToScene(event.position().toPoint())
            rect = self.rectangle
            tolerance = 12 / max(self.transform().m11(), 0.01)
            if (point - rect.bottomRight()).manhattanLength() <= tolerance:
                self._gesture = ('resize', point, QRectF(rect))
            elif rect.contains(point):
                self._gesture = ('move', point, QRectF(rect))
            else:
                self._gesture = None
            if self._gesture:
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._gesture:
            kind, start, initial = self._gesture
            delta = self.mapToScene(event.position().toPoint()) - start
            rect = QRectF(initial)
            if kind == 'move':
                rect.moveTopLeft(QPointF(round(max(0, min(initial.x() + delta.x(), self._image_bounds.width() - rect.width()))),
                                        round(max(0, min(initial.y() + delta.y(), self._image_bounds.height() - rect.height())))))
            else:
                rect.setWidth(max(5, min(initial.width() + delta.x(), self._image_bounds.right() - rect.x())))
                rect.setHeight(max(5, min(initial.height() + delta.y(), self._image_bounds.bottom() - rect.y())))
            self.set_rectangle(rect)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._gesture:
            self._gesture = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def hideEvent(self, event):
        self._gesture = None
        super().hideEvent(event)


class RegionPreviewDialog(QDialog):
    """Image pixels and ROI are primary-monitor physical pixels, never saved."""

    def __init__(self, image: QImage, region, parent=None):
        super().__init__(parent)
        region = validate_region(region)
        if region[4] != 1 or image.isNull() or not QRectF(0, 0, image.width(), image.height()).contains(QRectF(*region[:4])):
            raise ValueError('Invalid preview bounds')
        self.setWindowTitle('預覽／調整框選位置')
        self.resize(760, 620)
        self._image = image.copy()
        self._image.setDevicePixelRatio(1)
        self._selected_region = region
        self._view = _SelectionView(self._image, region, self)
        layout = QVBoxLayout(self)
        hint = QLabel('拖曳藍框移動位置；拖曳右下角調整大小。下方預覽目前範圍。')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addWidget(self._view, 1)
        zoom = QHBoxLayout()
        for text, action in [('縮小', lambda: self._view.scale(.8, .8)),
                             ('放大', lambda: self._view.scale(1.25, 1.25)),
                             ('顯示全畫面', self._view.fit)]:
            button = QPushButton(text)
            button.clicked.connect(action)
            zoom.addWidget(button)
        layout.addLayout(zoom)
        self._crop = QLabel()
        self._crop.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._crop.setMinimumHeight(80)
        self._crop.setMaximumHeight(140)
        layout.addWidget(self._crop)
        arrows = QHBoxLayout()
        for text, dx, dy in [('←', -1, 0), ('↑', 0, -1), ('↓', 0, 1), ('→', 1, 0)]:
            button = QPushButton(text)
            button.setToolTip('微調 1 像素；按住 Shift 微調 10 像素')
            button.clicked.connect(lambda checked=False, x=dx, y=dy: self.nudge(x, y))
            arrows.addWidget(button)
        layout.addLayout(arrows)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('套用位置')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._view.changed.connect(self._update_crop)
        self._update_crop(self._view.rectangle)

    @property
    def selected_region(self):
        return self._selected_region

    def nudge(self, dx, dy):
        from PySide6.QtWidgets import QApplication
        step = 10 if QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier else 1
        return self._view.set_rectangle(self._view.rectangle.translated(dx * step, dy * step))

    def _update_crop(self, rect):
        self._selected_region = tuple(round(value) for value in (rect.x(), rect.y(), rect.width(), rect.height())) + (1,)
        crop = QPixmap.fromImage(self._image.copy(rect.toAlignedRect()))
        self._crop.setPixmap(crop.scaled(max(100, self.width() - 40), 130, Qt.AspectRatioMode.KeepAspectRatio,
                                         Qt.TransformationMode.SmoothTransformation))

    def showEvent(self, event):
        super().showEvent(event)
        self._view.fit()

    def done(self, result):
        super().done(result)
        # The caller retains only the selected rectangle, never the snapshot.
        self._crop.clear()
        self._view.scene().clear()
        self._image = QImage()
