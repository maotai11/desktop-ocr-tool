"""Click-through desktop marks; never include this surface in an OCR grab."""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QApplication, QWidget

from .theme import _ACCENT, _BG, _SUCCESS, _TEXT_PRI, _WARNING


class SearchHighlightOverlay(QWidget):
    """Marks are primary-monitor-local physical pixels, independent of DPI."""

    def __init__(self):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.WindowTransparentForInput
                         | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._marks = ()
        self._suspended = False
        self._wanted = False
        self._fill_opacity = 12

    @property
    def marks(self):
        return tuple({'name': name, 'kind': kind, 'boxes': boxes}
                     for name, kind, boxes in self._marks)

    @staticmethod
    def validated_box(box):
        """Ignore malformed model coordinates before they reach a Qt painter."""
        if (not isinstance(box, (tuple, list)) or len(box) != 4
                or any(not isinstance(point, (tuple, list)) or len(point) != 2 for point in box)):
            return None
        try:
            polygon = tuple(tuple(float(value) for value in point) for point in box)
        except (TypeError, ValueError, OverflowError):
            return None
        if not all(math.isfinite(value) for point in polygon for value in point):
            return None
        if QPolygonF([QPointF(*point) for point in polygon]).boundingRect().isEmpty():
            return None
        return polygon

    def set_marks(self, marks, *, show=True):
        """Return False if any geometry was rejected; never publish a truncated set."""
        prepared = []
        count = 0
        valid = True
        if not isinstance(marks, (tuple, list)):
            self.clear()
            return False
        for mark in marks:
            if not isinstance(mark, dict) or not isinstance(mark.get('boxes'), (tuple, list)):
                valid = False
                continue
            name = str(mark.get('name', ''))[:32]
            kind = mark.get('kind', 'match')
            boxes = []
            for box in mark['boxes']:
                polygon = self.validated_box(box)
                if polygon is None:
                    valid = False
                    continue
                count += 1
                if count > 512:
                    self.clear()
                    return False
                boxes.append(polygon)
            if boxes:
                prepared.append((name, kind, tuple(boxes)))
        if not valid:
            self.clear()
            return False
        self._marks = tuple(prepared)
        self._wanted = bool(show and self._marks)
        self.update()
        if not self._suspended:
            self.restore_after_capture()
        return valid

    def hide_for_capture(self):
        self._suspended = True
        self.hide()

    def restore_after_capture(self):
        self._suspended = False
        screen = QApplication.primaryScreen()
        if self._wanted and self._marks and screen is not None:
            self.setGeometry(screen.geometry())
            self.show()
        else:
            self.hide()

    def clear(self):
        self._marks = ()
        self._wanted = False
        self.hide()
        self.update()

    def set_fill_opacity(self, percent):
        self._fill_opacity = max(0, min(60, int(percent)))
        self.update()

    def set_pinned(self, pinned):
        visible = self.isVisible()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, bool(pinned))
        if visible and not self._suspended and self._wanted:
            self.restore_after_capture()

    def paintEvent(self, event):
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        scale = screen.devicePixelRatio()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        for name, kind, boxes in self._marks:
            color = QColor(_ACCENT if kind == 'source' else _WARNING if kind == 'uncertain' else _SUCCESS)
            pen = QPen(color, 2)
            if kind == 'source':
                pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            fill = QColor(color)
            fill.setAlpha(round(255 * self._fill_opacity / 100))
            painter.setBrush(fill if kind != 'source' else Qt.BrushStyle.NoBrush)
            first = None
            for box in boxes:
                polygon = QPolygonF([QPointF(x / scale, y / scale) for x, y in box])
                painter.drawPolygon(polygon)
                if first is None:
                    first = polygon.boundingRect()
            if first is not None:
                width = min(self.width(), painter.fontMetrics().horizontalAdvance(name) + 14)
                label = QRectF(max(0, min(first.left(), self.width() - width)),
                               max(0, first.top() - 25), width, 23)
                painter.fillRect(label, QColor(_BG))
                painter.setPen(QColor(_TEXT_PRI))
                painter.drawText(label.adjusted(7, 0, -7, 0), Qt.AlignmentFlag.AlignVCenter, name)
                painter.setPen(pen)
