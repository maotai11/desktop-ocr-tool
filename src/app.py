# -*- coding: utf-8 -*-
import sys
import os
import logging
from src.core.version import APP_DISPLAY_NAME, APP_VERSION

logger = logging.getLogger(__name__)


def _setup_font(app, priority: list):
    from PySide6.QtGui import QFontDatabase, QFont
    for fname in priority:
        if not fname:
            break
        if QFontDatabase.hasFamily(fname):
            app.setFont(QFont(fname))
            logger.info(f"使用字型: {fname}")
            return
    logger.info("使用 Qt 預設字型")


def main() -> int:
    from PySide6.QtWidgets import QApplication, QMessageBox
    from PySide6.QtCore import Qt, QTimer

    from src.core.logger import setup_logger

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(APP_DISPLAY_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setQuitOnLastWindowClosed(False)

    try:
        setup_logger()
        logger.info(f"===== {APP_DISPLAY_NAME} v{APP_VERSION} 啟動 =====")
        from src.core.config import get_config
        cfg = get_config()
        # 1. Single instance
        from src.core.single_instance import acquire_instance_lock, bring_existing_to_front
        if cfg.get('general', 'single_instance', default=True) and not acquire_instance_lock():
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
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(
                None, "權限不足",
                f"無法寫入資料目錄：\n{data_dir}\n\n"
                "請將程式移至有寫入權限的目錄（例如桌面或 Documents），"
                "請勿以系統管理員身份執行可攜版本。"
            )
            return 1
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
        from src.workers.capture_worker import CaptureWorker
        from src.ui.capture_overlay import CaptureOverlay
        capture_worker = CaptureWorker(file_mgr)
        overlay = CaptureOverlay()

        # 11. Main UI
        from src.ui.widget import FloatingWidget
        from src.ui.tray_manager import TrayManager

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
                from src.data.hasher import sha256_text
                from src.data.models import ItemCreateDTO
                from src.core.constants import SOURCE_MODE_CLIPBOARD_TEXT, ITEM_TYPE_TEXT

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

        def on_hotkey(name: str):
            action = hotkey_actions.get(name)
            if action:
                action()

        hotkey_listener.hotkey_pressed.connect(on_hotkey)
        hotkey_listener.start()

        # QObject receiver affinity guarantees these slots execute on the GUI thread.
        from src.workers.pipeline import Pipeline
        pipeline = Pipeline(capture_worker, ocr_worker, db_worker, db_thread,
                            db, item_repo, file_mgr, widget, cfg,
                            hotkey_listener, clip_watcher)
        tray.set_quit_callback(pipeline.shutdown)
        pipeline.shutdown_finished.connect(app.quit)
        app.aboutToQuit.connect(pipeline.ensure_shutdown)
        from src.core.single_instance import release_instance_lock
        pipeline.shutdown_finished.connect(release_instance_lock)
        ocr_worker.ocr_progress.connect(widget.set_ocr_progress)
        ocr_worker.engine_progress.connect(widget.on_ocr_engine_progress)
        ocr_worker.engine_ready.connect(widget.on_ocr_engine_ready)
        ocr_worker.engine_failed.connect(widget.on_ocr_engine_failed)

        # Phase B: background model loading
        ocr_worker.start_loading()

        # Autostart
        if cfg.get('general', 'start_with_windows', default=False):
            from src.core.autostart import set_autostart, is_autostart_enabled
            if not is_autostart_enabled():
                set_autostart(True)

        logger.info("Phase A 完成，進入事件迴圈")
        return app.exec()

    except Exception as e:
        logger.critical(f"啟動失敗: {e}", exc_info=True)
        if 'pipeline' in locals():
            pipeline.ensure_shutdown()
        else:
            for owner in ('hotkey_listener', 'capture_worker', 'ocr_worker'):
                thread = locals().get(owner)
                if thread is not None:
                    thread.stop()
                    thread.wait()
            if 'db_thread' in locals():
                db_thread.quit()
                db_thread.wait()
            if 'db' in locals():
                db.close()
        try:
            QMessageBox.critical(
                None, "啟動失敗",
                f"應用程式啟動失敗：\n{str(e)}\n\n請檢查 logs/app.log 獲取詳細資訊。"
            )
        except Exception:
            pass  # nosec B110 — last-resort crash dialog; if Qt itself fails here, nothing more can be done
        return 1
