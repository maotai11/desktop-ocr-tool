"""Click-through desktop marks; never include this surface in an OCR grab."""
from __future__ import annotations

import math
import unicodedata

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QApplication, QWidget

from .theme import _BG, _SUCCESS, _TEXT_PRI, _WARNING


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
        font = QFont(self.font())
        font.setPixelSize(12)
        self.setFont(font)

    @property
    def marks(self):
        return tuple(dict(mark) for mark in self._marks)

    @staticmethod
    def _plain_text(value, limit=240):
        text = value if isinstance(value, str) else ''
        text = ''.join(' ' if character.isspace() else character for character in text)
        text = ''.join(character for character in text if unicodedata.category(character) not in ('Cc', 'Cf'))
        text = ' '.join(unicodedata.normalize('NFC', text).split())
        return text[:limit] + ('…' if len(text) > limit else '')

    @property
    def callouts(self):
        return self._layout_callouts()[0]

    @property
    def overflow_count(self):
        return self._layout_callouts()[1]

    @property
    def overflow_rect(self):
        rectangle = self._layout_callouts()[2]
        return tuple(rectangle.getRect()) if rectangle is not None else None

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
            name = self._plain_text(mark.get('name', ''), 32)
            status = mark.get('status', 'matched' if mark.get('kind', 'match') == 'match' else 'uncertain')
            status = status if status in ('matched', 'similar', 'uncertain') else 'uncertain'
            bounds = mark.get('bounds')
            if bounds is not None:
                if (not isinstance(bounds, (tuple, list)) or len(bounds) != 4
                        or any(type(value) not in (int, float) or not math.isfinite(value) for value in bounds)
                        or bounds[2] <= 0 or bounds[3] <= 0):
                    valid = False
                    continue
                bounds = tuple(bounds)
            boxes = []
            for box in mark['boxes']:
                polygon = self.validated_box(box)
                if polygon is None:
                    valid = False
                    continue
                if bounds is not None and not all(QRectF(*bounds).contains(QPointF(*point)) for point in polygon):
                    valid = False
                    continue
                count += 1
                if count > 512:
                    self.clear()
                    return False
                boxes.append(polygon)
            if boxes:
                prepared.append({'name': name, 'kind': 'match' if status == 'matched' else 'uncertain',
                                 'status': status, 'boxes': tuple(boxes), 'bounds': bounds,
                                 'a_text': self._plain_text(mark.get('a_text', '')),
                                 'b_text': self._plain_text(mark.get('b_text', ''))})
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

    def _layout_callouts(self):
        """Plain-text cards in B, nearest free slot first; never overlap cards."""
        screen = QApplication.primaryScreen()
        if screen is None or not self._marks:
            return (), 0, None
        scale = screen.devicePixelRatio()
        screen_rect = QRectF(0, 0, screen.geometry().width(), screen.geometry().height())
        metrics = QFontMetricsF(self.font())
        line_height = math.ceil(metrics.lineSpacing())
        height = 3 * line_height + 14
        areas = [QRectF(*(value / scale for value in mark['bounds'])).intersected(screen_rect)
                 if mark['bounds'] is not None else screen_rect for mark in self._marks]
        source_lines = [QPolygonF([QPointF(x / scale, y / scale) for x, y in box]).boundingRect()
                        for mark in self._marks for box in mark['boxes']]

        def arrange(reserve):
            placed, occupied = [], []
            for mark, original in zip(self._marks, areas):
                area = original.adjusted(3, 3, -3, -3 - reserve)
                width = min(280.0, area.width())
                if width < 40 or area.height() < height:
                    continue
                anchor = QPolygonF([QPointF(x / scale, y / scale) for x, y in mark['boxes'][0]]).boundingRect()
                xs = [area.left() + column * (width + 6)
                      for column in range(max(1, int((area.width() + 6) // (width + 6))))]
                xs += [max(area.left(), min(anchor.left(), area.right() - width)),
                       anchor.right() + 6, anchor.left() - width - 6]
                ys = [area.top() + row * (height + 6)
                      for row in range(max(1, int((area.height() + 6) // (height + 6))))]
                ys += [anchor.bottom() + 6, anchor.top() - height - 6]
                candidates = [QRectF(x, y, width, height) for x in xs for y in ys]
                candidates.sort(key=lambda rectangle: abs(rectangle.center().x() - anchor.center().x())
                                + abs(rectangle.center().y() - anchor.center().y()))
                available = [item for item in candidates if area.contains(item)
                             and not any(item.adjusted(-2, -2, 2, 2).intersects(other) for other in occupied)]
                rectangle = next((item for item in available if not any(item.intersects(line) for line in source_lines)),
                                 available[0] if available else None)
                if rectangle is None:
                    continue
                occupied.append(rectangle)
                status_text = {'matched': '一致', 'similar': '近似，需確認', 'uncertain': '需確認'}[mark['status']]
                lines = (f'{mark["name"]} · {status_text}', f'A  {mark["a_text"] or "—"}',
                         f'B  {mark["b_text"] or "—"}')
                displayed = tuple(metrics.elidedText(line, Qt.TextElideMode.ElideRight, width - 16) for line in lines)
                placed.append({**mark, 'rect': tuple(rectangle.getRect()), 'anchor': tuple(anchor.getRect()),
                               'display_lines': displayed, 'line_height': line_height})
            return tuple(placed)

        cards = arrange(0)
        if len(cards) == len(self._marks):
            return cards, 0, None
        cards = arrange(25)
        area = areas[0]
        overflow_rect = QRectF(area.left() + 3, max(area.top(), area.bottom() - 23),
                               max(0, area.width() - 6), min(20, area.height()))
        return cards, len(self._marks) - len(cards), overflow_rect

    def paintEvent(self, event):
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        scale = screen.devicePixelRatio()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        for mark in self._marks:
            painter.save()
            if mark['bounds'] is not None:
                painter.setClipRect(QRectF(*(value / scale for value in mark['bounds'])))
            color = QColor(_SUCCESS if mark['status'] == 'matched' else _WARNING)
            pen = QPen(color, 2)
            painter.setPen(pen)
            fill = QColor(color)
            fill.setAlpha(round(255 * self._fill_opacity / 100))
            painter.setBrush(fill)
            for box in mark['boxes']:
                polygon = QPolygonF([QPointF(x / scale, y / scale) for x, y in box])
                painter.drawPolygon(polygon)
            painter.restore()
        cards, overflow, overflow_rect = self._layout_callouts()
        for card in cards:
            rectangle, anchor = QRectF(*card['rect']), QRectF(*card['anchor'])
            color = QColor(_SUCCESS if card['status'] == 'matched' else _WARNING)
            painter.setPen(QPen(color, 1))
            painter.drawLine(anchor.center(), rectangle.center())
            painter.setBrush(QColor(_BG))
            painter.drawRoundedRect(rectangle, 5, 5)
            for index, line in enumerate(card['display_lines']):
                painter.setPen(color if index == 0 else QColor(_TEXT_PRI))
                line_rect = QRectF(rectangle.left() + 8, rectangle.top() + 7 + index * card['line_height'],
                                   rectangle.width() - 16, card['line_height'])
                painter.drawText(line_rect, Qt.AlignmentFlag.AlignVCenter, line)
        if overflow and overflow_rect is not None:
            painter.fillRect(overflow_rect, QColor(_BG))
            painter.setPen(QColor(_WARNING))
            text = f'另有 {overflow} 筆，請開啟搜尋面板查看'
            text = painter.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, max(0, int(overflow_rect.width() - 8)))
            painter.drawText(overflow_rect.adjusted(4, 0, -4, 0), Qt.AlignmentFlag.AlignVCenter, text)
