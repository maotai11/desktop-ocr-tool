"""Exercise actual QThread submission, disk images and shutdown; no direct run()."""
import numpy as np
from src.data.file_manager import FileManager
from src.workers.capture_worker import CaptureWorker


def test_capture_idle_resubmit_and_drain(qtbot, tmp_path, monkeypatch):
    class Backend:
        calls = 0
        def capture_region(self, *args):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError('injected capture failure')
            return np.full((20, 30, 3), self.calls, dtype=np.uint8)
    backend = Backend()
    monkeypatch.setattr('src.workers.capture_worker.get_capture_backend', lambda: backend)
    worker = CaptureWorker(FileManager(str(tmp_path)))
    completed, errors = [], []
    worker.capture_done.connect(lambda p,d: completed.append(d))
    worker.capture_failed.connect(errors.append)
    try:
        worker.capture_region(0,0,30,20,1,'region_ocr')
        qtbot.waitUntil(lambda: len(completed)==1)
        qtbot.wait(100)  # Old implementation exits after 50 ms of idle.
        for _ in range(30):
            worker.capture_region(0,0,30,20,1,'region_ocr')
        worker.stop()
        qtbot.waitUntil(lambda: not worker.isRunning(), timeout=5000)
        qtbot.waitUntil(lambda: len(completed)+len(errors)==31)
        assert len(completed)==30 and len(errors)==1
        assert len(list(tmp_path.rglob('*_thumb.png')))==30
        assert all(d.image_hash.startswith('sha256:') for d in completed)
    finally:
        worker.stop(); assert worker.wait(5000)
