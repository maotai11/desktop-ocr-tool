# -*- coding: utf-8 -*-
import logging
from PySide6.QtCore import QObject, QThread, Signal, Slot
from ..data.repository import ItemRepository
from ..data.models import ItemCreateDTO, OcrResultDTO
from ..data.file_manager import FileManager

logger = logging.getLogger(__name__)


class DbWorker(QObject):
    """
    Capture/OCR writes run here. Editor/tag writes use their caller's connection.
    Database serializes write transactions; each thread owns its connection.
    """
    item_saved = Signal(int)
    item_updated = Signal(int)
    item_deleted = Signal(int)
    save_failed = Signal(str)
    items_soft_deleted = Signal(list)   # list[int] — soft-deleted item ids
    item_pinned = Signal(int, bool)     # item_id, new value
    item_archived = Signal(int, bool)   # item_id, new value
    purge_done = Signal(int)            # count of hard-deleted records
    ocr_persisted = Signal(int, object)
    barrier_reached = Signal(str)

    def __init__(self, repo: ItemRepository, file_manager: FileManager,
                 enable_dedup: bool = True, save_raw_image: bool = True):
        super().__init__()
        self._repo = repo
        self._file_manager = file_manager
        self._enable_dedup = enable_dedup
        self._save_raw_image = save_raw_image

    @Slot(str)
    def barrier(self, token):
        self.barrier_reached.emit(token)

    @Slot(object)
    def save_item(self, dto: ItemCreateDTO):
        try:
            if self._enable_dedup and dto.source_mode not in ('import',):
                if self._repo.should_deduplicate(
                    dto.source_mode, dto.content_hash, dto.image_hash
                ):
                    logger.debug(f"去重跳過: source_mode={dto.source_mode}")
                    self._file_manager.delete_item_files(dto)
                    return

            item_id = self._repo.insert(dto)
            if item_id:
                logger.info(f"已儲存 item #{item_id}")
                self.item_saved.emit(item_id)
            else:
                self.save_failed.emit("儲存失敗")
        except Exception as e:
            logger.error(f"DbWorker save_item 失敗: {e}", exc_info=True)
            self.save_failed.emit(str(e))

    @Slot(int, object)
    def update_ocr(self, item_id: int, result: OcrResultDTO):
        try:
            self._repo.update_ocr_result(item_id, result)
            # The result transaction committed successfully before any unlink.
            # Failed/empty OCR retains its source for retry and review.
            if not self._save_raw_image and result.text and result.status in ('done', 'needs_review'):
                item = self._repo.get_by_id(item_id)
                if item and item.raw_image_path:
                    try:
                        from pathlib import Path
                        Path(self._file_manager.get_abs_path(item.raw_image_path)).unlink(missing_ok=True)
                        self._repo.clear_image_paths(item_id)
                    except (OSError, ValueError):
                        logger.exception('保留原圖路徑：原圖清理未完成 (item %s)', item_id)
            self.ocr_persisted.emit(item_id, result)
            self.item_updated.emit(item_id)
        except Exception as e:
            logger.error(f"DbWorker update_ocr 失敗: {e}", exc_info=True)
            self.save_failed.emit(f'OCR #{item_id} 寫入失敗；原圖已保留')

    @Slot(int, bool)
    def delete_item(self, item_id: int, delete_files: bool = True):
        try:
            item = self._repo.hard_delete(item_id)
            if item and delete_files:
                self._file_manager.delete_item_files(item)
            self.item_deleted.emit(item_id)
        except Exception as e:
            logger.error(f"DbWorker delete_item 失敗: {e}", exc_info=True)

    @Slot(list)
    def soft_delete_items(self, item_ids: list):
        try:
            for item_id in item_ids:
                self._repo.soft_delete(item_id)
            self.items_soft_deleted.emit(item_ids)
        except Exception as e:
            logger.error(f"DbWorker soft_delete_items 失敗: {e}", exc_info=True)

    @Slot(int, bool)
    def set_pinned(self, item_id: int, value: bool):
        try:
            self._repo.set_pinned(item_id, value)
            self.item_pinned.emit(item_id, value)
        except Exception as e:
            logger.error(f"DbWorker set_pinned 失敗: {e}", exc_info=True)

    @Slot(int, bool)
    def set_archived(self, item_id: int, value: bool):
        try:
            self._repo.set_archived(item_id, value)
            self.item_archived.emit(item_id, value)
        except Exception as e:
            logger.error(f"DbWorker set_archived 失敗: {e}", exc_info=True)

    @Slot(int)
    def clear_image_paths(self, item_id: int):
        try:
            self._repo.clear_image_paths(item_id)
        except Exception as e:
            logger.error(f"DbWorker clear_image_paths 失敗 (item #{item_id}): {e}",
                         exc_info=True)

    @Slot()
    def purge_soft_deleted(self):
        try:
            n, items = self._repo.hard_delete_all_soft_deleted()
            for item in items:
                self._file_manager.delete_item_files(item)
            self.purge_done.emit(n)
        except Exception as e:
            logger.error(f"DbWorker purge_soft_deleted 失敗: {e}", exc_info=True)


def create_db_worker_in_thread(repo: ItemRepository,
                                file_manager: FileManager,
                                enable_dedup: bool = True,
                                save_raw_image: bool = True) -> tuple:
    thread = QThread()
    worker = DbWorker(repo, file_manager, enable_dedup, save_raw_image)
    worker.moveToThread(thread)
    thread.finished.connect(worker.deleteLater)
    thread.start()
    return worker, thread
