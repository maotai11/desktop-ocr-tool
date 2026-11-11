"""Click-through text beneath A fields; never include it in an OCR grab."""
from __future__ import annotations

import math
import sys
import unicodedata

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
)
from PySide6.QtWidgets import QApplication, QWidget


def apply_native_pin(widget, pinned):
    """Reapply the native Z order after show/handle changes without taking focus."""
    if sys.platform != 'win32' or not widget.isVisible():
        return True
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    get_style = user32.GetWindowLongW
    get_style.argtypes = (wintypes.HWND, ctypes.c_int)
    get_style.restype = wintypes.LONG
    set_style = user32.SetWindowLongW
    set_style.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.LONG)
    set_style.restype = wintypes.LONG
    handle = int(widget.winId())
    # Hide these utility surfaces from the taskbar without Qt.Tool's
    # application-activation visibility policy.
    style = (get_style(handle, -20) | 0x80 | 0x08000000) & ~0x40000
    ctypes.set_last_error(0)
    if not set_style(handle, -20, style) and ctypes.get_last_error():
        return False
    position = user32.SetWindowPos
    position.argtypes = (wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                         ctypes.c_int, ctypes.c_int, wintypes.UINT)
    position.restype = wintypes.BOOL
    # NOMOVE | NOSIZE | NOACTIVATE | NOOWNERZORDER. No process/timer is created.
    return bool(position(handle, -1 if pinned else -2, 0, 0, 0, 0, 0x0013 | 0x0200))


class SearchHighlightOverlay(QWidget):
    """A anchors use primary-monitor-local physical pixels, independent of DPI."""

    def __init__(self):
        # An independent Window survives another application becoming active;
        # Tool windows can follow the owning application's activation visibility.
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.WindowTransparentForInput
                         | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._marks = ()
        self._suspended = False
        self._wanted = False
        self._pinned = True
        font = QFont(self.font())
        font.setPixelSize(13)
        font.setWeight(QFont.Weight.DemiBold)
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

    @staticmethod
    def _valid_rect(rectangle):
        return (isinstance(rectangle, (tuple, list)) and len(rectangle) == 4
                and all(type(value) in (int, float) and math.isfinite(value) for value in rectangle)
                and rectangle[2] > 0 and rectangle[3] > 0)

    def set_marks(self, marks, *, show=True):
        """Validate every anchor/candidate; reject a partial or malformed set."""
        prepared, count = [], 0
        if not isinstance(marks, (tuple, list)) or len(marks) > 20:
            self.clear()
            return False
        for mark in marks:
            if not isinstance(mark, dict) or not isinstance(mark.get('boxes'), (tuple, list)):
                self.clear()
                return False
            boxes = []
            bounds = mark.get('bounds')
            if bounds is not None and not self._valid_rect(bounds):
                self.clear()
                return False
            for box in mark['boxes']:
                polygon = self.validated_box(box)
                count += 1
                if (polygon is None or count > 512 or (bounds is not None
                        and not all(QRectF(*bounds).contains(QPointF(*point)) for point in polygon))):
                    self.clear()
                    return False
                boxes.append(polygon)
            anchor = mark.get('anchor')
            if anchor is None and boxes:
                anchor = QPolygonF([QPointF(*p) for p in boxes[0]]).boundingRect().getRect()
            if not self._valid_rect(anchor):
                self.clear()
                return False
            status = mark.get('status', 'matched' if mark.get('kind', 'match') == 'match' else 'uncertain')
            if status not in ('matched', 'similar', 'uncertain', 'missing', 'ambiguous'):
                status = 'uncertain'
            prepared.append({'name': self._plain_text(mark.get('name', ''), 32),
                             'kind': 'match' if status == 'matched' else 'uncertain',
                             'status': status, 'boxes': tuple(boxes), 'bounds': tuple(bounds) if bounds else None,
                             'anchor': tuple(anchor), 'a_text': self._plain_text(mark.get('a_text', '')),
                             'b_text': self._plain_text(mark.get('b_text', ''))})
        self._marks = tuple(prepared)
        self._wanted = bool(show and self._marks)
        self.update()
        if not self._suspended and not self.restore_after_capture():
            self.clear()
            return False
        return True

    def hide_for_capture(self):
        self._suspended = True
        self.hide()

    def restore_after_capture(self):
        self._suspended = False
        screen = QApplication.primaryScreen()
        if self._wanted and self._marks and screen is not None:
            self.setGeometry(screen.geometry())
            self.show()
            return apply_native_pin(self, self._pinned)
        self.hide()
        return True

    def clear(self):
        self._marks = ()
        self._wanted = False
        self.hide()
        self.update()

    def set_pinned(self, pinned):
        self._pinned = bool(pinned)
        visible = self.isVisible()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, self._pinned)
        if visible and not self._suspended and self._wanted:
            return self.restore_after_capture()
        return True

    def _layout_callouts(self):
        """One line at each A anchor; never relocate a result to an unrelated B slot."""
        screen = QApplication.primaryScreen()
        if screen is None or not self._marks:
            return (), 0, None
        scale = screen.devicePixelRatio()
        area = QRectF(2, 2, screen.geometry().width() - 4, screen.geometry().height() - 4)
        metrics = QFontMetricsF(self.font())
        line_height = math.ceil(metrics.lineSpacing())
        placed, occupied = [], []
        anchors = [QRectF(*(value / scale for value in mark['anchor'])) for mark in self._marks]
        for mark, anchor in zip(self._marks, anchors):
            if not area.intersects(anchor):
                continue
            status = {'matched': '一致', 'similar': '近似，需確認', 'uncertain': '需確認',
                      'missing': '未找到', 'ambiguous': '多處符合'}[mark['status']]
            text = mark['a_text'] or '未辨識到文字'
            suffix = f' · {status}'
            # Keep the status visible even when the OCR text must be shortened.
            width = min(420.0, max(120.0, anchor.width()), area.width())
            x = max(area.left(), min(anchor.left(), area.right() - width))
            height = line_height + 6
            candidates = (QRectF(x, anchor.bottom() + 3, width, height),
                          QRectF(x, anchor.top() - height - 3, width, height))
            rectangle = next((rect for rect in candidates if area.contains(rect)
                              and not any(rect.intersects(other) for other in occupied)
                              and not any(rect.intersects(other) for other in anchors)), None)
            if rectangle is None:
                continue
            occupied.append(rectangle)
            preview = metrics.elidedText(text, Qt.TextElideMode.ElideRight,
                                         max(0, width - metrics.horizontalAdvance(suffix) - 6))
            placed.append({**mark, 'rect': tuple(rectangle.getRect()), 'anchor': tuple(anchor.getRect()),
                           'display_lines': (preview + suffix,), 'line_height': line_height})
        # Overflow is reported by the existing restore control, not another floating UI.
        return tuple(placed), len(self._marks) - len(placed), None

    @staticmethod
    def _text_color(status):
        return QColor('#087443' if status == 'matched' else '#b42318' if status == 'missing' else '#885000')

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        metrics = QFontMetricsF(self.font())
        for label in self.callouts:
            rectangle = QRectF(*label['rect'])
            path = QPainterPath()
            path.addText(QPointF(rectangle.left() + 2, rectangle.top() + 3 + metrics.ascent()),
                         self.font(), label['display_lines'][0])
            # White glyph outline keeps text readable directly on the document.
            painter.setPen(QPen(QColor(255, 255, 255, 235), 2.5, Qt.PenStyle.SolidLine,
                                Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            painter.setBrush(self._text_color(label['status']))
            painter.drawPath(path)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawPath(path)
