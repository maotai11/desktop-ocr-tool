"""Compact manual invoice checking; previews and calibration stay in memory."""
from __future__ import annotations

from datetime import datetime
import math
import html

from PySide6.QtCore import QPoint, QRectF, Qt, Signal, QSignalBlocker
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog,
    QDialogButtonBox, QFormLayout, QFrame, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from .invoice_check_overlay import InvoiceCheckOverlay, pin_without_activation, prevent_button_activation, set_native_noactivate, is_native_mouse_activation, activate_native_editor
from .theme import (_BG, _BG_RAISE, _BG_HOVER, _BORDER_L, _TEXT_PRI, _TEXT_SEC,
                    _ACCENT, _ACCENT_T, _SUCCESS, _ERROR, _WARNING, apply_combo_box_theme)

_LABELS = {'net_amount': '未稅金額', 'total_amount': '總金額', 'tax_amount': '稅額',
           'issue_date': '開立日期', 'invoice_number': '發票號碼',
           'seller_tax_id': '賣方統編', 'buyer_tax_id': '買方統編'}
_COLORS = {'matched': _SUCCESS, 'different': _ERROR, 'review': _WARNING, 'missing': _WARNING}
_STATES = {'matched': '相符', 'different': '不同', 'review': '待確認', 'missing': '缺欄'}
_STYLE = f'''QWidget {{ background:{_BG}; color:{_TEXT_PRI}; font-size:12px; }}
QPushButton,QComboBox,QSpinBox {{ background:{_BG_RAISE}; color:{_TEXT_PRI}; border:1px solid {_BORDER_L}; border-radius:5px; padding:4px 7px; }}
QPushButton:hover {{ background:{_BG_HOVER}; }} QPushButton:disabled {{ color:{_TEXT_SEC}; }}
QLabel {{ background:transparent; }} QScrollArea {{ border:0; }}
QFrame#sourceCard {{ border:1px solid {_BORDER_L}; border-radius:7px; }}
QFrame#resultCard {{ border:0; border-bottom:1px solid {_BORDER_L}; border-radius:0; }}
QPushButton#checkInvoice {{ background:{_ACCENT}; color:{_ACCENT_T}; font-weight:600; }}
QPushButton#checkInvoice:disabled {{ background:{_BG_RAISE}; color:{_TEXT_SEC}; }}
QScrollBar:vertical {{ background:transparent; width:6px; }}
QScrollBar::handle:vertical {{ background:{_BORDER_L}; border-radius:3px; min-height:24px; }}
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical {{ height:0; }}
QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical {{ background:transparent; }}'''


def _label(text='', muted=False):
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setMinimumWidth(0)
    if muted:
        label.setStyleSheet(f'color:{_TEXT_SEC}; font-size:11px;')
    return label


def _image(value):
    """Detach a bounded preview; no capture or model work is started here."""
    if isinstance(value, QImage):
        if value.isNull() or value.width() * value.height() > 16_000_000:
            raise ValueError('Invalid preview image size')
        result = value.copy()
        original = result.width(), result.height()
    else:
        import numpy as np
        array = np.asarray(value)
        if array.dtype != np.uint8 or array.ndim != 3 or array.shape[2] != 3:
            raise ValueError('Invalid preview image')
        height, width = array.shape[:2]
        if width < 1 or height < 1 or width * height > 16_000_000:
            raise ValueError('Invalid preview image size')
        original = width, height
        step = max(1, math.ceil(math.sqrt(width * height / 4_000_000)))
        array = np.ascontiguousarray(array[::step, ::step])
        result = QImage(array.data, array.shape[1], array.shape[0], array.strides[0], QImage.Format.Format_BGR888).copy()
    if result.isNull() or result.width() * result.height() > 16_000_000:
        raise ValueError('Invalid preview image')
    if result.width() * result.height() > 4_000_000:
        result = result.scaled(2000, 2000, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    result.setDevicePixelRatio(1)
    return result, original


class _CheckButton(QPushButton):
    """Keep native child-HWND mouse activation local to the check action."""
    def nativeEvent(self, event_type, message):
        if prevent_button_activation(message, [self]):
            return True, 3  # MA_NOACTIVATE, while still delivering the click.
        return super().nativeEvent(event_type, message)


class _ActionBar(QWidget):
    check_requested = Signal()
    stop_requested = Signal()
    setup_requested = Signal()

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setStyleSheet(_STYLE)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(7, 5, 7, 5)
        layout.setSpacing(5)
        self.status = _label('發票核對', True)
        layout.addWidget(self.status)
        self.check = _CheckButton('辨識核對')
        self.check.setObjectName('checkInvoice')
        self.stop = QPushButton('停止')
        self.setup = QPushButton('設定')
        for button in (self.check, self.stop, self.setup):
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            layout.addWidget(button)
        self.check.clicked.connect(self.check_requested)
        self.stop.clicked.connect(self.stop_requested)
        self.setup.clicked.connect(self.setup_requested)

    def reveal(self, pinned):
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, pinned)
        self.adjustSize()
        screen = QApplication.primaryScreen()
        if screen is None:
            return False
        area = screen.availableGeometry()
        self.move(area.right() - self.width() - 8, area.bottom() - self.height() - 8)
        # Set native NOACTIVATE while hidden, including a recreated HWND after
        # the pin flag changes. Applying it after show is too late on Windows.
        if not pin_without_activation(self, pinned):
            self.hide()
            return False
        self.show()
        return pin_without_activation(self, pinned)


class _CalibrationDialog(QDialog):
    def __init__(self, image, side, overrides, row, header, parent=None):
        super().__init__(parent)
        from .document_template_window import RegionCanvas
        self.setWindowTitle('校正欄位位置')
        self.setStyleSheet(_STYLE)
        self.resize(720, 520)
        self._side = side
        self._closed = False
        self.overrides = dict(overrides)
        self.row, self.header = row, header
        self._original_height = image.height()
        outer = QVBoxLayout(self)
        hint = _label('選擇欄位，再拖曳框選；未指定的欄位仍自動辨識。', True)
        hint.setWordWrap(True)
        outer.addWidget(hint)
        bar = QHBoxLayout()
        self.field = QComboBox()
        for key, label in _LABELS.items():
            self.field.addItem(label, key)
        if side == 'table':
            self.field.addItem('資料列備用範圍', '__row__')
            self.field.addItem('手動指定標題列', '__header__')
            hint.setText('欄位框指定欄的左右位置；備用列只在無法定位反藍列時使用。')
        apply_combo_box_theme(self.field)
        bar.addWidget(self.field, 1)
        self.reset = QPushButton('此欄恢復自動')
        self.reset.clicked.connect(self._reset)
        bar.addWidget(self.reset)
        self.reset_all = QPushButton('全部自動')
        self.reset_all.clicked.connect(self._reset_all)
        bar.addWidget(self.reset_all)
        outer.addLayout(bar)
        self.canvas = RegionCanvas(self)
        self.canvas.setAccessibleName('發票欄位校正預覽')
        self.canvas.set_image(image)
        self.canvas.region_drawn.connect(self._drawn)
        self.field.currentIndexChanged.connect(self._redraw)
        outer.addWidget(self.canvas, 1)
        zoom = QHBoxLayout()
        for text, action in [('縮小', self.canvas.zoom_out), ('放大', self.canvas.zoom_in), ('適合視窗', self.canvas.fit_page)]:
            button = QPushButton(text)
            button.clicked.connect(action)
            zoom.addWidget(button)
        outer.addLayout(zoom)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('套用校正')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        self._redraw()

    def _drawn(self, region):
        key = self.field.currentData()
        values = (region.x, region.y, region.width, region.height)
        if key == '__row__':
            self.row = (values[1], values[1] + values[3])
        elif key == '__header__':
            self.header = (values[1], values[1] + values[3])
        else:
            self.overrides[key] = values
        self._redraw()

    def _reset(self):
        key = self.field.currentData()
        if key == '__row__': self.row = None
        elif key == '__header__': self.header = None
        else: self.overrides.pop(key, None)
        self._redraw()

    def _reset_all(self):
        self.overrides.clear()
        self.row = self.header = None
        self._redraw()

    def _redraw(self):
        from ..documents.template import Region
        regions = {_LABELS[key]: Region(*value) for key, value in self.overrides.items()}
        if self.row:
            regions['資料列'] = Region(0, self.row[0], 1, self.row[1] - self.row[0])
        if self.header:
            regions['標題列'] = Region(0, self.header[0], 1, self.header[1] - self.header[0])
        self.canvas.set_regions(regions, self.field.currentText())

    def done(self, result):
        if self._closed: return
        self._closed = True
        self.canvas.clear_image('')
        super().done(result)


class InvoiceCheckWindow(QWidget):
    region_requested = Signal(str)
    closed = Signal()

    def __init__(self, controller, cfg=None, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.controller, self._cfg = controller, cfg
        self._closing = self._closed = self._selection_active = False
        self._wanted = self._temporary_hide = self._capturing = self._compact = False
        self._ocr_ready = False
        self._regions = dict(getattr(controller, 'regions', {}))
        self._previews, self._image_sizes = {}, {}
        self._overrides = {'invoice': {}, 'table': {}}
        self._manual_row = self._header_row = None
        self._calibration = None
        self._snapshot = None
        self._accept_results = False
        self._visibility_generation = 0
        self.overlay = InvoiceCheckOverlay()
        self.action_bar = _ActionBar()
        self.destroyed.connect(self.overlay.deleteLater)
        self.destroyed.connect(self.action_bar.deleteLater)
        self.setWindowTitle('發票核對')
        self.setMinimumSize(320, 480)
        self.resize(348, 560)
        self.setStyleSheet(_STYLE)
        self._build()
        self.action_bar.check_requested.connect(self._check)
        self.action_bar.stop_requested.connect(self.stop)
        self.action_bar.setup_requested.connect(self.open)
        self._connections = ((controller.state_changed, self._state_changed),
                             (controller.results_ready, self._results_ready),
                             (controller.preview_ready, self._preview_ready),
                             (controller.regions_changed, self._regions_changed),
                             (controller.failed, self._failed),
                             (controller.capture_started, self._capture_started),
                             (controller.capture_finished, self._capture_finished))
        for signal, slot in self._connections:
            signal.connect(slot)
        self._refresh()

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(6)
        self._status = _label('框選發票與目前資料列')
        self._status.setWordWrap(True)
        outer.addWidget(self._status)
        scroll = QScrollArea()
        self._content_scroll = scroll
        scroll.setWidgetResizable(True)
        scroll.setMinimumHeight(0)
        content = QWidget()
        body = QVBoxLayout(content)
        body.setContentsMargins(0, 0, 3, 0)
        body.setSpacing(6)
        sources = QHBoxLayout()
        sources.setSpacing(6)
        self._preview_labels, self._select_buttons, self._calibrate_buttons = {}, {}, {}
        for side, title in [('invoice', '發票位置'), ('table', '目前資料列')]:
            card = QFrame()
            card.setObjectName('sourceCard')
            layout = QVBoxLayout(card)
            layout.setContentsMargins(6, 5, 6, 5)
            layout.setSpacing(3)
            label = _label(title)
            label.setStyleSheet('font-weight:600;')
            layout.addWidget(label)
            preview = _label('尚未框選', True)
            preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
            preview.setFixedHeight(44)
            preview.setToolTip('請在主要螢幕框選。這是靜態預覽；辨識核對會重新擷取畫面。')
            layout.addWidget(preview)
            buttons = QHBoxLayout()
            buttons.setSpacing(4)
            select = QPushButton('框選')
            select.clicked.connect(lambda checked=False, chosen=side: self._select(chosen))
            buttons.addWidget(select)
            calibrate = QPushButton('校正')
            calibrate.clicked.connect(lambda checked=False, chosen=side: self._calibrate(chosen))
            buttons.addWidget(calibrate)
            layout.addLayout(buttons)
            sources.addWidget(card, 1)
            self._preview_labels[side], self._select_buttons[side], self._calibrate_buttons[side] = preview, select, calibrate
        body.addLayout(sources)
        instruction = _label('資料列範圍請包含欄名；反藍儲存格只用來定位整列。', True)
        instruction.setWordWrap(True)
        body.addWidget(instruction)
        self._snapshot_label = _label('本次截圖 · 尚未辨識', True)
        body.addWidget(self._snapshot_label)
        self._result_rows = []
        for key in ('net_amount', 'tax_amount', 'issue_date', 'invoice_number', 'seller_tax_id'):
            card = QFrame()
            card.setObjectName('resultCard')
            row = QVBoxLayout(card)
            row.setContentsMargins(6, 3, 6, 3)
            row.setSpacing(0)
            heading = QHBoxLayout()
            name, state = _label(_LABELS[key]), _label('待辨識', True)
            heading.addWidget(name, 1)
            heading.addWidget(state)
            row.addLayout(heading)
            values = _label('發票 —  ／  資料列 —', True)
            row.addWidget(values)
            body.addWidget(card)
            self._result_rows.append((name, state, values))
        self._advanced_button = QPushButton('進階選項 ▸')
        self._advanced_button.setCheckable(True)
        body.addWidget(self._advanced_button)
        self._advanced = QWidget()
        advanced = QFormLayout(self._advanced)
        advanced.setContentsMargins(0, 0, 0, 0)
        self._amount = QComboBox()
        self._amount.addItem('未稅金額', 'net_amount')
        self._amount.addItem('總金額', 'total_amount')
        self._tax_id = QComboBox()
        self._tax_id.addItem('賣方統編', 'seller_tax_id')
        self._tax_id.addItem('買方統編', 'buyer_tax_id')
        for combo in (self._amount, self._tax_id):
            apply_combo_box_theme(combo)
            combo.currentIndexChanged.connect(self._field_options)
        advanced.addRow('核對金額', self._amount)
        advanced.addRow('核對統編', self._tax_id)
        body.addWidget(self._advanced)
        self._advanced.hide()
        self._advanced_button.toggled.connect(self._advanced.setVisible)
        self._advanced_button.toggled.connect(lambda value: self._advanced_button.setText('進階選項 ▾' if value else '進階選項 ▸'))
        body.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)
        appearance = QHBoxLayout()
        self._pin = QCheckBox('標示置頂')
        self._pin.setChecked(self._read_setting('overlay_on_top', True) is not False)
        self._pin.setToolTip('文字標示及核對小工具置頂；設定視窗仍可正常編輯。')
        self._pin.toggled.connect(self._pin_changed)
        appearance.addWidget(self._pin)
        appearance.addWidget(_label('背景透明', True))
        self._transparency = QSpinBox()
        self._transparency.setRange(0, 100)
        value = self._read_setting('overlay_transparency', 100)
        self._transparency.setValue(value if type(value) is int and 0 <= value <= 100 else 100)
        self._transparency.setSuffix(' %')
        self._transparency.setToolTip('只調整標示底板；文字保持清楚。100% 不顯示底板。')
        self._transparency.valueChanged.connect(self._transparency_changed)
        appearance.addWidget(self._transparency, 1)
        self._compact_button = QPushButton('收起')
        self._compact_button.setToolTip('收起設定，保留不搶焦點的辨識核對小工具；可先回來源軟體選定資料列。')
        self._compact_button.clicked.connect(self._show_compact)
        appearance.addWidget(self._compact_button)
        outer.addLayout(appearance)
        buttons = QHBoxLayout()
        self._check_button = _CheckButton('辨識核對')
        self._check_button.setObjectName('checkInvoice')
        self._check_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._check_button.setToolTip('只擷取並核對這一次。請先在來源軟體選定資料列。')
        self._check_button.clicked.connect(self._check)
        self._stop_button, self._clear_button = QPushButton('停止'), QPushButton('清除')
        self._stop_button.clicked.connect(self.stop)
        self._clear_button.clicked.connect(self.clear_results)
        buttons.addWidget(self._check_button, 1)
        buttons.addWidget(self._stop_button)
        buttons.addWidget(self._clear_button)
        outer.addLayout(buttons)
        self.overlay.set_pinned(self._pin.isChecked())
        self.overlay.set_background_transparency(self._transparency.value())

    def _read_setting(self, key, default):
        return self._cfg.get('invoice_check', key, default=default) if self._cfg is not None else default

    def _save_setting(self, key, value):
        if self._cfg is not None:
            update = getattr(self._cfg, 'update_section', None)
            if callable(update): update('invoice_check', {key: value})
            else: self._cfg.set('invoice_check', key, value)

    def _pin_changed(self, enabled):
        if not self._live():
            return
        try:
            self._save_setting('overlay_on_top', enabled)
        except (OSError, ValueError):
            with QSignalBlocker(self._pin): self._pin.setChecked(not enabled)
            self._status.setText('置頂設定未儲存，請稍後再試。')
            return
        if not self.overlay.set_pinned(enabled):
            self._failed('無法更新標示置頂。')
        self._restore_controls()

    def _transparency_changed(self, value):
        if not self._live():
            return
        old = self.overlay.background_transparency
        try:
            self._save_setting('overlay_transparency', value)
        except (OSError, ValueError):
            with QSignalBlocker(self._transparency): self._transparency.setValue(old)
            self._status.setText('透明度設定未儲存，請稍後再試。')
            return
        self.overlay.set_background_transparency(value)

    def _live(self):
        return not self._closing and not self._closed and self._wanted

    def _refresh(self):
        busy = bool(getattr(self.controller, 'busy', False))
        configured = all(side in self._regions for side in ('invoice', 'table'))
        enabled = self._ocr_ready and configured and not busy and not self._selection_active and not self._closing and not self._closed
        self._check_button.setEnabled(enabled)
        self.action_bar.check.setEnabled(enabled)
        self._stop_button.setEnabled(busy and not self._closing)
        self.action_bar.stop.setEnabled(busy and not self._closing)
        self._clear_button.setEnabled(self._snapshot is not None or busy)
        for side in ('invoice', 'table'):
            self._calibrate_buttons[side].setEnabled(side in self._previews and not busy)
            self._select_buttons[side].setText('重選' if side in self._regions else '框選')
            self._select_buttons[side].setEnabled(not busy)

    def set_ocr_ready(self, ready=True):
        if not self._closing:
            self._ocr_ready = bool(ready)
            self._refresh()

    def _select(self, side):
        if not self._live(): return
        self.stop()
        if self._live(): self.region_requested.emit(side)

    def set_selection_active(self, active):
        if self._closing or self._closed: return
        self._selection_active = bool(active)
        if active:
            self.controller.cancel()
            self._hide_temporarily()
        else:
            self.open()
        self._refresh()

    def _regions_changed(self, regions):
        if self._closing: return
        if not isinstance(regions, dict):
            regions = dict(getattr(self.controller, 'regions', {}))
        for side in ('invoice', 'table'):
            if self._regions.get(side) != regions.get(side):
                self._previews.pop(side, None)
                self._image_sizes.pop(side, None)
                self._overrides[side].clear()
                self._preview_labels[side].clear()
                self._preview_labels[side].setText('已框選，等待預覽' if side in regions else '尚未框選')
                if side == 'table': self._manual_row = self._header_row = None
        self._regions = dict(regions)
        self._accept_results = False
        self._retire_calibration()
        self._reset_rows()
        self.overlay.clear()
        self._snapshot = None
        self._refresh()

    def _preview_ready(self, side, value):
        if not self._live() or side not in ('invoice', 'table'): return
        try:
            image, size = _image(value)
        except (ValueError, TypeError):
            self._failed('無法顯示範圍預覽，請重新框選。')
            return
        self._previews[side], self._image_sizes[side] = image, size
        self._preview_labels[side].setPixmap(QPixmap.fromImage(image).scaled(136, 44, Qt.AspectRatioMode.KeepAspectRatio,
                                                                         Qt.TransformationMode.SmoothTransformation))
        self._refresh()

    def _calibrate(self, side):
        if not self._live() or side not in self._previews or getattr(self.controller, 'busy', False): return
        if self._calibration is not None:
            self._calibration.raise_()
            self._calibration.activateWindow()
            return
        self.controller.cancel()
        if not self._live(): return
        dialog = _CalibrationDialog(self._previews[side], side, self._overrides[side],
                                    self._manual_row if side == 'table' else None,
                                    self._header_row if side == 'table' else None, self)
        self._calibration = dialog
        lease = self._visibility_generation
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        self._calibration = None
        if not accepted or not self._live() or lease != self._visibility_generation: return
        if (side == 'table' and dialog.row and dialog.header and dialog.header[1] > dialog.row[0]):
            self._status.setText('標題列須在資料列上方，請重新校正。')
            return
        if not self.controller.set_overrides(side, dialog.overrides): return
        self._overrides[side] = dict(dialog.overrides)
        if side == 'table' and self._live():
            height = self._image_sizes[side][1]
            row = tuple(round(value * height) for value in dialog.row) if dialog.row else None
            header = tuple(round(value * height) for value in dialog.header) if dialog.header else None
            if self.controller.set_manual_row(row) and self._live():
                self.controller.set_header_row(header)
                self._manual_row, self._header_row = dialog.row, dialog.header
        if self._live(): self._status.setText('校正已套用；按辨識核對重新取圖。')

    def _field_options(self):
        if self._closing or self._closed: return
        self.controller.set_field_options(self._amount.currentData(), self._tax_id.currentData())
        if not self._live(): return
        self._result_rows[0][0].setText(_LABELS[self._amount.currentData()])
        self._result_rows[-1][0].setText(_LABELS[self._tax_id.currentData()])
        self.clear_results()

    def _check(self):
        if not self._live() or not self._check_button.isEnabled(): return
        self._compact = True
        self._accept_results = True
        self._hide_temporarily()
        accepted = self.controller.check_once()
        if accepted is False:
            self._accept_results = False
            self.overlay.finish_capture(False)
        self._refresh()
        self._restore_controls()

    def _hide_temporarily(self):
        self._temporary_hide = True
        try: self.hide()
        finally: self._temporary_hide = False
        self.action_bar.hide()
        self.overlay.hide_for_capture()

    def _capture_started(self):
        if not self._live(): return
        self._capturing = True
        self._retire_calibration()
        self._reset_rows()
        self.overlay.clear()
        self._snapshot = None
        self._hide_temporarily()
        self._refresh()

    def _capture_finished(self):
        self._capturing = False
        self.overlay.finish_capture(False)
        self._restore_controls()

    def _state_changed(self, state, message):
        if not self._live(): return
        self._status.setText(message)
        if state != 'ready' or self._snapshot is None:
            self.action_bar.status.setText(message[:18])
            self.action_bar.status.setStyleSheet(f'color:{_TEXT_SEC}; font-size:11px;')
            self.action_bar.status.setToolTip(message)
        self._refresh()
        self._restore_controls()

    def _restore_controls(self):
        if not self._live() or self._selection_active or self._capturing: return
        if getattr(self.controller, 'state', '') in ('capturing', 'verifying'): return
        if self._compact:
            self.action_bar.reveal(self._pin.isChecked())
        elif not self.isVisible():
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
            self.show()
        if self._snapshot is not None:
            self.overlay.restore()

    def _results_ready(self, snapshot):
        if not self._live() or not self._accept_results: return
        if (not isinstance(snapshot, dict) or not isinstance(snapshot.get('checks'), list)
                or len(snapshot['checks']) != 5 or any(not isinstance(item, dict) for item in snapshot['checks'])):
            self._failed('核對結果無效，請重新辨識。')
            return
        self._accept_results = False
        self._snapshot = snapshot
        checks = snapshot['checks']
        for index, (name, state, values) in enumerate(self._result_rows):
            item = checks[index] if index < len(checks) else {}
            status = item.get('status', 'missing')
            name.setText(str(item.get('label', '欄位')))
            state.setText(_STATES.get(status, '待確認'))
            state.setStyleSheet(f'color:{_COLORS.get(status, _WARNING)}; font-size:11px;')
            invoice, row = str((item.get('invoice') or {}).get('raw') or '—'), str((item.get('row') or {}).get('raw') or '—')
            text = f'發票 {invoice}  ／  資料列 {row}'
            values.setText(values.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, max(100, self.width() - 44)))
            values.setToolTip('<qt>' + html.escape(text + '\n' + str(item.get('message', ''))).replace('\n', '<br>') + '</qt>')
        stamp = snapshot.get('captured_at_ns')
        try:
            moment = datetime.fromtimestamp(stamp / 1_000_000_000).strftime('%H:%M:%S') if type(stamp) is int else ''
        except (ValueError, OSError, OverflowError):
            moment = ''
        self._snapshot_label.setText('本次截圖' + (f' · {moment}' if moment else ''))
        if not self.overlay.set_snapshot(snapshot):
            self._failed('核對位置無效，請重新框選後再試。')
            return
        self._status.setText('本次核對完成，請確認顏色與原文。')
        overflow = self.overlay.overflow_count
        statuses = [item.get('status') for item in checks]
        summary, color = ('有差異', _ERROR) if 'different' in statuses else (
            ('待確認', _WARNING) if any(status != 'matched' for status in statuses) else ('本次相符', _SUCCESS))
        self.action_bar.status.setText(summary + (f' · {overflow}項未標示' if overflow else ''))
        self.action_bar.status.setStyleSheet(f'color:{color}; font-size:11px;')
        self.action_bar.status.setToolTip('本次截圖結果。未標示欄位請回設定視窗查看。')
        self._refresh()
        self._restore_controls()

    def _failed(self, message):
        if not self._live(): return
        self.overlay.clear()
        self._accept_results = False
        self._snapshot = None
        self._reset_rows()
        self._status.setText(message)
        self.action_bar.status.setText('需要確認')
        self.action_bar.status.setToolTip(message)
        self._refresh()
        self._restore_controls()

    def stop(self):
        if self._closing or self._closed: return
        self._accept_results = False
        self.controller.cancel()
        self._capturing = False
        self.overlay.finish_capture(False)
        self.overlay.clear()
        self._snapshot = None
        self._reset_rows()
        self._refresh()
        self._restore_controls()

    def _reset_rows(self):
        for _, state, values in self._result_rows:
            state.setText('待辨識')
            state.setStyleSheet(f'color:{_TEXT_SEC}; font-size:11px;')
            values.setText('發票 —  ／  資料列 —')
            values.setToolTip('')
        self._snapshot_label.setText('本次截圖 · 尚未辨識')

    def _retire_calibration(self):
        if self._calibration is not None:
            self._visibility_generation += 1
            self._calibration.reject()

    def clear_results(self):
        if self._closing or self._closed: return
        self.stop()
        if self._closing or self._closed: return
        for _, state, values in self._result_rows:
            state.setText('待辨識')
            state.setStyleSheet(f'color:{_TEXT_SEC}; font-size:11px;')
            values.setText('發票 —  ／  資料列 —')
            values.setToolTip('')
        self._snapshot_label.setText('本次截圖 · 已清除')
        self._status.setText('已清除本次結果；框選位置保留。')

    def _show_compact(self):
        if not self._live(): return
        self._compact = True
        self._hide_temporarily()
        self.overlay.finish_capture(self._snapshot is not None and self._live())
        self._restore_controls()

    def open(self):
        if self._closing: return
        self._visibility_generation += 1
        self._retire_calibration()
        self._closed = False
        self._wanted = True
        self._compact = False
        self._accept_results = False
        if getattr(self.controller, 'busy', False): self.controller.cancel()
        if self._closing or self._closed or not self._wanted: return
        self._selection_active = False
        self.overlay.finish_capture(False)
        set_native_noactivate(self, True)
        self.showNormal()
        self._fit_to_available_screen()
        self.raise_()
        self.activateWindow()
        activate_native_editor(self)
        self.action_bar.hide()
        if self._snapshot is None:
            self._status.setText('按辨識核對取得本次畫面。' if len(self._regions) == 2 else '框選發票與目前資料列')
        self._refresh()

    def _available_geometry(self):
        screen = self.screen() or QApplication.primaryScreen()
        return screen.availableGeometry() if screen is not None else None

    def _fit_to_available_screen(self):
        available = self._available_geometry()
        if available is None or available.isEmpty():
            return
        frame = self.frameGeometry()
        extra_width = max(0, frame.width() - self.width())
        extra_height = max(0, frame.height() - self.height())
        maximum_width = max(1, available.width() - extra_width)
        maximum_height = max(1, available.height() - extra_height)
        # Source previews/results already scroll; keep actions outside that
        # viewport and allow a short desktop to resize below the normal minimum.
        self.setMinimumSize(min(320, maximum_width), min(480, maximum_height))
        self.resize(min(self.width(), maximum_width), min(self.height(), maximum_height))
        frame = self.frameGeometry()
        x = max(available.left(), min(frame.left(), available.right() - frame.width() + 1))
        y = max(available.top(), min(frame.top(), available.bottom() - frame.height() + 1))
        if frame.topLeft() != QPoint(x, y):
            self.move(self.pos() + QPoint(x, y) - frame.topLeft())

    def setVisible(self, visible):
        if visible and hasattr(self, '_check_button'):
            set_native_noactivate(self, True)
        super().setVisible(visible)
        if visible and hasattr(self, '_check_button'):
            set_native_noactivate(self, True)

    def showEvent(self, event):
        # A native passive HWND prevents Windows clearing the source foreground
        # before WM_MOUSEACTIVATE. Explicit editing/opening activates below.
        set_native_noactivate(self, True)
        if not self._closing and not self._temporary_hide:
            self._closed = False
            self._wanted = True
            self._refresh()
        super().showEvent(event)
        self._fit_to_available_screen()

    def _retire_visibility(self):
        self._visibility_generation += 1
        self._wanted = False
        self._accept_results = False
        self._retire_calibration()
        self.controller.cancel()
        self.action_bar.hide()
        self.overlay.clear()
        self._snapshot = None
        self._reset_rows()

    def hide(self):
        # A compact/capturing tool is already hidden. An explicit owner hide
        # still revokes its pending operation; Qt would emit no new hideEvent.
        if hasattr(self, '_wanted') and not self._temporary_hide and not self._closing:
            self._retire_visibility()
        super().hide()

    def hideEvent(self, event):
        if not self._temporary_hide and not self._closing and self._wanted:
            self._retire_visibility()
        super().hideEvent(event)

    def closeEvent(self, event):
        self._visibility_generation += 1
        self._closed = True
        self._wanted = False
        self._accept_results = False
        self._selection_active = False
        self._retire_calibration()
        self.controller.cancel()
        self.action_bar.hide()
        self.overlay.clear()
        self._snapshot = None
        self._reset_rows()
        self.closed.emit()
        event.accept()

    def shutdown(self):
        if self._closing: return
        self._closing = True
        self._wanted = False
        self._closed = True
        self._accept_results = False
        self._visibility_generation += 1
        self._retire_calibration()
        self.controller.shutdown()
        for signal, slot in self._connections:
            try: signal.disconnect(slot)
            except (TypeError, RuntimeError): pass
        self.action_bar.hide()
        self.overlay.clear()
        self._previews.clear()
        for preview in self._preview_labels.values(): preview.clear()
        self.hide()

    @property
    def is_stopped(self):
        return self.controller.is_stopped

    def nativeEvent(self, event_type, message):
        if prevent_button_activation(message, [self._check_button]):
            return True, 3
        if is_native_mouse_activation(message):
            self.activateWindow()
            activate_native_editor(self)
            return True, 1  # MA_ACTIVATE: intentional editing, click retained.
        return super().nativeEvent(event_type, message)
