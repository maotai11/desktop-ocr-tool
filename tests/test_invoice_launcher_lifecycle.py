"""The new tool shares one selector and cannot revive cancelled UI work."""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget
import pytest

from test_floating_shutdown_entries import running_pipeline, _select  # noqa: F401


class Controller:
    def __init__(self):
        self.calls = []
        self.on_cancel = self.on_region = None
        self.is_stopped = True

    def cancel(self):
        self.calls.append(('cancel',))
        if self.on_cancel:
            self.on_cancel()

    def set_region(self, side, region):
        self.calls.append(('region', side, region))
        if self.on_region:
            self.on_region()
        return True

    def preview_once(self, side):
        self.calls.append(('preview', side))

    def shutdown(self):
        self.calls.append(('shutdown',))


class Window(QWidget):
    closed = Signal()

    def open(self):
        self.showNormal()

    def set_selection_active(self, active):
        self.hide() if active else self.show()

    def set_ocr_ready(self, ready):
        self.ready = ready

    def shutdown(self):
        self.hide()

    def closeEvent(self, event):
        self.closed.emit()
        super().closeEvent(event)


@pytest.fixture
def invoice(qtbot, running_pipeline):
    pipeline, widget, overlay = running_pipeline
    window, controller = Window(), Controller()
    qtbot.addWidget(window)
    widget._invoice_window, widget._invoice_controller = window, controller
    window.closed.connect(widget._invoice_window_closed)
    window.show()
    return pipeline, widget, overlay, window, controller


def test_invoice_selector_accepts_one_region_without_saving_or_ocr(qtbot, invoice, monkeypatch):
    pipeline, widget, overlay, window, controller = invoice
    captures = []
    monkeypatch.setattr(pipeline.capture, 'capture_region', lambda *args: captures.append(args))
    pipeline.start_invoice_region_capture('invoice')
    assert overlay.isVisible() and not window.isVisible()
    _select(qtbot, overlay)
    qtbot.waitUntil(lambda: any(call[0] == 'preview' for call in controller.calls))
    assert len([call for call in controller.calls if call[0] == 'region']) == 1
    assert not captures and pipeline._invoice_selection is None
    assert window.isVisible()


def test_escape_discards_invoice_selector_without_reading(qtbot, invoice):
    from PySide6.QtCore import Qt
    pipeline, _, overlay, window, controller = invoice
    pipeline.start_invoice_region_capture('table')
    qtbot.keyClick(overlay, Qt.Key.Key_Escape)
    assert window.isVisible() and not overlay.isVisible()
    assert not any(call[0] in ('region', 'preview') for call in controller.calls)


def test_normal_capture_retires_delayed_invoice_selection(qtbot, invoice, monkeypatch):
    pipeline, _, overlay, window, controller = invoice
    captures = []
    monkeypatch.setattr(pipeline.capture, 'capture_region', lambda *args: captures.append(args))
    pipeline.start_invoice_region_capture('table')
    _select(qtbot, overlay)
    pipeline.start_region_capture('region_image')
    qtbot.wait(180)
    assert overlay.isVisible() and not window.isVisible()
    assert not captures and not any(call[0] in ('region', 'preview') for call in controller.calls)
    _select(qtbot, overlay)
    qtbot.waitUntil(lambda: len(captures) == 1)


def test_cancel_signal_reentry_retires_lease_before_selector_starts(invoice):
    pipeline, _, overlay, window, controller = invoice
    controller.on_cancel = window.close
    pipeline.start_invoice_region_capture('invoice')
    assert not overlay.isVisible() and pipeline._invoice_selection is None
    assert not any(call[0] in ('region', 'preview') for call in controller.calls)


def test_closing_from_region_notification_never_starts_preview(qtbot, invoice):
    pipeline, _, overlay, window, controller = invoice
    controller.on_region = window.close
    pipeline.start_invoice_region_capture('invoice')
    _select(qtbot, overlay)
    qtbot.waitUntil(lambda: any(call[0] == 'region' for call in controller.calls))
    assert not any(call[0] == 'preview' for call in controller.calls)


def test_shutdown_rejects_delayed_invoice_region_and_reopening(qtbot, invoice):
    pipeline, widget, overlay, window, controller = invoice
    pipeline.start_invoice_region_capture('invoice')
    _select(qtbot, overlay)
    pipeline.shutdown()
    qtbot.waitUntil(lambda: pipeline.closed)
    qtbot.wait(180)
    widget.open_invoice_check()
    pipeline.start_invoice_region_capture('table')
    assert not window.isVisible() and not overlay.isVisible()
    assert not any(call[0] in ('region', 'preview') for call in controller.calls)
