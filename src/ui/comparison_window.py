"""A quiet, session-only status window for repeated two-region comparison."""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QRectF, QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .theme import (
    _ACCENT,
    _ACCENT_T,
    _BG,
    _BG_HOVER,
    _BG_RAISE,
    _BORDER,
    _ERROR,
    _SUCCESS,
    _TEXT_PRI,
    _TEXT_SEC,
    _WARNING,
    apply_combo_box_theme,
)


def _label(text=''):
    result = QLabel(text)
    result.setTextFormat(Qt.TextFormat.PlainText)
    return result


class ComparisonWindow(QWidget):
    """User actions can show the window; incoming OCR never raises or focuses it."""

    select_requested = Signal(str)
    screen_invalidated = Signal()

    def __init__(self, controller, parent=None):
        super().__init__(parent, Qt.WindowType.Tool | Qt.WindowType.WindowMinimizeButtonHint)
        self.controller = controller
        self._compact = False
        self._selection_active = False
        self._closing = False
        self._screen = None
        self._last_result = None
        self._cycle_result_visible = False
        self.setWindowTitle('A／B 核對')
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.resize(640, 480)
        self.setMinimumSize(480, 380)
        self.setStyleSheet(f'''
            QWidget {{ background: {_BG}; color: {_TEXT_PRI}; font-size: 13px; }}
            QLabel#muted {{ color: {_TEXT_SEC}; }}
            QPushButton {{ background: {_BG_RAISE}; color: {_TEXT_PRI};
                border: 1px solid {_BORDER}; border-radius: 6px; padding: 7px 12px; }}
            QPushButton:hover {{ background: {_BG_HOVER}; }}
            QPushButton:focus {{ border-color: {_ACCENT}; }}
            QPushButton:disabled {{ color: {_TEXT_SEC}; }}
            QPushButton#startComparison {{ background: {_ACCENT}; color: {_ACCENT_T}; }}
            QPushButton#startComparison:disabled {{ background: {_BG_RAISE}; color: {_TEXT_SEC}; }}
            QPlainTextEdit, QSpinBox {{ background: {_BG_RAISE}; color: {_TEXT_PRI};
                border: 1px solid {_BORDER}; border-radius: 6px; padding: 6px; }}
            QFrame#statusCard {{ border: 1px solid {_BORDER}; border-radius: 8px; }}
        ''')

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(12)
        self._status_card = QFrame()
        self._status_card.setObjectName('statusCard')
        status_layout = QHBoxLayout(self._status_card)
        status_layout.setContentsMargins(14, 10, 14, 10)
        self._status = _label('先框選 A 與 B')
        self._status.setObjectName('comparisonStatus')
        self._status.setAccessibleName('核對結果')
        status_layout.addWidget(self._status, 1)
        self._activity = _label('尚未開始')
        self._activity.setObjectName('muted')
        status_layout.addWidget(self._activity)
        outer.addWidget(self._status_card)

        self._details = QWidget()
        details = QVBoxLayout(self._details)
        details.setContentsMargins(0, 0, 0, 0)
        details.setSpacing(12)
        hint = _label('框選一次，接著使用固定位置連續核對。')
        hint.setObjectName('muted')
        details.addWidget(hint)
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        self._region_labels = {}
        self._select_buttons = {}
        self._texts = {}
        for column, name in enumerate(('A', 'B')):
            button = QPushButton(f'框選 {name}')
            button.setObjectName(f'selectComparison{name}')
            button.clicked.connect(lambda checked=False, key=name: self._select(key))
            button.setToolTip('請框選主螢幕上的文字；位置會保留到本次程式結束。')
            self._select_buttons[name] = button
            grid.addWidget(button, 0, column)
            region = _label('尚未框選')
            region.setObjectName('muted')
            region.setWordWrap(True)
            self._region_labels[name] = region
            grid.addWidget(region, 1, column)
            text = QPlainTextEdit()
            text.setObjectName(f'comparisonText{name}')
            text.setAccessibleName(f'{name} 區域辨識文字')
            text.setReadOnly(True)
            text.setPlaceholderText(f'{name} 區域辨識文字')
            text.setMinimumHeight(110)
            self._texts[name] = text
            grid.addWidget(text, 2, column)
        details.addLayout(grid, 1)

        options = QHBoxLayout()
        self._mode = QComboBox()
        self._mode.setObjectName('comparisonMode')
        self._mode.addItem('只比文字與數字', 'normalized')
        self._mode.addItem('逐字完全相同', 'exact')
        self._mode.setToolTip('只比文字與數字會忽略空白、標點及符號，例如 -1.23 與 123 視為相同。金額可改用逐字核對。')
        apply_combo_box_theme(self._mode)
        self._mode.currentIndexChanged.connect(self._mode_changed)
        options.addWidget(self._mode, 1)
        options.addWidget(_label('每輪後等待'))
        self._interval = QSpinBox()
        self._interval.setObjectName('comparisonInterval')
        self._interval.setRange(1, 60)
        self._interval.setValue(2)
        self._interval.setSuffix(' 秒')
        self._interval.valueChanged.connect(controller.set_interval)
        options.addWidget(self._interval)
        details.addLayout(options)
        outer.addWidget(self._details, 1)

        self._note = _label('結果不會自動複製，也不會加入歷史。')
        self._note.setObjectName('muted')
        self._note.setWordWrap(True)
        outer.addWidget(self._note)
        buttons = QHBoxLayout()
        self._start_button = QPushButton('開始核對')
        self._start_button.setObjectName('startComparison')
        self._start_button.clicked.connect(self._start)
        self._pause_button = QPushButton('暫停')
        self._pause_button.clicked.connect(controller.pause)
        self._compact_button = QPushButton('精簡狀態')
        self._compact_button.setCheckable(True)
        self._compact_button.toggled.connect(self.set_compact)
        buttons.addWidget(self._start_button)
        buttons.addWidget(self._pause_button)
        buttons.addStretch()
        buttons.addWidget(self._compact_button)
        outer.addLayout(buttons)

        controller.updated.connect(self._updated)
        controller.state_changed.connect(self._state_changed)
        controller.failed.connect(self._failed)
        controller.before_capture = self.can_capture
        app = QApplication.instance()
        app.primaryScreenChanged.connect(self._bind_screen)
        self._bind_screen(app.primaryScreen(), initial=True)
        self._refresh_regions()
        self._sync_buttons()
        self._set_status('先框選 A 與 B', _TEXT_SEC)

    def _bind_screen(self, screen, initial=False):
        if self._screen is not None:
            for signal in (self._screen.geometryChanged, self._screen.logicalDotsPerInchChanged):
                try:
                    signal.disconnect(self._screen_changed)
                except (RuntimeError, TypeError):
                    pass
        self._screen = screen
        if screen is not None:
            screen.geometryChanged.connect(self._screen_changed)
            screen.logicalDotsPerInchChanged.connect(self._screen_changed)
        if not initial:
            self._screen_changed()

    def _screen_changed(self, *args):
        if self._closing:
            return
        self.screen_invalidated.emit()
        self.controller.invalidate_regions('螢幕配置已變更，請重新框選 A 與 B。')
        self._refresh_regions()
        self._sync_buttons()

    def _select(self, name):
        self.controller.pause()
        self.select_requested.emit(name)

    def set_selection_active(self, active):
        self._selection_active = bool(active)
        if active:
            self.controller.pause()
            self.hide()
        elif not self._closing:
            self.show()

    def set_region(self, name, region):
        accepted = self.controller.set_region(name, region)
        self._refresh_regions()
        self._sync_buttons()
        if accepted:
            self._note.setText('位置已保留，按「開始核對」使用固定位置。')
        return accepted

    def _refresh_regions(self):
        for name, label in self._region_labels.items():
            region = self.controller.regions.get(name)
            if region is None:
                label.setText('尚未框選')
            else:
                x, y, width, height, _monitor = region
                label.setText(f'位置 {x}, {y} · {width} × {height}')

    def _sync_buttons(self):
        running = self.controller.running
        configured = all(self.controller.regions.get(name) is not None for name in ('A', 'B'))
        self._start_button.setEnabled(configured and not running and not self._closing)
        self._pause_button.setEnabled(running and not self._closing)

    def _mode_changed(self):
        self.controller.set_mode(self._mode.currentData())
        self._note.setText('核對方式已變更，按「開始核對」重新核對。')

    def _start(self):
        if self._closing or self._selection_active:
            return
        self.avoid_regions()
        self.controller.start()
        self._sync_buttons()

    def _set_status(self, text, color):
        self._status.setText(text)
        self._status.setStyleSheet(f'color: {color}; font-size: 21px; font-weight: 600; border: none;')
        self._status_card.setStyleSheet(f'QFrame#statusCard {{ border: 1px solid {color}; border-radius: 8px; }}')

    def _state_changed(self, state):
        self._refresh_regions()
        self._sync_buttons()
        if state == 'running':
            self._cycle_result_visible = False
            self._set_status('核對中', _WARNING)
            self._activity.setText('辨識 A／B…')
        elif state == 'waiting':
            if not self._cycle_result_visible:
                self._activity.setText('等待辨識引擎…')
        elif state in ('paused', 'idle'):
            self._set_status('已暫停' if state == 'paused' else '先框選 A 與 B', _TEXT_SEC)
            self._activity.setText('已停止更新')

    def _updated(self, result):
        if self._closing or self._selection_active:
            return
        self._last_result = dict(result)
        self._cycle_result_visible = True
        self._texts['A'].setPlainText(result.get('a', ''))
        self._texts['B'].setPlainText(result.get('b', ''))
        verdict = result.get('verdict')
        title, color, note = {
            'match': ('相同', _SUCCESS, '依目前核對方式，A 與 B 內容一致。'),
            'mismatch': ('不同', _ERROR, 'A 與 B 內容不一致，請查看兩邊文字。'),
        }.get(verdict, ('待確認', _WARNING, '有空白或不確定的辨識結果，請查看原畫面。'))
        self._set_status(title, color)
        stamp = result.get('timestamp', '')
        try:
            stamp = datetime.fromisoformat(stamp).strftime('%H:%M:%S')
        except (TypeError, ValueError):
            stamp = ''
        self._activity.setText(f'第 {result.get("cycle", 0)} 次 · {stamp}')
        self._note.setText(note)
        self._sync_buttons()

    def _failed(self, message):
        self._set_status('待確認', _WARNING)
        self._activity.setText('請檢查後再開始')
        self._note.setText(message)
        self._refresh_regions()
        self._sync_buttons()

    def _physical_window_rect(self):
        screen = QApplication.primaryScreen()
        if screen is None:
            return None
        geometry = screen.geometry()
        frame = self.frameGeometry()
        scale = screen.devicePixelRatio()
        return QRectF((frame.x() - geometry.x()) * scale,
                      (frame.y() - geometry.y()) * scale,
                      frame.width() * scale, frame.height() * scale)

    def can_capture(self):
        if self._selection_active or self._closing:
            return False
        if not self.isVisible() or self.isMinimized():
            return True
        frame = self._physical_window_rect()
        if frame is None:
            return False
        return not any(frame.intersects(QRectF(*region[:4]))
                       for region in self.controller.regions.values() if region is not None)

    def avoid_regions(self):
        """Try free edges before starting; never hide a running status silently."""
        if self.can_capture() or not self.isVisible():
            return self.can_capture()
        screen = QApplication.primaryScreen()
        if screen is None:
            return False
        available = screen.availableGeometry()
        frame = self.frameGeometry()
        width, height = frame.width(), frame.height()
        x_candidates = {available.left(), available.right() - width + 1}
        y_candidates = {available.top(), available.bottom() - height + 1}
        scale = screen.devicePixelRatio()
        origin = screen.geometry().topLeft()
        for region in self.controller.regions.values():
            if region is None:
                continue
            x, y, w, h, _ = region
            x_candidates.update((round(origin.x() + x / scale) - width - 8,
                                 round(origin.x() + (x + w) / scale) + 8))
            y_candidates.update((round(origin.y() + y / scale) - height - 8,
                                 round(origin.y() + (y + h) / scale) + 8))
        original = self.pos()
        for y in sorted(y_candidates):
            for x in sorted(x_candidates):
                if not available.contains(frame.translated(x - frame.x(), y - frame.y())):
                    continue
                self.move(x, y)
                if self.can_capture():
                    return True
        self.move(original)
        return False

    def set_compact(self, compact):
        self._compact = bool(compact)
        self._details.setVisible(not compact)
        self._compact_button.setText('展開設定' if compact else '精簡狀態')
        with QSignalBlocker(self._compact_button):
            self._compact_button.setChecked(compact)
        if compact:
            self.setMinimumSize(350, 160)
        else:
            self.setMinimumSize(480, 380)
        visible = self.isVisible()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, compact)
        if compact:
            self.resize(380, 178)
        else:
            self.resize(640, 480)
        if visible:
            self.show()
            self.avoid_regions()

    def closeEvent(self, event):
        self.controller.pause()
        self.hide()
        event.accept()

    def shutdown(self):
        self._closing = True
        self.controller.shutdown()
        self.hide()

    @property
    def is_stopped(self):
        return self.controller.is_stopped
