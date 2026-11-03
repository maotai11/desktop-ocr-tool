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
    closed = Signal()

    def __init__(self, controller, parent=None):
        super().__init__(parent, Qt.WindowType.Tool | Qt.WindowType.WindowTitleHint
                         | Qt.WindowType.WindowSystemMenuHint | Qt.WindowType.WindowCloseButtonHint
                         | Qt.WindowType.WindowMinimizeButtonHint | Qt.WindowType.WindowStaysOnTopHint)
        self.controller = controller
        self._compact = False
        self._selection_active = False
        self._closing = False
        self._closed_by_user = False
        self._screen = None
        self._last_result = None
        self._cycle_result_visible = False
        self.setWindowTitle('A／B 核對')
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.resize(660, 470)
        self.setMinimumSize(540, 400)
        self.setStyleSheet(f'''
            QWidget {{ background: {_BG}; color: {_TEXT_PRI}; font-size: 13px; }}
            QLabel#muted {{ color: {_TEXT_SEC}; }}
            QPushButton {{ background: {_BG_RAISE}; color: {_TEXT_PRI};
                border: 1px solid {_BORDER}; border-radius: 6px; padding: 7px 12px; }}
            QPushButton:hover {{ background: {_BG_HOVER}; }}
            QPushButton:focus {{ border-color: {_ACCENT}; }}
            QPushButton:checked {{ border-color: {_ACCENT}; color: {_ACCENT}; }}
            QPushButton:disabled {{ color: {_TEXT_SEC}; }}
            QPushButton#startComparison {{ background: {_ACCENT}; color: {_ACCENT_T}; }}
            QPushButton#startComparison:disabled {{ background: {_BG_RAISE}; color: {_TEXT_SEC}; }}
            QPlainTextEdit, QSpinBox {{ background: {_BG_RAISE}; color: {_TEXT_PRI};
                border: 1px solid {_BORDER}; border-radius: 6px; padding: 6px; }}
            QFrame#statusCard, QFrame#comparisonPane {{ border: 1px solid {_BORDER}; border-radius: 10px; }}
        ''')

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(12)
        heading = QHBoxLayout()
        self._title = _label('文字核對')
        self._title.setStyleSheet('font-size: 16px; font-weight: 600;')
        heading.addWidget(self._title, 1)
        self._pin_button = QPushButton('保持置頂')
        self._pin_button.setObjectName('pinComparison')
        self._pin_button.setCheckable(True)
        self._pin_button.setChecked(True)
        self._pin_button.setToolTip('切換到其他軟體時仍顯示；可取消置頂，也可自由移動視窗。')
        self._pin_button.toggled.connect(self.set_pinned)
        heading.addWidget(self._pin_button)
        self._close_button = QPushButton('關閉')
        self._close_button.setObjectName('closeComparison')
        self._close_button.setToolTip('停止核對並關閉視窗')
        self._close_button.clicked.connect(self.close)
        heading.addWidget(self._close_button)
        outer.addLayout(heading)
        self._status_card = QFrame()
        self._status_card.setObjectName('statusCard')
        status_layout = QVBoxLayout(self._status_card)
        status_layout.setContentsMargins(16, 12, 16, 12)
        status_layout.setSpacing(4)
        self._status = _label('準備核對')
        self._status.setObjectName('comparisonStatus')
        self._status.setAccessibleName('核對結果')
        status_layout.addWidget(self._status)
        self._activity = _label('選取 A、B 兩處文字即可開始')
        self._activity.setObjectName('muted')
        status_layout.addWidget(self._activity)
        outer.addWidget(self._status_card)

        self._details = QWidget()
        details = QVBoxLayout(self._details)
        details.setContentsMargins(0, 0, 0, 0)
        details.setSpacing(12)
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        self._region_labels = {}
        self._select_buttons = {}
        self._texts = {}
        for column, name in enumerate(('A', 'B')):
            pane = QFrame()
            pane.setObjectName('comparisonPane')
            pane_layout = QVBoxLayout(pane)
            pane_layout.setContentsMargins(12, 10, 12, 12)
            pane_heading = QHBoxLayout()
            title = _label(f'{name} 區')
            title.setStyleSheet('font-size: 15px; font-weight: 600;')
            pane_heading.addWidget(title, 1)
            button = QPushButton('選取範圍')
            button.setObjectName(f'selectComparison{name}')
            button.clicked.connect(lambda checked=False, key=name: self._select(key))
            button.setToolTip('請框選主螢幕上的文字；位置會保留到本次程式結束。')
            self._select_buttons[name] = button
            pane_heading.addWidget(button)
            pane_layout.addLayout(pane_heading)
            region = _label('尚未選取')
            region.setObjectName('muted')
            region.setWordWrap(True)
            self._region_labels[name] = region
            pane_layout.addWidget(region)
            text = QPlainTextEdit()
            text.setObjectName(f'comparisonText{name}')
            text.setAccessibleName(f'{name} 區域辨識文字')
            text.setReadOnly(True)
            text.setPlaceholderText('辨識結果會顯示在這裡')
            text.setMinimumHeight(90)
            self._texts[name] = text
            pane_layout.addWidget(text, 1)
            grid.addWidget(pane, 0, column)
        details.addLayout(grid, 1)

        options = QHBoxLayout()
        self._mode = QComboBox()
        self._mode.setObjectName('comparisonMode')
        self._mode.addItem('忽略空白與標點', 'normalized')
        self._mode.addItem('逐字比對', 'exact')
        self._mode.setToolTip('只比文字與數字會忽略空白、標點及符號，例如 -1.23 與 123 視為相同。金額可改用逐字核對。')
        apply_combo_box_theme(self._mode)
        self._mode.currentIndexChanged.connect(self._mode_changed)
        options.addWidget(self._mode, 1)
        options.addWidget(_label('檢查間隔'))
        self._interval = QSpinBox()
        self._interval.setObjectName('comparisonInterval')
        self._interval.setRange(1, 60)
        self._interval.setValue(2)
        self._interval.setSuffix(' 秒')
        self._interval.valueChanged.connect(controller.set_interval)
        options.addWidget(self._interval)
        details.addLayout(options)
        outer.addWidget(self._details, 1)

        self._note = _label('畫面有變化才重新辨識。')
        self._note.setObjectName('muted')
        self._note.setWordWrap(True)
        outer.addWidget(self._note)
        buttons = QHBoxLayout()
        self._start_button = QPushButton('開始核對')
        self._start_button.setObjectName('startComparison')
        self._start_button.clicked.connect(self._start)
        self._pause_button = QPushButton('暫停')
        self._pause_button.clicked.connect(controller.pause)
        self._compact_button = QPushButton('縮小視窗')
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
        self._set_status('準備核對', _TEXT_SEC)

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
        if self._closing or self._closed_by_user:
            return
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
            self._note.setText('範圍已選好，按「開始核對」。')
        return accepted

    def _refresh_regions(self):
        for name, label in self._region_labels.items():
            region = self.controller.regions.get(name)
            if region is None:
                label.setText('尚未選取')
                label.setToolTip('')
                self._select_buttons[name].setText('選取範圍')
            else:
                x, y, width, height, _monitor = region
                label.setText('範圍已選取')
                label.setToolTip(f'位置 {x}, {y} · {width} × {height}')
                self._select_buttons[name].setText('重新選取')

    def _sync_buttons(self):
        running = self.controller.running
        configured = all(self.controller.regions.get(name) is not None for name in ('A', 'B'))
        self._start_button.setEnabled(configured and not running and not self._closing)
        self._pause_button.setEnabled(running and not self._closing)

    def _mode_changed(self):
        self.controller.set_mode(self._mode.currentData())
        self._note.setText('規則已更新，再按「開始核對」。')

    def _start(self):
        if self._closing or self._closed_by_user or self._selection_active:
            return
        self.avoid_regions()
        self.controller.start()
        self._sync_buttons()

    def _set_status(self, text, color):
        self._status.setText(text)
        self._status.setStyleSheet(f'color: {color}; font-size: 21px; font-weight: 600; border: none;')
        self._status_card.setStyleSheet(f'QFrame#statusCard {{ border: 1px solid {_BORDER}; '
                                       f'border-left: 4px solid {color}; border-radius: 10px; }}')

    def _state_changed(self, state):
        self._refresh_regions()
        self._sync_buttons()
        if state == 'running':
            self._cycle_result_visible = False
            self._set_status('核對中', _WARNING)
            self._activity.setText('正在讀取兩區文字…')
            self._note.setText('可切換到其他軟體，結果會在此更新。')
        elif state in ('checking', 'watching', 'unchanged'):
            if self._cycle_result_visible:
                self._note.setText('畫面未變，等待變化。' if state == 'unchanged'
                                   else '持續留意畫面變化。')
        elif state == 'waiting':
            if not self._cycle_result_visible:
                self._activity.setText('等待上一項辨識完成…')
        elif state in ('paused', 'idle'):
            self._set_status('已暫停' if state == 'paused' else '準備核對', _TEXT_SEC)
            self._activity.setText('按「開始核對」即可繼續')
            self._note.setText('暫停期間不擷取畫面。')

    def _updated(self, result):
        if self._closing or self._closed_by_user or self._selection_active:
            return
        self._last_result = dict(result)
        self._cycle_result_visible = True
        self._texts['A'].setPlainText(result.get('a', ''))
        self._texts['B'].setPlainText(result.get('b', ''))
        verdict = result.get('verdict')
        title, color, note = {
            'match': ('內容一致', _SUCCESS, '兩區文字相同。'),
            'mismatch': ('發現差異', _ERROR, '請查看 A、B 兩區文字。'),
        }.get(verdict, ('需要確認', _WARNING, '文字不完整，請確認原畫面。'))
        self._set_status(title, color)
        stamp = result.get('timestamp', '')
        try:
            stamp = datetime.fromisoformat(stamp).strftime('%H:%M:%S')
        except (TypeError, ValueError):
            stamp = ''
        self._activity.setText(f'更新於 {stamp} · 已核對 {result.get("cycle", 0)} 次')
        self._note.setText(note)
        self._sync_buttons()

    def _failed(self, message):
        self._set_status('需要確認', _WARNING)
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
        self._compact_button.setText('展開視窗' if compact else '縮小視窗')
        with QSignalBlocker(self._compact_button):
            self._compact_button.setChecked(compact)
        if compact:
            self.setMinimumSize(350, 225)
        else:
            self.setMinimumSize(540, 400)
        visible = self.isVisible()
        if compact:
            self.resize(380, 245)
        else:
            self.resize(660, 470)
        if visible:
            self.show()
            self.avoid_regions()

    def set_pinned(self, pinned):
        """A user-controlled z-order, independent of size, position or scanning."""
        visible, position = self.isVisible(), self.pos()
        with QSignalBlocker(self._pin_button):
            self._pin_button.setChecked(bool(pinned))
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, bool(pinned))
        self.move(position)
        if visible and not self._closing and not self._closed_by_user:
            self.show()

    def open(self):
        if self._closing:
            return
        self._closed_by_user = False
        self.show()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event):
        self._closed_by_user = True
        self.controller.pause()
        self.closed.emit()
        self._selection_active = False
        self.hide()
        event.accept()

    def shutdown(self):
        self._closing = True
        self.controller.shutdown()
        self.hide()

    @property
    def is_stopped(self):
        return self.controller.is_stopped
