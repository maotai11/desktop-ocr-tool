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
    assert '0, 0' in window._region_labels['A'].toolTip()
    assert window._region_labels['A'].text() == '範圍已選取'


@pytest.mark.parametrize('verdict,label,color', [
    ('match', '內容一致', _SUCCESS), ('mismatch', '發現差異', _ERROR),
    ('uncertain', '需要確認', _WARNING),
])
def test_result_has_text_color_raw_previews_count_and_time(window, verdict, label, color):
    window.controller.updated.emit(result(verdict))
    window.controller.state_changed.emit('waiting')
    assert window._status.text() == label
    assert color in window._status.styleSheet()
    assert window._details.isHidden()
    assert window._previews['A'].toolTip() == '<qt>&lt;tag&gt; A，123</qt>'
    assert window._texts['A'].toPlainText() == ''
    window.set_compact(False)
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
    assert window._compact_button.text() == '查看全文'
    window.set_compact(False)
    assert not window._details.isHidden()
    assert window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    window._pin_button.click()
    assert not window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    window.set_compact(True)
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
    available = QApplication.primaryScreen().availableGeometry()
    if available.height() >= window.frameGeometry().height() and available.width() >= window.frameGeometry().width():
        assert window.controller.running and window.can_capture()
    else:
        # A 2× offscreen screen can be shorter than the launcher's 480px
        # minimum. Refuse a covered selection rather than hiding the tool.
        assert not window.controller.running and '移開' in window._note.text()
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


def test_unchanged_check_keeps_result_color_timestamp_and_count(window):
    window.controller.updated.emit(result('mismatch'))
    title, timestamp, style = window._status.text(), window._activity.text(), window._status.styleSheet()
    for state in ('watching', 'checking', 'unchanged'):
        window.controller.state_changed.emit(state)
        assert (window._status.text(), window._activity.text(), window._status.styleSheet()) == (title, timestamp, style)
    assert window._note.text() == '畫面未變，等待變化。'


@pytest.mark.parametrize('compact', [False, True])
def test_pinning_is_optional_keeps_position_and_close_is_always_available(window, qapp, compact):
    configure(window)
    window.show()
    window.set_compact(compact)
    window.controller.running = True
    qapp.processEvents()
    for pinned in (False, True):
        window.move(123, 135)
        position = window.pos()
        window.set_pinned(pinned)
        qapp.processEvents()
        assert window.pos() == position
        assert bool(window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint) == pinned
        assert window.windowFlags() & Qt.WindowType.WindowCloseButtonHint
        assert window._close_button.isEnabled()
        assert window.controller.running
    window._close_button.click()
    assert not window.isVisible() and not window.controller.running
    # Late overlay completion / result delivery must not reopen a closed window.
    window.set_selection_active(False)
    window.controller.updated.emit(result())
    assert not window.isVisible() and window._texts['A'].toPlainText() == ''
    window.open()
    assert window.isVisible() and not window.controller.running


def test_windows_native_close_command_is_enabled_while_running(window, qapp):
    import sys
    if sys.platform != 'win32':
        pytest.skip('Native Windows caption')
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    user32.GetSystemMenu.argtypes = [wintypes.HWND, wintypes.BOOL]
    user32.GetSystemMenu.restype = wintypes.HMENU
    user32.GetMenuState.argtypes = [wintypes.HMENU, wintypes.UINT, wintypes.UINT]
    user32.GetMenuState.restype = wintypes.UINT
    window.show()
    window.controller.running = True
    for compact in (False, True):
        window.set_compact(compact)
        qapp.processEvents()
        menu = user32.GetSystemMenu(int(window.winId()), False)
        assert menu
        close_state = user32.GetMenuState(menu, 0xF060, 0)  # SC_CLOSE, MF_BYCOMMAND
        assert close_state != 0xFFFFFFFF and close_state & 3 == 0


def test_default_footprint_matches_launcher_and_details_do_not_resize(window, qapp):
    window.show()
    qapp.processEvents()
    assert (window.width(), window.height()) == (348, 560)
    assert (window.minimumWidth(), window.minimumHeight()) == (320, 280)
    assert window._details.isHidden()
    before = window.geometry()
    window.controller.updated.emit(dict(result(), a='A' * 100_000))
    qapp.processEvents()
    assert window.geometry() == before
    assert len(window._previews['A'].text()) < 100
    assert '查看全文' in window._previews['A'].toolTip()
    assert len(window._previews['A'].toolTip()) < 1000
    assert window._texts['A'].toPlainText() == ''
    window.set_compact(False)
    qapp.processEvents()
    assert window.geometry() == before
    assert len(window._texts['A'].toPlainText()) == 100_000
    window.set_compact(True)
    assert window._texts['A'].toPlainText() == ''


def test_ocr_tooltips_escape_html_and_preserve_plain_full_text(window):
    raw = '<img src="https://example.invalid/private.png"> & <b>OCR</b>'
    window.controller.updated.emit(dict(result(), a=raw))
    tooltip = window._previews['A'].toolTip()
    assert '<img' not in tooltip and '<b>' not in tooltip
    assert '&lt;img' in tooltip and '&amp;' in tooltip
    window.set_compact(False)
    assert window._texts['A'].toPlainText() == raw


def _assert_mini_results_visible(window):
    from PySide6.QtCore import QPoint, QRect
    for widget in (window._mini_group_combo, window._mini_status,
                   window._mini_previews['A'], window._mini_previews['B'],
                   window._start_button, window._pause_button, window._close_button):
        assert widget.isVisible()
        bounds = QRect(widget.mapTo(window, QPoint(0, 0)), widget.size())
        assert window.rect().contains(bounds), (widget.objectName(), bounds, window.rect())


def test_mini_mode_keeps_both_live_results_visible_and_restores_size_position(window, qapp, monkeypatch):
    configure(window)
    window.show()
    window.move(130, 90)
    qapp.processEvents()
    original = window.geometry()
    original_position = window.pos()
    window._mini_button.click()
    qapp.processEvents()
    assert window._mini_mode and window._mini_button.text() == '還原'
    assert (window.width(), window.height()) == (320, 300)
    assert window.pos() == original_position
    assert not window._scroll.isVisible()
    _assert_mini_results_visible(window)
    calls = []
    for method in ('show', 'raise_', 'activateWindow', 'resize', 'move'):
        monkeypatch.setattr(window, method, lambda *args, name=method: calls.append(name))
    window.controller.updated.emit(dict(result('mismatch'), a='A 新文字 123', b='B 新文字 456'))
    qapp.processEvents()
    assert window._mini_status.text() == '發現差異'
    assert window._mini_previews['A']._full_text == 'A 新文字 123'
    assert window._mini_previews['B']._full_text == 'B 新文字 456'
    assert calls == []
    _assert_mini_results_visible(window)
    monkeypatch.undo()
    window._mini_button.click()
    qapp.processEvents()
    assert not window._mini_mode and window.geometry() == original
    assert window._scroll.isVisible()


@pytest.mark.parametrize('height', [480, 300, 280])
def test_manual_shrink_uses_visible_result_layout_at_every_small_size(window, qapp, height):
    window.show()
    qapp.processEvents()
    window.resize(320, height)
    window.controller.updated.emit(dict(result(), a='A123', b='A123'))
    qapp.processEvents()
    assert window._mini_mode and window.height() == height
    _assert_mini_results_visible(window)
    assert window._mini_previews['A'].text() == 'A123'
    assert window._mini_previews['B'].text() == 'A123'
    window.resize(348, 560)
    qapp.processEvents()
    assert not window._mini_mode and window._scroll.isVisible()


def test_requesting_full_text_from_mini_restores_normal_view_without_losing_result(window, qapp):
    raw = '<b>A</b>\n' + '長文字' * 50
    window.controller.updated.emit(dict(result(), a=raw))
    window.show()
    window.set_mini(True)
    window._compact_button.click()
    qapp.processEvents()
    assert not window._mini_mode and not window._compact
    assert window._details.isVisible()
    assert window._texts['A'].toPlainText() == raw


def test_mini_from_expanded_details_can_reopen_full_text_in_one_click(window, qapp):
    window.show()
    window.controller.updated.emit(dict(result(), a='完整原文'))
    window.set_compact(False)
    window.set_mini(True)
    assert window._compact_button.text() == '查看全文'
    window._compact_button.click()
    qapp.processEvents()
    assert not window._mini_mode and not window._compact
    assert window._details.isVisible()
    assert window._texts['A'].toPlainText() == '完整原文'


def test_mini_group_switch_and_close_keep_independent_results(group_window, qapp, monkeypatch):
    window, controller = group_window, group_window.controller
    first = controller.active_group_id
    second = controller.add_group('第二組')
    controller._groups[first]['result'] = dict(result(), group_id=first, a='第一組 A', b='第一組 B')
    controller._groups[second]['result'] = dict(result('mismatch'), group_id=second, a='第二組 A', b='第二組 B')
    window.show()
    window.set_mini(True)
    controller.running = True
    window._mini_group_combo.setCurrentIndex(window._mini_group_combo.findData(first))
    qapp.processEvents()
    assert controller.running and window._mini_previews['A']._full_text == '第一組 A'
    window._mini_group_combo.setCurrentIndex(window._mini_group_combo.findData(second))
    assert controller.running and window._mini_previews['B']._full_text == '第二組 B'
    assert window._mini_status.text() == '發現差異'
    assert controller.rename_group(second, '新組名')
    assert window._mini_group_combo.currentText() == '新組名'
    assert controller.add_group('尚未設定')
    assert window._mini_previews['A']._full_text == ''
    assert window._mini_previews['B']._full_text == ''
    assert window._mini_status.text() == '準備核對'
    window._close_button.click()
    assert not controller.running and not window.isVisible()
    assert not any(timer.isActive() for timer in
                   (controller._cycle_timer, controller._retry, controller._deadline))


@pytest.fixture
def group_window(qapp, qtbot):
    from src.comparison.controller import ComparisonController
    from test_comparison_controller import FakeCapture, FakeOcr
    controller = ComparisonController(FakeOcr(), capture_worker=FakeCapture())
    window = ComparisonWindow(controller)
    qtbot.addWidget(window)
    yield window
    window.shutdown()


def test_group_actions_switch_without_losing_regions_or_resuming(group_window, monkeypatch):
    from PySide6.QtWidgets import QInputDialog
    window, controller = group_window, group_window.controller
    configure(window)
    first = controller.active_group_id
    window._add_group_button.click()
    second = controller.active_group_id
    assert second != first and len(controller.groups) == 2
    assert not controller.regions and not window._start_button.isEnabled()
    assert set(window._group_result_cards) == {first, second}
    monkeypatch.setattr(QInputDialog, 'getText', lambda *args, **kwargs: ('<b>訂單</b>', True))
    window._rename_action.trigger()
    assert window._group_combo.currentText() == '<b>訂單</b>'
    assert '&lt;b&gt;' in window._group_result_cards[second].name.toolTip()
    configure(window)
    assert window._start_button.isEnabled()
    controller.running = True
    window._group_combo.setCurrentIndex(0)
    assert controller.active_group_id == first and controller.running
    assert controller.regions == {'A': (0, 0, 30, 30, 1), 'B': (40, 0, 30, 30, 1)}
    window._group_combo.setCurrentIndex(1)
    window._remove_action.trigger()
    assert not controller.running and len(controller.groups) == 1
    assert controller.active_group_id == first
    assert not window._remove_action.isEnabled()


def test_foreign_group_results_do_not_replace_selected_preview_or_focus(group_window, monkeypatch):
    window, controller = group_window, group_window.controller
    first = controller.active_group_id
    configure(window)
    second = controller.add_group('金額')
    configure(window)
    controller.select_group(first)
    shown = dict(result('mismatch'), group_id=first, group_name='第 1 組')
    window._updated(shown)
    title = window._status.text()
    calls = []
    for method in ('show', 'raise_', 'activateWindow'):
        monkeypatch.setattr(window, method, lambda name=method: calls.append(name))
    window._updated(dict(result(), group_id=second, group_name='金額', a='OTHER GROUP'))
    assert window._status.text() == title
    assert 'OTHER GROUP' not in window._previews['A'].toolTip()
    assert not calls and controller.active_group_id == first


def test_overlap_guard_checks_unselected_group_targets(group_window, monkeypatch):
    window, controller = group_window, group_window.controller
    window.show()
    frame = window._physical_window_rect()
    controller.set_region('A', (int(frame.x()), int(frame.y()), 20, 20, 1))
    controller.set_region('B', (int(frame.x()) + 30, int(frame.y()), 20, 20, 1))
    controller.add_group('第二組')
    controller.set_region('A', (1000, 1000, 20, 20, 1))
    controller.set_region('B', (1100, 1000, 20, 20, 1))
    assert not window.can_capture()


def _populate_result_groups(window, count=3):
    controller = window.controller
    ids = []
    for index in range(count):
        group_id = controller.active_group_id if index == 0 else controller.add_group(f'欄位 {index + 1}')
        configure(window)
        ids.append(group_id)
        controller._groups[group_id]['result'] = dict(
            result('mismatch' if index == 1 else 'match'), group_id=group_id,
            a=f'A 組{index + 1}', b=f'B 組{index + 1}', cycle=index + 1)
    controller.select_group(ids[0])
    controller.groups_changed.emit()
    return ids


def _fully_inside_viewport(card, viewport):
    from PySide6.QtCore import QPoint, QRect
    return viewport.rect().contains(QRect(card.mapTo(viewport, QPoint()), card.size()))


def test_all_group_results_visible_together_without_changing_selected_group(group_window, qapp):
    window = group_window
    ids = _populate_result_groups(window)
    selected = window.controller.active_group_id
    window.show()
    qapp.processEvents()
    assert (window.width(), window.height()) == (348, 560)
    assert window._all_groups_panel.isVisible()
    assert window._status_card.isHidden()
    for index, group_id in enumerate(ids):
        card = window._group_result_cards[group_id]
        assert card.isVisible() and _fully_inside_viewport(card, window._scroll.viewport())
        assert card.previews['A']._full_text == f'A 組{index + 1}'
        assert card.previews['B']._full_text == f'B 組{index + 1}'
        assert card.status.text().startswith('不同' if index == 1 else '一致')
        assert card.activity.text() == '12:34:56'
    assert window.controller.active_group_id == selected


def test_mini_shows_multiple_live_group_pairs_without_selector_navigation(group_window, qapp, monkeypatch):
    window, controller = group_window, group_window.controller
    ids = _populate_result_groups(window)
    window.show()
    window.set_mini(True)
    qapp.processEvents()
    assert window._mini_group_combo.isHidden()
    assert window._mini_groups_scroll.isVisible()
    viewport = window._mini_groups_scroll.viewport()
    for group_id in ids[:2]:
        card = window._mini_group_result_cards[group_id]
        assert card.isVisible() and _fully_inside_viewport(card, viewport)
        assert card.previews['A']._full_text and card.previews['B']._full_text
    first_card = window._mini_group_result_cards[ids[0]]
    second_card = window._mini_group_result_cards[ids[1]]
    original = first_card.previews['A']._full_text
    before = window.geometry()
    selected = controller.active_group_id
    calls = []
    for method in ('show', 'raise_', 'activateWindow', 'resize', 'move'):
        monkeypatch.setattr(window, method, lambda *args, name=method: calls.append(name))
    controller._groups[ids[1]]['result'] = dict(result('uncertain'), group_id=ids[1], a='新的 A', b='新的 B')
    controller.groups_changed.emit()
    qapp.processEvents()
    assert window._mini_group_result_cards[ids[0]] is first_card
    assert window._mini_group_result_cards[ids[1]] is second_card
    assert first_card.previews['A']._full_text == original
    assert second_card.previews['A']._full_text == '新的 A'
    assert second_card.previews['B']._full_text == '新的 B'
    assert second_card.status.text() == '待確認'
    assert window.geometry() == before and not calls and controller.active_group_id == selected


def test_all_group_cards_remove_reset_and_scrolling_are_independent(group_window, qapp):
    window, controller = group_window, group_window.controller
    ids = _populate_result_groups(window, 6)
    window.show()
    qapp.processEvents()
    bar = window._scroll.verticalScrollBar()
    bar.setValue(bar.maximum() // 2)
    position = bar.value()
    raw = '<img src="https://example.invalid/private"> & 新文字'
    controller._groups[ids[-1]]['result'] = dict(result('mismatch'), group_id=ids[-1], a=raw)
    controller.groups_changed.emit()
    qapp.processEvents()
    assert bar.value() == position
    card = window._group_result_cards[ids[-1]]
    assert '&lt;img' in card.previews['A'].toolTip() and '<img' not in card.previews['A'].toolTip()
    controller.select_group(ids[-1])
    controller.set_region('A', (1, 1, 20, 20, 1))
    assert window._group_result_cards[ids[-1]].previews['A']._full_text == ''
    assert window._mini_group_result_cards[ids[-1]].previews['B']._full_text == ''
    assert controller.remove_group(ids[1])
    assert ids[1] not in window._group_result_cards and ids[1] not in window._mini_group_result_cards
    for group in list(controller.groups)[1:]:
        controller.remove_group(group['id'])
    qapp.processEvents()
    assert len(window._group_result_cards) == 1
    assert window._all_groups_panel.isHidden() and window._status_card.isVisible()
