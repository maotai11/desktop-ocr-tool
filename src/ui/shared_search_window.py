"""Session-only controls for multiple A fields searched in one shared B area."""
from __future__ import annotations

from datetime import datetime

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
        self._select = QPushButton('框選')
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
        self._name.setText(field.get('name', 'A'))
        self._region.setText(_region_text(field.get('region')))
        status = result.get('status')
        label, color = {
            'matched': ('已找到', _SUCCESS), 'missing': ('未找到', _ERROR),
            'ambiguous': ('多處符合，請選一處', _WARNING),
            'uncertain': ('需要確認', _WARNING),
        }.get(status, ('等待搜尋' if field.get('region') else '請框選位置', _TEXT_SEC))
        self._status.setText(label)
        self._status.setStyleSheet(f'color: {color};')
        text = result.get('text', '')
        self._text.setText(text[:240] + ('…' if len(text) > 240 else ''))
        self._text.setVisible(bool(text))
        candidates = result.get('candidates', [])
        selected = result.get('selected_id')
        signature = tuple((row.get('id'), row.get('text')) for row in candidates), selected
        if signature != self._signature:
            self._signature = signature
            with QSignalBlocker(self._candidates):
                self._candidates.clear()
                self._candidates.addItem('選擇符合位置…', None)
                for index, candidate in enumerate(candidates, 1):
                    preview = ' '.join(candidate.get('text', '').split())[:36]
                    self._candidates.addItem(f'位置 {index} · {preview}', candidate['id'])
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
        self.resume_button.hide()
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
        self._status = _label('先選 B，再新增 A 欄位')
        self._status.setStyleSheet('font-size: 15px; font-weight: 600;')
        title.addWidget(self._status, 1)
        self._pin = QCheckBox('標示置頂')
        self._pin.setToolTip('讓 A 欄下的文字與恢復介面按鈕置頂；設定面板維持一般視窗。')
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
        self._select_b = QPushButton('框選 B 範圍')
        self._select_b.clicked.connect(lambda: self._select('B'))
        body.addWidget(self._select_b)
        self._b_label = _label()
        self._b_label.setObjectName('muted')
        body.addWidget(self._b_label)
        self._add = QPushButton('新增 A 欄位')
        self._add.clicked.connect(self._add_field)
        body.addWidget(self._add)
        self._cards_layout = QVBoxLayout()
        self._cards_layout.setContentsMargins(0, 0, 0, 0)
        self._cards_layout.setSpacing(8)
        body.addLayout(self._cards_layout)
        self._select_tracking = QPushButton('框選表格範圍')
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
        self._row_pitch.setToolTip('請依表格實際列高設定；上一列與下一列會移動所有 A 欄位。')
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
        self._mode.addItem('數字嚴格／文字近似', 'normalized')
        self._mode.addItem('逐字完全相同', 'exact')
        self._mode.setToolTip('數字保留正負號、小數點、千分位、百分號與前導零。\n'
                              '文字忽略空白與標點，相似度至少 95% 才列為候選；近似候選仍需確認。\n'
                              '逐字完全相同會保留所有字元，停用近似搜尋。')
        apply_combo_box_theme(self._mode)
        self._mode.currentIndexChanged.connect(self._mode_changed)
        body.addWidget(self._mode)
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
        self._note = _label('開始後，辨識文字與核對狀態顯示在 A 欄下方。')
        self._note.setObjectName('muted')
        outer.addWidget(self._note)
        buttons = QHBoxLayout()
        self._start_button = QPushButton('開始搜尋')
        self._start_button.setObjectName('startSearch')
        self._start_button.clicked.connect(self._start)
        self._pause_button = QPushButton('暫停')
        self._pause_button.clicked.connect(self.pause)
        self._clear_button = QPushButton('清除結果')
        self._clear_button.clicked.connect(self.pause)
        buttons.addWidget(self._start_button)
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
            self._failed('目前請在主螢幕框選 A、B 與表格範圍。')
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
        self._status.setText(f'找到 {matched}／{len(fields)}' + (f' · 未找到 {missing}' if missing else ''))
        color = _SUCCESS if fields and matched == len(fields) else _ERROR if missing else _WARNING
        self._status.setStyleSheet(f'font-size: 15px; font-weight: 600; color: {color};')
        stamp = snapshot.get('timestamp')
        if stamp:
            try:
                self._activity.setText(f'{datetime.fromisoformat(stamp).strftime("%H:%M:%S")} · 第 {snapshot.get("cycle", 0)} 次搜尋')
            except (ValueError, TypeError):
                pass
        self._note.setText('辨識文字與核對狀態顯示在 A 欄下方。')
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
                        self._pause_with_error('辨識位置無效，請調整 B 範圍後重試。')
                        return
                    polygons = [self.highlight_overlay.validated_box(box) for box in boxes]
                    if any(polygon is None for polygon in polygons):
                        self._pause_with_error('辨識位置無效，請調整 B 範圍後重試。')
                        return
                    if selected is not None:
                        status = ('similar' if selected.get('match_type') == 'similar' else
                                  'uncertain' if selected.get('needs_review') or result.get('status') != 'matched' else 'matched')
                    else:
                        status = result.get('status', 'uncertain')
                        if status == 'matched':
                            status = 'uncertain'
                    marks.append({'name': field['name'], 'status': status, 'a_text': result.get('text', ''),
                                  'anchor': tuple(region[:4]),
                                  'b_text': selected.get('text', '') if selected else '',
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
        self.resume_button.show()
        if not apply_native_pin(self.resume_button, self._pin.isChecked()):
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
        self.controller.invalidate_regions('螢幕配置已變更，請重新框選 A、B 與表格範圍。')

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
