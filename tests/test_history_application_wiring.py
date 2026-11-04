"""Maintenance remains owned until its database acknowledgement is drained."""
from threading import Event

from src.ui.settings_dialog import SettingsDialog
from src.workers.capture_worker import CaptureWorker
from src.workers.db_worker import create_db_worker_in_thread
from src.workers.history_maintenance import HistoryMaintenanceController
from src.workers.ocr_worker import OcrWorker
from src.workers.pipeline import Pipeline
from test_clipboard_copy_controls import Config
from test_pipeline_shutdown import SlowEngine, Widget


def test_history_setting_zero_and_days_are_saved(qtbot, monkeypatch):
    monkeypatch.setattr('src.core.autostart.set_autostart', lambda *_: True)
    cfg = Config()
    cfg.save = lambda: None
    for days in (0, 90, 180):
        dialog = SettingsDialog(cfg)
        qtbot.addWidget(dialog)
        dialog._sp_archive_days.setValue(days)
        dialog._save()
        assert cfg.get('history', 'auto_archive_days') == days


def test_shutdown_cancels_pending_maintenance_before_database_exit(
        qtbot, tmp_db, item_repo, file_mgr, monkeypatch):
    entered, release = Event(), Event()
    original = item_repo.archive_inactive_batch

    def delayed(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(item_repo, 'archive_inactive_batch', delayed)
    capture, ocr = CaptureWorker(file_mgr), OcrWorker(SlowEngine())
    worker, thread = create_db_worker_in_thread(item_repo, file_mgr)
    pipeline = Pipeline(capture, ocr, worker, thread, tmp_db, item_repo, file_mgr, Widget(), Config())
    maintenance = HistoryMaintenanceController(worker, Config(), parent=pipeline)
    pipeline.set_history_maintenance(maintenance)
    try:
        ocr.start_loading()
        maintenance.start()
        qtbot.waitUntil(entered.is_set)
        pipeline.shutdown()
        assert not pipeline.closed and not maintenance.is_stopped
        release.set()
        qtbot.waitUntil(lambda: pipeline.closed, timeout=5000)
        assert maintenance.is_stopped and tmp_db._closed and not thread.isRunning()
        assert not maintenance._daily_timer.isActive() and not maintenance._batch_timer.isActive()
    finally:
        release.set()
        pipeline.shutdown()
        qtbot.waitUntil(lambda: pipeline.closed, timeout=5000)
