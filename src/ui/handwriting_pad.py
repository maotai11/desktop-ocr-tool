"""Mouse and single-touch handwriting pad. No raster OCR or network calls."""
from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QEventPoint, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ..handwriting.protocol import CANVAS_SIZE, MAX_POINTS
from ..handwriting.strokes import StrokeBuffer


class HandwritingPad(QWidget):
    changed = Signal()
    stroke_finished = Signal()
    limit_reached = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.buffer = StrokeBuffer()
        self._mouse_active = False
        self._touch_id = None
        self.setMinimumSize(220, 220)
        self.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName('手寫區')
        self.setToolTip('在方框內寫一個字，點選候選字即可複製')

    def canvas_rect(self):
        size = max(1, min(self.width(), self.height()) - 8)
        return QRectF((self.width() - size) / 2, (self.height() - size) / 2, size, size)

    def _point(self, position):
        rect = self.canvas_rect()
        return tuple(max(0, min(CANVAS_SIZE, round(value * CANVAS_SIZE))) for value in (
            (position.x() - rect.left()) / rect.width(),
            (position.y() - rect.top()) / rect.height()))

    def _begin(self, position):
        if not self.canvas_rect().contains(position):
            return False
        if not self.buffer.begin(*self._point(position)):
            self.limit_reached.emit()
            return False
        self.changed.emit()
        self.update()
        return True

    def _append(self, position):
        point = self._point(position)
        if self.buffer.append(*point):
            self.changed.emit()
            self.update()
        elif (self.buffer.active and point != self.buffer.active[-1] and
              sum(map(len, self.buffer.strokes)) + len(self.buffer.active) >= MAX_POINTS):
            self.limit_reached.emit()

    def _finish(self):
        if self.buffer.finish():
            self.changed.emit()
            self.stroke_finished.emit()
            self.update()

    def clear(self):
        self._mouse_active = False
        self._touch_id = None
        self.buffer.clear()
        self.changed.emit()
        self.update()

    def undo(self):
        self._mouse_active = False
        self._touch_id = None
        self.buffer.undo()
        self.changed.emit()
        self.update()
        if self.buffer.strokes:
            self.stroke_finished.emit()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._touch_id is None:
            self._mouse_active = self._begin(event.position())
            event.accept()
        else:
            event.ignore()

    def mouseMoveEvent(self, event):
        if self._mouse_active:
            self._append(event.position())
            event.accept()
        else:
            event.ignore()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._mouse_active:
            self._append(event.position())
            self._mouse_active = False
            self._finish()
            event.accept()
        else:
            event.ignore()

    def event(self, event):
        kind = event.type()
        if kind == QEvent.Type.TouchBegin:
            if self._mouse_active or not event.points():
                event.accept()
                return True
            point = event.points()[0]
            if self._begin(point.position()):
                self._touch_id = point.id()
            event.accept()
            return True
        if kind in (QEvent.Type.TouchUpdate, QEvent.Type.TouchEnd):
            for point in event.points():
                if point.id() == self._touch_id:
                    self._append(point.position())
                    if point.state() == QEventPoint.State.Released or kind == QEvent.Type.TouchEnd:
                        self._touch_id = None
                        self._finish()
                    break
            if kind == QEvent.Type.TouchEnd and self._touch_id is not None:
                self._touch_id = None
                self._finish()
            event.accept()
            return True
        if kind == QEvent.Type.TouchCancel:
            self._touch_id = None
            self.buffer.cancel()
            self.changed.emit()
            self.update()
            event.accept()
            return True
        return super().event(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.canvas_rect()
        painter.setBrush(QColor('#F7F5F0'))
        painter.setPen(QPen(QColor('#C6CECF'), 1))
        painter.drawRoundedRect(rect, 10, 10)
        painter.setPen(QPen(QColor('#D4DEDC'), 1, Qt.PenStyle.DashLine))
        painter.drawLine(QPointF(rect.center().x(), rect.top()), QPointF(rect.center().x(), rect.bottom()))
        painter.drawLine(QPointF(rect.left(), rect.center().y()), QPointF(rect.right(), rect.center().y()))
        painter.setClipRect(rect)
        painter.setPen(QPen(QColor('#0A213E'), 4, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        strokes = [*self.buffer.strokes]
        if self.buffer.active:
            strokes.append(self.buffer.active)
        def map_point(point):
            return QPointF(rect.left() + point[0] * rect.width() / CANVAS_SIZE,
                           rect.top() + point[1] * rect.height() / CANVAS_SIZE)
        for stroke in strokes:
            if len(stroke) == 1:
                painter.drawPoint(map_point(stroke[0]))
            else:
                for start, end in zip(stroke, stroke[1:]):
                    painter.drawLine(map_point(start), map_point(end))
