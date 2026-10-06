import threading
from src.workers.ocr_worker import OcrWorker


class Engine:
    def __init__(self):
        self.ready = False
        self.calls = []
    def set_progress_callback(self, callback): self.callback = callback
    def is_ready(self): return self.ready
    def load(self, progress_cb):
        self.owner = threading.get_ident()
        progress_cb(100, 'ready'); self.ready = True
    def run_ocr_from_path(self, path, mode):
        assert threading.get_ident()==self.owner
        self.calls.append(path)
        return dict(text=path, confidence=1., status='done', engine='fixture', model_version='sha256:fixture')


def test_worker_load_idle_resubmit_provenance_and_stop(qtbot):
    engine = Engine(); worker = OcrWorker(engine); done=[]
    worker.ocr_done.connect(lambda i,r: done.append((i,r)))
    try:
        worker.start_loading(); worker.start_loading()  # double start is idempotent
        qtbot.waitUntil(engine.is_ready)
        qtbot.wait(100)
        for i in range(50): worker.queue_ocr(i,str(i))
        worker.stop()
        qtbot.waitUntil(lambda: len(done)==50, timeout=5000)
        assert worker.wait(5000)
        assert [i for i,_ in done]==list(range(50))
        assert all(r.engine=='fixture' and r.model_version=='sha256:fixture' for _,r in done)
        assert engine.owner!=threading.get_ident()
        failed=[]; worker.ocr_failed.connect(lambda i,e: failed.append(i))
        worker.queue_ocr(51,'after stop')
        assert failed==[51]
    finally:
        worker.stop(); assert worker.wait(5000)
