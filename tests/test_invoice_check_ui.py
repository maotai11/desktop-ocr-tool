"""Real Qt manual controls, preview/calibration and terminal-state behavior."""
from copy import deepcopy
import time

import pytest
from PySide6.QtCore import QObject, QPoint, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from src.ui.invoice_check_window import InvoiceCheckWindow


class Controller(QObject):
    state_changed = Signal(str, str)
    results_ready = Signal(object)
    preview_ready = Signal(str, object)
    regions_changed = Signal(object)
    failed = Signal(str)
    capture_started = Signal()
    capture_finished = Signal()

    def __init__(self):
        super().__init__()
        self.regions = {'invoice': (300, 30, 400, 600, 1), 'table': (10, 60, 250, 200, 1)}
        self.busy = False
        self.state = 'idle'
        self.is_stopped = True
        self.checks = self.cancels = 0
        self.overrides = []
        self.rows = []
        self.headers = []
        self.options = []

    def cancel(self):
        self.cancels += 1
        self.busy = False
        self.state = 'idle'
        self.state_changed.emit('idle', '已停止')

    def shutdown(self):
        self.cancel()

    def check_once(self):
        self.checks += 1
        self.busy = True
        self.state = 'capturing'
        self.capture_started.emit()
        self.state_changed.emit('capturing', '擷取本次畫面')
        return True

    def reading(self):
        self.state = 'reading'
        self.capture_finished.emit()
        self.state_changed.emit('reading', '辨識本次畫面')

    def complete(self, result):
        self.results_ready.emit(result)
        self.busy = False
        self.state = 'ready'
        self.state_changed.emit('ready', '本次核對完成')

    def set_overrides(self, side, value):
        self.overrides.append((side, value))
        return True

    def set_manual_row(self, value):
        self.rows.append(value)
        return True

    def set_header_row(self, value):
        self.headers.append(value)
        return True

    def set_field_options(self, *values):
        self.options.append(values)
        return True


class Config:
    def __init__(self): self.values = {}
    def get(self, *keys, default=None): return self.values.get(tuple(keys), default)
    def set(self, *keys): self.values[tuple(keys[:-1])] = keys[-1]


def picture(width=240, height=160):
    value = QImage(width, height, QImage.Format.Format_RGB32)
    value.fill(QColor('white'))
    return value


def snapshot():
    keys = ['net_amount', 'tax_amount', 'issue_date', 'invoice_number', 'seller_tax_id']
    return {'checks': [{'key': key, 'label': key, 'status': 'matched' if index != 1 else 'different',
                        'invoice': {'raw': '7000', 'value': '7000', 'box': [20, 20 + index * 100, 100, 20]},
                        'row': {'raw': '7001' if index == 1 else '7000', 'value': '7000'}, 'message': '原文'}
                       for index, key in enumerate(keys)],
            'regions': {'invoice': (300, 30, 400, 600, 1), 'table': (10, 60, 250, 200, 1)},
            'captured_at_ns': time.time_ns(), 'generation': 1, 'kind': 'manual_snapshot'}


@pytest.fixture
def setup(qtbot):
    controller, cfg = Controller(), Config()
    window = InvoiceCheckWindow(controller, cfg)
    qtbot.addWidget(window)
    window.open()
    window.set_ocr_ready(True)
    yield window, controller, cfg
    window.shutdown()


def test_open_and_preview_never_start_ocr_or_write_settings(setup, qtbot):
    window, controller, cfg = setup
    assert controller.checks == 0 and cfg.values == {}
    controller.preview_ready.emit('invoice', picture())
    controller.preview_ready.emit('table', picture())
    assert window._calibrate_buttons['invoice'].isEnabled()
    assert window._preview_labels['invoice'].pixmap() is not None
    assert controller.checks == 0 and cfg.values == {}
    assert not window._amount.isVisible()
    assert window._pin.isChecked() and window._transparency.value() == 100


def test_manual_check_hides_itself_for_both_captures_and_keeps_one_snapshot(setup):
    window, controller, cfg = setup
    window._check_button.click()
    assert controller.checks == 1 and not window.isVisible() and not window.action_bar.isVisible()
    controller.reading()
    assert window.action_bar.isVisible() and not window.action_bar.check.isEnabled()
    controller.state = 'verifying'
    controller.capture_started.emit()
    assert not window.action_bar.isVisible() and not window.overlay.isVisible()
    controller.capture_finished.emit()
    controller.complete(snapshot())
    assert controller.checks == 1 and window.action_bar.isVisible()
    assert len(window.overlay.marks) == 5 and window.overlay.isVisible()
    assert window._result_rows[1][1].text() == '不同'
    assert window._result_rows[0][1].text() == '相符'
    assert window._snapshot_label.text().startswith('本次截圖')
    assert window.action_bar.check.focusPolicy() == Qt.FocusPolicy.NoFocus


@pytest.mark.parametrize('retire', ['hide', 'close', 'shutdown'])
def test_late_capture_and_results_cannot_reopen_retired_ui(setup, retire):
    window, controller, _ = setup
    window._check_button.click()
    getattr(window, retire)()
    controller.capture_finished.emit()
    controller.complete(snapshot())
    assert not window.overlay.isVisible() and not window.action_bar.isVisible() and not window.isVisible()
    assert window._snapshot is None


def test_close_reopen_requires_a_new_manual_request_and_rejects_old_result(setup):
    window, controller, _ = setup
    window._check_button.click()
    window.close()
    window.open()
    controller.complete(snapshot())
    assert window.isVisible() and window._snapshot is None and not window.overlay.marks
    assert controller.checks == 1
    window._check_button.click()
    controller.reading()
    controller.complete(snapshot())
    assert controller.checks == 2 and window._snapshot is not None


def test_owner_hide_rejects_real_calibration_without_applying_draft(setup):
    window, controller, _ = setup
    controller.preview_ready.emit('invoice', picture())
    QTimer.singleShot(0, window.hide)
    window._calibrate('invoice')
    assert window._calibration is None and not controller.overrides
    assert QApplication.activeModalWidget() is None


def test_real_canvas_drag_applies_optional_normalized_field_calibration(setup, qtbot):
    window, controller, _ = setup
    controller.preview_ready.emit('invoice', picture())

    def draw_and_accept():
        dialog = window._calibration
        view = dialog.canvas
        first, last = view.mapFromScene(20, 30), view.mapFromScene(140, 70)
        qtbot.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=first)
        qtbot.mouseMove(view.viewport(), last)
        qtbot.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=last)
        assert dialog.overrides
        dialog.accept()

    QTimer.singleShot(0, draw_and_accept)
    window._calibrate('invoice')
    side, overrides = controller.overrides[-1]
    assert side == 'invoice' and 'net_amount' in overrides
    assert all(0 <= value <= 1 for value in overrides['net_amount'])
    assert controller.checks == 0


def test_invalid_header_order_never_partially_applies_calibration(setup):
    window, controller, _ = setup
    controller.preview_ready.emit('table', picture())

    def invalid_and_accept():
        dialog = window._calibration
        dialog.row, dialog.header = (.2, .4), (.3, .5)
        dialog.accept()

    QTimer.singleShot(0, invalid_and_accept)
    window._calibrate('table')
    assert not controller.overrides and not controller.rows and not controller.headers


def test_background_transparency_never_changes_text_opacity_or_restarts_work(setup):
    window, controller, cfg = setup
    window._check_button.click()
    controller.reading()
    controller.complete(snapshot())
    window.open()
    window._transparency.setValue(30)
    assert window.overlay.background_transparency == 30 and window.overlay.windowOpacity() == 1
    assert cfg.get('invoice_check', 'overlay_transparency') == 30 and controller.checks == 1
    window._show_compact()
    assert window.overlay.isVisible()
    window._clear_button.click()
    assert not window.overlay.marks and window._snapshot is None


def test_changing_region_drops_old_preview_and_result_without_ocr(setup):
    window, controller, _ = setup
    controller.preview_ready.emit('invoice', picture())
    controller.regions['invoice'] = (320, 30, 400, 600, 1)
    controller.regions_changed.emit(deepcopy(controller.regions))
    assert 'invoice' not in window._previews and controller.checks == 0
    assert '等待預覽' in window._preview_labels['invoice'].text()


@pytest.mark.parametrize('size', [(348, 560), (320, 480)])
def test_control_window_stays_compact_with_readable_manual_actions(setup, size):
    window, controller, _ = setup
    window.resize(*size)
    assert (window.width(), window.height()) == size
    for button in (window._check_button, window._stop_button, window._clear_button):
        assert button.isVisible() and button.width() >= button.fontMetrics().horizontalAdvance(button.text())


def test_short_desktop_keeps_manual_actions_visible_and_content_scrollable(setup, monkeypatch, qtbot):
    from PySide6.QtCore import QRect
    window, controller, _ = setup
    monkeypatch.setattr(window, '_available_geometry', lambda: QRect(0, 0, 512, 360))
    window.open()
    qtbot.wait(1)
    assert window.frameGeometry().top() >= 0
    assert window.frameGeometry().bottom() < 360
    assert window.height() < 480
    for button in (window._check_button, window._stop_button, window._clear_button):
        bounds = QRect(button.mapTo(window, QPoint()), button.size())
        assert button.isVisible() and window.rect().contains(bounds)
    scroll = window._content_scroll
    assert scroll.verticalScrollBar().maximum() > 0
    last_value = window._result_rows[-1][2]
    scroll.ensureWidgetVisible(last_value)
    qtbot.wait(1)
    assert scroll.verticalScrollBar().value() > 0
    bounds = QRect(last_value.mapTo(scroll.viewport(), QPoint()), last_value.size())
    assert scroll.viewport().rect().intersects(bounds)
    scroll.ensureWidgetVisible(window._select_buttons['invoice'])
    qtbot.wait(1)
    bounds = QRect(window._select_buttons['invoice'].mapTo(scroll.viewport(), QPoint()),
                   window._select_buttons['invoice'].size())
    assert scroll.viewport().rect().contains(bounds)
    assert controller.checks == 0


def test_compact_tool_prepares_nonactivation_before_first_and_recreated_show(setup, monkeypatch):
    from src.ui import invoice_check_window as module
    window, _, _ = setup
    calls = []
    monkeypatch.setattr(module, 'pin_without_activation',
                        lambda widget, enabled: calls.append((widget.isVisible(), enabled)) or True)
    bar = window.action_bar
    assert bar.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
    assert not window.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
    assert bar.reveal(True)
    assert calls == [(False, True), (True, True)]
    calls.clear()
    assert bar.reveal(False)
    assert calls == [(False, False), (True, False)]
    assert bar.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
    assert bar.check.isEnabled()


def test_compact_tool_does_not_show_when_native_nonactivation_setup_fails(setup, monkeypatch):
    from src.ui import invoice_check_window as module
    monkeypatch.setattr(module, 'pin_without_activation', lambda *a: False)
    assert not setup[0].action_bar.reveal(True)
    assert not setup[0].action_bar.isVisible()


def test_only_check_buttons_guard_native_child_activation(setup, monkeypatch):
    from src.ui import invoice_check_window as module
    window, _, _ = setup
    guarded = []
    original_guard = module.prevent_button_activation
    def synthetic_guard(message, buttons):
        if message == 1:
            guarded.append(buttons)
            return True
        return original_guard(message, buttons)
    # PytestQt dispatches native events before fixture teardown. Restore the
    # scoped mock before returning so real WM_PAINT/CLOSE keep their handlers.
    with monkeypatch.context() as patch:
        patch.setattr(module, 'prevent_button_activation', synthetic_guard)
        for button in (window._check_button, window.action_bar.check):
            assert isinstance(button, module._CheckButton)
            assert button.nativeEvent(b'windows_generic_MSG', 1) == (True, 3)
            assert guarded[-1] == [button]
        assert guarded == [[window._check_button], [window.action_bar.check]]
    assert not isinstance(window._select_buttons['invoice'], module._CheckButton)
    assert not isinstance(window._compact_button, module._CheckButton)



def test_passive_main_is_prepared_before_show_and_noncheck_input_activates_editing(setup, monkeypatch):
    from src.ui import invoice_check_window as module
    window, _, _ = setup
    prepared, activated, qt_activations = [], [], []
    original_guard = module.prevent_button_activation
    native_classifier = module.is_native_mouse_activation
    with monkeypatch.context() as patch:
        patch.setattr(module, 'set_native_noactivate',
                      lambda owner, enabled: prepared.append((owner.isVisible(), enabled)) or True)
        patch.setattr(module, 'activate_native_editor', lambda owner: activated.append(owner) or True)
        window.hide()
        window.show()
        assert prepared and prepared[0] == (False, True)
        assert not window.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
        # Only pointer1 is synthetic; preserve every real Windows classification
        # and restore these mocks before PytestQt dispatches teardown events.
        patch.setattr(window, 'activateWindow', lambda: qt_activations.append(True))
        patch.setattr(module, 'prevent_button_activation',
                      lambda message, buttons: False if message == 1 else original_guard(message, buttons))
        patch.setattr(module, 'is_native_mouse_activation',
                      lambda message: True if message == 1 else native_classifier(message))
        assert window.nativeEvent(b'windows_generic_MSG', 1) == (True, 1)
        assert activated == [window] and qt_activations == [True]
        patch.setattr(module, 'prevent_button_activation',
                      lambda message, buttons: True if message == 1 else original_guard(message, buttons))
        assert window.nativeEvent(b'windows_generic_MSG', 1) == (True, 3)
        assert activated == [window] and qt_activations == [True]
