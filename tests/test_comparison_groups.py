"""Independent group state on one serialized, cancellable comparison pipeline."""
import pytest

from src.comparison.controller import ComparisonController
from test_comparison_controller import FakeCapture, FakeOcr


def configure(controller, first_x=0):
    assert controller.set_region('A', (first_x, 0, 5, 5, 1))
    assert controller.set_region('B', (first_x + 10, 0, 5, 5, 1))


@pytest.fixture
def grouped(qapp):
    ocr, capture = FakeOcr(), FakeCapture()
    controller = ComparisonController(ocr, capture_worker=capture)
    first = controller.active_group_id
    configure(controller)
    second = controller.add_group('訂單金額')
    configure(controller, 30)
    assert controller.select_group(first)
    yield controller, ocr, capture, first, second
    controller.shutdown()


def advance(controller):
    controller._cycle_timer.stop()
    controller._begin_cycle()


def finish(capture, ocr, values, texts=('甲123', '甲123')):
    capture.finish(values=values)
    ocr.finish(texts[0])
    ocr.finish(texts[1])


def result_for(controller, group_id):
    return next(group['result'] for group in controller.groups if group['id'] == group_id)


def test_all_groups_run_serially_with_independent_positions_results_and_round_interval(grouped):
    controller, ocr, capture, first, second = grouped
    updates = []
    controller.updated.connect(updates.append)
    assert controller.start()
    assert len(capture.requests) == 1 and capture.requests[0][1] == controller.groups[0]['regions']
    assert controller.scanning_group_id == first
    finish(capture, ocr, (1, 2))
    assert len(capture.requests) == 1 and len(ocr.requests) == 2
    assert controller._cycle_timer.interval() == 0
    assert result_for(controller, first)['cycle'] == 1
    assert result_for(controller, second) is None
    advance(controller)
    assert capture.requests[-1][1] == controller.groups[1]['regions']
    assert controller.scanning_group_id == second
    finish(capture, ocr, (3, 4), ('甲123', '甲456'))
    assert controller._cycle_timer.interval() == controller.interval * 1000
    assert [update['group_id'] for update in updates] == [first, second]
    assert [update['verdict'] for update in updates] == ['match', 'mismatch']
    assert all(update['cycle'] == 1 for update in updates)
    assert len(ocr.requests) == 4 and not controller._images and not controller._results
    assert not controller._retry.isActive() and not controller._deadline.isActive()


def test_each_group_skips_its_own_unchanged_frame_and_only_changed_pair_runs(grouped):
    controller, ocr, capture, first, second = grouped
    updates = []
    controller.updated.connect(updates.append)
    controller.start()
    finish(capture, ocr, (1, 2))
    advance(controller)
    finish(capture, ocr, (3, 4))
    originals = {group['id']: group['result'] for group in controller.groups}
    for _ in range(3):
        advance(controller)
        capture.finish(values=(1, 2))
        assert controller._cycle_timer.interval() == 0
        advance(controller)
        capture.finish(values=(3, 4))
        assert controller._cycle_timer.interval() == controller.interval * 1000
        assert len(ocr.requests) == 4 and len(updates) == 2
        assert {group['id']: group['result'] for group in controller.groups} == originals
    advance(controller)
    capture.finish(values=(1, 2))
    advance(controller)
    finish(capture, ocr, (3, 5), ('新甲', '新乙'))
    assert len(ocr.requests) == 6 and len(updates) == 3
    assert result_for(controller, first) == originals[first]
    assert result_for(controller, second)['cycle'] == 2
    assert result_for(controller, second)['verdict'] == 'mismatch'


def test_view_selection_during_inference_cannot_redirect_the_running_pair(grouped):
    controller, ocr, capture, first, second = grouped
    controller.start()
    capture.finish(values=(1, 2))
    assert controller.select_group(second) and controller.running
    assert controller.regions == controller.groups[1]['regions']
    assert controller.scanning_group_id == first
    ocr.finish('第一組')
    ocr.finish('第一組')
    assert result_for(controller, first)['a'] == '第一組'
    assert result_for(controller, second) is None
    advance(controller)
    assert capture.requests[-1][1] == controller.groups[1]['regions']


@pytest.mark.parametrize('stage', ['capture', 'first_ocr', 'second_ocr', 'between_groups'])
def test_pausing_at_any_boundary_cancels_remaining_groups_and_clears_hashes(grouped, stage):
    controller, ocr, capture, first, second = grouped
    updates = []
    controller.updated.connect(updates.append)
    controller.start()
    if stage != 'capture':
        capture.finish(values=(1, 2))
    if stage in ('second_ocr', 'between_groups'):
        ocr.finish('甲')
    if stage == 'between_groups':
        ocr.finish('甲')
    requests_before = len(ocr.requests)
    controller.pause()
    assert not controller.running and controller.scanning_group_id is None
    assert controller._event.is_set()
    assert not controller._images and not controller._results
    assert not any(group['fingerprint'] for group in controller._groups.values())
    assert not any(timer.isActive() for timer in
                   (controller._cycle_timer, controller._retry, controller._deadline))
    if stage == 'capture':
        capture.finish(values=(1, 2))
    elif stage != 'between_groups':
        ocr.finish('晚到結果')
    controller._begin_cycle()
    assert len(capture.requests) == 1 and len(ocr.requests) == requests_before
    assert len(updates) == int(stage == 'between_groups')
    assert set(controller.groups[0]['regions']) == {'A', 'B'}


def test_delete_inflight_group_ignores_its_late_result_then_other_group_can_run(grouped):
    controller, ocr, capture, first, second = grouped
    updates = []
    controller.updated.connect(updates.append)
    controller.start()
    capture.finish(values=(1, 2))
    assert controller.remove_group(first)
    ocr.finish('移除組別的舊結果')
    assert not updates and len(ocr.requests) == 1
    assert controller.active_group_id == second
    assert controller.start()
    finish(capture, ocr, (3, 4), ('留下的組別', '留下的組別'))
    assert [update['group_id'] for update in updates] == [second]
    assert controller.groups[0]['result']['a'] == '留下的組別'


def test_remove_during_native_capture_then_restart_does_not_queue_behind_old_frame(grouped, qtbot):
    controller, ocr, capture, first, second = grouped
    updates = []
    controller.updated.connect(updates.append)
    controller.start()
    assert controller.remove_group(first)
    assert capture.requests[0][-1].is_set()
    assert controller.start()
    assert len(capture.requests) == 1
    capture.finish(index=0, values=(1, 2))
    assert not ocr.requests
    qtbot.waitUntil(lambda: len(capture.requests) == 2)
    assert capture.requests[1][1] == controller.groups[0]['regions']
    finish(capture, ocr, (3, 4))
    assert len(ocr.requests) == 2
    assert [update['group_id'] for update in updates] == [second]


def test_missing_group_position_blocks_entire_batch_before_capture(grouped):
    controller, _, capture, _, _ = grouped
    missing = controller.add_group('未設定組別')
    failures = []
    controller.failed.connect(failures.append)
    assert not controller.start() and not capture.requests
    assert failures == ['「未設定組別」尚未選好 A、B 範圍。']
    assert controller.remove_group(missing)
    assert controller.start()


@pytest.mark.parametrize('name', ['', ' ', 'a\nb', 'a\rb', 'a\tb', 'a\u2028b', 'a\u2029b', 'a' * 33, None, 123])
def test_group_rename_rejects_invalid_labels_without_altering_positions(grouped, name):
    controller, _, _, first, _ = grouped
    before = controller.groups
    assert not controller.rename_group(first, name)
    assert controller.groups == before


def test_group_capacity_duplicates_minimum_and_session_only_snapshots(grouped):
    controller, _, _, first, second = grouped
    assert controller.add_group('訂單金額') is None
    assert controller.rename_group(first, 'Order')
    assert controller.add_group('order') is None
    for _ in range(controller.MAX_GROUPS - len(controller.groups)):
        assert controller.add_group() is not None
    assert len(controller.groups) == 20 and controller.add_group() is None
    exposed = controller.groups
    exposed[0]['regions'].clear()
    assert controller.groups[0]['regions']
    for group in list(controller.groups)[1:]:
        assert controller.remove_group(group['id'])
    assert not controller.remove_group(first)
    assert controller.active_group_id == first and len(controller.groups) == 1


def test_all_group_positions_invalidated_and_target_guard_includes_unselected_regions(grouped):
    controller, _, _, first, second = grouped
    expected = tuple(region for group in controller.groups for region in group['regions'].values())
    assert controller.target_regions == expected
    controller.start()
    controller.invalidate_regions('')
    assert not controller.running and not controller.target_regions
    assert all(not group['regions'] and group['result'] is None for group in controller.groups)
    assert not controller.start()


def test_native_layout_change_invalidates_all_groups_even_if_view_is_elsewhere(grouped):
    controller, _, capture, first, second = grouped
    controller.start()
    assert controller.select_group(second)
    token = capture.requests[0][0]
    capture.failed.emit(token, '螢幕配置已變更', True)
    assert not controller.running and not controller.target_regions
    assert all(not group['regions'] and group['result'] is None for group in controller.groups)
    assert controller.active_group_id == second and not controller.start()


def test_reselection_clears_only_selected_group_result_and_mode_clears_all_results(grouped):
    controller, ocr, capture, first, second = grouped
    controller.start()
    finish(capture, ocr, (1, 2))
    advance(controller)
    finish(capture, ocr, (3, 4))
    assert controller.set_region('A', (1, 0, 5, 5, 1))
    assert result_for(controller, first) is None
    assert result_for(controller, second) is not None
    assert controller.set_mode('exact')
    assert all(group['result'] is None for group in controller.groups)


def test_reentrant_deletion_on_result_notification_cannot_publish_removed_group(grouped):
    controller, ocr, capture, first, second = grouped
    updates = []
    controller.updated.connect(updates.append)
    controller.groups_changed.connect(lambda: controller.remove_group(first)
                                      if controller.running and result_for(controller, first) else None)
    controller.start()
    finish(capture, ocr, (1, 2))
    assert not updates and [group['id'] for group in controller.groups] == [second]
