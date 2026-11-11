"""Session-only controls for multiple A fields searched in one shared B area."""
from __future__ import annotations

from datetime import datetime
import math

from PySide6.QtCore import QRectF, QSignalBlocker, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .search_highlight_overlay import SearchHighlightOverlay, apply_native_pin
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
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(True)
    widget.setMinimumWidth(0)
    return widget


def _region_text(region):
    if region is None:
        return '尚未框選'
    x, y, width, height, _ = region
    return f'{x}, {y} · {width} × {height}'


def _score_text(candidate):
    score = candidate.get('score')
    if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score) or not 0 <= score <= 1:
        return ''
    # A near match must never be rounded up to an apparent exact match.
    return f'{math.floor(score * 1000) / 10:g}%'


class _FieldCard(QFrame):
    select_requested = Signal(str)
    rename_requested = Signal(str)
    remove_requested = Signal(str)
    candidate_requested = Signal(str, str)

    def __init__(self, field_id):
        super().__init__()
        self.field_id = field_id
        self.setObjectName('fieldCard')
        self._signature = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 9, 10, 9)
        layout.setSpacing(6)
        header = QHBoxLayout()
        self._name = _label()
        self._name.setStyleSheet('font-weight: 600;')
        header.addWidget(self._name, 1)
        self._select = QPushButton('框選數值')
        self._select.clicked.connect(lambda: self.select_requested.emit(self.field_id))
        header.addWidget(self._select)
        self._edit = QPushButton('改名')
        self._edit.clicked.connect(lambda: self.rename_requested.emit(self.field_id))
        header.addWidget(self._edit)
        self._remove = QPushButton('移除')
        self._remove.clicked.connect(lambda: self.remove_requested.emit(self.field_id))
        header.addWidget(self._remove)
        layout.addLayout(header)
        self._region = _label()
        self._region.setObjectName('muted')
        layout.addWidget(self._region)
        self._status = _label('等待搜尋')
        layout.addWidget(self._status)
        self._text = _label()
        self._text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self._text)
        candidate_row = QHBoxLayout()
        self._candidates = QComboBox()
        self._candidates.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._candidates.setMinimumWidth(0)
        apply_combo_box_theme(self._candidates)
        self._candidates.setMinimumWidth(0)
        self._candidates.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self._candidates.setMinimumContentsLength(8)
        self._apply = QPushButton('採用')
        self._apply.clicked.connect(self._choose)
        self._candidates.currentIndexChanged.connect(
            lambda: self._apply.setEnabled(self._candidates.currentData() is not None))
        candidate_row.addWidget(self._candidates, 1)
        candidate_row.addWidget(self._apply)
        layout.addLayout(candidate_row)
        self._candidates.hide()
        self._apply.hide()

    def _choose(self):
        candidate = self._candidates.currentData()
        if candidate is not None:
            self.candidate_requested.emit(self.field_id, candidate)

    def set_field(self, field):
        result = field.get('result') or {}
        candidates = result.get('candidates', [])
        selected = result.get('selected_id')
        chosen = next((candidate for candidate in candidates if candidate.get('id') == selected), None)
        self._name.setText(field.get('name', '欄位'))
        self._select.setToolTip(f'框住「{self._name.text()}」這一欄輸入的數值。')
        self._region.setText(_region_text(field.get('region')))
        status = result.get('status')
        label, color = {
            'matched': ('已找到', _SUCCESS), 'missing': ('未找到', _ERROR),
            'ambiguous': ('多處符合，請選一處', _WARNING),
            'uncertain': ('需要確認', _WARNING),
        }.get(status, ('等待搜尋' if field.get('region') else '請框選數值', _TEXT_SEC))
        if chosen is not None:
            if chosen.get('match_type') in ('similar', 'numeric_similar'):
                label, color = '相似，需確認', _WARNING
            score = _score_text(chosen)
            if score:
                label += f' · {score}'
            if chosen.get('match_type') == 'date_equivalent':
                label += '（日期換算）'
        self._status.setText(label)
        self._status.setToolTip(result.get('reason', '') + '\n百分比是文字／數值比對程度，不是 OCR 正確機率。')
        self._status.setStyleSheet(f'color: {color};')
        text = result.get('text', '')
        self._text.setText(text[:240] + ('…' if len(text) > 240 else ''))
        self._text.setVisible(bool(text))
        signature = tuple((row.get('id'), row.get('text'), row.get('score'), row.get('match_type'))
                          for row in candidates), selected
        if signature != self._signature:
            self._signature = signature
            with QSignalBlocker(self._candidates):
                self._candidates.clear()
                self._candidates.addItem('選擇符合位置…', None)
                for index, candidate in enumerate(candidates, 1):
                    preview = ' '.join(candidate.get('text', '').split())[:36]
                    score = _score_text(candidate)
                    suffix = ' · 日期換算' if candidate.get('match_type') == 'date_equivalent' else ''
                    self._candidates.addItem(f'位置 {index} · {score} · {preview}{suffix}', candidate['id'])
                self._candidates.setCurrentIndex(max(0, self._candidates.findData(selected)))
        editable = bool(candidates) and status != 'matched'
        self._candidates.setVisible(editable)
        self._apply.setVisible(editable)
        self._apply.setEnabled(self._candidates.currentData() is not None)


class SharedSearchWindow(QWidget):
    select_requested = Signal(str)
    screen_invalidated = Signal()
    closed = Signal()

    def __init__(self, controller, parent=None):
        super().__init__(parent, Qt.WindowType.Window | Qt.WindowType.WindowTitleHint
                         | Qt.WindowType.WindowSystemMenuHint | Qt.WindowType.WindowCloseButtonHint
                         | Qt.WindowType.WindowMinimizeButtonHint)
        self.controller = controller
        self.highlight_overlay = SearchHighlightOverlay()
        self.resume_button = QPushButton('恢復介面')
        self.resume_button.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint
                                         | Qt.WindowType.WindowStaysOnTopHint
                                         | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.resume_button.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.resume_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.resume_button.setStyleSheet(f'QPushButton {{ background: {_BG}; color: {_TEXT_PRI}; '
                                         f'border: 1px solid {_BORDER}; border-radius: 5px; padding: 6px 10px; }}')
        self.resume_button.clicked.connect(self.open)
        self.destroyed.connect(self.resume_button.deleteLater)
        self.manual_button = QPushButton('手動辨識')
        self.manual_button.setWindowFlags(self.resume_button.windowFlags())
        self.manual_button.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.manual_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.manual_button.setStyleSheet(self.resume_button.styleSheet())
        self.manual_button.setToolTip('重新讀取這一行數值與發票，即使畫面沒有改變。')
        self.manual_button.clicked.connect(self._manual_refresh)
        self.destroyed.connect(self.manual_button.deleteLater)
        self.resume_button.hide()
        self.manual_button.hide()
        self._capture_active = False
        self._closing = self._closed_by_user = self._selection_active = False
        self._recover_controls = False
        self._screen = None
        self._fields_cards = {}
        self._visual_epoch = 0
        self._capture_epoch = None
        self._last_regions = None
        self._calibration_remaining = 0
        self._calibration_timer = QTimer(self)
        self._calibration_timer.setInterval(1000)
        self._calibration_timer.timeout.connect(self._calibration_tick)
        self.setWindowTitle('整列搜尋')
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.resize(348, 560)
        self.setMinimumSize(320, 300)
        self.setStyleSheet(f'''
            QWidget {{ background: {_BG}; color: {_TEXT_PRI}; font-size: 12px; }}
            QLabel {{ background: transparent; border: none; }}
            QLabel#muted {{ color: {_TEXT_SEC}; }}
            QPushButton {{ background: {_BG_RAISE}; color: {_TEXT_PRI}; border: 1px solid {_BORDER};
                border-radius: 6px; padding: 5px 7px; }}
            QPushButton:hover {{ background: {_BG_HOVER}; }}
            QPushButton:disabled {{ color: {_TEXT_SEC}; }}
            QPushButton#startSearch {{ background: {_ACCENT}; color: {_ACCENT_T}; }}
            QPushButton#startSearch:disabled {{ background: {_BG_RAISE}; color: {_TEXT_SEC}; }}
            QFrame#fieldCard {{ background: {_BG_RAISE}; border: 1px solid {_BORDER}; border-radius: 8px; }}
            QSpinBox {{ background: {_BG_RAISE}; color: {_TEXT_PRI}; border: 1px solid {_BORDER}; padding: 4px; }}
            QScrollArea {{ border: none; }}
            QScrollBar:vertical {{ width: 5px; background: transparent; }}
            QScrollBar::handle:vertical {{ background: {_BORDER}; min-height: 24px; border-radius: 2px; }}
        ''')
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)
        title = QHBoxLayout()
        self._status = _label('設定搜尋位置')
        self._status.setStyleSheet('font-size: 15px; font-weight: 600;')
        title.addWidget(self._status, 1)
        self._pin = QCheckBox('標示置頂')
        self._pin.setToolTip('讓各欄下方的文字與恢復介面按鈕置頂；設定面板維持一般視窗。')
        self._pin.setChecked(True)
        self._pin.toggled.connect(self.set_pinned)
        title.addWidget(self._pin)
        outer.addLayout(title)
        self._activity = _label('尚未開始')
        self._activity.setObjectName('muted')
        outer.addWidget(self._activity)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        body = QVBoxLayout(content)
        body.setContentsMargins(0, 0, 4, 0)
        body.setSpacing(9)
        self._select_b = QPushButton('框選發票位置（B）')
        self._select_b.setToolTip('所有數值共用此發票範圍，只在範圍內找符合位置。')
        self._select_b.clicked.connect(lambda: self._select('B'))
        body.addWidget(self._select_b)
        self._b_hint = _label('圈住發票上要核對的區域，所有數值共用。')
        self._b_hint.setObjectName('muted')
        body.addWidget(self._b_hint)
        self._b_label = _label()
        self._b_label.setObjectName('muted')
        body.addWidget(self._b_label)
        self._a_heading = _label('輸入的數值欄位（A）')
        self._a_heading.setStyleSheet('font-weight: 600;')
        body.addWidget(self._a_heading)
        self._a_hint = _label('同一行，每欄各框一個數值。')
        self._a_hint.setObjectName('muted')
        body.addWidget(self._a_hint)
        self._add = QPushButton('新增數值欄位')
        self._add.clicked.connect(self._add_field)
        body.addWidget(self._add)
        self._cards_layout = QVBoxLayout()
        self._cards_layout.setContentsMargins(0, 0, 0, 0)
        self._cards_layout.setSpacing(8)
        body.addLayout(self._cards_layout)
        self._select_tracking = QPushButton('框選輸入表格（換列用）')
        self._select_tracking.setToolTip('自動跟隨選取列時，圈住輸入數值所在的表格；發票位置另行框選。')
        self._select_tracking.clicked.connect(lambda: self._select('TRACK'))
        body.addWidget(self._select_tracking)
        self._tracking_label = _label()
        self._tracking_label.setObjectName('muted')
        body.addWidget(self._tracking_label)
        row = QHBoxLayout()
        row.addWidget(_label('列高／間距'))
        self._row_pitch = QSpinBox()
        self._row_pitch.setRange(1, 500)
        self._row_pitch.setValue(30)
        self._row_pitch.setSuffix(' px')
        self._row_pitch.setToolTip('請依表格實際列高設定；上一列與下一列會移動所有輸入數值欄位。')
        self._row_pitch.valueChanged.connect(self._set_row_pitch)
        row.addWidget(self._row_pitch, 1)
        body.addLayout(row)
        row = QHBoxLayout()
        self._previous = QPushButton('上一列')
        self._next = QPushButton('下一列')
        self._previous.clicked.connect(lambda: self._move_rows(-1))
        self._next.clicked.connect(lambda: self._move_rows(1))
        row.addWidget(self._previous)
        row.addWidget(self._next)
        body.addLayout(row)
        self._calibrate = QPushButton('校正目前選取列')
        self._calibrate.clicked.connect(self._calibrate_tracking)
        body.addWidget(self._calibrate)
        self._auto = QCheckBox('自動跟隨表格')
        self._auto.toggled.connect(self._auto_changed)
        body.addWidget(self._auto)
        self._mode = QComboBox()
        self._mode.addItem('數值核對／相似提醒', 'normalized')
        self._mode.addItem('逐字完全相同', 'exact')
        self._mode.setToolTip('相同數值列為找到；單一數值的位數與格式相同、只差一位數字時，列為相似供確認。\n'
                              '正負號、小數點、千分位、百分號與前導零仍須相符；文字近似門檻為 95%。\n'
                              '百分比是比對程度，不是 OCR 正確機率。逐字模式停用相似搜尋與日期換算。')
        apply_combo_box_theme(self._mode)
        self._mode.currentIndexChanged.connect(self._mode_changed)
        body.addWidget(self._mode)
        self._date_equivalence = QCheckBox('日期換算（民國／西元）')
        self._date_equivalence.setChecked(getattr(controller, 'date_equivalence', True))
        self._date_equivalence.setToolTip('例如民國 115/10/12 與西元 2026/10/12 視為同一天；逐字模式依原文比對。')
        self._date_equivalence.toggled.connect(self._date_mode_changed)
        body.addWidget(self._date_equivalence)
        row = QHBoxLayout()
        row.addWidget(_label('每輪後等待'))
        self._interval = QSpinBox()
        self._interval.setRange(1, 60)
        self._interval.setValue(2)
        self._interval.setSuffix(' 秒')
        self._interval.valueChanged.connect(controller.set_interval)
        row.addWidget(self._interval)
        body.addLayout(row)
        body.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)
        appearance = QHBoxLayout()
        appearance.addWidget(_label('標示背景透明度'))
        self._background_transparency = QSpinBox()
        self._background_transparency.setRange(0, 100)
        self._background_transparency.setValue(100)
        self._background_transparency.setSuffix(' %')
        self._background_transparency.setToolTip('100% 隱藏背景，只留下數值與相似度；0% 使用白底。文字保持清楚。')
        self._background_transparency.valueChanged.connect(
            lambda percent: self.highlight_overlay.set_background_opacity(100 - percent))
        appearance.addWidget(self._background_transparency, 1)
        outer.addLayout(appearance)
        self._note = _label('開始後，在發票找到的位置下方顯示數值與相似度。')
        self._note.setObjectName('muted')
        outer.addWidget(self._note)
        buttons = QHBoxLayout()
        self._start_button = QPushButton('開始搜尋')
        self._start_button.setObjectName('startSearch')
        self._start_button.clicked.connect(self._start)
        self._manual_button = QPushButton('手動辨識')
        self._manual_button.clicked.connect(self._manual_refresh)
        self._pause_button = QPushButton('暫停')
        self._pause_button.clicked.connect(self.pause)
        self._clear_button = QPushButton('清除結果')
        self._clear_button.clicked.connect(self.pause)
        buttons.addWidget(self._start_button)
        buttons.addWidget(self._manual_button)
        buttons.addWidget(self._pause_button)
        buttons.addWidget(self._clear_button)
        outer.addLayout(buttons)
        controller.fields_changed.connect(self._refresh_fields)
        controller.updated.connect(self._updated)
        controller.state_changed.connect(self._state_changed)
        controller.failed.connect(self._failed)
        controller.before_capture = self.before_capture
        controller.after_capture = self.after_capture
        QApplication.instance().primaryScreenChanged.connect(self._bind_screen)
        self._bind_screen(QApplication.primaryScreen(), initial=True)
        self._refresh_fields()

    def _refresh_fields(self):
        fields = self.controller.fields
        signature = (tuple((field['id'], field.get('region')) for field in fields),
                     self.controller.b_region, self.controller.tracking_region)
        if signature != self._last_regions:
            self._last_regions = signature
            self._clear_visuals()
        elif not any(field.get('result') is not None for field in fields):
            self._clear_visuals()
        ids = {field['id'] for field in fields}
        for field_id in tuple(self._fields_cards):
            if field_id not in ids:
                card = self._fields_cards.pop(field_id)
                self._cards_layout.removeWidget(card)
                card.deleteLater()
        for index, field in enumerate(fields):
            card = self._fields_cards.get(field['id'])
            if card is None:
                card = _FieldCard(field['id'])
                card.select_requested.connect(self._select)
                card.rename_requested.connect(self._rename)
                card.remove_requested.connect(self._remove)
                card.candidate_requested.connect(self._select_candidate)
                self._fields_cards[field['id']] = card
                self._cards_layout.insertWidget(index, card)
            card.set_field(field)
        self._b_label.setText(_region_text(self.controller.b_region))
        self._tracking_label.setText(_region_text(self.controller.tracking_region))
        with QSignalBlocker(self._auto):
            self._auto.setChecked(self.controller.auto_follow)
        with QSignalBlocker(self._row_pitch):
            self._row_pitch.setValue(self.controller.row_pitch)
        self._add.setEnabled(len(fields) < 20 and not self._closing)
        configured = bool(fields) and self.controller.b_region is not None and all(field['region'] for field in fields)
        self._start_button.setEnabled(configured and not self.controller.running and not self._closing)
        self._manual_button.setEnabled(self.controller.running and not self._closing)
        self._pause_button.setEnabled(self.controller.running or self._calibration_timer.isActive())

    def _clear_visuals(self):
        self._visual_epoch += 1
        self.highlight_overlay.clear()
        self.resume_button.setText('恢復介面')

    def _cancel_countdown(self):
        self._calibration_timer.stop()
        self._calibration_remaining = 0
        self._calibrate.setText('校正目前選取列')

    def pause(self, *, preserve_recovery=False):
        if not preserve_recovery:
            self._recover_controls = False
        self._cancel_countdown()
        self._clear_visuals()
        self.controller.pause()
        self.resume_button.hide()
        self.manual_button.hide()
        self._refresh_fields()

    def _select(self, key):
        self.pause()
        self.select_requested.emit(key)

    def set_selection_active(self, active):
        if self._closing or self._closed_by_user:
            return
        self._selection_active = bool(active)
        if active:
            self.pause()
            self.hide()
        else:
            self.show()

    def set_region(self, key, region):
        self.pause()
        if not isinstance(region, (tuple, list)) or len(region) != 5 or region[4] != 1:
            self._failed('請在主螢幕框選輸入數值欄位、發票位置與輸入表格。')
            return False
        accepted = (self.controller.set_b_region(region) if key == 'B' else
                    self.controller.set_tracking_region(region) if key == 'TRACK' else
                    self.controller.set_a_region(key, region))
        self._refresh_fields()
        return accepted

    def _add_field(self):
        self.pause()
        self.controller.add_field()

    def _rename(self, field_id):
        field = next((item for item in self.controller.fields if item['id'] == field_id), None)
        if field is None:
            return
        self.pause()
        name, accepted = QInputDialog.getText(self, '更改欄位名稱', '欄位名稱', text=field['name'])
        if accepted:
            self.controller.rename_field(field_id, name)

    def _remove(self, field_id):
        self.pause()
        self.controller.remove_field(field_id)

    def _set_row_pitch(self, value):
        self.pause()
        self.controller.set_row_pitch(value)

    def _auto_changed(self, enabled):
        self._cancel_countdown()
        self._clear_visuals()
        self.controller.set_auto_follow(enabled)
        self._refresh_fields()

    def _move_rows(self, delta):
        self._cancel_countdown()
        self._clear_visuals()
        self.controller.move_rows(delta)
        self._refresh_fields()

    def _mode_changed(self):
        self.pause()
        self.controller.set_mode(self._mode.currentData())
        self._date_equivalence.setEnabled(self._mode.currentData() != 'exact')

    def _date_mode_changed(self, enabled):
        self.pause()
        self.controller.set_date_equivalence(enabled)

    def _manual_refresh(self):
        if (self._closing or self._closed_by_user or self._selection_active
                or not self.controller.running):
            return
        self._cancel_countdown()
        self._clear_visuals()
        self.hide()
        self._recover_controls = True
        self.controller.refresh_now()
        self._refresh_fields()
        if self.controller.running:
            self._show_resume_control()

    def _select_candidate(self, field_id, candidate_id):
        self._clear_visuals()
        self.controller.select_candidate(field_id, candidate_id)

    def _calibrate_tracking(self):
        self.pause()
        self.avoid_regions()
        self._calibration_remaining = 3
        self._calibrate.setText('3 秒後校正…')
        self._note.setText('請回到表格選取一列；3 秒後記住目前的選取列。')
        self._calibration_timer.start()
        self._refresh_fields()

    def _calibration_tick(self):
        if self._closing or self._closed_by_user or self._selection_active:
            self._cancel_countdown()
            return
        self._calibration_remaining -= 1
        if self._calibration_remaining > 0:
            self._calibrate.setText(f'{self._calibration_remaining} 秒後校正…')
            return
        self._cancel_countdown()
        self.controller.calibrate_tracking()
        self._refresh_fields()

    def _start(self):
        if self._closing or self._closed_by_user or self._selection_active:
            return
        self._cancel_countdown()
        self._recover_controls = True
        self.hide()
        self.controller.start()
        if self._closing or self._closed_by_user or self._selection_active:
            return
        if self.controller.running:
            self._show_resume_control()
        else:
            self.show()
        self._refresh_fields()

    def _state_changed(self, state):
        if state in ('paused', 'idle', 'lost', 'ambiguous', 'failed', 'checking', 'reading',
                     'changed', 'tracking_pending', 'tracking_lost', 'calibrating', 'calibrated'):
            self._cancel_countdown()
            self._clear_visuals()
            self._status.setText('已暫停' if state in ('paused', 'idle') else '等待確認')
            self._status.setStyleSheet(f'font-size: 15px; font-weight: 600; color: {_WARNING};')
        self._activity.setText({'paused': '已暫停，標記已清除', 'idle': '尚未開始',
                                'calibrated': '已記住目前選取列', 'calibrating': '正在校正選取列…',
                                'checking': '確認畫面…', 'reading': '正在辨識目前這列…',
                                'running': '正在搜尋…', 'waiting': '等待下一次檢查…',
                                'tracking_pending': '等選取列穩定後重新搜尋…',
                                'tracking_lost': '找不到選取列，請重新選取或校正。',
                                'changed': '畫面已變，重新確認…', 'ready': '已更新目前結果',
                                'unchanged': '畫面未變，等待變化。'}.get(state, '請查看目前狀態'))
        self._refresh_fields()
        if not self.controller.running:
            self.resume_button.hide()
            self.manual_button.hide()

    def _failed(self, message):
        recover = self._recover_controls
        self._recover_controls = False
        self._clear_visuals()
        self._status.setText('需要確認')
        self._status.setStyleSheet(f'font-size: 15px; font-weight: 600; color: {_WARNING};')
        self._note.setText(message)
        self._refresh_fields()
        if (recover and not self.isVisible() and not self._closing
                and not self._closed_by_user and not self._selection_active):
            self.open()

    def _pause_with_error(self, message):
        # Internal display failures must restore an active compact session. An
        # explicit pause/mode switch, in contrast, revokes that recovery intent.
        self._recover_controls = self._recover_controls or self.controller.running
        self.pause(preserve_recovery=True)
        self._failed(message)

    def _updated(self, snapshot):
        if self._closing or self._closed_by_user or self._selection_active:
            return
        self._refresh_fields()
        fields = snapshot.get('fields', self.controller.fields)
        matched = sum((field.get('result') or {}).get('status') == 'matched' for field in fields)
        missing = sum((field.get('result') or {}).get('status') == 'missing' for field in fields)
        similar = sum(any(candidate.get('id') == (field.get('result') or {}).get('selected_id')
                          and candidate.get('match_type') in ('similar', 'numeric_similar')
                          for candidate in (field.get('result') or {}).get('candidates', [])) for field in fields)
        self._status.setText(f'找到 {matched}／{len(fields)}' + (f' · 相似 {similar}' if similar else '')
                             + (f' · 未找到 {missing}' if missing else ''))
        color = _SUCCESS if fields and matched == len(fields) else _ERROR if missing else _WARNING
        self._status.setStyleSheet(f'font-size: 15px; font-weight: 600; color: {color};')
        stamp = snapshot.get('timestamp')
        if stamp:
            try:
                self._activity.setText(f'{datetime.fromisoformat(stamp).strftime("%H:%M:%S")} · 第 {snapshot.get("cycle", 0)} 次搜尋')
            except (ValueError, TypeError):
                pass
        self._note.setText('綠色：找到 · 橘色：相似／待確認 · 紅色：未找到')
        b_region = snapshot.get('b_region', self.controller.b_region)
        marks = []
        if self.controller.running and b_region is not None:
            bx, by = b_region[:2]
            for field in fields:
                result = field.get('result') or {}
                selected = next((candidate for candidate in result.get('candidates', [])
                                 if candidate.get('id') == result.get('selected_id')), None)
                region = field.get('region')
                if result and region is not None:
                    boxes = selected.get('boxes') if selected is not None else []
                    if selected is not None and (not isinstance(boxes, (list, tuple)) or not boxes):
                        self._pause_with_error('辨識位置無效，請調整發票位置後重試。')
                        return
                    polygons = [self.highlight_overlay.validated_box(box) for box in boxes]
                    if any(polygon is None for polygon in polygons):
                        self._pause_with_error('辨識位置無效，請調整發票位置後重試。')
                        return
                    if selected is not None:
                        status = ('similar' if selected.get('match_type') in ('similar', 'numeric_similar') else
                                  'uncertain' if selected.get('needs_review') or result.get('status') != 'matched' else 'matched')
                    else:
                        status = result.get('status', 'uncertain')
                        if status == 'matched':
                            status = 'uncertain'
                    marks.append({'name': field['name'], 'status': status, 'a_text': result.get('text', ''),
                                  'anchor': tuple(region[:4]),
                                  'b_text': selected.get('text', '') if selected else '',
                                  'similarity': selected.get('similarity', selected.get('score')) if selected else None,
                                  'match_type': selected.get('match_type') if selected else None,
                                  'bounds': tuple(b_region[:4]),
                                  'boxes': [[[x + bx, y + by] for x, y in box] for box in polygons]})
        if not self.highlight_overlay.set_marks(marks):
            self._pause_with_error('文字標示位置無效，請重新框選後重試。')
        elif self.controller.running:
            self._show_resume_control()

    def _physical_window_rect(self):
        screen = QApplication.primaryScreen()
        if screen is None:
            return None
        frame, origin, scale = self.frameGeometry(), screen.geometry().topLeft(), screen.devicePixelRatio()
        return QRectF((frame.x() - origin.x()) * scale, (frame.y() - origin.y()) * scale,
                      frame.width() * scale, frame.height() * scale)

    def can_capture(self):
        if self._closing or self._closed_by_user or self._selection_active:
            return False
        if not self.isVisible() or self.isMinimized():
            return True
        frame = self._physical_window_rect()
        return frame is not None and not any(frame.intersects(QRectF(*region[:4]))
                                            for region in self.controller.target_regions if region is not None)

    def before_capture(self):
        if not self.can_capture():
            return False
        self._capture_epoch = self._visual_epoch
        self._capture_active = True
        self.resume_button.hide()
        self.manual_button.hide()
        self.highlight_overlay.hide_for_capture()
        return True

    def after_capture(self):
        if (self._capture_epoch == self._visual_epoch and not self._closing and not self._closed_by_user
                and not self._selection_active and self.controller.running):
            if not self.highlight_overlay.restore_after_capture():
                self._pause_with_error('無法顯示置頂文字，請重新開啟搜尋。')
        else:
            self.highlight_overlay.clear()
            self.highlight_overlay.restore_after_capture()
        self._capture_epoch = None
        self._capture_active = False
        if self.controller.running:
            self._show_resume_control()

    def _show_resume_control(self):
        if (self._closing or self._closed_by_user or self._selection_active
                or self._capture_active or not self.controller.running):
            return
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        overflow = self.highlight_overlay.overflow_count
        self.resume_button.setText(f'恢復介面 · {overflow} 筆未展開' if overflow else '恢復介面')
        self.resume_button.adjustSize()
        area = screen.availableGeometry()
        self.resume_button.move(area.right() - self.resume_button.width() - 10,
                                area.bottom() - self.resume_button.height() - 10)
        self.manual_button.adjustSize()
        x = self.resume_button.x() - self.manual_button.width() - 8
        y = self.resume_button.y()
        if x < area.left():
            x = self.resume_button.x()
            y -= self.manual_button.height() + 6
        self.manual_button.move(x, y)
        self.resume_button.show()
        self.manual_button.show()
        if not (apply_native_pin(self.resume_button, self._pin.isChecked())
                and apply_native_pin(self.manual_button, self._pin.isChecked())):
            self._pause_with_error('無法顯示浮動控制，請重新開啟搜尋。')

    def avoid_regions(self):
        if self.can_capture() or not self.isVisible():
            return self.can_capture()
        screen = QApplication.primaryScreen()
        if screen is None:
            return False
        available, frame = screen.availableGeometry(), self.frameGeometry()
        width, height = frame.width(), frame.height()
        candidates_x = {available.left(), available.right() - width + 1}
        candidates_y = {available.top(), available.bottom() - height + 1}
        scale, origin = screen.devicePixelRatio(), screen.geometry().topLeft()
        for x, y, w, h, _ in self.controller.target_regions:
            candidates_x.update((round(origin.x() + x / scale) - width - 8, round(origin.x() + (x + w) / scale) + 8))
            candidates_y.update((round(origin.y() + y / scale) - height - 8, round(origin.y() + (y + h) / scale) + 8))
        original = self.pos()
        for y in sorted(candidates_y):
            for x in sorted(candidates_x):
                if available.contains(frame.translated(x - frame.x(), y - frame.y())):
                    self.move(x, y)
                    if self.can_capture():
                        return True
        self.move(original)
        return False

    def set_pinned(self, pinned):
        with QSignalBlocker(self._pin):
            self._pin.setChecked(bool(pinned))
        if not self.highlight_overlay.set_pinned(pinned):
            self._pause_with_error('無法更新文字置頂，請重新開啟搜尋。')
        self.resume_button.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, bool(pinned))
        self.manual_button.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, bool(pinned))
        self._show_resume_control()

    def _bind_screen(self, screen, initial=False):
        if self._screen is not None:
            for signal in (self._screen.geometryChanged, self._screen.logicalDotsPerInchChanged):
                try:
                    signal.disconnect(self._screen_changed)
                except (TypeError, RuntimeError):
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
        self.pause(preserve_recovery=True)
        self.screen_invalidated.emit()
        self.controller.invalidate_regions('螢幕配置已變更，請重新框選輸入數值欄位、發票位置與輸入表格。')

    def open(self):
        if self._closing:
            return
        self._closed_by_user = False
        self.showNormal()
        self.raise_()
        self.activateWindow()
        if self.controller.running:
            self.avoid_regions()

    def closeEvent(self, event):
        self._closed_by_user = True
        self.pause()
        self.closed.emit()
        self._selection_active = False
        self.hide()
        event.accept()

    def shutdown(self):
        self._closing = True
        self.pause()
        self.controller.shutdown()
        self.highlight_overlay.clear()
        self.hide()

    @property
    def is_stopped(self):
        return self.controller.is_stopped
