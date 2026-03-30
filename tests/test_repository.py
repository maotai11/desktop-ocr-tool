# -*- coding: utf-8 -*-
"""
回歸測試：ItemRepository 資料層
涵蓋 Patch 1/2/5/7 涉及的寫入路徑。
無 Qt 依賴，純 SQLite，可在任何環境直接執行。
"""


class TestSoftDelete:
    def test_soft_delete_sets_flag(self, item_repo, make_text_dto):
        """Patch 1/2: soft_delete 應設 is_deleted=True。"""
        iid = item_repo.insert(make_text_dto())
        item_repo.soft_delete(iid)
        assert item_repo.get_by_id(iid).is_deleted is True

    def test_multi_soft_delete_all_marked(self, item_repo, make_text_dto):
        """Patch 1: 5 筆批量軟刪除後每筆都應是 is_deleted=True。"""
        ids = [item_repo.insert(make_text_dto(f"item {i}")) for i in range(5)]
        for iid in ids:
            item_repo.soft_delete(iid)
        assert all(item_repo.get_by_id(iid).is_deleted for iid in ids)

    def test_soft_deleted_excluded_from_list_recent(self, item_repo, make_text_dto):
        """Patch 1/3: 軟刪除後 list_recent 預設不回傳該筆。"""
        iid = item_repo.insert(make_text_dto("will be deleted"))
        item_repo.soft_delete(iid)
        listed_ids = {i.id for i in item_repo.list_recent(limit=200)}
        assert iid not in listed_ids

    def test_hard_delete_all_soft_deleted_removes_only_deleted(
        self, item_repo, make_text_dto
    ):
        """Patch 2: hard_delete_all_soft_deleted 移除軟刪筆、保留未刪筆。"""
        del_ids = [item_repo.insert(make_text_dto(f"del{i}")) for i in range(3)]
        keep_id = item_repo.insert(make_text_dto("keep me"))
        for iid in del_ids:
            item_repo.soft_delete(iid)

        count, _ = item_repo.hard_delete_all_soft_deleted()

        assert count == 3
        assert all(item_repo.get_by_id(iid) is None for iid in del_ids)
        assert item_repo.get_by_id(keep_id) is not None


class TestClearImagePaths:
    def test_nullifies_raw_image_path(self, item_repo, make_image_dto):
        """Patch 7: clear_image_paths → raw_image_path 設為 None。"""
        iid = item_repo.insert(make_image_dto(rel_path="captures/a.png"))
        item_repo.clear_image_paths(iid)
        assert item_repo.get_by_id(iid).raw_image_path is None

    def test_preserves_thumbnail_path(self, item_repo, make_image_dto):
        """Patch 7: clear_image_paths 不應碰 thumbnail_path（縮圖仍可供詳情面板使用）。"""
        iid = item_repo.insert(make_image_dto(
            rel_path="captures/a.png",
            thumb_path="thumbnails/a_thumb.png",
        ))
        item_repo.clear_image_paths(iid)
        assert item_repo.get_by_id(iid).thumbnail_path == "thumbnails/a_thumb.png"


class TestOcrResultOrdering:
    def test_update_ocr_sets_mixed_when_image_present(
        self, item_repo, make_image_dto, make_ocr_result
    ):
        """Patch 7 ordering: update_ocr_result 在 raw_image_path 存在時應設 item_type='mixed'。
        這個前提是 clear_image_paths 必須在 update_ocr_result *之後* dispatch。
        """
        iid = item_repo.insert(make_image_dto(rel_path="captures/x.png"))
        item_repo.update_ocr_result(iid, make_ocr_result(text="some text"))
        item = item_repo.get_by_id(iid)
        assert item.item_type == 'mixed'
        assert item.text_content == "some text"

    def test_clear_after_ocr_preserves_text_content(
        self, item_repo, make_image_dto, make_ocr_result
    ):
        """Patch 7: clear_image_paths 在 update_ocr_result 之後執行不影響 text_content。"""
        iid = item_repo.insert(make_image_dto(rel_path="captures/x.png"))
        item_repo.update_ocr_result(iid, make_ocr_result(text="kept text"))
        item_repo.clear_image_paths(iid)
        item = item_repo.get_by_id(iid)
        assert item.text_content == "kept text"
        assert item.raw_image_path is None


class TestDeduplication:
    def test_same_hash_within_window_returns_true(self, item_repo, make_text_dto):
        """Patch 5: 相同 content_hash + source_mode 在 DEDUP_SECONDS 內應回 True。"""
        from src.data.hasher import sha256_text
        text = "duplicate content"
        item_repo.insert(make_text_dto(text))
        assert item_repo.should_deduplicate(
            "clipboard_text", content_hash=sha256_text(text)
        ) is True

    def test_different_hash_returns_false(self, item_repo, make_text_dto):
        """不同 content_hash 不觸發去重。"""
        from src.data.hasher import sha256_text
        item_repo.insert(make_text_dto("first content"))
        assert item_repo.should_deduplicate(
            "clipboard_text", content_hash=sha256_text("completely different")
        ) is False

    def test_empty_repo_returns_false(self, item_repo):
        """空 repo 不觸發去重（無前次紀錄可比較）。"""
        from src.data.hasher import sha256_text
        assert item_repo.should_deduplicate(
            "clipboard_text", content_hash=sha256_text("anything")
        ) is False
