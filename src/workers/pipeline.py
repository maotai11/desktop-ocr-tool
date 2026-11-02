"""Qt owner-thread dispatch and ordered, draining shutdown."""
import logging

from PySide6.QtCore import QEventLoop, QObject, Qt, QTimer, Signal, Slot

logger = logging.getLogger(__name__)


class Pipeline(QObject):
    save_requested = Signal(object)
    ocr_update_requested = Signal(int, object)
    barrier_requested = Signal(str)
    shutdown_finished = Signal()
    shutdown_stalled = Signal(str)
    selection_requested = Signal(int, object)

    def __init__(self, capture, ocr, db_worker, db_thread, database,
                 repo, files, widget, cfg, hotkeys=None, clipboard=None):
        super().__init__()
        self.capture, self.ocr = capture, ocr
        self.db_worker, self.db_thread, self.database = db_worker, db_thread, database
        self.repo, self.files, self.widget, self.cfg = repo, files, widget, cfg
        self.hotkeys, self.clipboard = hotkeys, clipboard
        ocr.set_repository(repo, files)
        self.closing = self.closed = False
        self._database_closed = False
        self._ocr_selections = {}
        self._tools_poll = QTimer(self)
        self._tools_poll.setInterval(25)
        self._tools_poll.timeout.connect(self._finish_shutdown)
        self._shutdown_timer = QTimer(self)
        self._shutdown_timer.setSingleShot(True)
        self._shutdown_timer.setInterval(15000)
        self._shutdown_timer.timeout.connect(self.report_shutdown_stall)
        self.hotkey_actions = {}
        if hotkeys:
            hotkeys.hotkey_pressed.connect(self.dispatch_hotkey)
        self.save_requested.connect(db_worker.save_item, Qt.ConnectionType.QueuedConnection)
        self.ocr_update_requested.connect(db_worker.update_ocr, Qt.ConnectionType.QueuedConnection)
        self.barrier_requested.connect(db_worker.barrier, Qt.ConnectionType.QueuedConnection)
        capture.capture_done.connect(self.captured)
        capture.capture_failed.connect(self.capture_failed)
        # Preserve the public item_saved(int) signal for existing UI consumers;
        # the contextual acknowledgement avoids a late DB save resetting intent.
        db_worker.item_saved_with_context.connect(self.saved)
        db_worker.item_updated.connect(self.updated)
        db_worker.save_failed.connect(self.save_failed)
        db_worker.ocr_persisted.connect(self.persisted)
        db_worker.barrier_reached.connect(self.barrier)
        ocr.ocr_done.connect(self.recognized)
        ocr.submission_rejected.connect(self.submission_failed)
        capture.finished.connect(self.capture_stopped)
        ocr.finished.connect(self.ocr_stopped)
        db_thread.finished.connect(self.db_stopped)

    @Slot(str)
    def dispatch_hotkey(self, name):
        if not self.closing:
            action = self.hotkey_actions.get(name)
            if action:
                action()

    @Slot(str, object)
    def captured(self, path, dto):
        if not self.closing:
            self.widget.show()
        self.save_requested.emit(dto)

    @Slot(str)
    def capture_failed(self, error):
        if not self.closing:
            self.widget.show()
        self.widget.set_ocr_status('截圖失敗：' + error)

    @Slot(str)
    def save_failed(self, error):
        self.widget.set_ocr_status('儲存失敗：' + error)

    @Slot(int)
    @Slot(int, object)
    def saved(self, item_id, clipboard_epoch=None):
        self.widget.refresh_list()
        item = self.repo.get_by_id(item_id)
        auto_capture = self.cfg and self.cfg.get('capture', 'auto_ocr_on_capture', default=True)
        if item and item.raw_image_path and (item.source_mode == 'region_ocr' or
                (auto_capture and item.source_mode in ('region_image', 'fullscreen'))):
            self.ocr.queue_ocr(item_id, self.files.get_abs_path(item.raw_image_path),
                               clipboard_epoch=clipboard_epoch)

    @Slot(int)
    def updated(self, item_id):
        self.widget.refresh_list()

    @Slot(int, object)
    def recognized(self, item_id, result):
        self.ocr_update_requested.emit(item_id, result)

    @Slot(int, str)
    def submission_failed(self, item_id, error):
        self.widget.set_ocr_status(f'OCR #{item_id} 未加入佇列：{error}')
        if hasattr(self.widget, 'set_ocr_progress'):
            self.widget.set_ocr_progress(0)

    @Slot(int, object)
    def persisted(self, item_id, result):
        # A queued acknowledgement may arrive after a GUI edit/delete/rerun.
        # Re-read at the actual publication boundary, not only at DB commit.
        item = self.repo.get_by_id(item_id)
        if (not item or item.is_deleted or item.ocr_job_id != result.job_id):
            return
        if hasattr(self.widget, 'set_ocr_progress'):
            self.widget.set_ocr_progress(0)
        if result.status == 'failed' or not result.text or not result.text.strip():
            self.widget.set_ocr_status('未找到文字，已保留先前內容')
            return
        needs_review = result.status == 'needs_review' or item.ocr_status == 'needs_review'
        if needs_review:
            self.widget.set_ocr_status('有些文字待確認，請對照原圖')
        else:
            self.widget.set_ocr_status('辨識完成')
        if (not self.closing and item.edited_text is None and
                item.edit_revision == result.base_edit_revision and
                result.status in ('done', 'needs_review', 'confirmed') and
                item.ocr_status in ('done', 'needs_review', 'confirmed')):
            # A late background result must not interrupt newer manual intent,
            # whichever copy mode is currently selected.
            from ..clipboard.writer import copy_text, get_clipboard_epoch
            if (result.clipboard_epoch is not None and
                    result.clipboard_epoch != get_clipboard_epoch()):
                return
            copy_mode = self.cfg.get('clipboard', 'ocr_copy_mode', default='all') if self.cfg else 'all'
            if copy_mode == 'select':
                self.widget.set_ocr_status('有些文字待確認，選取後再複製' if needs_review
                                           else '辨識完成，選取要複製的文字')
                self.selection_requested.emit(item_id, result)
            elif copy_text(item.get_effective_text(), self.cfg, automatic=True):
                self.widget.set_ocr_status('有些文字待確認；已複製全部文字，請對照原圖' if needs_review
                                           else '已複製全部文字')

    def _selection_is_current(self, item_id, result):
        item = self.repo.get_by_id(item_id)
        return (not self.closing and item is not None and not item.is_deleted and
                item.ocr_job_id == result.job_id and item.edited_text is None and
                item.edit_revision == result.base_edit_revision and
                result.status in ('done', 'needs_review', 'confirmed') and
                item.ocr_status in ('done', 'needs_review', 'confirmed'))

    @Slot(int, object)
    def select_ocr_text(self, item_id, result):
        """Present the saved result without blocking capture or worker draining."""
        if not self._selection_is_current(item_id, result):
            return
        from PySide6.QtWidgets import QWidget

        from ..ui.ocr_result_dialog import OcrResultDialog
        item = self.repo.get_by_id(item_id)
        image_path = None
        if item.raw_image_path:
            try:
                image_path = self.files.get_abs_path(item.raw_image_path)
            except ValueError:
                logger.warning('Ignoring invalid stored image path for item %s', item_id)
        existing = self._ocr_selections.get(item_id)
        if existing is not None:
            existing.reject()
        dialog = OcrResultDialog(
            item.get_effective_text(), item.ocr_detail_json, image_path,
            needs_review=item.ocr_status == 'needs_review',
            parent=self.widget if isinstance(self.widget, QWidget) else None)
        self._ocr_selections[item_id] = dialog
        dialog.copy_requested.connect(
            lambda text: self.copy_ocr_selection(item_id, result, text))

        def finished(_):
            if self._ocr_selections.get(item_id) is dialog:
                self._ocr_selections.pop(item_id, None)
            dialog.deleteLater()

        dialog.finished.connect(finished)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def copy_ocr_selection(self, item_id, result, text):
        # The user may edit, delete or rerun the item while this modeless dialog
        # is open. Validate again at the actual write boundary.
        if not self._selection_is_current(item_id, result):
            if not self.closing:
                self.widget.set_ocr_status('內容已更新，請從歷史紀錄重新開啟')
            return
        from ..clipboard.writer import copy_text
        if copy_text(text, self.cfg):
            self.widget.set_ocr_status('已複製所選文字')
        else:
            self.widget.set_ocr_status('沒有可複製的文字')

    @Slot()
    def shutdown(self):
        if self.closing:
            return
        self.closing = True
        for dialog in list(self._ocr_selections.values()):
            dialog.reject()
        self._shutdown_timer.start()
        self.widget.setEnabled(False)
        if hasattr(self.widget, 'shutdown_handwriting'):
            self.widget.shutdown_handwriting()
        if hasattr(self.widget, 'shutdown_documents'):
            self.widget.shutdown_documents()
        self.widget.set_ocr_status('正在完成已接受的工作並關閉…')
        if self.clipboard:
            self.clipboard.pause(True)
        if self.hotkeys and not self.hotkeys.stop():
            logger.warning('熱鍵執行緒仍在停止中')
        self.capture.stop()
        if not self.capture._started_once:
            self.capture_stopped()

    @Slot()
    def report_shutdown_stall(self):
        if self.closed:
            return
        # Native in-process inference cannot safely be killed. Surface the
        # delay without reporting success, terminating a QThread, or closing
        # SQLite underneath a live writer. Process isolation is a separate gate.
        message = ('關閉仍在等待背景工具；資料庫已關閉' if self._database_closed else
                   '關閉仍在等待背景工作；資料庫尚未關閉，請勿強制結束程式')
        logger.error(message)
        self.widget.set_ocr_status(message)
        self.shutdown_stalled.emit(message)

    @Slot()
    def capture_stopped(self):
        if self.closing:
            self.capture.wait()
            self.barrier_requested.emit('captures')

    @Slot(str)
    def barrier(self, token):
        if token == 'captures':
            # Every preceding item_saved callback has now queued its OCR job.
            self.ocr.stop()
            if not self.ocr._started_once:
                self.ocr_stopped()
        elif token == 'results':
            self.db_thread.quit()

    @Slot()
    def ocr_stopped(self):
        if self.closing:
            self.ocr.wait()
            self.barrier_requested.emit('results')

    @Slot()
    def db_stopped(self):
        self.db_thread.wait()
        if self.hotkeys:
            self.hotkeys.wait()
        self.database.close()
        self._database_closed = True
        self._finish_shutdown()

    @Slot()
    def _finish_shutdown(self):
        if not self._database_closed or self.closed:
            return
        if (hasattr(self.widget, 'handwriting_is_stopped') and
                not self.widget.handwriting_is_stopped()):
            # QProcess remains owned until finished, never a GUI-thread wait.
            self._tools_poll.start()
            return
        if (hasattr(self.widget, 'documents_is_stopped') and
                not self.widget.documents_is_stopped()):
            self._tools_poll.start()
            return
        self._tools_poll.stop()
        self._shutdown_timer.stop()
        self.closed = True
        self.shutdown_finished.emit()

    @Slot()
    def ensure_shutdown(self):
        """aboutToQuit fallback: keep delivering queued callbacks until drained."""
        if self.closed:
            return
        loop = QEventLoop()
        self.shutdown_finished.connect(loop.quit)
        self.shutdown()
        if not self.closed:
            loop.exec()
        self.shutdown_finished.disconnect(loop.quit)
