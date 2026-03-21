# -*- coding: utf-8 -*-
import sys
import os
import logging

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
    setup_logger()
    logger.info("===== 桌面OCR擷取工具 v1.0.0 啟動 =====")

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("桌面OCR擷取工具")
    app.setQuitOnLastWindowClosed(False)

    try:
        # 1. Single instance
        from src.core.single_instance import acquire_instance_lock, bring_existing_to_front
        if not acquire_instance_lock():
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
                "或以系統管理員身份執行。"
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
            item_repo, file_mgr, enable_dedup
        )

        # 9. OCR Engine + Worker
        from src.ocr.engine import OcrEngine
        from src.workers.ocr_worker import OcrWorker
        ocr_engine = OcrEngine(
            confidence_accept=cfg.get('ocr', 'confidence_accept', default=0.85),
            confidence_review=cfg.get('ocr', 'confidence_review', default=0.60),
        )
        ocr_worker = OcrWorker(ocr_engine)

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
        )

        tray = TrayManager(widget)
        tray.show()
        widget.show()

        # 12. Clipboard watcher
        clip_watcher = None
        if cfg.get('clipboard', 'monitor_clipboard', default=True):
            from src.clipboard.watcher import ClipboardWatcher
            clip_watcher = ClipboardWatcher()
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
                    QTimer.singleShot(0, db_worker, lambda dto=dto: db_worker.save_item(dto))

                clip_watcher.text_captured.connect(on_clipboard_text)

        # 13. Hotkeys
        from src.core.hotkey import HotkeyListener
        hotkey_listener = HotkeyListener()
        hk = cfg.get('hotkeys', default={})

        def do_region_ocr():
            widget.hide()
            overlay.start_capture(
                lambda x, y, w, h, mon:
                    capture_worker.capture_region(x, y, w, h, mon, 'region_ocr')
            )

        def do_region_image():
            widget.hide()
            overlay.start_capture(
                lambda x, y, w, h, mon:
                    capture_worker.capture_region(x, y, w, h, mon, 'region_image')
            )

        def do_fullscreen():
            capture_worker.capture_fullscreen(1)

        widget.set_capture_callbacks(do_region_ocr, do_region_image)

        hotkey_actions = {
            'capture_region_ocr': do_region_ocr,
            'capture_region_image': do_region_image,
            'capture_fullscreen': do_fullscreen,
            'toggle_widget': widget.toggle_visibility,
            'open_console': widget.open_console,
            'paste_last': widget.paste_last_item,
        }

        for name, default_key in [
            ('capture_region_ocr', 'Ctrl+Shift+O'),
            ('capture_region_image', 'Ctrl+Shift+S'),
            ('capture_fullscreen', 'Ctrl+Shift+F'),
            ('toggle_widget', 'Ctrl+Shift+Space'),
            ('open_console', 'Ctrl+Shift+M'),
            ('paste_last', 'Ctrl+Shift+V'),
        ]:
            hotkey_listener.register(name, hk.get(name, default_key))

        def on_hotkey(name: str):
            action = hotkey_actions.get(name)
            if action:
                action()

        hotkey_listener.hotkey_pressed.connect(on_hotkey)
        hotkey_listener.start()

        # 14. Capture worker signals
        _pending_ocr: dict = {}

        def on_capture_done(image_path: str, dto):
            # UI op must run on main thread; DB op must run on db_thread
            QTimer.singleShot(0, widget, widget.show)
            # store path for post-save OCR trigger
            if dto.source_mode == 'region_ocr':
                _pending_ocr['path'] = image_path
            # Queue save_item onto db_thread via context object
            QTimer.singleShot(0, db_worker, lambda: db_worker.save_item(dto))

        capture_worker.capture_done.connect(on_capture_done)
        capture_worker.capture_failed.connect(
            lambda err: QMessageBox.warning(None, "截圖失敗", err)
        )

        # 15. DB worker signals — UI updates must run on main thread (3-arg singleShot)
        def on_item_saved(item_id: int):
            def _update():
                widget.refresh_list()
                if 'path' in _pending_ocr and ocr_engine.is_ready():
                    path = _pending_ocr.pop('path')
                    ocr_worker.queue_ocr(item_id, path, 'screen')
            QTimer.singleShot(0, widget, _update)  # context=widget → runs in main thread

        db_worker.item_saved.connect(on_item_saved)
        db_worker.item_updated.connect(
            lambda _: QTimer.singleShot(0, widget, widget.refresh_list)
        )
        db_worker.save_failed.connect(
            lambda err: QTimer.singleShot(
                0, widget, lambda: widget.set_ocr_status(f"儲存失敗: {err}")
            )
        )

        # 16. OCR worker signals — update_ocr must run on db_thread
        def on_ocr_done(item_id: int, result):
            QTimer.singleShot(0, db_worker, lambda: db_worker.update_ocr(item_id, result))
            if result.status == 'failed':
                QTimer.singleShot(0, widget, lambda: widget.set_ocr_status("OCR 失敗"))

        ocr_worker.ocr_done.connect(on_ocr_done)
        ocr_worker.ocr_failed.connect(
            lambda item_id, err: QTimer.singleShot(
                0, widget, lambda: widget.set_ocr_status(f"OCR #{item_id} 失敗")
            )
        )
        ocr_worker.engine_progress.connect(widget.on_ocr_engine_progress)
        ocr_worker.engine_ready.connect(widget.on_ocr_engine_ready)
        ocr_worker.engine_failed.connect(widget.on_ocr_engine_failed)

        # Cleanup on quit
        def _on_quit():
            hotkey_listener.stop()
            db_thread.quit()
            db_thread.wait(2000)
            db.close()
            from src.core.single_instance import release_instance_lock
            release_instance_lock()
        app.aboutToQuit.connect(_on_quit)

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
        try:
            QMessageBox.critical(
                None, "啟動失敗",
                f"應用程式啟動失敗：\n{str(e)}\n\n請檢查 logs/app.log 獲取詳細資訊。"
            )
        except Exception:
            pass
        return 1
