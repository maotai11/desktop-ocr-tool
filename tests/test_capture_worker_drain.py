# -*- coding: utf-8 -*-
"""
回歸測試：CaptureWorker queue drain
涵蓋 Patch 4：while-loop 連續處理所有 task，單筆例外不中斷後續。

需要 pytest-qt (pip install pytest-qt>=4)：
    qapp fixture 提供 QApplication，使 QThread 子類可正常實例化、
    Signal emit 在主執行緒以 DirectConnection 觸發。

run() 直接呼叫（非 start()），確保在測試執行緒同步完成。
"""
import numpy as np
import pytest
from unittest.mock import MagicMock, patch


def _black_frame():
    """100×100 黑色 numpy BGR frame，模擬截圖後端回傳。"""
    return np.zeros((100, 100, 3), dtype=np.uint8)


class TestCaptureWorkerDrain:
    def test_all_queued_tasks_processed(self, qapp, tmp_path):
        """Patch 4: 事先 enqueue 3 個 region task，run() 應全部 emit capture_done。"""
        from src.data.file_manager import FileManager
        from src.workers.capture_worker import CaptureWorker

        file_mgr = FileManager(str(tmp_path / "data"))
        fake_img = str(tmp_path / "fake.png")
        file_mgr.save_capture = MagicMock(return_value=(fake_img, "captures/fake.png"))
        file_mgr.create_thumbnail = MagicMock(
            return_value=(fake_img, "thumbnails/fake_thumb.png")
        )

        mock_backend = MagicMock()
        mock_backend.capture_region.return_value = _black_frame()

        worker = CaptureWorker(file_mgr)
        # 直接入列，不呼叫 capture_region()，避免 start() 在 patch 外提前觸發
        for _ in range(3):
            worker._queue.put(('region', 0, 0, 100, 100, 1, 'region_ocr'))

        done_count = [0]
        worker.capture_done.connect(
            lambda p, d: done_count.__setitem__(0, done_count[0] + 1)
        )

        with patch('src.workers.capture_worker.get_capture_backend',
                   return_value=mock_backend), \
             patch('src.workers.capture_worker.phash_image', return_value='fakehash'):
            worker.run()

        assert done_count[0] == 3, (
            f"Expected 3 capture_done emissions, got {done_count[0]}. "
            "Patch 4 queue drain may not be working."
        )

    def test_exception_mid_queue_does_not_halt_drain(self, qapp, tmp_path):
        """Patch 4: 第 2 個 task 拋例外後，第 3 個仍應被處理（emit capture_done），
        且第 2 個應 emit capture_failed。
        """
        from src.data.file_manager import FileManager
        from src.workers.capture_worker import CaptureWorker

        file_mgr = FileManager(str(tmp_path / "data"))
        fake_img = str(tmp_path / "fake.png")
        file_mgr.create_thumbnail = MagicMock(
            return_value=(fake_img, "thumbnails/fake_thumb.png")
        )

        call_n = [0]

        def save_side(*args, **kwargs):
            call_n[0] += 1
            if call_n[0] == 2:
                raise RuntimeError("模擬第 2 筆截圖後端失敗")
            return (fake_img, "captures/fake.png")

        file_mgr.save_capture = MagicMock(side_effect=save_side)

        mock_backend = MagicMock()
        mock_backend.capture_region.return_value = _black_frame()

        worker = CaptureWorker(file_mgr)
        # 直接入列，不呼叫 capture_region()，避免 start() 在 patch 外提前觸發
        for _ in range(3):
            worker._queue.put(('region', 0, 0, 100, 100, 1, 'region_ocr'))

        done_count = [0]
        fail_count = [0]
        worker.capture_done.connect(
            lambda p, d: done_count.__setitem__(0, done_count[0] + 1)
        )
        worker.capture_failed.connect(
            lambda e: fail_count.__setitem__(0, fail_count[0] + 1)
        )

        with patch('src.workers.capture_worker.get_capture_backend',
                   return_value=mock_backend), \
             patch('src.workers.capture_worker.phash_image', return_value='fakehash'):
            worker.run()

        assert done_count[0] == 2, (
            f"Expected 2 successful captures, got {done_count[0]}."
        )
        assert fail_count[0] == 1, (
            f"Expected 1 failed capture, got {fail_count[0]}."
        )
