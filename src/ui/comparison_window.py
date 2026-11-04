"""A launcher-sized, quiet panel for session-only comparison groups."""
from __future__ import annotations

from datetime import datetime
from html import escape

from PySide6.QtCore import QRectF, QSize, QSignalBlocker, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFrame, QHBoxLayout, QInputDialog, QLabel,
    QListWidget, QListWidgetItem, QMenu, QPlainTextEdit, QPushButton,
    QScrollArea, QSizePolicy, QSpinBox, QVBoxLayout, QWidget,
)

from .theme import (
    _ACCENT, _ACCENT_T, _BG, _BG_HOVER, _BG_RAISE, _BORDER, _BORDER_L,
    _ERROR, _SUCCESS, _TEXT_PRI, _TEXT_SEC, _WARNING, apply_combo_box_theme,
)


def _label(text=''):
    result = QLabel(text)
    result.setTextFormat(Qt.TextFormat.PlainText)
    return result


def _plain_tooltip(text):
    # Qt auto-detects rich text in tooltips. OCR and group names are plain data.
    return '<qt>' + escape(text).replace('\n', '<br>') + '</qt>' if text else ''


class _PreviewLabel(QLabel):
    """One result line, with full plain text in the tooltip and detail view."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._full_text = ''
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(22)

    def set_preview(self, text):
        self._full_text = text
        self.setToolTip(_plain_tooltip(text[:800] + ('…\n按「查看全文」閱讀完整內容' if len(text) > 800 else '')))
        self._elide()

    def _elide(self):
        line = ' '.join(self._full_text[:512].splitlines()) or '等待辨識…'
        if len(self._full_text) > 512:
            line += '…'
        self.setText(self.fontMetrics().elidedText(line, Qt.TextElideMode.ElideRight, max(0, self.width())))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()


class ComparisonWindow(QWidget):
    """Incoming results never show, resize, raise or focus the panel."""
    select_requested = Signal(str)
    screen_invalidated = Signal()
    closed = Signal()

    def __init__(self, controller, parent=None):
        super().__init__(parent, Qt.WindowType.Tool | Qt.WindowType.WindowTitleHint
                         | Qt.WindowType.WindowSystemMenuHint | Qt.WindowType.WindowCloseButtonHint
                         | Qt.WindowType.WindowMinimizeButtonHint | Qt.WindowType.WindowStaysOnTopHint)
        self.controller = controller
        self._compact = True
        self._mini_mode = False
        self._mini_ready = False
        self._changing_mini_layout = False
        self._normal_size = QSize(348, 560)
        self._selection_active = self._closing = self._closed_by_user = False
        self._screen = self._last_result = None
        self._cycle_result_visible = False
        self.setWindowTitle('A／B 核對')
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        # Match the desktop launcher, including its minimum supported footprint.
        self.setMinimumSize(320, 280)
        self.resize(348, 560)
        self.setStyleSheet(f'''
            QWidget {{ background: {_BG}; color: {_TEXT_PRI}; font-size: 12px; }}
            QLabel#muted {{ color: {_TEXT_SEC}; }}
            QLabel {{ border: none; background: transparent; }}
            QPushButton {{ background: {_BG_RAISE}; color: {_TEXT_PRI}; border: 1px solid transparent;
                border-radius: 7px; padding: 5px 9px; }}
            QPushButton:hover {{ background: {_BG_HOVER}; }}
            QPushButton:focus {{ border-color: {_ACCENT}; }}
            QPushButton:checked {{ color: {_ACCENT}; border-color: {_BORDER_L}; }}
            QPushButton:disabled {{ color: {_TEXT_SEC}; }}
            QPushButton#startComparison {{ background: {_ACCENT}; color: {_ACCENT_T}; font-weight: 600; }}
            QPushButton#startComparison:disabled {{ background: {_BG_RAISE}; color: {_TEXT_SEC}; }}
            QFrame#statusCard, QFrame#comparisonPane {{ background: {_BG_RAISE}; border: 1px solid {_BORDER}; border-radius: 9px; }}
            QPlainTextEdit, QSpinBox {{ background: {_BG_RAISE}; color: {_TEXT_PRI};
                border: 1px solid {_BORDER}; border-radius: 6px; padding: 4px; }}
            QScrollArea {{ border: none; }}
            QScrollBar:vertical {{ background: transparent; width: 4px; margin: 0; }}
            QScrollBar::handle:vertical {{ background: {_BORDER_L}; border-radius: 2px; min-height: 22px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
            QListWidget {{ background: transparent; border: none; outline: none; }}
            QListWidget::item {{ border-radius: 4px; padding: 3px 7px; }}
            QListWidget::item:selected {{ background: {_BG_RAISE}; }}
            QMenu {{ background: {_BG_RAISE}; color: {_TEXT_PRI}; border: 1px solid {_BORDER_L}; }}
            QMenu::item {{ padding: 6px 16px; }}
            QMenu::item:selected {{ background: {_BG_HOVER}; }}
        ''')
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(9)
        heading = QHBoxLayout()
        badge = _label('A/B')
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setFixedSize(32, 30)
        badge.setStyleSheet(f'color: {_ACCENT}; background: {_BG_RAISE}; border-radius: 7px; font-weight: 600;')
        heading.addWidget(badge)
        title = _label('文字核對')
        title.setStyleSheet('font-size: 15px; font-weight: 600;')
        heading.addWidget(title, 1)
        self._mini_button = QPushButton('迷你')
        self._mini_button.setObjectName('miniComparison')
        self._mini_button.setCheckable(True)
        self._mini_button.setToolTip('縮成仍顯示 A／B 結果的迷你視窗')
        self._mini_button.toggled.connect(self.set_mini)
        heading.addWidget(self._mini_button)
        self._pin_button = QPushButton('置頂')
        self._pin_button.setObjectName('pinComparison')
        self._pin_button.setCheckable(True)
        self._pin_button.setChecked(True)
        self._pin_button.setToolTip('保持置頂；可隨時取消並自由移動視窗')
        self._pin_button.toggled.connect(self.set_pinned)
        heading.addWidget(self._pin_button)
        self._close_button = QPushButton('×')
        self._close_button.setAccessibleName('停止核對並關閉')
        self._close_button.setObjectName('closeComparison')
        self._close_button.setFixedSize(28, 28)
        self._close_button.setToolTip('停止核對並關閉')
        self._close_button.clicked.connect(self.close)
        heading.addWidget(self._close_button)
        outer.addLayout(heading)

        self._mini_panel = QWidget()
        mini_layout = QVBoxLayout(self._mini_panel)
        mini_layout.setContentsMargins(0, 0, 0, 0)
        mini_layout.setSpacing(6)
        self._mini_group_combo = QComboBox()
        self._mini_group_combo.setObjectName('miniComparisonGroups')
        self._mini_group_combo.setAccessibleName('迷你視窗核對組別')
        self._mini_group_combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._mini_group_combo.setMinimumWidth(0)
        apply_combo_box_theme(self._mini_group_combo)
        self._mini_group_combo.currentIndexChanged.connect(self._select_mini_group)
        mini_layout.addWidget(self._mini_group_combo)
        self._mini_status_card = QFrame()
        self._mini_status_card.setObjectName('statusCard')
        mini_status_layout = QVBoxLayout(self._mini_status_card)
        mini_status_layout.setContentsMargins(10, 7, 10, 7)
        mini_status_layout.setSpacing(2)
        self._mini_status = _label('準備核對')
        self._mini_status.setAccessibleName('迷你視窗核對結果')
        self._mini_activity = _label('選好 A、B，即可開始')
        self._mini_activity.setObjectName('muted')
        mini_status_layout.addWidget(self._mini_status)
        mini_status_layout.addWidget(self._mini_activity)
        mini_layout.addWidget(self._mini_status_card)
        self._mini_previews = {}
        for name in ('A', 'B'):
            pane = QFrame()
            pane.setObjectName('comparisonPane')
            row = QHBoxLayout(pane)
            row.setContentsMargins(9, 3, 9, 3)
            row.setSpacing(6)
            label = _label(name)
            label.setStyleSheet('font-weight: 600;')
            row.addWidget(label)
            preview = _PreviewLabel()
            preview.setObjectName(f'miniComparisonPreview{name}')
            preview.setAccessibleName(f'迷你視窗 {name} 區辨識文字')
            preview.set_preview('')
            self._mini_previews[name] = preview
            row.addWidget(preview, 1)
            mini_layout.addWidget(pane)
        mini_layout.addStretch(1)
        self._mini_panel.hide()
        outer.addWidget(self._mini_panel, 1)

        scroll = self._scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(9)
        group_heading = QHBoxLayout()
        group_heading.addWidget(_label('核對組別'))
        group_heading.addStretch()
        self._group_summary = _label('1 組')
        self._group_summary.setObjectName('muted')
        group_heading.addWidget(self._group_summary)
        layout.addLayout(group_heading)
        group_bar = QHBoxLayout()
        group_bar.setSpacing(5)
        self._group_combo = QComboBox()
        self._group_combo.setObjectName('comparisonGroups')
        self._group_combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._group_combo.setMinimumWidth(0)
        apply_combo_box_theme(self._group_combo)
        self._group_combo.currentIndexChanged.connect(self._select_group)
        group_bar.addWidget(self._group_combo, 1)
        self._add_group_button = QPushButton('＋')
        self._add_group_button.setObjectName('addComparisonGroup')
        self._add_group_button.setAccessibleName('新增核對組別')
        self._add_group_button.setFixedSize(29, 29)
        self._add_group_button.setToolTip('新增一組 A／B')
        self._add_group_button.clicked.connect(self._add_group)
        group_bar.addWidget(self._add_group_button)
        self._group_menu_button = QPushButton('⋯')
        self._group_menu_button.setObjectName('manageComparisonGroup')
        self._group_menu_button.setAccessibleName('管理目前組別')
        self._group_menu_button.setFixedSize(29, 29)
        menu = QMenu(self._group_menu_button)
        self._rename_action = menu.addAction('更改組名', self._rename_group)
        self._remove_action = menu.addAction('移除此組', self._remove_group)
        self._group_menu_button.setMenu(menu)
        group_bar.addWidget(self._group_menu_button)
        layout.addLayout(group_bar)
        self._group_list = QListWidget()
        self._group_list.setObjectName('comparisonGroupResults')
        self._group_list.setAccessibleName('各組核對狀態')
        self._group_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._group_list.setFixedHeight(72)
        self._group_list.currentItemChanged.connect(self._list_group_selected)
        layout.addWidget(self._group_list)

        self._status_card = QFrame()
        self._status_card.setObjectName('statusCard')
        status_layout = QVBoxLayout(self._status_card)
        status_layout.setContentsMargins(12, 10, 12, 10)
        status_layout.setSpacing(3)
        self._status = _label('準備核對')
        self._status.setObjectName('comparisonStatus')
        self._status.setAccessibleName('目前組別核對結果')
        status_layout.addWidget(self._status)
        self._activity = _label('選好 A、B，即可開始')
        self._activity.setObjectName('muted')
        status_layout.addWidget(self._activity)
        layout.addWidget(self._status_card)

        self._region_labels, self._select_buttons, self._texts, self._previews = {}, {}, {}, {}
        for name in ('A', 'B'):
            pane = QFrame()
            pane.setObjectName('comparisonPane')
            pane_layout = QVBoxLayout(pane)
            pane_layout.setContentsMargins(10, 7, 10, 7)
            pane_layout.setSpacing(3)
            row = QHBoxLayout()
            row.setSpacing(5)
            label = _label(f'{name} 區')
            label.setStyleSheet('font-weight: 600;')
            row.addWidget(label)
            region = _label('尚未選取')
            region.setObjectName('muted')
            row.addWidget(region, 1)
            self._region_labels[name] = region
            button = QPushButton('框選')
            button.setObjectName(f'selectComparison{name}')
            button.setToolTip(f'框選目前組別的 {name} 區，位置保留至程式結束')
            button.clicked.connect(lambda checked=False, key=name: self._select(key))
            self._select_buttons[name] = button
            row.addWidget(button)
            pane_layout.addLayout(row)
            preview = _PreviewLabel()
            preview.setObjectName(f'comparisonPreview{name}')
            preview.setAccessibleName(f'{name} 區辨識文字摘要')
            preview.set_preview('')
            self._previews[name] = preview
            pane_layout.addWidget(preview)
            layout.addWidget(pane)

        options = QHBoxLayout()
        options.setSpacing(6)
        self._mode = QComboBox()
        self._mode.setObjectName('comparisonMode')
        self._mode.addItem('忽略空白與標點', 'normalized')
        self._mode.addItem('逐字比對', 'exact')
        self._mode.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._mode.setMinimumWidth(0)
        self._mode.setToolTip('只比文字與數字，也忽略符號；-1.23 與 123 可能相同。金額請用逐字比對。')
        apply_combo_box_theme(self._mode)
        self._mode.currentIndexChanged.connect(self._mode_changed)
        options.addWidget(self._mode, 1)
        self._interval = QSpinBox()
        self._interval.setObjectName('comparisonInterval')
        self._interval.setRange(1, 60)
        self._interval.setValue(controller.interval)
        self._interval.setSuffix(' 秒')
        self._interval.setToolTip('每批完成後，等待多久再檢查全部組別')
        self._interval.setFixedWidth(70)
        self._interval.valueChanged.connect(controller.set_interval)
        options.addWidget(self._interval)
        layout.addLayout(options)
        self._details = QWidget()
        details = QVBoxLayout(self._details)
        details.setContentsMargins(0, 0, 0, 0)
        details.setSpacing(4)
        for name in ('A', 'B'):
            details.addWidget(_label(f'{name} 區全文'))
            text = QPlainTextEdit()
            text.setObjectName(f'comparisonText{name}')
            text.setAccessibleName(f'{name} 區域辨識全文')
            text.setReadOnly(True)
            text.setFixedHeight(66)
            self._texts[name] = text
            details.addWidget(text)
        self._details.hide()
        layout.addWidget(self._details)
        layout.addStretch(1)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        self._note = _label('各組依序核對，畫面未變就略過。')
        self._note.setObjectName('muted')
        self._note.setWordWrap(True)
        self._note.setMinimumHeight(30)
        outer.addWidget(self._note)
        buttons = QHBoxLayout()
        buttons.setSpacing(5)
        self._start_button = QPushButton('開始全部')
        self._start_button.setObjectName('startComparison')
        self._start_button.setToolTip('依序核對所有組別；每組都需選好 A、B')
        self._start_button.clicked.connect(self._start)
        self._pause_button = QPushButton('暫停')
        self._pause_button.clicked.connect(controller.pause)
        self._compact_button = QPushButton('查看全文')
        self._compact_button.setCheckable(True)
        self._compact_button.setChecked(True)
        self._compact_button.toggled.connect(self.set_compact)
        buttons.addWidget(self._start_button, 1)
        buttons.addWidget(self._pause_button)
        buttons.addWidget(self._compact_button)
        outer.addLayout(buttons)
        controller.updated.connect(self._updated)
        controller.state_changed.connect(self._state_changed)
        controller.failed.connect(self._failed)
        if hasattr(controller, 'groups_changed'):
            controller.groups_changed.connect(self._groups_changed)
            controller.group_changed.connect(self._group_changed)
        controller.before_capture = self.can_capture
        app = QApplication.instance()
        app.primaryScreenChanged.connect(self._bind_screen)
        self._bind_screen(app.primaryScreen(), initial=True)
        self._refresh_groups()
        self._refresh_regions()
        self._sync_buttons()
        self._set_status('準備核對', _TEXT_SEC)
        self._sync_mini_result()
        self._mini_ready = True

    def _groups(self):
        return getattr(self.controller, 'groups', [{'id': 'default', 'name': '第 1 組',
                    'regions': self.controller.regions, 'result': None}])

    def _active_group_id(self):
        return getattr(self.controller, 'active_group_id', 'default')

    def _current_group(self):
        return next((group for group in self._groups() if group['id'] == self._active_group_id()), None)

    def _refresh_groups(self):
        groups, active = self._groups(), self._active_group_id()
        labels = {'match': ('一致', _SUCCESS), 'mismatch': ('不同', _ERROR), 'uncertain': ('待確認', _WARNING)}
        different = sum((group.get('result') or {}).get('verdict') == 'mismatch' for group in groups)
        self._group_summary.setText(f'{len(groups)} 組' + (f' · {different} 組不同' if different else ''))
        with QSignalBlocker(self._group_combo), QSignalBlocker(self._group_list), QSignalBlocker(self._mini_group_combo):
            self._group_combo.clear()
            self._group_list.clear()
            self._mini_group_combo.clear()
            for index, group in enumerate(groups):
                self._group_combo.addItem(group['name'], group['id'])
                self._mini_group_combo.addItem(group['name'], group['id'])
                result = group.get('result') or {}
                configured = set(group['regions']) == {'A', 'B'}
                status, color = labels.get(result.get('verdict'), ('待核對' if configured else '未設定', _TEXT_SEC))
                item = QListWidgetItem(f'●  {group["name"]}    {status}')
                item.setData(Qt.ItemDataRole.UserRole, group['id'])
                item.setToolTip(_plain_tooltip(f'{group["name"]} · {status}'))
                item.setForeground(QColor(color))
                item.setSizeHint(QSize(0, 23))
                self._group_list.addItem(item)
                if group['id'] == active:
                    self._group_combo.setCurrentIndex(index)
                    self._mini_group_combo.setCurrentIndex(index)
                    self._group_list.setCurrentItem(item)
        self._group_list.setVisible(len(groups) > 1)
        editable = hasattr(self.controller, 'add_group') and not self._closing
        self._add_group_button.setEnabled(editable and len(groups) < 20)
        self._group_menu_button.setEnabled(editable)
        self._remove_action.setEnabled(editable and len(groups) > 1)

    def _groups_changed(self):
        self._refresh_groups()
        current = self._current_group()
        result = current.get('result') if current else None
        if not self._closing and not self._closed_by_user and not self._selection_active:
            if result and result != self._last_result:
                self._render_result(result)
            elif result is None and self._last_result is not None:
                self._group_changed()
        self._sync_buttons()
        self._sync_mini_result()

    def _group_changed(self):
        self._refresh_groups()
        self._refresh_regions()
        current = self._current_group()
        result = current.get('result') if current else None
        self._last_result = None
        self._cycle_result_visible = False
        for side in ('A', 'B'):
            self._texts[side].clear()
            self._previews[side].set_preview('')
        if result:
            self._render_result(result)
        else:
            self._set_status('準備核對', _TEXT_SEC)
            self._activity.setText('選好 A、B，即可開始' if not current or len(current['regions']) < 2 else '尚未產生結果')
        self._sync_buttons()
        self._sync_mini_result()

    def _select_group(self):
        group_id = self._group_combo.currentData()
        if group_id is not None and hasattr(self.controller, 'select_group'):
            self.controller.select_group(group_id)

    def _select_mini_group(self):
        group_id = self._mini_group_combo.currentData()
        if group_id is not None and hasattr(self.controller, 'select_group'):
            self.controller.select_group(group_id)

    def _list_group_selected(self, current, previous):
        if current is not None and hasattr(self.controller, 'select_group'):
            self.controller.select_group(current.data(Qt.ItemDataRole.UserRole))

    def _add_group(self):
        if hasattr(self.controller, 'add_group'):
            self.controller.add_group()

    def _rename_group(self):
        group = self._current_group()
        if group is None or not hasattr(self.controller, 'rename_group'):
            return
        name, accepted = QInputDialog.getText(self, '更改組名', '組別名稱', text=group['name'])
        if accepted:
            self.controller.rename_group(group['id'], name)

    def _remove_group(self):
        if hasattr(self.controller, 'remove_group'):
            self.controller.remove_group(self._active_group_id())

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
        self.controller.invalidate_regions('螢幕配置已變更，請重新框選各組 A、B。')
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
        else:
            self.show()

    def set_region(self, name, region):
        accepted = self.controller.set_region(name, region)
        self._refresh_regions()
        self._refresh_groups()
        self._sync_buttons()
        if accepted:
            self._note.setText('選好各組 A、B，按「開始全部」。')
        return accepted

    def _refresh_regions(self):
        for name, label in self._region_labels.items():
            region = self.controller.regions.get(name)
            if region is None:
                label.setText('尚未選取')
                label.setToolTip('')
                self._select_buttons[name].setText('框選')
            else:
                x, y, width, height, _monitor = region
                label.setText('範圍已選取')
                label.setToolTip(f'位置 {x}, {y} · {width} × {height}')
                self._select_buttons[name].setText('重選')

    def _sync_buttons(self):
        groups = self._groups()
        configured = bool(groups) and all(set(group['regions']) == {'A', 'B'} for group in groups)
        self._start_button.setEnabled(configured and not self.controller.running and not self._closing)
        self._pause_button.setEnabled(self.controller.running and not self._closing)

    def _mode_changed(self):
        self.controller.set_mode(self._mode.currentData())
        self._note.setText('規則已更新，按「開始全部」。')

    def _start(self):
        if self._closing or self._closed_by_user or self._selection_active:
            return
        self.avoid_regions()
        self.controller.start()
        self._sync_buttons()

    def _set_status(self, text, color):
        self._status.setText(text)
        self._status.setStyleSheet(f'color: {color}; font-size: 19px; font-weight: 600; border: none;')
        self._status_card.setStyleSheet(f'QFrame#statusCard {{ border: 1px solid {_BORDER}; '
                                       f'border-left: 3px solid {color}; border-radius: 9px; }}')
        self._mini_status.setText(text)
        self._mini_status.setStyleSheet(f'color: {color}; font-size: 16px; font-weight: 600; border: none;')
        self._mini_status_card.setStyleSheet(self._status_card.styleSheet())

    def _state_changed(self, state):
        self._refresh_regions()
        self._sync_buttons()
        if state == 'running':
            if not self._cycle_result_visible:
                self._set_status('核對中', _WARNING)
                self._activity.setText('正在讀取各組文字…')
            self._note.setText('各組依序核對，畫面未變就略過。')
        elif state in ('checking', 'watching', 'unchanged'):
            if self._cycle_result_visible:
                self._note.setText('畫面未變，等待變化。' if state == 'unchanged' else '持續留意各組畫面。')
        elif state == 'waiting':
            self._note.setText('等待上一項辨識完成…')
        elif state in ('paused', 'idle'):
            if not self._cycle_result_visible:
                self._set_status('已暫停' if state == 'paused' else '準備核對', _TEXT_SEC)
                self._activity.setText('選好 A、B，即可開始')
            self._note.setText('已暫停，不擷取畫面。' if state == 'paused' else '各組依序核對，畫面未變就略過。')
        self._sync_mini_result()

    def _updated(self, result):
        if self._closing or self._closed_by_user or self._selection_active:
            return
        self._refresh_groups()
        if result.get('group_id', self._active_group_id()) != self._active_group_id():
            return
        self._render_result(result)

    def _render_result(self, result):
        self._last_result = dict(result)
        self._cycle_result_visible = True
        for name, key in (('A', 'a'), ('B', 'b')):
            value = result.get(key, '')
            if not self._compact:
                self._texts[name].setPlainText(value)
            else:
                self._texts[name].clear()
            self._previews[name].set_preview(value)
        title, color, note = {
            'match': ('內容一致', _SUCCESS, '兩區文字相同。'),
            'mismatch': ('發現差異', _ERROR, '查看目前組別的 A、B 文字。'),
        }.get(result.get('verdict'), ('需要確認', _WARNING, '文字不完整，請查看原畫面。'))
        self._set_status(title, color)
        stamp = result.get('timestamp', '')
        try:
            stamp = datetime.fromisoformat(stamp).strftime('%H:%M:%S')
        except (TypeError, ValueError):
            stamp = ''
        self._activity.setText(f'{stamp} · 第 {result.get("cycle", 0)} 次核對')
        self._note.setText(note)
        self._sync_buttons()
        self._sync_mini_result()

    def _failed(self, message):
        self._set_status('需要確認', _WARNING)
        self._note.setText(message)
        self._refresh_groups()
        self._refresh_regions()
        self._sync_buttons()
        self._sync_mini_result()

    def _sync_mini_result(self):
        self._mini_activity.setText(self._activity.text())
        for name, key in (('A', 'a'), ('B', 'b')):
            self._mini_previews[name].set_preview((self._last_result or {}).get(key, ''))

    def _target_regions(self):
        return getattr(self.controller, 'target_regions', tuple(self.controller.regions.values()))

    def _physical_window_rect(self):
        screen = QApplication.primaryScreen()
        if screen is None:
            return None
        geometry, frame = screen.geometry(), self.frameGeometry()
        scale = screen.devicePixelRatio()
        return QRectF((frame.x() - geometry.x()) * scale, (frame.y() - geometry.y()) * scale,
                      frame.width() * scale, frame.height() * scale)

    def can_capture(self):
        if self._selection_active or self._closing:
            return False
        if not self.isVisible() or self.isMinimized():
            return True
        frame = self._physical_window_rect()
        return frame is not None and not any(frame.intersects(QRectF(*region[:4]))
                                            for region in self._target_regions() if region is not None)

    def avoid_regions(self):
        if self.can_capture() or not self.isVisible():
            return self.can_capture()
        screen = QApplication.primaryScreen()
        if screen is None:
            return False
        available, frame = screen.availableGeometry(), self.frameGeometry()
        width, height = frame.width(), frame.height()
        x_candidates = {available.left(), available.right() - width + 1}
        y_candidates = {available.top(), available.bottom() - height + 1}
        scale, origin = screen.devicePixelRatio(), screen.geometry().topLeft()
        for region in self._target_regions():
            if region is None:
                continue
            x, y, w, h, _ = region
            x_candidates.update((round(origin.x() + x / scale) - width - 8, round(origin.x() + (x + w) / scale) + 8))
            y_candidates.update((round(origin.y() + y / scale) - height - 8, round(origin.y() + (y + h) / scale) + 8))
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
        if not compact and self._mini_mode:
            self.set_mini(False)
        self._compact = bool(compact)
        self._details.setVisible(not compact)
        self._compact_button.setText('查看全文' if compact else '收合全文')
        with QSignalBlocker(self._compact_button):
            self._compact_button.setChecked(compact)
        for name, key in (('A', 'a'), ('B', 'b')):
            self._texts[name].setPlainText((self._last_result or {}).get(key, '') if not compact else '')
        # The result update and detail toggle never grow or reposition the tool.

    def set_mini(self, mini, *, resize=True):
        """Keep live result lines visible when the user shrinks the tool."""
        mini = bool(mini)
        if self._mini_mode == mini:
            return
        self._changing_mini_layout = True
        try:
            position = self.pos()
            if mini and self.height() >= 540:
                self._normal_size = QSize(self.size())
            self._mini_mode = mini
            with QSignalBlocker(self._mini_button):
                self._mini_button.setChecked(mini)
            self._mini_button.setText('還原' if mini else '迷你')
            self._scroll.setVisible(not mini)
            self._mini_panel.setVisible(mini)
            self._note.setVisible(not mini)
            with QSignalBlocker(self._compact_button):
                self._compact_button.setChecked(True if mini else self._compact)
            self._compact_button.setText('查看全文' if mini or self._compact else '收合全文')
            self._sync_mini_result()
            if resize:
                self.resize(QSize(320, 300) if mini else self._normal_size)
                self.move(position)
        finally:
            self._changing_mini_layout = False

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self._mini_ready or self._changing_mini_layout:
            return
        if not self._mini_mode and self.height() < 540:
            self.set_mini(True, resize=False)
        elif self._mini_mode and self.height() >= 560:
            self.set_mini(False, resize=False)

    def set_pinned(self, pinned):
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
