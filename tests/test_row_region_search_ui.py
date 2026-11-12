"""Whole-source selection, current-row marks and preview routes share one lifecycle."""
import copy

import pytest

from src.ui.shared_search_window import SharedSearchWindow
from test_invoice_search_ui import InvoiceController


class RowController(InvoiceController):
    def __init__(self):
        super().__init__()
        self.fields = []
        self.source_mode = 'fields'
        self.source_region = None
        self.source_rows = []
        self.current_row_index = 0
        self.active_row_region = None

    def set_source_mode(self, mode):
        self.pause()
        self.source_mode = mode
        self.fields = []
        self.source_rows = []
        self.active_row_region = None
        self.fields_changed.emit()
        return True

    def set_source_region(self, region):
        self.pause()
        self.source_region = region
        self.fields_changed.emit()
        return True

    def publish_row(self, index=0):
        self.running = True
        self.source_rows = [{'id': 'row-0', 'region': (20, 30, 150, 20, 1), 'value_count': 1},
                            {'id': 'row-1', 'region': (20, 65, 150, 20, 1), 'value_count': 1}]
        self.current_row_index = index
        self.active_row_region = self.source_rows[index]['region']
        value = {'id': 'near', 'text': '12346', 'score': .8, 'needs_review': True,
                 'match_type': 'similar', 'boxes': [[[10, 10], [60, 10], [60, 30], [10, 30]]]}
        self.fields = [{'id': 'row-value', 'name': '數值 1', 'region': self.active_row_region,
                        'result': {'status': 'uncertain', 'text': '12345', 'selected_id': 'near',
                                   'candidates': [value]}}]
        snapshot = self.snapshot()
        snapshot['active_row_region'] = self.active_row_region
        self.updated.emit(snapshot)

    def move_rows(self, delta):
        self.publish_row(self.current_row_index + delta)
        return True


@pytest.fixture
def window(qtbot):
    controller = RowController()
    widget = SharedSearchWindow(controller)
    for owned in (widget, widget.highlight_overlay, widget.resume_button, widget.manual_button):
        qtbot.addWidget(owned)
    yield widget
    widget.shutdown()


def test_fresh_source_uses_screen_region_and_mode_switch_survives_pause_refresh(window):
    assert window.controller.source_mode == 'rows'
    assert window._source_mode.currentData() == 'rows'
    assert not window._start_button.isEnabled()
    requested = []
    window.select_requested.connect(requested.append)
    window._select_source.click()
    assert requested == ['SOURCE']
    assert window.set_region('SOURCE', (20, 20, 150, 120, 1))
    assert window._start_button.isEnabled()  # A can start before any row OCR exists.
    assert not window._previous.isEnabled() and not window._next.isEnabled()
    b = window.controller.b_region
    window._source_mode.setCurrentIndex(window._source_mode.findData('fields'))
    assert window.controller.source_mode == 'fields'
    window._source_mode.setCurrentIndex(window._source_mode.findData('rows'))
    assert window.controller.source_mode == 'rows'
    assert window.controller.b_region == b
    assert '20, 20' not in window._source_label.text()
    assert '200, 100' not in window._b_label.text()


def test_current_row_arrow_and_near_value_follow_source_row_without_moving_invoice(window):
    window.set_region('SOURCE', (20, 20, 150, 120, 1))
    source, invoice = window.controller.source_region, window.controller.b_region
    window.controller.publish_row()
    assert '第 1 列／共 2 列' in window._current_row.text()
    assert window.highlight_overlay.source_row == (20, 30, 150, 20)
    assert window.highlight_overlay.source_row_arrow is not None
    callout = window.highlight_overlay.callouts[0]
    assert callout['display_lines'][0].endswith('80%')
    assert callout['status'] == 'similar'
    card = window._fields_cards['row-value']
    for button in (card._select, card._preview, card._edit, card._remove):
        assert button.isHidden()
    assert not window._previous.isEnabled() and window._next.isEnabled()
    window._next.click()
    assert '第 2 列／共 2 列' in window._current_row.text()
    assert window.highlight_overlay.source_row == (20, 65, 150, 20)
    assert window.controller.source_region == source and window.controller.b_region == invoice
    assert window._previous.isEnabled() and not window._next.isEnabled()


def test_preview_pauses_and_capture_close_cannot_resurrect_source_arrow(window):
    window.set_region('SOURCE', (20, 20, 150, 120, 1))
    window.controller.publish_row()
    previews = []
    window.preview_requested.connect(previews.append)
    window._preview_source.click()
    assert previews == ['SOURCE']
    assert not window.controller.running and window.highlight_overlay.source_row is None
    window.controller.publish_row()
    assert window.before_capture()
    assert not window.highlight_overlay.isVisible()
    window._background_transparency.setValue(50)
    assert not window.highlight_overlay.isVisible()
    window.close()
    window.after_capture()
    window.controller.updated.emit(copy.deepcopy(window.controller.snapshot()))
    assert window.highlight_overlay.source_row is None
    assert not window.highlight_overlay.isVisible()
    window._preview_region('B')
    assert previews == ['SOURCE']


def test_queued_legacy_field_edit_cannot_mutate_virtual_row_values(window, monkeypatch):
    window.set_region('SOURCE', (20, 20, 150, 120, 1))
    window.controller.publish_row()
    before = copy.deepcopy(window.controller.fields)
    selections = []
    window.select_requested.connect(selections.append)
    monkeypatch.setattr('src.ui.shared_search_window.QInputDialog.getText',
                        lambda *args, **kwargs: pytest.fail('Virtual values must not open a rename dialog'))
    window._select('row-value')
    window._preview_region('row-value')
    window._rename('row-value')
    window._remove('row-value')
    window._add_field()
    assert window.controller.fields == before
    assert not selections


def test_removed_current_row_explains_missing_row_and_allows_explicit_previous(window):
    window.set_region('SOURCE', (20, 20, 150, 120, 1))
    window.controller.publish_row()
    window.controller.current_row_index = 2
    window.controller.active_row_region = None
    window.controller.source_reason = '目前列已不在畫面，請按上一列選擇。'
    window.controller.fields = []
    window.controller.updated.emit(window.controller.snapshot())
    assert window.controller.source_reason == window._current_row.text()
    assert window._previous.isEnabled() and not window._next.isEnabled()
    assert window.highlight_overlay.source_row is None
    window._previous.click()
    assert window.controller.current_row_index == 1
    assert '第 2 列／共 2 列' in window._current_row.text()


def test_queued_row_and_selection_actions_after_close_do_not_restart(window):
    window.set_region('SOURCE', (20, 20, 150, 120, 1))
    window.controller.publish_row()
    selected = []
    window.select_requested.connect(selected.append)
    window.close()
    window._move_rows(1)
    window._select('SOURCE')
    window._source_mode.setCurrentIndex(window._source_mode.findData('fields'))
    assert not window.controller.running
    assert window.controller.current_row_index == 0
    assert window.controller.source_mode == 'rows'
    assert window._source_mode.currentData() == 'rows'
    assert not selected


def test_real_controller_publishes_whole_row_near_and_exact_marks_and_moves_arrow(qtbot):
    from src.comparison.shared_controller import SharedSearchController
    from test_shared_search_controller import Capture, Ocr, take
    from test_shared_source_rows_controller import SOURCE, complete, line

    ocr, capture = Ocr(), Capture()
    controller = SharedSearchController(ocr, capture_worker=capture, settle_ms=0)
    widget = SharedSearchWindow(controller)
    for owned in (widget, widget.highlight_overlay, widget.resume_button, widget.manual_button):
        qtbot.addWidget(owned)
    try:
        assert controller.source_mode == 'rows'
        assert widget.set_region('SOURCE', (20, 20, 150, 110, 1))
        assert widget.set_region('B', (300, 20, 400, 220, 1))
        invoice = [line('12346', 10, 10, 90), line('7000', 10, 70, 90), line('54321', 10, 130, 90)]
        widget._start()
        take(controller, capture)
        complete(controller, ocr, capture, source=SOURCE, invoice=invoice)
        assert len(widget.highlight_overlay.callouts) == 2
        near = next(mark for mark in widget.highlight_overlay.callouts if mark['status'] == 'similar')
        assert near['display_lines'][0] == '12345 · 80%'
        assert widget.highlight_overlay.source_row == tuple(controller.active_row_region[:4])
        b, source = controller.b_region, controller.source_region
        assert widget._next.isEnabled()
        widget._next.click()
        take(controller, capture)
        complete(controller, ocr, capture, source=SOURCE, invoice=invoice)
        assert controller.current_row_index == 1
        assert controller.b_region == b and controller.source_region == source
        assert [field['result']['text'] for field in controller.fields] == ['54321']
        assert widget.highlight_overlay.source_row == tuple(controller.active_row_region[:4])
        assert '第 2 列／共 2 列' in widget._current_row.text()
    finally:
        widget.shutdown()


@pytest.mark.parametrize('action', ['preview', 'select', 'mode'])
def test_source_action_cannot_continue_when_pause_synchronously_closes_window(window, action):
    window.set_region('SOURCE', (20, 20, 150, 120, 1))
    closed = []
    requests = []
    window.preview_requested.connect(requests.append)
    window.select_requested.connect(requests.append)

    def close_on_pause(state):
        if state == 'paused' and not closed:
            closed.append(True)
            window.close()

    window.controller.state_changed.connect(close_on_pause)
    if action == 'mode':
        window._source_mode.setCurrentIndex(window._source_mode.findData('fields'))
    elif action == 'preview':
        window._preview_region('SOURCE')
    else:
        window._select('SOURCE')
    assert closed and window.controller.source_mode == 'rows'
    assert not requests
    assert not window.controller.running
