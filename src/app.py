import logging
import os
import sys

from src.core.version import APP_DISPLAY_NAME, APP_VERSION

logger = logging.getLogger(__name__)


def _setup_font(app, priority: list):
    from PySide6.QtGui import QFont, QFontDatabase
    for fname in priority:
        if not fname:
            break
        if QFontDatabase.hasFamily(fname):
            app.setFont(QFont(fname))
            logger.info(f"使用字型: {fname}")
            return
    logger.info("使用 Qt 預設字型")


def main(smoke_report=None) -> int:
    from src.core.validation_report import database_integrity, executable_sha256, write_validation_report
    smoke = {'schema': 2, 'probe': 'application_lifecycle', 'version': APP_VERSION,
             'phase_a': False, 'engine_ready': False, 'shutdown_clean': False,
             'passed': False, 'clean_machine_verified': False,
             'network_observation': 'NOT_RUN',
             'frozen': bool(getattr(sys, 'frozen', False)), 'platform': sys.platform}
    instance_locked = False
    try:
        if smoke_report is not None:
            smoke['executable_sha256'] = executable_sha256()
            write_validation_report(smoke_report, smoke)
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication, QMessageBox

        from src.core.logger import setup_logger

        app = QApplication.instance() or QApplication(sys.argv)
        app.setApplicationName(APP_DISPLAY_NAME)
        app.setApplicationVersion(APP_VERSION)
        app.setQuitOnLastWindowClosed(False)
        smoke['qt_platform'] = app.platformName()
        setup_logger()
        logger.info(f"===== {APP_DISPLAY_NAME} v{APP_VERSION} 啟動 =====")
        from src.core.config import get_config
        cfg = get_config()
        # 1. Single instance
        from src.core.single_instance import (
            acquire_instance_lock,
            bring_existing_to_front,
        )
        if cfg.get('general', 'single_instance', default=True):
            instance_locked = acquire_instance_lock()
            if not instance_locked:
                if smoke_report is not None:
                    smoke['error'] = 'another application instance holds the lock'
                    return 1
                bring_existing_to_front()
                return 0

        # 2. Config
        from src.core.config import get_config
        cfg = get_config()

        # 3. Font
        fonts = cfg.get('ui', 'font_family_priority',
                         default=['Microsoft JhengHei UI', 'Microsoft JhengHei', ''])
        _setup_font(app, fonts)

        # 4. Data dir
        data_dir = cfg.get_data_directory()
        parent_dir = os.path.dirname(data_dir) or '.'
        if not os.access(parent_dir, os.W_OK):
            raise PermissionError(
                f"無法寫入資料目錄：{data_dir}。請將程式移至可寫入的目錄，"
                "請勿以系統管理員身份執行可攜版本。")
        os.makedirs(data_dir, exist_ok=True)

        # 5. Database
        from src.data.database import Database
        db = Database(os.path.join(data_dir, 'app.db'))

        # 6. Repositories
        from src.data.repository import ItemRepository, TagRepository
        item_repo = ItemRepository(db)
        tag_repo = TagRepository(db)

        # 7. File manager
        from src.data.file_manager import FileManager
        thumb_size = cfg.get('ui', 'thumbnail_size', default=[80, 80])
        file_mgr = FileManager(data_dir, tuple(thumb_size))

        # 8. DB Worker
        from src.workers.db_worker import create_db_worker_in_thread
        enable_dedup = cfg.get('clipboard', 'deduplicate', default=True)
        db_worker, db_thread = create_db_worker_in_thread(
            item_repo, file_mgr, enable_dedup,
            save_raw_image=cfg.get("capture", "save_raw_image", default=True)
        )

        # 9. OCR Engine + Worker（支援引擎優先級）
        from src.ocr.engine import OcrEngine
        from src.workers.ocr_worker import OcrWorker

        # 建立主引擎（優化繁體中文/小字/複雜結構辨識）
        ocr_engine = OcrEngine(
            confidence_accept=cfg.get('ocr', 'confidence_accept', default=0.85),
            confidence_review=cfg.get('ocr', 'confidence_review', default=0.60),
            max_image_short_side=cfg.get('ocr', 'max_image_short_side', default=1280),  # 提高解析度
            enable_second_pass=cfg.get('ocr', 'enable_second_pass', default=True),
            enable_handwriting_mode=cfg.get('ocr', 'enable_handwriting_mode', default=False),
        )
        ocr_worker = OcrWorker(ocr_engine)

        # Core builds deliberately expose only the verified, bundled engine.
        # Optional providers need a separate offline bundle and integration gate.
        logger.info('OCR: bundled RapidOCR PP-OCRv4; secondary engines disabled in Core')

        # 10. Capture worker + overlay
        from src.ui.capture_overlay import CaptureOverlay
        from src.workers.capture_worker import CaptureWorker
        capture_worker = CaptureWorker(file_mgr)
        overlay = CaptureOverlay()

        # 11. Main UI
        from src.ui.tray_manager import TrayManager
        from src.ui.widget import FloatingWidget

        widget = FloatingWidget(
            item_repo=item_repo,
            file_mgr=file_mgr,
            db_worker=db_worker,
            ocr_worker=ocr_worker,
            cfg=cfg,
            data_dir=data_dir,
            tag_repo=tag_repo,
        )

        # 讓 settings dialog 可以即時更新 OCR engine 參數
        widget._ocr_engine = ocr_engine

        overlay.cancelled.connect(widget.show)
        tray = TrayManager(widget)
        tray.show()
        if not cfg.get("general", "start_minimized", default=True) or not tray.is_available():
            widget.show()

        # 12. Clipboard watcher
        clip_watcher = None
        if cfg.get('clipboard', 'monitor_clipboard', default=False):
            from src.clipboard.watcher import ClipboardWatcher
            clip_watcher = ClipboardWatcher(ignore_self=cfg.get('clipboard', 'ignore_self', default=True))
            widget._clip_watcher = clip_watcher

            if cfg.get('clipboard', 'auto_save_text', default=True):
                from src.core.constants import (
                    ITEM_TYPE_TEXT,
                    SOURCE_MODE_CLIPBOARD_TEXT,
                )
                from src.data.hasher import sha256_text
                from src.data.models import ItemCreateDTO

                def on_clipboard_text(text: str):
                    max_len = cfg.get('clipboard', 'max_text_length', default=50000)
                    if len(text) > max_len:
                        return
                    dto = ItemCreateDTO(
                        item_type=ITEM_TYPE_TEXT,
                        source_mode=SOURCE_MODE_CLIPBOARD_TEXT,
                        text_content=text,
                        content_hash=sha256_text(text),
                        ocr_status='none',
                    )
                    pipeline.save_requested.emit(dto)

                clip_watcher.text_captured.connect(on_clipboard_text)

        # 13. Hotkeys
        from src.core.hotkey import HotkeyListener
        hotkey_listener = HotkeyListener()
        hk = cfg.get('hotkeys', default={})

        def do_region_ocr():
            if pipeline.closing:
                return
            widget.hide()
            overlay.start_capture(
                lambda x, y, w, h, mon:
                    capture_worker.capture_region(x, y, w, h, mon, 'region_ocr')
            )

        def do_region_image():
            if pipeline.closing:
                return
            widget.hide()
            overlay.start_capture(
                lambda x, y, w, h, mon:
                    capture_worker.capture_region(x, y, w, h, mon, 'region_image')
            )

        def do_fullscreen():
            if not pipeline.closing:
                capture_worker.capture_fullscreen(1)

        widget.set_capture_callbacks(do_region_ocr, do_region_image)

        hotkey_actions = {
            'capture_region_ocr': do_region_ocr,
            'capture_region_image': do_region_image,
            'capture_fullscreen': do_fullscreen,
            'toggle_widget': widget.toggle_visibility,
            'open_console': widget.open_console,
            'quick_search': widget.focus_search,
            'paste_last': widget.paste_last_item,
        }

        for name, default_key in [
            ('capture_region_ocr', 'Ctrl+Shift+O'),
            ('capture_region_image', 'Ctrl+Shift+S'),
            ('capture_fullscreen', 'Ctrl+Shift+F'),
            ('toggle_widget', 'Ctrl+Shift+Space'),
            ('open_console', 'Ctrl+Shift+M'),
            ('quick_search', 'Ctrl+Shift+Q'),
            ('paste_last', 'Ctrl+Shift+V'),
        ]:
            hotkey_listener.register(name, hk.get(name, default_key))

        # QObject receiver affinity guarantees these slots execute on the GUI thread.
        from src.workers.pipeline import Pipeline
        pipeline = Pipeline(capture_worker, ocr_worker, db_worker, db_thread,
                            db, item_repo, file_mgr, widget, cfg,
                            hotkey_listener, clip_watcher)
        pipeline.hotkey_actions = hotkey_actions
        hotkey_listener.start()
        tray.set_quit_callback(pipeline.shutdown)
        pipeline.shutdown_finished.connect(app.quit)
        app.aboutToQuit.connect(pipeline.ensure_shutdown)
        ocr_worker.ocr_progress.connect(widget.set_ocr_progress)
        ocr_worker.engine_progress.connect(widget.on_ocr_engine_progress)
        ocr_worker.engine_ready.connect(widget.on_ocr_engine_ready)
        ocr_worker.engine_failed.connect(widget.on_ocr_engine_failed)

        if smoke_report is not None:
            smoke['phase_a'] = True
            smoke_timer = QTimer(widget)
            smoke_timer.setSingleShot(True)
            def smoke_ready():
                smoke_timer.stop()
                smoke['engine_ready'] = True
                pipeline.shutdown()
            def smoke_failed(error):
                smoke_timer.stop()
                smoke['error'] = error
                pipeline.shutdown()
            def smoke_finished():
                import sqlite3
                smoke['threads_stopped'] = {
                    'capture': not capture_worker.isRunning(),
                    'ocr': not ocr_worker.isRunning(),
                    'database': not db_thread.isRunning(),
                    'hotkeys': not hotkey_listener.isRunning(),
                }
                smoke['shutdown_clean'] = (pipeline.closed and db._closed
                                           and all(smoke['threads_stopped'].values()))
                try:
                    smoke['database_integrity'] = database_integrity(db._db_path)
                except (sqlite3.Error, OSError) as exc:
                    smoke['error'] = f'database verification failed: {exc}'
                smoke['passed'] = (smoke['engine_ready'] and smoke['shutdown_clean']
                                   and smoke.get('database_integrity') == 'ok'
                                   and 'error' not in smoke)
            ocr_worker.engine_ready.connect(smoke_ready)
            ocr_worker.engine_failed.connect(smoke_failed)
            pipeline.shutdown_finished.connect(smoke_finished)
            smoke_timer.timeout.connect(lambda: smoke_failed('startup timeout'))
            smoke_timer.start(30000)

        # Phase B: background model loading
        ocr_worker.start_loading()

        # Autostart
        if cfg.get('general', 'start_with_windows', default=False):
            from src.core.autostart import is_autostart_enabled, set_autostart
            if not is_autostart_enabled():
                set_autostart(True)

        logger.info("Phase A 完成，進入事件迴圈")
        code = app.exec()
        if smoke_report is not None and not smoke['passed']:
            return 1
        return code

    except Exception as e:
        logger.critical(f"啟動失敗: {e}", exc_info=True)
        smoke['error'] = str(e)
        smoke['passed'] = False
        if smoke_report is None:
            try:
                QMessageBox.critical(
                    None, "啟動失敗",
                    f"應用程式啟動失敗：\n{e!s}\n\n請檢查 logs/app.log 獲取詳細資訊。"
                )
            except Exception:  # noqa: BLE001 - Qt crash reporting is a last-resort path
                logger.exception('無法顯示啟動失敗訊息')
        return 1
    finally:
        try:
            if 'pipeline' in locals():
                pipeline.ensure_shutdown()
            else:
                for owner in ('hotkey_listener', 'capture_worker', 'ocr_worker'):
                    worker = locals().get(owner)
                    if worker is not None:
                        worker.stop()
                        worker.wait()
                if 'db_thread' in locals():
                    db_thread.quit()
                    db_thread.wait()
                if 'db' in locals():
                    db.close()
        finally:
            if instance_locked:
                from src.core.single_instance import release_instance_lock
                release_instance_lock()
            if smoke_report is not None:
                try:
                    write_validation_report(smoke_report, smoke)
                except OSError:
                    logger.exception('無法寫入 application smoke 報告')
                    return 1
