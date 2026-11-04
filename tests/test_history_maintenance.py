"""Non-destructive age archiving, bounded owner-thread work and cancellation."""
from datetime import datetime, timedelta, timezone
from threading import Event

import pytest
from PySide6.QtCore import QObject, Signal

from src.data.database import Database
from src.data.repository import ItemRepository
from src.workers.history_maintenance import HistoryMaintenanceController


NOW = datetime(2026, 11, 4, 12, 0, 0)
CUTOFF = NOW - timedelta(days=90)


def dated(item_repo, make_text_dto, text='old', *, updated=CUTOFF, **flags):
    item_id = item_repo.insert(make_text_dto(text))
    stamp = updated.strftime('%Y-%m-%d %H:%M:%S') if isinstance(updated, datetime) else updated
    values = {'created_at': '2020-01-01 00:00:00', 'updated_at': stamp, **flags}
    item_repo._conn.execute('UPDATE items SET ' + ','.join(key + '=?' for key in values) + ' WHERE id=?',
                            (*values.values(), item_id))
    item_repo._conn.commit()
    return item_id


def archive(repo, **kwargs):
    return repo.archive_inactive_batch(90, now=NOW, **kwargs)


def test_exact_90_day_boundary_uses_last_update_not_creation(item_repo, make_text_dto):
    eligible = dated(item_repo, make_text_dto)
    older = dated(item_repo, make_text_dto, 'older', updated=CUTOFF - timedelta(seconds=1))
    recent = dated(item_repo, make_text_dto, 'recent', updated=CUTOFF + timedelta(seconds=1))
    result = archive(item_repo)
    assert set(result['archived_ids']) == {eligible, older}
    assert result['has_more'] is False and result['cancelled'] is False
    assert not item_repo.get_by_id(recent).is_archived
    assert item_repo.get_by_id(recent).created_at == '2020-01-01 00:00:00'
    assert archive(item_repo)['archived_ids'] == []


def test_pinned_deleted_archived_and_active_ocr_are_protected(item_repo, make_text_dto):
    protected = [dated(item_repo, make_text_dto, **flags) for flags in (
        {'is_pinned': 1}, {'is_deleted': 1}, {'is_archived': 1},
        {'ocr_status': 'pending'}, {'ocr_status': 'processing'})]
    active_job = dated(item_repo, make_text_dto)
    job, _ = item_repo.begin_ocr_attempt(active_job)
    item_repo._conn.execute("UPDATE items SET ocr_status='done',updated_at=? WHERE id=?",
                            (CUTOFF.strftime('%Y-%m-%d %H:%M:%S'), active_job))
    item_repo._conn.commit()
    before = {item_id: item_repo.get_by_id(item_id) for item_id in [*protected, active_job]}
    assert archive(item_repo)['archived_ids'] == []
    assert {item_id: item_repo.get_by_id(item_id) for item_id in before} == before
    assert item_repo.get_ocr_attempt(active_job, job)['completed_at'] is None


def test_obsolete_pending_attempt_does_not_block_completed_current_job(item_repo, make_text_dto, make_ocr_result):
    item_id = dated(item_repo, make_text_dto)
    obsolete, _ = item_repo.begin_ocr_attempt(item_id)
    current, revision = item_repo.begin_ocr_attempt(item_id)
    result = make_ocr_result('current')
    result.job_id, result.base_edit_revision = current, revision
    assert item_repo.update_ocr_result(item_id, result)
    item_repo._conn.execute('UPDATE items SET updated_at=? WHERE id=?',
                            (CUTOFF.strftime('%Y-%m-%d %H:%M:%S'), item_id))
    item_repo._conn.commit()
    assert archive(item_repo)['archived_ids'] == [item_id]
    assert item_repo.get_ocr_attempt(item_id, obsolete)['completed_at'] is None


@pytest.mark.parametrize('stamp', [None, '', 'bad-date', '2024-02-30 00:00:00',
                                  '2024-01-01T00:00:00', '2024-01-01 00:00:00Z'])
def test_malformed_legacy_timestamps_are_not_archived(item_repo, make_text_dto, stamp):
    item_id = dated(item_repo, make_text_dto, updated=stamp)
    assert archive(item_repo)['archived_ids'] == []
    assert not item_repo.get_by_id(item_id).is_archived


def test_batches_are_bounded_and_do_not_touch_text_or_attachments(item_repo, make_image_dto, file_mgr):
    paths = []
    for name in ('captures/keep.png', 'thumbnails/keep.png'):
        path = file_mgr.get_abs_path(name)
        from pathlib import Path
        file = Path(path)
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(b'not modified by archive')
        paths.append(file)
    ids = [item_repo.insert(make_image_dto('captures/keep.png', 'thumbnails/keep.png')) for _ in range(5)]
    item_repo._conn.execute('UPDATE items SET text_content=?,updated_at=?',
                            ('原文123', CUTOFF.strftime('%Y-%m-%d %H:%M:%S')))
    item_repo._conn.commit()
    first, second, last = [archive(item_repo, limit=2) for _ in range(3)]
    assert [len(r['archived_ids']) for r in (first, second, last)] == [2, 2, 1]
    assert [r['has_more'] for r in (first, second, last)] == [True, True, False]
    assert all(item_repo.get_by_id(i).is_archived for i in ids)
    assert all(item_repo.get_by_id(i).text_content == '原文123' for i in ids)
    assert all(item_repo.get_by_id(i).raw_image_path == 'captures/keep.png' for i in ids)
    assert all(p.read_bytes() == b'not modified by archive' for p in paths)


@pytest.mark.parametrize('cancel_check', [1, 2, 3, 4])
def test_cancellation_rolls_back_even_after_update(item_repo, make_text_dto, cancel_check):
    item_id = dated(item_repo, make_text_dto)
    class Cancel:
        checks = 0

        def is_set(self):
            self.checks += 1
            return self.checks >= cancel_check
    result = archive(item_repo, cancelled=Cancel())
    assert result['cancelled'] is True and not result['archived_ids']
    assert not item_repo.get_by_id(item_id).is_archived
    assert item_repo.get_by_id(item_id).updated_at == CUTOFF.strftime('%Y-%m-%d %H:%M:%S')


def test_unarchive_and_edit_start_new_age_period(item_repo, make_text_dto, monkeypatch):
    item_id = dated(item_repo, make_text_dto)
    assert archive(item_repo)['archived_ids'] == [item_id]
    monkeypatch.setattr(item_repo, '_now', lambda: NOW.strftime('%Y-%m-%d %H:%M:%S'))
    item_repo.set_archived(item_id, False)
    assert not archive(item_repo)['archived_ids']
    item_repo._conn.execute('UPDATE items SET updated_at=? WHERE id=?',
                            (CUTOFF.strftime('%Y-%m-%d %H:%M:%S'), item_id))
    item_repo._conn.commit()
    item_repo.update_edited_text(item_id, 'new edit')
    assert not archive(item_repo)['archived_ids']


@pytest.mark.parametrize('assignment,value', [('is_pinned', 1), ('is_deleted', 1),
    ('ocr_status', 'processing'), ('updated_at', NOW.strftime('%Y-%m-%d %H:%M:%S'))])
def test_final_update_rechecks_item_changed_after_candidate_selection(
        item_repo, make_text_dto, assignment, value):
    item_id = dated(item_repo, make_text_dto)
    conn = item_repo._conn
    changed = []
    def competing_change(sql):
        if not changed and sql.startswith('UPDATE items SET is_archived=1,updated_at='):
            changed.append(True)
            conn.execute(f'UPDATE items SET {assignment}=? WHERE id=?', (value, item_id))
    conn.set_trace_callback(competing_change)
    try:
        result = archive(item_repo)
    finally:
        conn.set_trace_callback(None)
    assert changed and not result['archived_ids']
    assert not item_repo.get_by_id(item_id).is_archived


def test_real_tag_assignment_refreshes_age_but_noop_does_not(item_repo, tag_repo, make_text_dto):
    item_id = dated(item_repo, make_text_dto)
    tag = tag_repo.create('工作')
    tag_repo.add_to_item(item_id, tag.id)
    assert item_repo.get_by_id(item_id).updated_at != CUTOFF.strftime('%Y-%m-%d %H:%M:%S')
    item_repo._conn.execute('UPDATE items SET updated_at=? WHERE id=?',
                            (CUTOFF.strftime('%Y-%m-%d %H:%M:%S'), item_id))
    item_repo._conn.commit()
    tag_repo.add_to_item(item_id, tag.id)
    assert item_repo.get_by_id(item_id).updated_at == CUTOFF.strftime('%Y-%m-%d %H:%M:%S')
    tag_repo.remove_from_item(item_id, tag.id)
    assert item_repo.get_by_id(item_id).updated_at != CUTOFF.strftime('%Y-%m-%d %H:%M:%S')


@pytest.mark.parametrize('kwargs', [{'days': True}, {'days': -1}, {'days': 36501},
                                   {'limit': 0}, {'limit': 501}, {'limit': True},
                                   {'now': NOW.replace(tzinfo=timezone.utc)}])
def test_invalid_maintenance_arguments_fail_before_mutation(item_repo, make_text_dto, kwargs):
    item_id = dated(item_repo, make_text_dto)
    args = {'days': 90, 'now': NOW, **kwargs}
    with pytest.raises(ValueError):
        item_repo.archive_inactive_batch(**args)
    assert not item_repo.get_by_id(item_id).is_archived


def test_disabled_age_does_not_archive(item_repo, make_text_dto):
    item_id = dated(item_repo, make_text_dto)
    assert not item_repo.archive_inactive_batch(0, now=NOW)['archived_ids']
    assert not item_repo.get_by_id(item_id).is_archived


def test_archive_only_filter_keeps_existing_show_archived_behavior(item_repo, make_text_dto):
    normal = item_repo.insert(make_text_dto('normal'))
    archived = item_repo.insert(make_text_dto('archived'))
    deleted = item_repo.insert(make_text_dto('deleted archive'))
    item_repo.set_archived(archived, True)
    item_repo.set_archived(deleted, True)
    item_repo.soft_delete(deleted)
    assert {i.id for i in item_repo.list_recent()} == {normal}
    assert {i.id for i in item_repo.list_recent(show_archived=True)} == {normal, archived}
    assert {i.id for i in item_repo.list_recent(archived_only=True)} == {archived}
    assert {i.id for i in item_repo.list_recent(archived_only=True, show_deleted=True)} == {archived, deleted}


def test_legacy_fts_trigger_is_replaced_without_rebuilding_content(tmp_path, make_text_dto):
    path = str(tmp_path / 'legacy.db')
    db = Database(path)
    repo = ItemRepository(db)
    item_id = dated(repo, make_text_dto, 'searchable original')
    conn = db.get_connection()
    conn.execute('DROP TRIGGER items_fts_update')
    conn.execute("""CREATE TRIGGER items_fts_update AFTER UPDATE ON items BEGIN
        INSERT INTO items_fts(items_fts,rowid,text_content,edited_text,note_plaintext)
        VALUES('delete',old.id,old.text_content,old.edited_text,old.note_plaintext);
        INSERT INTO items_fts(rowid,text_content,edited_text,note_plaintext)
        VALUES(new.id,new.text_content,new.edited_text,new.note_plaintext); END""")
    conn.commit()
    db.close()
    db = Database(path)
    try:
        repo = ItemRepository(db)
        conn = db.get_connection()
        assert [i.id for i in repo.search_fulltext('searchable')] == [item_id]
        # SQLite total_changes includes trigger writes; metadata-only archive
        # must now change exactly the item row, not rebuild its FTS entries.
        before = conn.total_changes
        assert archive(repo)['archived_ids'] == [item_id]
        assert conn.total_changes - before == 1
        assert [i.id for i in repo.search_fulltext('searchable')] == [item_id]
        repo.update_edited_text(item_id, 'replacement novelterm')
        assert [i.id for i in repo.search_fulltext('novelterm')] == [item_id]
        from src.data.repository import _ARCHIVE_ELIGIBLE
        plan = conn.execute('EXPLAIN QUERY PLAN SELECT id FROM items INDEXED BY idx_items_archive_age '
                            f'WHERE {_ARCHIVE_ELIGIBLE} ORDER BY updated_at,id LIMIT 200',
                            ('2026-01-01 00:00:00',)).fetchall()
        assert any('idx_items_archive_age' in row['detail'] for row in plan)
        assert not any('TEMP B-TREE' in row['detail'] for row in plan)
    finally:
        db.close()


class Config:
    def __init__(self, days=90):
        self.days = days

    def get(self, section, key, default=None):
        return self.days


class Worker(QObject):
    history_maintenance_done = Signal(object)

    def __init__(self):
        super().__init__()
        self.requests = []

    def archive_inactive(self, request):
        self.requests.append(request)

    def reply(self, *, count=0, more=False, error=False, index=-1):
        request = self.requests[index]
        self.history_maintenance_done.emit({'request_id': request['request_id'],
            'generation': request['generation'], 'archived_ids': list(range(count)),
            'has_more': more, 'error': error, 'cancelled': request['cancelled'].is_set()})


def controller(qtbot, cfg=None):
    worker = Worker()
    cfg = cfg or Config()
    clock = [NOW]
    control = HistoryMaintenanceController(worker, cfg, clock=lambda: clock[0])
    return control, worker, cfg, clock


def test_startup_batches_yield_and_daily_check_coalesces_save_hooks(qtbot):
    control, worker, cfg, clock = controller(qtbot)
    changed = []
    control.archived.connect(changed.append)
    try:
        control.start()
        qtbot.waitUntil(lambda: len(worker.requests) == 1)
        assert worker.requests[0]['days'] == 90 and worker.requests[0]['limit'] == 200
        assert control._daily_timer.interval() == 24 * 60 * 60 * 1000
        for _ in range(20):
            control.on_item_saved(123)
        assert len(worker.requests) == 1
        worker.reply(count=200, more=True)
        assert len(worker.requests) == 1  # No synchronous recursive DB call.
        qtbot.waitUntil(lambda: len(worker.requests) == 2)
        worker.reply(count=2)
        assert changed == [200, 2]
        for _ in range(20):
            control.request_check()
        qtbot.wait(10)
        assert len(worker.requests) == 2
        clock[0] += timedelta(days=1)
        control.request_check()
        qtbot.waitUntil(lambda: len(worker.requests) == 3)
        worker.reply()
    finally:
        control.stop()


def test_settings_change_cancels_pending_generation_before_restarting(qtbot):
    control, worker, cfg, _ = controller(qtbot)
    try:
        control.start()
        qtbot.waitUntil(lambda: len(worker.requests) == 1)
        original = worker.requests[0]
        cfg.days = 30
        control.refresh()
        assert original['cancelled'].is_set() and len(worker.requests) == 1
        worker.reply()
        qtbot.waitUntil(lambda: len(worker.requests) == 2)
        assert worker.requests[1]['days'] == 30
        worker.reply()
        cfg.days = 0
        control.refresh()
        assert control.is_stopped and not control._daily_timer.isActive()
        control.request_check()
        assert len(worker.requests) == 2
    finally:
        control.stop()


@pytest.mark.parametrize('stage', ['before_batch', 'queued', 'between_batches'])
def test_shutdown_stops_timers_and_waits_only_accepted_reply(qtbot, stage):
    control, worker, _, _ = controller(qtbot)
    stopped = []
    control.stopped.connect(lambda: stopped.append(True))
    control.start()
    if stage != 'before_batch':
        qtbot.waitUntil(lambda: len(worker.requests) == 1)
    if stage == 'between_batches':
        worker.reply(count=1, more=True)
    control.stop()
    assert not control._daily_timer.isActive() and not control._batch_timer.isActive()
    if stage == 'queued':
        assert not control.is_stopped and not stopped
        worker.reply()
    assert control.is_stopped and stopped == [True]
    before = len(worker.requests)
    control.start()
    control.refresh()
    control.request_check()
    qtbot.wait(10)
    assert len(worker.requests) == before


def test_failure_is_reported_without_save_hook_retry_loop(qtbot):
    control, worker, _, _ = controller(qtbot)
    failures = []
    control.failed.connect(failures.append)
    try:
        control.start()
        qtbot.waitUntil(lambda: len(worker.requests) == 1)
        worker.reply(error=True)
        assert len(failures) == 1
        for _ in range(20):
            control.request_check()
        qtbot.wait(10)
        assert len(worker.requests) == 1
        control.request_check(force=True)
        qtbot.waitUntil(lambda: len(worker.requests) == 2)
        worker.reply()
    finally:
        control.stop()


def test_db_worker_archive_slot_preserves_files_and_returns_safe_summary(db_worker, item_repo, make_text_dto):
    item_id = dated(item_repo, make_text_dto)
    responses = []
    db_worker.history_maintenance_done.connect(responses.append)
    db_worker.archive_inactive({'request_id': 'fixed', 'generation': 1, 'days': 90,
                               'now': NOW, 'cancelled': Event(), 'limit': 1})
    assert responses[-1]['archived_ids'] == [item_id]
    assert responses[-1]['error'] is False
    db_worker.archive_inactive({'request_id': 'invalid', 'generation': 2, 'days': -1, 'now': NOW})
    assert responses[-1]['error'] is True and not responses[-1]['archived_ids']


def test_real_worker_thread_runs_and_stop_drains_maintenance(qtbot, item_repo, file_mgr, make_text_dto):
    from src.workers.db_worker import create_db_worker_in_thread
    ids = [dated(item_repo, make_text_dto, f'old {i}') for i in range(3)]
    worker, thread = create_db_worker_in_thread(item_repo, file_mgr)
    control = HistoryMaintenanceController(worker, Config(), clock=lambda: NOW)
    counts = []
    control.archived.connect(counts.append)
    try:
        control.start()
        qtbot.waitUntil(lambda: sum(counts) == 3)
        assert all(item_repo.get_by_id(item_id).is_archived for item_id in ids)
        control.stop()
        qtbot.waitUntil(lambda: control.is_stopped)
    finally:
        control.stop()
        thread.quit()
        assert thread.wait(5000)
