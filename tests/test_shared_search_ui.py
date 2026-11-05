"""Shared-search controls and click-through marks do not outlive their work."""
import copy

import pytest
from PySide6.QtCore import QObject, Qt, Signal

from src.ui.search_highlight_overlay import SearchHighlightOverlay
from src.ui.shared_search_window import SharedSearchWindow


def candidate(identifier='one', text='王小明', *, needs_review=False):
    return {'id': identifier, 'text': text, 'boxes': [[[10, 10], [60, 10], [60, 30], [10, 30]]],
            'needs_review': needs_review, 'position_kind': 'line'}


class Controller(QObject):
    updated = Signal(object)
    fields_changed = Signal()
    state_changed = Signal(str)
    failed = Signal(str)
    stopped = Signal()

    def __init__(self):
        super().__init__()
        self.fields = [{'id': 'a1', 'name': '姓名', 'region': (10, 10, 80, 30, 1), 'result': None}]
        self.b_region = (200, 100, 120, 100, 1)
        self.tracking_region = (10, 10, 100, 100, 1)
        self.row_pitch = 30
        self.auto_follow = self.calibrated = self.running = False
        self.is_stopped = True
        self.mode = 'normalized'
        self.interval = 2
        self.calibrations = self.starts = 0
        self.selected = []
        self.before_capture = self.after_capture = None
        self.shutdown_called = False

    @property
    def target_regions(self):
        return tuple([field['region'] for field in self.fields if field['region']]
                     + [region for region in (self.b_region, self.tracking_region) if region])

    def snapshot(self):
        return {'fields': copy.deepcopy(self.fields), 'b_region': self.b_region,
                'tracking_region': self.tracking_region, 'timestamp': '2026-11-05T12:34:56',
                'cycle': 7, 'row_pitch': self.row_pitch, 'auto_follow': self.auto_follow,
                'calibrated': self.calibrated}

    def publish(self, status='matched', candidates=None):
        self.running = True
        candidates = candidates if candidates is not None else [candidate()]
        self.fields[0]['result'] = {'status': status, 'text': '王小明', 'candidates': candidates,
                                    'selected_id': candidates[0]['id'] if status == 'matched' else None}
        self.updated.emit(self.snapshot())

    def start(self):
        self.starts += 1
        self.running = True
        self.state_changed.emit('running')
        return True

    def pause(self):
        self.running = False
        for field in self.fields:
            field['result'] = None
        self.fields_changed.emit()
        self.state_changed.emit('paused')

    def shutdown(self):
        self.pause()
        self.shutdown_called = True

    def set_a_region(self, key, region):
        next(field for field in self.fields if field['id'] == key)['region'] = region
        self.fields_changed.emit()
        return True

    def set_b_region(self, region):
        self.b_region = region
        self.fields_changed.emit()
        return True

    def set_tracking_region(self, region):
        self.tracking_region = region
        self.fields_changed.emit()
        return True

    def set_mode(self, mode):
        self.mode = mode
        return True

    def set_interval(self, interval):
        self.interval = interval
        return True

    def set_row_pitch(self, pitch):
        self.row_pitch = pitch
        return True

    def set_auto_follow(self, enabled):
        self.auto_follow = enabled
        return True

    def calibrate_tracking(self):
        self.calibrations += 1
        self.calibrated = True
        self.state_changed.emit('calibrated')
        return True

    def move_rows(self, delta):
        self.pause()
        self.auto_follow = False
        for field in self.fields:
            x, y, width, height, monitor = field['region']
            field['region'] = x, y + delta * self.row_pitch, width, height, monitor
        self.fields_changed.emit()
        return self.start()

    def select_candidate(self, field_id, identifier):
        self.selected.append((field_id, identifier))
        return True

    def add_field(self):
        key = f'a{len(self.fields) + 1}'
        self.fields.append({'id': key, 'name': key, 'region': None, 'result': None})
        self.fields_changed.emit()
        return key

    def remove_field(self, key):
        self.fields = [field for field in self.fields if field['id'] != key]
        self.fields_changed.emit()

    def rename_field(self, key, name):
        next(field for field in self.fields if field['id'] == key)['name'] = name
        self.fields_changed.emit()

    def invalidate_regions(self, message):
        self.pause()
        self.b_region = self.tracking_region = None
        for field in self.fields:
            field['region'] = None
        self.fields_changed.emit()
        self.failed.emit(message)


@pytest.fixture
def window(qtbot):
    owner = Controller()
    window = SharedSearchWindow(owner)
    qtbot.addWidget(window)
    qtbot.addWidget(window.highlight_overlay)
    yield window
    window.shutdown()


def test_open_does_not_start_or_create_marks(window):
    window.open()
    assert window.controller.starts == 0
    assert not window.controller.running
    assert not window.highlight_overlay.isVisible()
    assert window.size().width() == 348


def test_result_maps_b_local_boxes_and_source_outlines_without_clipboard_or_focus(window, qapp, monkeypatch):
    qapp.clipboard().setText('KEEP')
    called = []
    for method in ('show', 'activateWindow', 'raise_'):
        monkeypatch.setattr(window, method, lambda name=method: called.append(name))
    window.controller.publish()
    marks = window.highlight_overlay.marks
    found = next(mark for mark in marks if mark['kind'] == 'match')
    assert found['boxes'][0][0] == (210.0, 110.0)
    assert any(mark['kind'] == 'source' for mark in marks)
    assert window.highlight_overlay.isVisible()
    assert called == [] and qapp.clipboard().text() == 'KEEP'
    assert '1／1' in window._status.text()
    assert '12:34:56' in window._activity.text()


def test_capture_hides_marks_then_restores_only_same_running_generation(window):
    window.controller.publish()
    assert window.before_capture()
    assert not window.highlight_overlay.isVisible()
    window.after_capture()
    assert window.highlight_overlay.isVisible()
    assert window.before_capture()
    window.pause()
    window.after_capture()
    assert not window.highlight_overlay.isVisible()
    assert not window.highlight_overlay.marks
    assert window.controller.fields[0]['result'] is None


@pytest.mark.parametrize('action', ['close', 'shutdown', 'selection', 'layout'])
def test_late_restore_cannot_resurrect_marks_after_lifecycle_change(window, action):
    window.controller.publish()
    assert window.before_capture()
    if action == 'selection':
        window.set_selection_active(True)
    elif action == 'layout':
        window._screen_changed()
    else:
        getattr(window, action)()
    window.after_capture()
    window.controller.updated.emit(window.controller.snapshot())
    assert not window.highlight_overlay.isVisible()
    assert not window.highlight_overlay.marks


def test_candidate_choice_is_explicit_and_at_most_one_requested(window):
    window.controller.publish('ambiguous', [candidate('one'), candidate('two')])
    card = window._fields_cards['a1']
    assert card._candidates.currentData() is None
    assert not card._apply.isEnabled()
    assert not any(mark['kind'] == 'match' for mark in window.highlight_overlay.marks)
    card._candidates.setCurrentIndex(2)
    assert window.controller.selected == []
    card._apply.click()
    assert window.controller.selected == [('a1', 'two')]
    assert not window.highlight_overlay.marks


def test_region_routes_reject_nonprimary_and_pause_clear_before_edit(window, qtbot):
    window.controller.publish()
    with qtbot.waitSignal(window.select_requested) as request:
        window._fields_cards['a1']._select.click()
    assert request.args == ['a1']
    assert not window.controller.running and not window.highlight_overlay.marks
    assert not window.set_region('B', (0, 0, 10, 10, 2))
    assert window.controller.b_region == (200, 100, 120, 100, 1)
    assert window.set_region('B', (300, 100, 200, 100, 1))
    assert window.set_region('TRACK', (5, 5, 200, 200, 1))
    assert window.set_region('a1', (10, 40, 80, 30, 1))
    assert window.controller.fields[0]['region'][1] == 40


def test_calibration_countdown_allows_table_focus_and_never_starts_ocr(window):
    window._calibrate_tracking()
    assert window._calibration_timer.isActive()
    assert window.controller.calibrations == 0
    window._calibration_tick()
    window._calibration_tick()
    assert window.controller.calibrations == 0
    window._calibration_tick()
    assert window.controller.calibrations == 1
    assert window.controller.starts == 0
    assert not window._calibration_timer.isActive()


@pytest.mark.parametrize('stop', ['controller_pause', 'close', 'selection', 'shutdown'])
def test_calibration_countdown_cancelled_by_external_pause_and_lifecycle(window, stop):
    window._calibrate_tracking()
    if stop == 'controller_pause':
        window.controller.pause()
    elif stop == 'selection':
        window.set_selection_active(True)
    else:
        getattr(window, stop)()
    assert not window._calibration_timer.isActive()
    assert window.controller.calibrations == 0


def test_manual_row_move_clears_old_marks_and_disables_auto_follow(window):
    window.controller.auto_follow = True
    window.controller.publish()
    window._next.click()
    assert not window.controller.auto_follow
    assert window.controller.fields[0]['region'][1] == 40
    assert not window.highlight_overlay.marks
    assert not window._auto.isChecked()
    assert window.controller.running


def test_region_change_signal_clears_stale_overlay_before_new_result(window):
    window.controller.publish()
    window.controller.fields[0]['region'] = (10, 70, 80, 30, 1)
    window.controller.fields_changed.emit()
    assert not window.highlight_overlay.marks


@pytest.mark.parametrize('state', ['checking', 'reading', 'changed', 'tracking_pending', 'tracking_lost'])
def test_new_capture_or_unconfirmed_row_cannot_restore_old_success(window, state):
    window.controller.publish()
    assert window.before_capture()
    window.controller.state_changed.emit(state)
    window.after_capture()
    assert not window.highlight_overlay.isVisible()
    assert not window.highlight_overlay.marks
    assert window._status.text() == '等待確認'


def test_cleared_controller_results_remove_marks_before_next_capture(window):
    window.controller.publish()
    window.controller.fields[0]['result'] = None
    window.controller.fields_changed.emit()
    assert not window.highlight_overlay.marks
    assert not window.highlight_overlay.isVisible()


def test_numeric_mode_and_interval_controls_match_controller_contract(window):
    assert window._mode.currentText() == '數字嚴格／文字近似'
    assert '95%' in window._mode.toolTip()
    assert '前導零' in window._mode.toolTip()
    assert window._row_pitch.value() == window.controller.row_pitch == 30
    window._mode.setCurrentIndex(1)
    assert window.controller.mode == 'exact'
    window._interval.setValue(5)
    assert window.controller.interval == 5


@pytest.mark.parametrize('boxes', [None, 'invalid', [None], [[[1], None, [1, 2], [3, 4]]],
                                  [[[object(), 1], [2, 1], [2, 2], [1, 2]]],
                                  candidate()['boxes'] * 513])
def test_malformed_or_excessive_selected_geometry_does_not_show_partial_success(window, boxes):
    malformed = candidate()
    malformed['boxes'] = boxes
    window.controller.publish(candidates=[malformed])
    assert not window.highlight_overlay.isVisible()
    assert not window.highlight_overlay.marks
    assert window._status.text() == '需要確認'
    assert not window.controller.running
    assert window._fields_cards['a1']._status.text() == '等待搜尋'


def test_opacity_and_repin_keep_clickthrough_and_do_not_resurrect_hidden_capture(window):
    window.controller.publish()
    overlay = window.highlight_overlay
    assert overlay.windowFlags() & Qt.WindowType.WindowTransparentForInput
    assert overlay.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
    assert overlay.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    window._opacity.setValue(0)
    assert overlay._fill_opacity == 0 and overlay.windowOpacity() == 1.0
    assert window.before_capture()
    window.set_pinned(False)
    window.set_pinned(True)
    assert not overlay.isVisible()
    window.after_capture()
    assert overlay.isVisible()
    assert overlay.windowFlags() & Qt.WindowType.WindowTransparentForInput


def test_all_field_cards_stay_visible_in_scroll_content(window):
    for _ in range(19):
        window.controller.add_field()
    assert len(window._fields_cards) == 20
    assert not window._add.isEnabled()
    assert all(not card.isHidden() for card in window._fields_cards.values())


def test_overlay_clear_invalid_geometry_and_high_dpi_paint(qtbot, qapp):
    overlay = SearchHighlightOverlay()
    qtbot.addWidget(overlay)
    assert not overlay.set_marks([{'name': 'A1', 'boxes': candidate()['boxes']},
                                  {'name': 'bad', 'boxes': [[[float('nan'), 1]] * 4]}])
    assert not overlay.marks
    assert overlay.set_marks([{'name': 'A1', 'boxes': candidate()['boxes']}])
    qapp.processEvents()
    assert len(overlay.marks) == 1
    image = overlay.grab().toImage()
    assert not image.isNull()
    assert image.devicePixelRatio() == overlay.devicePixelRatioF()
    overlay.clear()
    overlay.restore_after_capture()
    assert not overlay.isVisible() and not overlay.marks


def test_overlay_malformed_input_is_safe_and_overflow_never_leaves_partial_marks(qtbot):
    overlay = SearchHighlightOverlay()
    qtbot.addWidget(overlay)
    assert not overlay.set_marks([None, {'boxes': None}, {'boxes': [None, 'bad', [None] * 4]},
                                  {'name': 'valid', 'boxes': candidate()['boxes']}])
    assert not overlay.marks
    assert not overlay.set_marks([{'name': 'too many', 'boxes': candidate()['boxes'] * 513}])
    assert not overlay.marks and not overlay.isVisible()
    assert not overlay.set_marks(None)
