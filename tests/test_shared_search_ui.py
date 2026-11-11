"""Shared-search controls and click-through marks do not outlive their work."""
import copy

import pytest
from PySide6.QtCore import QObject, QRectF, Qt, Signal
from PySide6.QtWidgets import QWidget

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
    qtbot.addWidget(window.resume_button)
    yield window
    window.shutdown()


def test_open_does_not_start_or_create_marks(window):
    window.open()
    assert window.controller.starts == 0
    assert not window.controller.running
    assert not window.highlight_overlay.isVisible()
    assert window.size().width() == 348


def test_result_maps_b_local_boxes_and_inline_text_without_clipboard_or_focus(window, qapp, monkeypatch):
    qapp.clipboard().setText('KEEP')
    called = []
    for method in ('show', 'activateWindow', 'raise_'):
        monkeypatch.setattr(window, method, lambda name=method: called.append(name))
    window.controller.publish()
    marks = window.highlight_overlay.marks
    found = next(mark for mark in marks if mark['kind'] == 'match')
    assert found['boxes'][0][0] == (210.0, 110.0)
    assert len(marks) == 1
    assert found['a_text'] == found['b_text'] == '王小明'
    assert found['bounds'] == (200, 100, 120, 100)
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


def test_repin_keeps_clickthrough_and_does_not_resurrect_hidden_capture(window):
    window.controller.publish()
    overlay = window.highlight_overlay
    assert overlay.windowFlags() & Qt.WindowType.WindowTransparentForInput
    assert overlay.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
    assert overlay.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    assert overlay.windowOpacity() == 1.0
    assert window.resume_button.isVisible()
    assert window.before_capture()
    window.set_pinned(False)
    window.set_pinned(True)
    assert not overlay.isVisible() and not window.resume_button.isVisible()
    window.after_capture()
    assert overlay.isVisible() and window.resume_button.isVisible()
    assert overlay.windowFlags() & Qt.WindowType.WindowTransparentForInput


def test_overlay_pin_is_independent_of_normal_panel_and_minimize(window, qtbot):
    window.controller.publish()
    overlay = window.highlight_overlay
    assert window._pin.text() == '標示置頂'
    assert not window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    window.open()
    window.set_pinned(False)
    assert overlay.isVisible()
    assert not overlay.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    assert not window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    window.set_pinned(True)
    assert overlay.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    foreground = QWidget()
    qtbot.addWidget(foreground)
    foreground.show()
    foreground.activateWindow()
    window.showMinimized()
    assert window.isMinimized() and window.controller.running
    assert overlay.isVisible()
    assert not window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint


def test_inline_text_is_below_a_without_b_text_cards_or_original_text_overlap(qtbot, qapp):
    overlay = SearchHighlightOverlay()
    qtbot.addWidget(overlay)
    scale = qapp.primaryScreen().devicePixelRatio()
    anchors = [(20 * scale, (20 + index * 70) * scale, 180 * scale, 25 * scale) for index in range(3)]
    marks = [{'name': 'DO NOT DISPLAY', 'a_text': '<b>王</b>\n測試\u202e' + '金額' * 1000,
              'b_text': 'B SHOULD NOT DISPLAY', 'status': 'matched' if index else 'similar',
              'anchor': anchor, 'boxes': []} for index, anchor in enumerate(anchors)]
    assert overlay.set_marks(marks)
    labels = overlay.callouts
    assert len(labels) == 3 and overlay.overflow_count == 0
    for label, physical_anchor in zip(labels, anchors):
        anchor = QRectF(*(value / scale for value in physical_anchor))
        rectangle = QRectF(*label['rect'])
        assert tuple(anchor.getRect()) == label['anchor']
        assert rectangle.top() == anchor.bottom() + 3
        assert all(not rectangle.intersects(QRectF(*(value / scale for value in other))) for other in anchors)
        assert len(label['display_lines']) == 1
        assert 'DO NOT DISPLAY' not in label['display_lines'][0]
        assert 'B SHOULD NOT DISPLAY' not in label['display_lines'][0]
        assert '\n' not in label['a_text'] and '\u202e' not in label['a_text']
        assert len(label['a_text']) <= 241
    assert '需確認' in labels[0]['display_lines'][0]
    assert '一致' in labels[1]['display_lines'][0]
    assert not overlay.grab().isNull()


def test_near_or_low_confidence_selected_result_never_has_green_callout(window):
    chosen = candidate(needs_review=True)
    chosen['match_type'] = 'similar'
    window.controller.publish(candidates=[chosen])
    mark = window.highlight_overlay.marks[0]
    assert mark['status'] == 'similar' and mark['kind'] == 'uncertain'
    window.controller.state_changed.emit('changed')
    assert not window.highlight_overlay.callouts
    assert window.highlight_overlay.overflow_count == 0


def test_small_b_does_not_clip_a_text_and_colliding_labels_report_overflow(qtbot, qapp):
    overlay = SearchHighlightOverlay()
    qtbot.addWidget(overlay)
    scale = qapp.primaryScreen().devicePixelRatio()
    bounds = (300 * scale, 100 * scale, 20 * scale, 20 * scale)
    box = [[[301 * scale, 101 * scale], [315 * scale, 101 * scale],
            [315 * scale, 115 * scale], [301 * scale, 115 * scale]]]
    mark = {'name': '窄欄位', 'a_text': 'A', 'b_text': 'B', 'anchor': (20 * scale, 100 * scale, 30 * scale, 20 * scale),
            'bounds': bounds, 'boxes': box}
    assert overlay.set_marks([mark] * 20)
    assert len(overlay.callouts) == 2 and overlay.overflow_count == 18
    assert overlay.overflow_rect is None
    assert all(QRectF(*label['rect']).left() == 20 for label in overlay.callouts)
    mark['bounds'] = (310 * scale, 110 * scale, 20 * scale, 20 * scale)
    assert not overlay.set_marks([mark])
    assert not overlay.marks and not overlay.isVisible()


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


@pytest.mark.parametrize('status,label', [('missing', '未找到'), ('ambiguous', '多處符合'), ('uncertain', '需確認')])
def test_nonmatching_result_still_shows_a_recognized_text_and_status(window, status, label):
    window.controller.publish(status, candidates=[])
    callout = window.highlight_overlay.callouts[0]
    assert callout['anchor'] == (10.0, 10.0, 80.0, 30.0)
    assert callout['status'] == status
    assert '王小明' in callout['display_lines'][0] and label in callout['display_lines'][0]
    assert callout['boxes'] == ()


def test_start_hides_panel_restore_opens_normal_panel_pause_removes_floating_ui(window):
    window.open()
    window._start()
    assert window.controller.running and not window.isVisible()
    assert window.resume_button.isVisible()
    window.controller.publish()
    assert window.highlight_overlay.isVisible()
    window.resume_button.click()
    assert window.isVisible() and window.controller.running
    assert not window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    window._pause_button.click()
    assert not window.controller.running
    assert not window.highlight_overlay.isVisible() and not window.resume_button.isVisible()


def test_restore_control_remains_available_while_waiting_without_old_results(window):
    window._start()
    window.controller.publish()
    assert window.before_capture()
    window.controller.state_changed.emit('checking')
    assert not window.resume_button.isVisible()
    window.after_capture()
    assert not window.highlight_overlay.isVisible()
    assert window.resume_button.isVisible()
    window.set_pinned(False)
    assert not window.resume_button.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    window.set_pinned(True)
    assert window.resume_button.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    window.close()
    window.after_capture()
    assert not window.resume_button.isVisible()


def test_capture_failure_returns_hidden_panel_to_user(window):
    window._start()
    window.controller.pause()
    window.controller.failed.emit('請重新框選')
    assert window.isVisible() and window._note.text() == '請重新框選'
    assert not window.resume_button.isVisible()


def test_native_annotation_failure_does_not_publish_partial_result(window, monkeypatch):
    monkeypatch.setattr('src.ui.search_highlight_overlay.apply_native_pin', lambda *args: False)
    window.controller.publish()
    assert not window.controller.running and not window.highlight_overlay.isVisible()
    assert window.isVisible() and window._status.text() == '需要確認'


@pytest.mark.parametrize('stage', ['capture_restore', 'toggle'])
def test_failed_native_repin_restores_controls_and_clears_result(window, monkeypatch, stage):
    window.controller.publish()
    if stage == 'capture_restore':
        assert window.before_capture()
    monkeypatch.setattr('src.ui.search_highlight_overlay.apply_native_pin', lambda *args: False)
    if stage == 'capture_restore':
        window.after_capture()
    else:
        window.set_pinned(False)
    assert not window.controller.running
    assert not window.highlight_overlay.isVisible()
    assert not window.resume_button.isVisible()
    assert window.isVisible()


def test_restore_control_restores_previously_minimized_panel_repeatedly(window):
    window._start()
    for _ in range(3):
        window.open()
        window.showMinimized()
        assert window.isMinimized()
        window.resume_button.click()
        assert window.isVisible() and not window.isMinimized()
        assert window.controller.running
