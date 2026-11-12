"""Whole-region rows use same-frame OCR and keep the invoice ROI fixed."""
import pytest
from test_shared_search_controller import Capture, Ocr, take, next_cycle
from src.comparison.shared_controller import SharedSearchController


def line(text, x, y, width=35, height=12):
    return {'text': text, 'confidence': .99,
            'box': [[x, y], [x + width, y], [x + width, y + height], [x, y + height]]}


SOURCE = [line('12345', 2, 2), line('7000', 45, 2), line('54321', 2, 32)]
INVOICE = [line('12346', 2, 2), line('7000', 2, 32), line('54321', 2, 62)]


def finish(ocr, rows):
    token, page, name, *_ = ocr.requests[-1]
    ocr.document_ocr_done.emit(token, page, name,
                              {'text': '\n'.join(row['text'] for row in rows),
                               'status': 'done', 'detail': rows})


def complete(controller, ocr, capture, values=None, source=SOURCE, invoice=INVOICE):
    while controller._inflight is not None:
        finish(ocr, invoice if controller._inflight == 'B' else source)
    take(controller, capture, values)


@pytest.fixture
def rows_controller(qapp):
    ocr, capture = Ocr(), Capture()
    controller = SharedSearchController(ocr, capture_worker=capture, settle_ms=0)
    assert controller.set_source_mode('rows')
    assert controller.set_source_region((5, 10, 80, 60, 1))
    assert controller.set_b_region((110, 0, 100, 100, 1))
    yield controller, ocr, capture
    controller.shutdown()


def test_whole_region_reads_invoice_then_source_and_exposes_real_row_values(rows_controller):
    controller, ocr, capture = rows_controller
    updates = []
    controller.updated.connect(updates.append)
    assert controller.fields == [] and controller.active_row_region is None
    assert controller.start()
    take(controller, capture)
    assert ocr.requests[-1][2] == 'B'
    finish(ocr, INVOICE)
    assert ocr.requests[-1][2] == 'SOURCE'
    finish(ocr, SOURCE)
    assert not updates and controller.fields == []
    take(controller, capture)
    assert len(updates) == 1 and len(controller.source_rows) == 2
    assert controller.active_row_region == (7, 12, 78, 12, 1)
    assert controller.source_rows[0]['value_count'] == 2
    assert [field['result']['text'] for field in controller.fields] == ['12345', '7000']
    assert [field['result']['status'] for field in controller.fields] == ['uncertain', 'matched']
    assert controller.fields[0]['result']['score'] == .8
    assert updates[0]['source_mode'] == 'rows' and updates[0]['current_row_index'] == 0
    assert 'text' not in controller.source_rows[0]


def test_row_arrow_reuses_only_unchanged_invoice_and_never_moves_either_roi(rows_controller):
    controller, ocr, capture = rows_controller
    controller.start(); take(controller, capture); complete(controller, ocr, capture)
    b, source = controller.b_region, controller.source_region
    assert controller.move_rows(1)
    assert controller.current_row_index == 1 and controller.active_row_region == (7, 42, 35, 12, 1)
    assert controller.fields == []
    take(controller, capture)
    assert [job[2] for job in ocr.requests] == ['B', 'SOURCE', 'SOURCE']
    complete(controller, ocr, capture)
    assert [field['result']['text'] for field in controller.fields] == ['54321']
    assert controller.b_region == b and controller.source_region == source
    assert not controller.move_rows(1) and controller.current_row_index == 1 and controller.running
    assert controller.move_rows(-1)
    take(controller, capture); complete(controller, ocr, capture)
    assert len(controller.fields) == 2 and controller.b_region == b


def test_changed_invoice_same_roi_invalidates_cache_and_old_display(rows_controller):
    controller, ocr, capture = rows_controller
    controller.start(); take(controller, capture); complete(controller, ocr, capture)
    b = controller.b_region
    next_cycle(controller); take(controller, capture, {'B': 9})
    assert all(field['result'] is None for field in controller.fields)
    assert ocr.requests[-1][2] == 'B'
    complete(controller, ocr, capture, {'B': 9}, invoice=[line('98765', 2, 2)])
    assert controller.b_region == b
    assert all(field['result']['status'] != 'matched' for field in controller.fields)


def test_invoice_change_during_read_discards_every_value_before_publication(rows_controller):
    controller, ocr, capture = rows_controller
    published = []
    controller.updated.connect(published.append)
    controller.start(); take(controller, capture)
    finish(ocr, INVOICE); finish(ocr, SOURCE)
    take(controller, capture, {'B': 9})
    assert not published and controller.fields == [] and controller.state == 'changed'
    next_cycle(controller); take(controller, capture, {'B': 9})
    assert ocr.requests[-1][2] == 'B'


def test_manual_refresh_forces_both_ocr_while_automatic_unchanged_skips(rows_controller):
    controller, ocr, capture = rows_controller
    controller.start(); take(controller, capture); complete(controller, ocr, capture)
    next_cycle(controller); take(controller, capture)
    assert len(ocr.requests) == 2
    assert controller.refresh_now()
    take(controller, capture); complete(controller, ocr, capture)
    assert [job[2] for job in ocr.requests] == ['B', 'SOURCE', 'B', 'SOURCE']
    assert controller.current_row_index == 0 and len(controller.fields) == 2


def test_disappearing_current_row_does_not_silently_switch_to_another_row(rows_controller):
    controller, ocr, capture = rows_controller
    controller.start(); take(controller, capture); complete(controller, ocr, capture)
    controller.move_rows(1); take(controller, capture); complete(controller, ocr, capture)
    next_cycle(controller); take(controller, capture, {'SOURCE': 9})
    complete(controller, ocr, capture, {'SOURCE': 9}, source=SOURCE[:2])
    assert controller.current_row_index == 1 and controller.fields == []
    assert controller.active_row_region is None and controller.source_reason
    assert controller.move_rows(-1)


def test_mode_switch_retires_late_source_and_preserves_legacy_fields(rows_controller):
    controller, ocr, capture = rows_controller
    controller.start(); take(controller, capture)
    finish(ocr, INVOICE)
    token, page, name, *_ = ocr.requests[-1]
    assert controller.set_source_mode('fields')
    ocr.document_ocr_done.emit(token, page, name, {'text': '12345', 'status': 'done', 'detail': SOURCE})
    assert not controller.running and controller.source_rows == []
    assert len(controller.fields) == 1 and controller.fields[0]['region'] is None
    assert controller.active_row_region is None and controller.b_region == (110, 0, 100, 100, 1)
    assert not any(timer.isActive() for timer in (controller._poll, controller._settle, controller._retry, controller._deadline))


def test_source_reframe_is_atomic_and_clears_row_metadata(rows_controller):
    controller, ocr, capture = rows_controller
    controller.start(); take(controller, capture); complete(controller, ocr, capture)
    old_region = controller.source_region
    assert not controller.set_source_region((110, 5, 20, 20, 1))
    assert controller.source_region == old_region and len(controller.source_rows) == 2
    assert not controller.running and all(field['result'] is None for field in controller.fields)
    assert controller.set_source_region((5, 10, 85, 60, 1))
    assert controller.source_rows == [] and controller.current_row_index == 0
    assert controller.fields == [] and controller.active_row_region is None


def test_row_metadata_is_copy_and_fractional_boxes_have_valid_capture_regions(rows_controller):
    controller, ocr, capture = rows_controller
    fractional = [line('1250', 2.2, 2.2, 35.2, 11.2)]
    controller.start(); take(controller, capture); complete(controller, ocr, capture, source=fractional)
    assert controller.active_row_region == (7, 12, 36, 12, 1)
    assert all(type(value) is int for value in controller.active_row_region)
    copy = controller.source_rows
    copy[0]['region'] = (0, 0, 1, 1, 1)
    assert controller.active_row_region != copy[0]['region']
    controller.invalidate_regions('')
    assert controller.source_region is None and controller.source_rows == []


def test_explicit_previous_arrow_recovers_after_many_rows_disappear(rows_controller):
    controller, ocr, capture = rows_controller
    source = [line(str(1200 + index), 2, index * 15) for index in range(4)]
    controller.start(); take(controller, capture); complete(controller, ocr, capture, source=source)
    assert controller.set_current_row(3)
    take(controller, capture); complete(controller, ocr, capture, source=source)
    next_cycle(controller); take(controller, capture, {'SOURCE': 8})
    complete(controller, ocr, capture, {'SOURCE': 8}, source=source[:1])
    assert controller.current_row_index == 3 and controller.active_row_region is None
    assert controller.fields == []
    assert controller.move_rows(-1) and controller.current_row_index == 0
    take(controller, capture, {'SOURCE': 8})
    complete(controller, ocr, capture, {'SOURCE': 8}, source=source[:1])
    assert controller.fields[0]['result']['text'] == '1200'


@pytest.mark.parametrize('setting', ['source', 'mode', 'row', 'invoice'])
def test_shutdown_reentered_during_pause_rejects_configuration_mutation(rows_controller, setting):
    controller, ocr, capture = rows_controller
    controller.start(); take(controller, capture); complete(controller, ocr, capture)
    original = controller.source_region, controller.b_region, controller.source_mode
    controller.state_changed.connect(lambda state: controller.shutdown() if state == 'paused' else None)
    if setting == 'source':
        accepted = controller.set_source_region((5, 10, 85, 60, 1))
    elif setting == 'invoice':
        accepted = controller.set_b_region((115, 0, 100, 100, 1))
    elif setting == 'mode':
        accepted = controller.set_source_mode('fields')
    else:
        accepted = controller.set_current_row(1)
    assert not accepted and not controller.running
    assert (controller.source_region, controller.b_region, controller.source_mode) == original
    assert not any(timer.isActive() for timer in (controller._poll, controller._settle, controller._retry, controller._deadline))


def test_whole_source_blue_follow_selects_ocr_row_without_moving_regions(rows_controller):
    controller, ocr, capture = rows_controller
    assert controller.set_tracking_region((0, 0, 100, 100, 1))
    controller.start(); take(controller, capture); complete(controller, ocr, capture)
    invoice, source = controller.b_region, controller.source_region
    assert controller.calibrate_tracking()
    take(controller, capture, row=8)
    assert controller.calibrated
    assert controller.set_auto_follow(True) and controller.start()
    take(controller, capture, row=8)
    if controller._inflight is not None:
        complete(controller, ocr, capture)
    # A new blue row is accepted only after repeated stable observations.
    next_cycle(controller); take(controller, capture, row=38)
    assert controller.state == 'tracking_pending'
    next_cycle(controller); take(controller, capture, row=38)
    assert controller._inflight == 'SOURCE'
    finish(ocr, SOURCE)
    take(controller, capture, row=38)
    assert controller.current_row_index == 1
    assert [field['result']['text'] for field in controller.fields] == ['54321']
    assert controller.b_region == invoice and controller.source_region == source


def test_closing_real_window_during_row_pause_cannot_restart_hidden_ocr(qtbot, rows_controller):
    from src.ui.shared_search_window import SharedSearchWindow
    controller, ocr, capture = rows_controller
    window = SharedSearchWindow(controller)
    for owned in (window, window.highlight_overlay, window.resume_button, window.manual_button):
        qtbot.addWidget(owned)
    controller.start(); take(controller, capture); complete(controller, ocr, capture)
    closed = []

    def close_during_pause(state):
        if state == 'paused' and not closed:
            closed.append(True)
            window.close()

    controller.state_changed.connect(close_during_pause)
    count = len(ocr.requests)
    assert not controller.move_rows(1)
    assert window._closed_by_user and not controller.running
    assert controller.current_row_index == 0 and len(ocr.requests) == count
    assert not any(timer.isActive() for timer in (controller._poll, controller._settle, controller._retry, controller._deadline))
    window.shutdown()
