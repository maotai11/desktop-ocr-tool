"""Fault injection and real Qt dispatch at OCR edit/persist/publish boundaries."""
import json
import sqlite3
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image
from PySide6.QtCore import QObject, QThread, Signal, Qt

from src.data.database import Database, _CREATE_TABLES
from src.data.hasher import sha256_text
from src.data.models import OcrResultDTO
from src.workers.db_worker import DbWorker, create_db_worker_in_thread
from src.workers.ocr_worker import OcrWorker
from src.workers.pipeline import Pipeline


def result(text='new OCR', status='done', **kwargs):
    return OcrResultDTO(text=text, confidence=.9, status=status,
                        engine='fixture', model_version='sha256:model', **kwargs)


def reserve(item_repo, item_id, text='new OCR', status='done', **kwargs):
    job, revision = item_repo.begin_ocr_attempt(item_id)
    return result(text, status, job_id=job, base_edit_revision=revision, **kwargs)


@pytest.mark.parametrize('edited', ['manual correction', ''])
def test_success_preserves_manual_edits_and_effective_hash(item_repo, make_image_dto, edited):
    item_id = item_repo.insert(make_image_dto())
    item_repo.update_edited_text(item_id, edited)
    dto = reserve(item_repo, item_id)
    assert item_repo.update_ocr_result(item_id, dto)
    item = item_repo.get_by_id(item_id)
    assert item.edited_text == edited
    assert item.text_content == 'new OCR'
    assert item.get_effective_text() == edited
    assert item.content_hash == sha256_text(edited)
    assert item.edit_revision == 1


@pytest.mark.parametrize('failure_text,status', [('', 'failed'), ('', 'done'), ('partial error', 'failed')])
def test_failure_or_empty_retry_preserves_last_good_text_and_provenance(
        item_repo, make_image_dto, failure_text, status):
    item_id = item_repo.insert(make_image_dto())
    item_repo.update_ocr_result(item_id, result('last good'))
    item_repo.update_edited_text(item_id, 'saved correction')
    failed = reserve(item_repo, item_id, failure_text, status,
                     error_message='native fault', provenance_json=json.dumps({'warnings': ['fault']}))
    failed.engine, failed.model_version = 'failing engine', 'bad-model'
    assert item_repo.update_ocr_result(item_id, failed)
    item = item_repo.get_by_id(item_id)
    assert item.text_content == 'last good' and item.edited_text == 'saved correction'
    assert item.ocr_engine == 'fixture' and item.ocr_model_version == 'sha256:model'
    assert item.ocr_status == 'failed' and item.ocr_error_message == 'native fault'
    attempt = item_repo.get_ocr_attempts(item_id)[-1]
    assert attempt['text_content'] == failure_text and attempt['engine'] == 'failing engine'
    assert json.loads(attempt['provenance_json']) == {'warnings': ['fault']}


def test_out_of_order_result_is_audited_but_does_not_overwrite_latest(item_repo, make_image_dto):
    item_id = item_repo.insert(make_image_dto())
    old = reserve(item_repo, item_id, 'obsolete')
    latest = reserve(item_repo, item_id, 'current')
    assert item_repo.update_ocr_result(item_id, latest)
    assert not item_repo.update_ocr_result(item_id, old)
    assert item_repo.get_by_id(item_id).text_content == 'current'
    assert [a['disposition'] for a in item_repo.get_ocr_attempts(item_id)] == ['obsolete', 'applied']


@pytest.mark.parametrize('restore', [False, True])
def test_deleted_item_invalidates_job_even_if_restored_before_result(
        item_repo, file_mgr, make_image_dto, restore):
    absolute, relative = file_mgr.save_capture(Image.new('RGB', (20, 20)))
    item_id = item_repo.insert(make_image_dto(rel_path=relative))
    dto = reserve(item_repo, item_id)
    item_repo.soft_delete(item_id)
    if restore:
        item_repo.restore(item_id)
    worker = DbWorker(item_repo, file_mgr, save_raw_image=False)
    published = []
    worker.ocr_persisted.connect(lambda *args: published.append(args))
    worker.update_ocr(item_id, dto)
    assert published == [] and Path(absolute).exists()
    assert item_repo.get_by_id(item_id).text_content is None
    assert item_repo.get_ocr_attempts(item_id)[0]['disposition'] == ('obsolete' if restore else 'deleted')


def test_hard_deleted_late_result_does_not_recreate_record(item_repo, make_image_dto):
    item_id = item_repo.insert(make_image_dto())
    dto = reserve(item_repo, item_id)
    item_repo.hard_delete(item_id)
    assert not item_repo.update_ocr_result(item_id, dto)
    assert item_repo.get_by_id(item_id) is None
    assert item_repo.get_ocr_attempts(item_id) == []


def test_duplicate_delivery_only_publishes_once(item_repo, file_mgr, make_image_dto):
    item_id = item_repo.insert(make_image_dto())
    dto = reserve(item_repo, item_id)
    worker = DbWorker(item_repo, file_mgr)
    published = []
    worker.ocr_persisted.connect(lambda *args: published.append(args))
    worker.update_ocr(item_id, dto)
    worker.update_ocr(item_id, dto)
    assert len(published) == 1 and len(item_repo.get_ocr_attempts(item_id)) == 1


def test_result_and_attempt_rollback_together_after_fts_fault(tmp_db, item_repo, make_image_dto):
    item_id = item_repo.insert(make_image_dto())
    item_repo.update_ocr_result(item_id, result('old'))
    dto = reserve(item_repo, item_id)
    conn = tmp_db.get_connection()
    conn.execute("CREATE TRIGGER fault AFTER UPDATE ON items BEGIN SELECT RAISE(FAIL,'disk full'); END")
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        item_repo.update_ocr_result(item_id, dto)
    assert item_repo.get_by_id(item_id).text_content == 'old'
    assert item_repo.get_ocr_attempts(item_id)[-1]['completed_at'] is None
    conn.execute('DROP TRIGGER fault'); conn.commit()
    assert item_repo.update_ocr_result(item_id, dto)
    assert item_repo.get_by_id(item_id).text_content == 'new OCR'


def test_unversioned_failed_commit_does_not_poison_dto_for_retry(tmp_db, item_repo, make_image_dto):
    item_id = item_repo.insert(make_image_dto())
    dto = result()
    conn = tmp_db.get_connection()
    conn.execute("CREATE TRIGGER fault AFTER UPDATE ON items BEGIN SELECT RAISE(FAIL,'fault'); END")
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        item_repo.update_ocr_result(item_id, dto)
    assert dto.job_id is None and item_repo.get_ocr_attempts(item_id) == []
    conn.execute('DROP TRIGGER fault'); conn.commit()
    assert item_repo.update_ocr_result(item_id, dto)


def test_editor_save_is_atomic_when_note_update_fails(tmp_db, item_repo, make_text_dto):
    item_id = item_repo.insert(make_text_dto('old'))
    conn = tmp_db.get_connection()
    conn.execute("CREATE TRIGGER fault AFTER UPDATE OF note_plaintext ON items BEGIN SELECT RAISE(FAIL,'note fault'); END")
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        item_repo.save_editor_content(item_id, 'draft', '<p>note</p>', 'note', 0)
    item = item_repo.get_by_id(item_id)
    assert item.text_content == 'old' and item.edited_text is None
    assert item.note_plaintext is None and item.edit_revision == 0


def test_stale_editor_revision_does_not_overwrite_newer_manual_edit(item_repo, make_text_dto):
    item_id = item_repo.insert(make_text_dto('old'))
    item_repo.update_edited_text(item_id, 'newer saved edit')
    with pytest.raises(ValueError, match='另一個視窗'):
        item_repo.save_editor_content(item_id, 'stale', '', '', 0)
    assert item_repo.get_by_id(item_id).edited_text == 'newer saved edit'


def test_v4_migration_preserves_original_edit_notes_and_fts(tmp_path):
    path = tmp_path / 'legacy.db'
    with sqlite3.connect(path) as conn:
        for statement in _CREATE_TABLES:
            conn.execute(statement)
        conn.execute("INSERT INTO app_meta VALUES('schema_version','4')")
        conn.execute("INSERT INTO items(text_content,edited_text,note_plaintext) VALUES('original','manual','note')")
    db = Database(str(path))
    try:
        from src.data.repository import ItemRepository
        repo = ItemRepository(db)
        item = repo.get_by_id(1)
        assert (item.text_content, item.edited_text, item.note_plaintext) == ('original', 'manual', 'note')
        assert item.edit_revision == 0 and item.ocr_job_id is None
        assert repo.search_fulltext('manual')[0].id == 1
        assert db.get_connection().execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    finally:
        db.close()


def test_unlink_then_metadata_fault_is_truthful_and_recoverable(
        item_repo, file_mgr, make_image_dto, monkeypatch):
    absolute, relative = file_mgr.save_capture(Image.new('RGB', (20, 20)))
    item_id = item_repo.insert(make_image_dto(rel_path=relative))
    worker = DbWorker(item_repo, file_mgr, save_raw_image=False)
    alerts = []
    worker.save_failed.connect(alerts.append)
    finish = item_repo.finish_image_cleanup
    monkeypatch.setattr(item_repo, 'finish_image_cleanup', lambda *a: (_ for _ in ()).throw(sqlite3.OperationalError('disk full')))
    worker.update_ocr(item_id, reserve(item_repo, item_id))
    assert not Path(absolute).exists()
    assert item_repo.get_by_id(item_id).raw_image_path == relative
    assert len(item_repo.pending_image_cleanups()) == 1
    assert any('原圖已移除' in alert for alert in alerts)
    assert not any('原圖已保留' in alert for alert in alerts)
    monkeypatch.setattr(item_repo, 'finish_image_cleanup', finish)
    worker.reconcile_image_cleanup()
    assert item_repo.get_by_id(item_id).raw_image_path is None
    assert item_repo.pending_image_cleanups() == []


def test_failed_cleanup_intent_still_acknowledges_committed_ocr(
        item_repo, file_mgr, make_image_dto, monkeypatch):
    absolute, relative = file_mgr.save_capture(Image.new('RGB', (20, 20)))
    item_id = item_repo.insert(make_image_dto(rel_path=relative))
    worker = DbWorker(item_repo, file_mgr, save_raw_image=False)
    published, alerts = [], []
    worker.ocr_persisted.connect(lambda *args: published.append(args))
    worker.save_failed.connect(alerts.append)
    monkeypatch.setattr(item_repo, 'plan_image_cleanup', lambda *a: (_ for _ in ()).throw(sqlite3.OperationalError('disk full')))
    worker.update_ocr(item_id, reserve(item_repo, item_id))
    assert len(published) == 1 and Path(absolute).exists()
    assert item_repo.get_by_id(item_id).text_content == 'new OCR'
    assert any('尚未清理原圖' in alert for alert in alerts)


class Capture(QObject):
    capture_done = Signal(str, object)
    capture_failed = Signal(str)
    finished = Signal()
    _started_once = False
    def stop(self): pass
    def wait(self): return True


class Widget(QObject):
    def __init__(self):
        super().__init__()
        self.status, self.progress = [], []
    def show(self): pass
    def setEnabled(self, value): pass
    def set_ocr_status(self, text): self.status.append(text)
    def set_ocr_progress(self, value): self.progress.append(value)
    def refresh_list(self): pass


class Engine:
    def set_progress_callback(self, callback): self.callback = callback
    def is_ready(self): return True
    def run_ocr_from_path(self, path, mode):
        self.callback(80, 'postprocess')
        return dict(text=path, confidence=.9, status='done', engine='fixture', model_version='sha256:model',
                    warnings=['uncertain seam'], preprocessing={'strategy': 'tiles'},
                    hypotheses={'first_pass': [['raw']]})


def make_pipeline(tmp_db, item_repo, file_mgr):
    worker, thread, ocr, widget = DbWorker(item_repo, file_mgr), QThread(), OcrWorker(Engine()), Widget()
    capture = Capture()
    pipeline = Pipeline(capture, ocr, worker, thread, tmp_db, item_repo, file_mgr, widget, None)
    return pipeline, worker, thread, ocr, capture, widget


@pytest.mark.parametrize('change', ['delete', 'delete_restore', 'edit', 'rerun'])
def test_queued_ack_rechecks_item_before_clipboard_publication(
        qtbot, tmp_db, item_repo, file_mgr, make_image_dto, monkeypatch, change):
    pipeline, worker, thread, ocr, capture, widget = make_pipeline(tmp_db, item_repo, file_mgr)
    copies = []
    monkeypatch.setattr('src.clipboard.writer.write_text_to_clipboard', copies.append)
    worker.ocr_persisted.disconnect(pipeline.persisted)
    worker.ocr_persisted.connect(pipeline.persisted, Qt.ConnectionType.QueuedConnection)
    item_id = item_repo.insert(make_image_dto())
    worker.update_ocr(item_id, reserve(item_repo, item_id))
    if change in ('delete', 'delete_restore'):
        item_repo.soft_delete(item_id)
        if change == 'delete_restore': item_repo.restore(item_id)
    elif change == 'edit':
        item_repo.update_edited_text(item_id, 'manual edit after commit')
    else:
        item_repo.begin_ocr_attempt(item_id)
    qtbot.wait(20)
    assert copies == []


def test_unmodified_current_ack_copies_success_and_clears_progress(
        qtbot, tmp_db, item_repo, file_mgr, make_image_dto, monkeypatch):
    pipeline, worker, thread, ocr, capture, widget = make_pipeline(tmp_db, item_repo, file_mgr)
    copies = []
    monkeypatch.setattr('src.clipboard.writer.write_text_to_clipboard', copies.append)
    item_id = item_repo.insert(make_image_dto())
    worker.update_ocr(item_id, reserve(item_repo, item_id))
    assert copies == ['new OCR'] and widget.progress[-1] == 0


def test_queue_database_failure_is_rejected_without_start_or_lost_text(
        qtbot, item_repo, make_text_dto, monkeypatch):
    item_id = item_repo.insert(make_text_dto('saved'))
    worker = OcrWorker(Engine()); worker.set_repository(item_repo)
    rejected = []
    worker.submission_rejected.connect(lambda *args: rejected.append(args))
    monkeypatch.setattr(item_repo, 'begin_ocr_attempt', lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError('locked')))
    assert worker.queue_ocr(item_id, 'capture') is False
    assert not worker._started_once and rejected[0][0] == item_id
    assert item_repo.get_by_id(item_id).text_content == 'saved'


def test_capacity_rejection_does_not_supersede_existing_job(qtbot, item_repo, make_image_dto):
    item_id = item_repo.insert(make_image_dto())
    dto = reserve(item_repo, item_id, 'accepted')
    worker = OcrWorker(Engine()); worker.set_repository(item_repo); worker._capacity = 0
    assert worker.queue_ocr(item_id, 'rejected') is False
    assert item_repo.get_by_id(item_id).ocr_job_id == dto.job_id
    assert len(item_repo.get_ocr_attempts(item_id)) == 1
    assert item_repo.update_ocr_result(item_id, dto)


def test_worker_preserves_full_provenance_and_emits_terminal_progress(qtbot):
    worker = OcrWorker(Engine()); done, progress = [], []
    worker.ocr_done.connect(lambda item_id, dto: done.append(dto))
    worker.ocr_progress.connect(lambda pct, msg: progress.append(pct))
    try:
        assert worker.queue_ocr(1, 'OCR text')
        worker.stop()
        qtbot.waitUntil(lambda: len(done) == 1)
        assert worker.wait(5000)
        qtbot.waitUntil(lambda: progress and progress[-1] == 100)
        snapshot = json.loads(done[0].provenance_json)
        assert snapshot['warnings'] == ['uncertain seam']
        assert snapshot['preprocessing']['strategy'] == 'tiles'
        assert snapshot['hypotheses']['first_pass'] == [['raw']]
        assert json.loads(done[0].detail_json) == []
    finally:
        worker.stop(); assert worker.wait(5000)


def test_editor_rerun_preserves_unsaved_text_and_note(qtbot, item_repo, file_mgr, make_image_dto):
    from src.ui.editor_window import EditorWindow
    item_id = item_repo.insert(make_image_dto())
    queued = []
    ocr = SimpleNamespace(queue_ocr=lambda *args: queued.append(args) or True)
    editor = EditorWindow(item_repo.get_by_id(item_id), item_repo, file_mgr, ocr)
    qtbot.addWidget(editor)
    editor._text_edit.setPlainText('unsaved correction')
    editor._note_edit.setPlainText('unsaved note')
    editor._rerun_ocr()
    assert len(queued) == 1
    item = item_repo.get_by_id(item_id)
    assert item.edited_text == 'unsaved correction' and item.note_plaintext == 'unsaved note'
    item_repo.update_ocr_result(item_id, reserve(item_repo, item_id))
    assert item_repo.get_by_id(item_id).get_effective_text() == 'unsaved correction'


def test_untouched_editor_rerun_does_not_create_manual_override(qtbot, item_repo, file_mgr, make_image_dto):
    from src.ui.editor_window import EditorWindow
    item_id = item_repo.insert(make_image_dto())
    item_repo.update_ocr_result(item_id, result('old OCR'))
    ocr = SimpleNamespace(queue_ocr=lambda *args: True)
    editor = EditorWindow(item_repo.get_by_id(item_id), item_repo, file_mgr, ocr)
    qtbot.addWidget(editor)
    editor._rerun_ocr()
    assert item_repo.get_by_id(item_id).edited_text is None
    item_repo.update_ocr_result(item_id, reserve(item_repo, item_id, 'improved OCR'))
    assert item_repo.get_by_id(item_id).get_effective_text() == 'improved OCR'


def test_real_qthreads_latest_failed_rerun_keeps_manual_and_last_good_until_drained(
        qtbot, tmp_db, item_repo, file_mgr, make_image_dto, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    class BlockingEngine(Engine):
        def run_ocr_from_path(self, path, mode):
            self.calls = getattr(self, 'calls', 0) + 1
            if self.calls == 1:
                entered.set()
                assert release.wait(5)
                return super().run_ocr_from_path(path, mode)
            raise RuntimeError('native inference fault')
    absolute, relative = file_mgr.save_capture(Image.new('RGB', (20, 20)))
    item_id = item_repo.insert(make_image_dto(rel_path=relative))
    item_repo.update_ocr_result(item_id, result('last good'))
    item_repo.update_edited_text(item_id, 'manual retained')
    capture, ocr, widget = Capture(), OcrWorker(BlockingEngine()), Widget()
    worker, thread = create_db_worker_in_thread(item_repo, file_mgr, enable_dedup=False)
    pipeline = Pipeline(capture, ocr, worker, thread, tmp_db, item_repo, file_mgr, widget, None)
    copies = []
    monkeypatch.setattr('src.clipboard.writer.write_text_to_clipboard', copies.append)
    try:
        assert ocr.queue_ocr(item_id, absolute)
        qtbot.waitUntil(entered.is_set)
        assert ocr.queue_ocr(item_id, absolute)
        release.set()
        qtbot.waitUntil(lambda: all(a['completed_at'] for a in item_repo.get_ocr_attempts(item_id)), timeout=5000)
        item = item_repo.get_by_id(item_id)
        assert item.text_content == 'last good' and item.edited_text == 'manual retained'
        assert item.ocr_status == 'failed' and copies == []
        attempts = item_repo.get_ocr_attempts(item_id)
        assert [a['disposition'] for a in attempts][-2:] == ['obsolete', 'applied']
        assert json.loads(attempts[-1]['provenance_json'])['stage'] == 'worker'
        pipeline.shutdown()
        qtbot.waitUntil(lambda: pipeline.closed, timeout=5000)
        assert not thread.isRunning() and not ocr.isRunning()
    finally:
        release.set()
        pipeline.shutdown()
        qtbot.waitUntil(lambda: pipeline.closed, timeout=5000)


def test_worker_failure_still_emits_terminal_progress(qtbot):
    class FailedEngine(Engine):
        def run_ocr_from_path(self, path, mode):
            self.callback(80, 'working')
            raise RuntimeError('native fault')
    worker = OcrWorker(FailedEngine()); done, progress = [], []
    worker.ocr_done.connect(lambda item_id, dto: done.append(dto))
    worker.ocr_progress.connect(lambda pct, msg: progress.append(pct))
    try:
        assert worker.queue_ocr(1, 'image')
        worker.stop()
        qtbot.waitUntil(lambda: len(done) == 1 and progress and progress[-1] == 100)
        assert worker.wait(5000)
        assert done[0].status == 'failed'
        assert json.loads(done[0].provenance_json)['error'] == 'native fault'
    finally:
        worker.stop(); assert worker.wait(5000)


def test_shutdown_stall_is_reported_without_unsafe_thread_termination(
        qtbot, tmp_db, item_repo, file_mgr, make_image_dto):
    entered, release = threading.Event(), threading.Event()
    class HeldEngine(Engine):
        def run_ocr_from_path(self, path, mode):
            entered.set()
            assert release.wait(5)
            return super().run_ocr_from_path(path, mode)
    absolute, relative = file_mgr.save_capture(Image.new('RGB', (20, 20)))
    item_id = item_repo.insert(make_image_dto(rel_path=relative))
    capture, ocr, widget = Capture(), OcrWorker(HeldEngine()), Widget()
    worker, thread = create_db_worker_in_thread(item_repo, file_mgr, enable_dedup=False)
    pipeline = Pipeline(capture, ocr, worker, thread, tmp_db, item_repo, file_mgr, widget, None)
    stalls = []
    pipeline.shutdown_stalled.connect(stalls.append)
    pipeline._shutdown_timer.setInterval(20)
    try:
        assert ocr.queue_ocr(item_id, absolute)
        qtbot.waitUntil(entered.is_set)
        pipeline.shutdown()
        qtbot.waitUntil(lambda: len(stalls) == 1)
        assert not pipeline.closed and ocr.isRunning() and thread.isRunning()
        assert item_repo.get_by_id(item_id) is not None
        release.set()
        qtbot.waitUntil(lambda: pipeline.closed, timeout=5000)
        assert not ocr.isRunning() and not thread.isRunning()
    finally:
        release.set()
        pipeline.shutdown()
        qtbot.waitUntil(lambda: pipeline.closed, timeout=5000)


def test_obsolete_job_cannot_delete_source_reserved_during_cleanup_plan(
        item_repo, file_mgr, make_image_dto, monkeypatch):
    absolute, relative = file_mgr.save_capture(Image.new('RGB', (20, 20)))
    item_id = item_repo.insert(make_image_dto(rel_path=relative))
    dto = reserve(item_repo, item_id, 'old result')
    plan = item_repo.plan_image_cleanup
    newer_jobs = []
    def interleave(*args):
        path = plan(*args)
        newer_jobs.append(item_repo.begin_ocr_attempt(item_id)[0])
        return path
    monkeypatch.setattr(item_repo, 'plan_image_cleanup', interleave)
    DbWorker(item_repo, file_mgr, save_raw_image=False).update_ocr(item_id, dto)
    item = item_repo.get_by_id(item_id)
    assert item.ocr_job_id == newer_jobs[0] and item.ocr_status == 'pending'
    assert item.raw_image_path == relative and Path(absolute).exists()


def test_cleanup_serializes_real_concurrent_reservation_and_rejects_stale_source(
        item_repo, file_mgr, make_image_dto, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    absolute, relative = file_mgr.save_capture(Image.new('RGB', (20, 20)))
    item_id = item_repo.insert(make_image_dto(rel_path=relative))
    dto = reserve(item_repo, item_id)
    entered, attempted = threading.Event(), threading.Event()
    original = Path.unlink
    def blocked_unlink(path, *args, **kwargs):
        entered.set()
        assert attempted.wait(5)
        return original(path, *args, **kwargs)
    def reserve_concurrently():
        assert entered.wait(5)
        attempted.set()
        return item_repo.begin_ocr_attempt(
            item_id, source_check=lambda path: path == relative and Path(absolute).is_file())
    monkeypatch.setattr(Path, 'unlink', blocked_unlink)
    with ThreadPoolExecutor(1) as pool:
        reservation = pool.submit(reserve_concurrently)
        DbWorker(item_repo, file_mgr, save_raw_image=False).update_ocr(item_id, dto)
        with pytest.raises(ValueError, match='原圖已移除或變更'):
            reservation.result(timeout=5)
    assert not Path(absolute).exists()
    assert item_repo.get_by_id(item_id).raw_image_path is None
    assert len(item_repo.get_ocr_attempts(item_id)) == 1


def test_runtime_queue_rejects_stale_or_missing_source_before_job_reservation(
        qtbot, item_repo, file_mgr, make_image_dto):
    absolute, relative = file_mgr.save_capture(Image.new('RGB', (20, 20)))
    item_id = item_repo.insert(make_image_dto(rel_path=relative))
    worker = OcrWorker(Engine()); worker.set_repository(item_repo, file_mgr)
    item_repo.clear_image_paths(item_id)
    assert worker.queue_ocr(item_id, absolute) is False
    assert item_repo.get_ocr_attempts(item_id) == [] and not worker._started_once


def test_untouched_stale_editor_rerun_does_not_overwrite_newer_notes(qtbot, item_repo, file_mgr, make_image_dto):
    from src.ui.editor_window import EditorWindow
    item_id = item_repo.insert(make_image_dto())
    item_repo.update_ocr_result(item_id, result('old OCR'))
    item_repo.update_note(item_id, '<p>old note</p>', 'old note')
    item = item_repo.get_by_id(item_id)
    queued = []
    editor = EditorWindow(item, item_repo, file_mgr,
                          SimpleNamespace(queue_ocr=lambda *args: queued.append(args) or True))
    qtbot.addWidget(editor)
    item_repo.save_editor_content(item_id, 'new manual edit', '<p>new note</p>', 'new note', item.edit_revision)
    editor._rerun_ocr()
    item = item_repo.get_by_id(item_id)
    assert item.edited_text == 'new manual edit' and item.note_plaintext == 'new note'
    assert len(queued) == 1


def test_dirty_note_rerun_uses_revision_check_without_creating_manual_text_override(
        qtbot, item_repo, file_mgr, make_image_dto, monkeypatch):
    from src.ui.editor_window import EditorWindow
    item_id = item_repo.insert(make_image_dto())
    item_repo.update_ocr_result(item_id, result('old OCR'))
    queued = []
    editor = EditorWindow(item_repo.get_by_id(item_id), item_repo, file_mgr,
                          SimpleNamespace(queue_ocr=lambda *args: queued.append(args) or True))
    qtbot.addWidget(editor)
    editor._note_edit.setPlainText('draft note')
    editor._rerun_ocr()
    item = item_repo.get_by_id(item_id)
    assert item.edited_text is None and item.note_plaintext == 'draft note'
    assert len(queued) == 1
    stale = EditorWindow(item, item_repo, file_mgr,
                         SimpleNamespace(queue_ocr=lambda *args: queued.append(args) or True))
    qtbot.addWidget(stale)
    stale._note_edit.setPlainText('stale draft')
    item_repo.update_note(item_id, '<p>newer note</p>', 'newer note')
    warnings = []
    monkeypatch.setattr('src.ui.editor_window.QMessageBox.warning', lambda *a: warnings.append(a))
    stale._rerun_ocr()
    assert len(queued) == 1 and len(warnings) == 1
    assert item_repo.get_by_id(item_id).note_plaintext == 'newer note'
