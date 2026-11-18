"""Non-activating, click-through annotations for one verified invoice snapshot."""
from __future__ import annotations

import math
import sys

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFontMetricsF, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QWidget

from .theme import _BG_SIDE

_CALLOUT_COLORS = {'matched': '#9cf2b9', 'different': '#ffb5af',
                   'review': '#ffe094', 'missing': '#ffb5af'}


def pin_without_activation(widget, enabled=True, *, click_through=False):
    """Pin an owned tool HWND without taking focus from the source software."""
    if sys.platform != 'win32':
        return True
    try:
        import ctypes
        from ctypes import wintypes
        user = ctypes.WinDLL('user32', use_last_error=True)
        user.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
        user.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        user.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        user.SetWindowLongPtrW.restype = ctypes.c_ssize_t
        user.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                      ctypes.c_int, ctypes.c_int, wintypes.UINT]
        user.SetWindowPos.restype = wintypes.BOOL
        handle = int(widget.winId())
        style = user.GetWindowLongPtrW(handle, -20) | 0x08000000 | 0x00000080
        if click_through:
            style |= 0x00000020
        ctypes.set_last_error(0)
        previous = user.SetWindowLongPtrW(handle, -20, style)
        if previous == 0 and ctypes.get_last_error():
            return False
        return bool(user.SetWindowPos(handle, -1 if enabled else -2, 0, 0, 0, 0,
                                      0x0001 | 0x0002 | 0x0010 | 0x0020))
    except (OSError, AttributeError, ValueError):
        return False



def set_native_noactivate(widget, enabled):
    """Change only this HWND's activation bit; do not focus or move a window."""
    if sys.platform != 'win32':
        return True
    try:
        import ctypes
        from ctypes import wintypes
        user = ctypes.WinDLL('user32', use_last_error=True)
        user.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
        user.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        user.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        user.SetWindowLongPtrW.restype = ctypes.c_ssize_t
        handle = int(widget.winId())
        style = user.GetWindowLongPtrW(handle, -20)
        updated = style | 0x08000000 if enabled else style & ~0x08000000
        if updated == style:
            return True
        ctypes.set_last_error(0)
        previous = user.SetWindowLongPtrW(handle, -20, updated)
        return bool(previous or not ctypes.get_last_error())
    except (OSError, AttributeError, ValueError):
        return False



def is_native_mouse_activation(message):
    if sys.platform != 'win32':
        return False
    try:
        import ctypes
        from ctypes import wintypes
        class Message(ctypes.Structure):
            _fields_ = [('hwnd', wintypes.HWND), ('message', wintypes.UINT),
                        ('wParam', wintypes.WPARAM), ('lParam', wintypes.LPARAM),
                        ('time', wintypes.DWORD), ('pt', wintypes.POINT)]
        return Message.from_address(int(message)).message == 0x0021
    except (OSError, ValueError, AttributeError):
        return False


def activate_native_editor(widget):
    """Explicit non-check user input may activate the otherwise passive HWND."""
    if sys.platform != 'win32':
        return True
    try:
        import ctypes
        from ctypes import wintypes
        user = ctypes.WinDLL('user32', use_last_error=True)
        user.SetForegroundWindow.argtypes = [wintypes.HWND]
        user.SetForegroundWindow.restype = wintypes.BOOL
        return bool(user.SetForegroundWindow(int(widget.winId())))
    except (OSError, AttributeError, ValueError):
        return False

def prevent_button_activation(message, buttons):
    """MA_NOACTIVATE applies only over explicit check buttons, not editors."""
    if sys.platform != 'win32':
        return False
    try:
        import ctypes
        from ctypes import wintypes
        class Message(ctypes.Structure):
            _fields_ = [('hwnd', wintypes.HWND), ('message', wintypes.UINT),
                        ('wParam', wintypes.WPARAM), ('lParam', wintypes.LPARAM),
                        ('time', wintypes.DWORD), ('pt', wintypes.POINT)]
        incoming = Message.from_address(int(message))
        if incoming.message != 0x0021:
            return False
        point = wintypes.POINT()
        user = ctypes.WinDLL('user32', use_last_error=True)
        user.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        user.GetCursorPos.restype = wintypes.BOOL
        if not user.GetCursorPos(ctypes.byref(point)):
            return False
        user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user.GetWindowRect.restype = wintypes.BOOL
        for button in buttons:
            if not button.isVisible():
                continue
            # Both Win32 calls use physical screen coordinates. Comparing a
            # physical cursor with Qt logical mapToGlobal breaks at high DPI.
            rectangle = wintypes.RECT()
            if (user.GetWindowRect(int(button.winId()), ctypes.byref(rectangle))
                    and rectangle.left <= point.x < rectangle.right
                    and rectangle.top <= point.y < rectangle.bottom):
                return True
        return False
    except (OSError, ValueError, AttributeError):
        return False


class InvoiceCheckOverlay(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowTransparentForInput
                            | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._marks = ()
        self._wanted = False
        self._suspended = False
        self._pinned = True
        self._transparency = 100
        self.hide()

    @property
    def marks(self):
        return self._marks

    @property
    def background_transparency(self):
        return self._transparency

    @property
    def overflow_count(self):
        return len(self._marks) - len(self.callouts)

    def set_background_transparency(self, percent):
        if type(percent) not in (int, float) or not math.isfinite(percent):
            return False
        self._transparency = max(0, min(100, percent))
        self.update()
        return True

    def set_pinned(self, enabled):
        self._pinned = bool(enabled)
        visible = self.isVisible()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, self._pinned)
        if visible and not self._suspended:
            return self.restore()
        return True

    def set_snapshot(self, snapshot):
        """Only actual invoice OCR boxes can locate an annotation."""
        regions = snapshot.get('regions') if isinstance(snapshot, dict) else None
        region = regions.get('invoice') if isinstance(regions, dict) else None
        if (not isinstance(region, (tuple, list)) or len(region) != 5 or region[4] != 1
                or any(type(value) is not int for value in region)
                or min(region[:2]) < 0 or min(region[2:4]) <= 0):
            self.clear()
            return False
        checks = snapshot.get('checks')
        if not isinstance(checks, list) or len(checks) > 5:
            self.clear()
            return False
        marks = []
        for item in checks:
            if not isinstance(item, dict):
                self.clear()
                return False
            invoice = item.get('invoice') or {}
            row = item.get('row') or {}
            if not isinstance(invoice, dict) or not isinstance(row, dict):
                self.clear()
                return False
            box = invoice.get('box')
            if box is None:
                continue
            if (not isinstance(box, (list, tuple)) or len(box) != 4
                    or any(type(value) not in (int, float) or not math.isfinite(value) for value in box)
                    or min(box[:2]) < 0 or min(box[2:]) <= 0
                    or box[0] + box[2] > region[2] or box[1] + box[3] > region[3]):
                self.clear()
                return False
            status = item.get('status')
            if status not in ('matched', 'different', 'review', 'missing'):
                self.clear()
                return False
            label = str(item.get('label', ''))[:24]
            value = str(row.get('raw') or '未取得')[:80]
            value = ' '.join(value.split())
            marks.append({'anchor': (region[0] + box[0], region[1] + box[1], box[2], box[3]),
                          'bounds': tuple(region[:4]), 'status': status,
                          'text': f'{label} {value} · ' + {'matched': '相符', 'different': '不同',
                                                          'review': '待確認', 'missing': '缺欄'}[status]})
        self._marks = tuple(marks)
        self._wanted = bool(marks)
        self.update()
        return self.restore() if not self._suspended else True

    @property
    def callouts(self):
        screen = QApplication.primaryScreen()
        if screen is None:
            return ()
        scale = screen.devicePixelRatio()
        area = QRectF(2, 2, screen.geometry().width() - 4, screen.geometry().height() - 4)
        anchors = [QRectF(*(value / scale for value in mark['anchor'])) for mark in self._marks]
        metrics = QFontMetricsF(self.font())
        occupied, result = [], []
        for mark, anchor in zip(self._marks, anchors):
            bounds = area.intersected(QRectF(*(value / scale for value in mark['bounds'])))
            if not bounds.contains(anchor):
                continue
            width = min(300, metrics.horizontalAdvance(mark['text']) + 8, bounds.width())
            height = math.ceil(metrics.lineSpacing()) + 4
            candidates = [QRectF(max(bounds.left(), min(anchor.left(), bounds.right() - width)), anchor.bottom() + 2, width, height),
                          QRectF(anchor.right() + 4, anchor.top(), width, height),
                          QRectF(anchor.left() - width - 4, anchor.top(), width, height)]
            rectangle = next((rectangle for rectangle in candidates if bounds.contains(rectangle)
                              and not any(rectangle.intersects(other) for other in anchors + occupied)), None)
            if rectangle is None:
                continue
            occupied.append(rectangle)
            text = metrics.elidedText(mark['text'], Qt.TextElideMode.ElideRight, max(0, width - 6))
            result.append({**mark, 'rect': tuple(rectangle.getRect()), 'text': text})
        return tuple(result)

    def hide_for_capture(self):
        self._suspended = True
        self.hide()

    def restore(self):
        if self._suspended:
            return True
        screen = QApplication.primaryScreen()
        if self._wanted and screen is not None:
            self.setGeometry(screen.geometry())
            if not pin_without_activation(self, self._pinned, click_through=True):
                self.hide()
                return False
            self.show()
            return pin_without_activation(self, self._pinned, click_through=True)
        self.hide()
        return True

    def finish_capture(self, restore=False):
        self._suspended = False
        if restore:
            return self.restore()
        self.hide()
        return True

    def clear(self):
        self._marks = ()
        self._wanted = False
        self.hide()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        metrics = QFontMetricsF(self.font())
        background = QColor(_BG_SIDE)
        background.setAlpha(round(255 * (100 - self._transparency) / 100))
        for item in self.callouts:
            rectangle = QRectF(*item['rect'])
            if background.alpha():
                painter.fillRect(rectangle, background)
            path = QPainterPath()
            path.addText(QPointF(rectangle.left() + 3, rectangle.top() + 2 + metrics.ascent()), self.font(), item['text'])
            painter.setPen(QPen(QColor(_BG_SIDE), 2))
            painter.setBrush(QColor(_CALLOUT_COLORS[item['status']]))
            painter.drawPath(path)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawPath(path)
