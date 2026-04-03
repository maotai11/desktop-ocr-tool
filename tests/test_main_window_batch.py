# -*- coding: utf-8 -*-
"""
回歸測試：MainWindow 批量選取模式狀態機
涵蓋 Group B：_toggle_batch_mode / _on_selection_changed / _on_row_changed

需要 pytest-qt (pip install pytest-qt>=4)。
MainWindow 以 MagicMock 注入最小依賴，不跑真實 DB 操作。
"""
import pytest
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# Fixture：最小化 MainWindow 依賴注入
# ---------------------------------------------------------------------------

@pytest.fixture
def mw(qapp, tmp_path, db_worker):
    """建立帶真實 DbWorker 但 mock item_repo / ocr_worker / cfg 的 MainWindow。"""
    from src.ui.main_window import MainWindow
    from src.data.file_manager import FileManager

    item_repo = MagicMock()
    item_repo.list_recent.return_value = []
    item_repo.search_fulltext.return_value = []
    stats = MagicMock()
    stats.total = 0
    stats.ocr_done = 0
    stats.ocr_needs_review = 0
    item_repo.get_statistics.return_value = stats

    file_mgr = FileManager(str(tmp_path / "data"))

    win = MainWindow(
        item_repo=item_repo,
        tag_repo=MagicMock(),
        file_mgr=file_mgr,
        db_worker=db_worker,
        ocr_worker=MagicMock(),
        cfg=MagicMock(),
        data_dir=str(tmp_path),
    )
    yield win
    win.close()


# ---------------------------------------------------------------------------
# 批量模式狀態機
# ---------------------------------------------------------------------------

class TestBatchModeToggle:
    def test_initial_state_is_normal(self, mw):
        """初始狀態：批量模式關閉，action bar 隱藏。"""
        assert mw._batch_mode is False
        # isVisible() 在 parent 未 show 時恆 False；用 isHidden() 測 widget 自身旗標
        assert mw._batch_bar.isHidden()
        assert not mw._btn_toggle_batch.isChecked()

    def test_enter_batch_mode(self, mw):
        """進入批量模式：flag 設為 True，action bar 不再隱藏，按鈕 checked。"""
        mw._toggle_batch_mode(True)
        assert mw._batch_mode is True
        assert not mw._batch_bar.isHidden()
        assert mw._btn_toggle_batch.isChecked()

    def test_exit_batch_mode(self, mw):
        """離開批量模式：flag 回 False，action bar 重新隱藏，刪除按鈕停用。"""
        mw._toggle_batch_mode(True)
        mw._toggle_batch_mode(False)
        assert mw._batch_mode is False
        assert mw._batch_bar.isHidden()
        assert not mw._btn_batch_delete.isEnabled()

    def test_toggle_without_arg_reads_button_state(self, mw):
        """呼叫 _toggle_batch_mode() 無參數時，以按鈕 checked 狀態為準。"""
        mw._btn_toggle_batch.setChecked(True)
        mw._toggle_batch_mode()
        assert mw._batch_mode is True

        mw._btn_toggle_batch.setChecked(False)
        mw._toggle_batch_mode()
        assert mw._batch_mode is False

    def test_exit_clears_selection(self, mw, qtbot):
        """離開批量模式時，table selection 應被清空。"""
        # 加入一行以便選取
        from PySide6.QtWidgets import QTableWidgetItem
        mw._table.setRowCount(1)
        for col in range(mw._table.columnCount()):
            mw._table.setItem(0, col, QTableWidgetItem("x"))

        mw._toggle_batch_mode(True)
        mw._table.selectAll()
        assert len(mw._table.selectionModel().selectedRows()) == 1

        mw._toggle_batch_mode(False)
        assert len(mw._table.selectionModel().selectedRows()) == 0


# ---------------------------------------------------------------------------
# 選取計數
# ---------------------------------------------------------------------------

class TestBatchSelectionCounter:
    def _add_rows(self, mw, n: int):
        from PySide6.QtWidgets import QTableWidgetItem
        mw._table.setRowCount(n)
        for r in range(n):
            for c in range(mw._table.columnCount()):
                mw._table.setItem(r, c, QTableWidgetItem(str(r)))

    def test_counter_updates_on_selection(self, mw):
        """選取 2 行時 sel_count_lbl 顯示「已選 2 筆」。"""
        self._add_rows(mw, 5)
        mw._toggle_batch_mode(True)
        mw._table.selectRow(0)
        mw._table.selectRow(1)
        # _on_selection_changed 會透過 selectionChanged signal 觸發
        n = len(mw._table.selectionModel().selectedRows())
        # 手動驗證 label 文字與實際選取數一致
        mw._on_selection_changed()
        assert f"已選 {n} 筆" in mw._sel_count_lbl.text()

    def test_delete_button_disabled_when_none_selected(self, mw):
        """無選取時刪除按鈕應停用。"""
        mw._toggle_batch_mode(True)
        mw._table.clearSelection()
        mw._on_selection_changed()
        assert not mw._btn_batch_delete.isEnabled()

    def test_delete_button_enabled_when_row_selected(self, mw):
        """有選取時刪除按鈕應啟用。"""
        self._add_rows(mw, 3)
        mw._toggle_batch_mode(True)
        mw._table.selectRow(0)
        mw._on_selection_changed()
        assert mw._btn_batch_delete.isEnabled()

    def test_counter_no_update_in_normal_mode(self, mw):
        """normal mode 下 _on_selection_changed 不修改計數 label。"""
        self._add_rows(mw, 3)
        assert mw._batch_mode is False
        original = mw._sel_count_lbl.text()
        mw._table.selectAll()
        mw._on_selection_changed()   # should be no-op in normal mode
        assert mw._sel_count_lbl.text() == original


# ---------------------------------------------------------------------------
# detail panel 保護
# ---------------------------------------------------------------------------

class TestDetailPanelProtection:
    def test_row_change_in_batch_mode_no_detail_update(self, mw):
        """批量模式下 _on_row_changed 應不更新 _selected_item。"""
        mw._toggle_batch_mode(True)
        mw._current_items = [MagicMock()]
        mw._selected_item = None
        mw._on_row_changed(0)
        assert mw._selected_item is None   # 不應被更新

    def test_row_change_in_normal_mode_updates_detail(self, mw):
        """normal mode 下 _on_row_changed 應更新 _selected_item。"""
        fake_item = MagicMock()
        fake_item.thumbnail_path = None
        fake_item.raw_image_path = None
        fake_item.get_effective_text.return_value = "test text"
        fake_item.item_type = 'text'
        fake_item.ocr_status = 'done'
        fake_item.ocr_confidence = 0.9
        fake_item.created_at = '2026-01-01'
        fake_item.updated_at = '2026-01-01'
        fake_item.source_mode = 'clipboard_text'
        fake_item.is_pinned = False

        mw._current_items = [fake_item]
        assert mw._batch_mode is False
        mw._on_row_changed(0)
        assert mw._selected_item is fake_item
