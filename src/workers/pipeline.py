"""Qt owner-thread dispatch and ordered, draining shutdown."""
import logging
from PySide6.QtCore import QObject, Signal, Slot, Qt, QEventLoop
from ..data.models import OcrResultDTO

logger = logging.getLogger(__name__)


class Pipeline(QObject):
    save_requested = Signal(object)
    ocr_update_requested = Signal(int, object)
    barrier_requested = Signal(str)
    shutdown_finished = Signal()

    def __init__(self, capture, ocr, db_worker, db_thread, database,
                 repo, files, widget, cfg, hotkeys=None, clipboard=None):
        super().__init__()
        self.capture, self.ocr = capture, ocr
        self.db_worker, self.db_thread, self.database = db_worker, db_thread, database
        self.repo, self.files, self.widget, self.cfg = repo, files, widget, cfg
        self.hotkeys, self.clipboard = hotkeys, clipboard
        self.closing = self.closed = False
        self.save_requested.connect(db_worker.save_item, Qt.ConnectionType.QueuedConnection)
        self.ocr_update_requested.connect(db_worker.update_ocr, Qt.ConnectionType.QueuedConnection)
        self.barrier_requested.connect(db_worker.barrier, Qt.ConnectionType.QueuedConnection)
        capture.capture_done.connect(self.captured)
        capture.capture_failed.connect(self.capture_failed)
        db_worker.item_saved.connect(self.saved)
        db_worker.item_updated.connect(self.updated)
        db_worker.save_failed.connect(self.save_failed)
        db_worker.ocr_persisted.connect(self.persisted)
        db_worker.barrier_reached.connect(self.barrier)
        ocr.ocr_done.connect(self.recognized)
        ocr.ocr_failed.connect(self.recognition_failed)
        capture.finished.connect(self.capture_stopped)
        ocr.finished.connect(self.ocr_stopped)
        db_thread.finished.connect(self.db_stopped)

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
    def saved(self, item_id):
        self.widget.refresh_list()
        item = self.repo.get_by_id(item_id)
        auto_capture = self.cfg and self.cfg.get('capture', 'auto_ocr_on_capture', default=True)
        if item and item.raw_image_path and (item.source_mode == 'region_ocr' or
                (auto_capture and item.source_mode in ('region_image', 'fullscreen'))):
            self.ocr.queue_ocr(item_id, self.files.get_abs_path(item.raw_image_path))

    @Slot(int)
    def updated(self, item_id):
        self.widget.refresh_list()

    @Slot(int, object)
    def recognized(self, item_id, result):
        self.ocr_update_requested.emit(item_id, result)

    @Slot(int, str)
    def recognition_failed(self, item_id, error):
        self.ocr_update_requested.emit(item_id, OcrResultDTO(
            text='', confidence=0.0, status='failed', error_message=error))

    @Slot(int, object)
    def persisted(self, item_id, result):
        if result.status == 'failed':
            self.widget.set_ocr_status(f'OCR #{item_id} 失敗；原圖已保留')
        elif result.text and not self.closing:
            from ..clipboard.writer import write_text_to_clipboard
            write_text_to_clipboard(result.text)

    @Slot()
    def shutdown(self):
        if self.closing:
            return
        self.closing = True
        self.widget.setEnabled(False)
        self.widget.set_ocr_status('正在完成已接受的工作並關閉…')
        if self.clipboard:
            self.clipboard.pause(True)
        if self.hotkeys and not self.hotkeys.stop():
            logger.warning('熱鍵執行緒仍在停止中')
        self.capture.stop()
        if not self.capture._started_once:
            self.capture_stopped()

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
