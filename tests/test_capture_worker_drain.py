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


def test_qr_reader_load_failure_returns_to_launcher_without_stopping_worker(qtbot, tmp_path, monkeypatch):
    import sys
    from threading import Event
    from src.ui.invoice_items_dialog import InvoiceItemsDialog

    worker = CaptureWorker(FileManager(str(tmp_path)))
    monkeypatch.setitem(sys.modules, 'src.invoice_check.qr_items', None)
    errors = []
    worker.invoice_items_ready.connect(lambda request, result, error: errors.append((result, error)))
    try:
        worker.capture_invoice_items((0, 0, 50, 50, 1), {}, Event())
        qtbot.waitUntil(lambda: bool(errors))
        result, message = errors[0]
        assert result is None and '無法載入' in message and worker.isRunning()
        dialog = InvoiceItemsDialog(None)
        qtbot.addWidget(dialog)
        dialog.set_result(result, message)
        assert dialog.summary.text() == message and not dialog.copy_button.isEnabled()
    finally:
        worker.stop()
        assert worker.wait(5000)
