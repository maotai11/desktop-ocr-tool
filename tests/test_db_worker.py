# -*- coding: utf-8 -*-
"""
回歸測試：DbWorker slots 的資料層效果
直接呼叫 slot 方法（不走 QThread），驗證 DB 狀態。
涵蓋 Patch 2/7。

注意：Signal 在此不做連接驗證（需要 Qt event loop）。
     本檔只確認 slot 呼叫後 DB 狀態正確，UI 訊號路徑見 SMOKE_TEST_CHECKLIST.md。
"""


class TestSoftDeleteItemsSlot:
    def test_marks_all_items_as_deleted(self, item_repo, db_worker, make_text_dto):
        """Patch 2: soft_delete_items([id…]) 讓每筆 is_deleted=True。"""
        ids = [item_repo.insert(make_text_dto(f"t{i}")) for i in range(3)]
        db_worker.soft_delete_items(ids)
        assert all(item_repo.get_by_id(iid).is_deleted for iid in ids)

    def test_partial_list_only_marks_given_ids(self, item_repo, db_worker, make_text_dto):
        """Patch 2: soft_delete_items 只影響傳入的 ids，其他筆不受影響。"""
        del_ids = [item_repo.insert(make_text_dto(f"del{i}")) for i in range(2)]
        keep_id = item_repo.insert(make_text_dto("keep"))
        db_worker.soft_delete_items(del_ids)
        assert all(item_repo.get_by_id(iid).is_deleted for iid in del_ids)
        assert item_repo.get_by_id(keep_id).is_deleted is False


class TestPurgeSoftDeletedSlot:
    def test_hard_deletes_soft_deleted_only(self, item_repo, db_worker, make_text_dto):
        """Patch 2: purge_soft_deleted 永久刪除軟刪筆，保留未刪筆。"""
        del_ids = [item_repo.insert(make_text_dto(f"del{i}")) for i in range(2)]
        keep_id = item_repo.insert(make_text_dto("keep me"))
        for iid in del_ids:
            item_repo.soft_delete(iid)

        db_worker.purge_soft_deleted()

        assert all(item_repo.get_by_id(iid) is None for iid in del_ids)
        assert item_repo.get_by_id(keep_id) is not None


class TestClearImagePathsSlot:
    def test_nullifies_raw_image_path(self, item_repo, db_worker, make_image_dto):
        """Patch 7: clear_image_paths slot 在 DB 清空 raw_image_path。"""
        iid = item_repo.insert(make_image_dto(rel_path="captures/patch7.png"))
        db_worker.clear_image_paths(iid)
        assert item_repo.get_by_id(iid).raw_image_path is None

    def test_preserves_thumbnail_path(self, item_repo, db_worker, make_image_dto):
        """Patch 7: clear_image_paths slot 不影響 thumbnail_path。"""
        iid = item_repo.insert(make_image_dto(
            rel_path="captures/patch7.png",
            thumb_path="thumbnails/patch7_thumb.png",
        ))
        db_worker.clear_image_paths(iid)
        assert item_repo.get_by_id(iid).thumbnail_path == "thumbnails/patch7_thumb.png"


class TestPinArchiveSlots:
    def test_set_pinned_toggle(self, item_repo, db_worker, make_text_dto):
        """Patch 2: set_pinned slot 正確寫入 is_pinned，可雙向 toggle。"""
        iid = item_repo.insert(make_text_dto())
        db_worker.set_pinned(iid, True)
        assert item_repo.get_by_id(iid).is_pinned is True
        db_worker.set_pinned(iid, False)
        assert item_repo.get_by_id(iid).is_pinned is False

    def test_set_archived_toggle(self, item_repo, db_worker, make_text_dto):
        """Patch 2: set_archived slot 正確寫入 is_archived，可雙向 toggle。"""
        iid = item_repo.insert(make_text_dto())
        db_worker.set_archived(iid, True)
        assert item_repo.get_by_id(iid).is_archived is True
        db_worker.set_archived(iid, False)
        assert item_repo.get_by_id(iid).is_archived is False
