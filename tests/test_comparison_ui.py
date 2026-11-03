"""Comparison status is visible without mutating clipboard or stealing focus."""
import pytest
from PySide6.QtCore import QObject, QRect, Qt, Signal
from PySide6.QtWidgets import QApplication

from src.ui.comparison_window import ComparisonWindow
from src.ui.theme import _ERROR, _SUCCESS, _WARNING


class Controller(QObject):
    updated = Signal(object)
    state_changed = Signal(str)
    failed = Signal(str)
    stopped = Signal()

    def __init__(self):
        super().__init__()
        self.regions = {}
        self.running = False
        self.is_stopped = True
        self.before_capture = None
        self.mode = 'normalized'
        self.interval = 2
        self.starts = self.pauses = 0
        self.shutdown_called = False

    def start(self):
        if not self.before_capture():
            self.failed.emit('核對視窗遮住辨識位置，請移開後再開始。')
            return False
        self.starts += 1
        self.running = True
        self.state_changed.emit('running')
        return True

    def pause(self):
        self.running = False
        self.pauses += 1
        self.state_changed.emit('paused')

    def set_region(self, name, region):
        self.pause()
        self.regions[name] = region
        return True

    def set_interval(self, value):
        self.interval = value
        return True

    def set_mode(self, mode):
        self.pause()
        self.mode = mode
        return True

    def invalidate_regions(self, message):
        self.pause()
        self.regions.clear()
        self.failed.emit(message)

    def shutdown(self):
        self.pause()
        self.shutdown_called = True


@pytest.fixture
def window(qtbot):
    controller = Controller()
    window = ComparisonWindow(controller)
    qtbot.addWidget(window)
    yield window
    window.shutdown()


def configure(window):
    window.set_region('A', (0, 0, 30, 30, 1))
    window.set_region('B', (40, 0, 30, 30, 1))


def result(verdict='match'):
    return {'a': '<tag> A，123', 'b': 'A123', 'verdict': verdict,
            'cycle': 7, 'timestamp': '2026-11-03T12:34:56'}


def test_configure_once_and_resume_reuses_fixed_regions(window, qtbot):
    assert not window._start_button.isEnabled()
    configure(window)
    original = dict(window.controller.regions)
    window._start_button.click()
    assert window.controller.running
    assert window._pause_button.isEnabled()
    window._pause_button.click()
    assert window._start_button.isEnabled()
    window._start_button.click()
    assert window.controller.starts == 2
    assert window.controller.regions == original
    assert '0, 0' in window._region_labels['A'].text()


@pytest.mark.parametrize('verdict,label,color', [
    ('match', '相同', _SUCCESS), ('mismatch', '不同', _ERROR),
    ('uncertain', '待確認', _WARNING),
])
def test_result_has_text_color_raw_previews_count_and_time(window, verdict, label, color):
    window.controller.updated.emit(result(verdict))
    window.controller.state_changed.emit('waiting')
    assert window._status.text() == label
    assert color in window._status.styleSheet()
    assert window._texts['A'].toPlainText() == '<tag> A，123'
    assert window._texts['B'].toPlainText() == 'A123'
    assert '7' in window._activity.text() and '12:34:56' in window._activity.text()


def test_background_results_never_show_raise_activate_or_change_clipboard(window, qapp, monkeypatch):
    qapp.clipboard().setText('KEEP CLIPBOARD')
    calls = []
    for method in ('show', 'raise_', 'activateWindow'):
        monkeypatch.setattr(window, method, lambda name=method: calls.append(name))
    window.controller.updated.emit(result('mismatch'))
    window.controller.state_changed.emit('waiting')
    assert calls == []
    assert qapp.clipboard().text() == 'KEEP CLIPBOARD'
    assert not window.isVisible()


def test_close_pauses_without_forgetting_regions(window):
    configure(window)
    original = dict(window.controller.regions)
    window.show()
    window.controller.running = True
    window.close()
    assert not window.controller.running and not window.isVisible()
    assert window.controller.regions == original
    assert not window.controller.shutdown_called
    assert not window.testAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)


def test_selection_pauses_and_emits_named_side_then_restores_without_resume(window, qtbot):
    configure(window)
    window.controller.running = True
    with qtbot.waitSignal(window.select_requested) as signal:
        window._select_buttons['B'].click()
    assert signal.args == ['B']
    assert not window.controller.running
    window.set_selection_active(True)
    assert not window.isVisible() and not window.can_capture()
    window.set_selection_active(False)
    assert window.isVisible() and not window.controller.running
    assert window.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)


def test_modes_intervals_and_compact_controls(window, qapp):
    assert window._mode.currentData() == 'normalized'
    assert '-1.23' in window._mode.toolTip()
    window._mode.setCurrentIndex(1)
    assert window.controller.mode == 'exact'
    window._interval.setValue(60)
    assert window.controller.interval == 60
    assert (window._interval.minimum(), window._interval.maximum()) == (1, 60)
    window.show()
    window.set_compact(True)
    qapp.processEvents()
    assert window._details.isHidden()
    assert window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    assert window._compact_button.text() == '展開設定'
    window.set_compact(False)
    assert not window._details.isHidden()
    assert not window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint


def test_overlap_guard_maps_logical_frame_to_primary_physical_pixels(window, monkeypatch):
    class Screen:
        def geometry(self):
            return QRect(100, 200, 800, 600)

        def devicePixelRatio(self):
            return 2

    window.show()
    monkeypatch.setattr(QApplication, 'primaryScreen', lambda: Screen())
    monkeypatch.setattr(window, 'frameGeometry', lambda: QRect(110, 220, 30, 40))
    window.controller.regions = {'A': (20, 40, 10, 10, 1), 'B': (700, 400, 10, 10, 1)}
    assert not window.can_capture()
    window.controller.regions['A'] = (100, 140, 10, 10, 1)
    assert window.can_capture()
    window.hide()
    window.controller.regions['A'] = (20, 40, 10, 10, 1)
    assert window.can_capture()


def test_start_moves_status_clear_of_targets_or_refuses_if_no_space(window, qapp):
    window.show()
    window.set_compact(True)
    qapp.processEvents()
    physical = window._physical_window_rect()
    window.controller.regions = {'A': (int(physical.x()), int(physical.y()), 20, 20, 1),
                                 'B': (int(physical.x()) + 30, int(physical.y()), 20, 20, 1)}
    assert not window.can_capture()
    window._start()
    assert window.controller.running and window.can_capture()
    window.controller.pause()
    screen = QApplication.primaryScreen()
    size = screen.geometry().size() * screen.devicePixelRatio()
    window.controller.regions = {'A': (0, 0, size.width(), size.height(), 1)}
    window._start()
    assert not window.controller.running
    assert '移開' in window._note.text()


def test_dpi_change_pauses_and_requires_new_regions(window, qtbot):
    configure(window)
    window.controller.running = True
    with qtbot.waitSignal(window.screen_invalidated):
        window._screen_changed()
    assert not window.controller.running
    assert not window.controller.regions
    assert not window._start_button.isEnabled()
    assert '重新框選' in window._note.text()


def test_shutdown_discards_late_results_and_stops_controller(window):
    window.shutdown()
    assert window.controller.shutdown_called and window.is_stopped
    before = window._status.text()
    window.controller.updated.emit(result('mismatch'))
    assert window._status.text() == before
