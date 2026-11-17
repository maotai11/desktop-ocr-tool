"""Real Qt event dispatch + real SQLite/files; injected slow OCR only."""
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import time

from PySide6.QtCore import QObject
from src.workers.capture_worker import CaptureWorker
from src.workers.ocr_worker import OcrWorker
from src.workers.db_worker import create_db_worker_in_thread
from src.workers.pipeline import Pipeline


class Widget(QObject):
    def show(self): pass
    def setEnabled(self,value): pass
    def set_ocr_status(self,text): pass
    def refresh_list(self): pass


class SlowEngine:
    def set_progress_callback(self,cb): pass
    def is_ready(self): return getattr(self,'ready',False)
    def load(self,progress_cb): time.sleep(.02); self.ready=True
    def run_ocr_from_path(self,path,mode):
        time.sleep(.002)
        return dict(text='臺灣稅務',confidence=.9,status='done',engine='test',model_version='test')


def test_shutdown_during_load_drains_capture_save_ocr_and_result(qtbot,tmp_db,item_repo,file_mgr,monkeypatch):
    import numpy as np
    class Backend:
        def capture_region(self,*a):
            time.sleep(.002)
            return np.zeros((20,30,3),np.uint8)
    monkeypatch.setattr('src.workers.capture_worker.get_capture_backend',lambda:Backend())
    capture=CaptureWorker(file_mgr); ocr=OcrWorker(SlowEngine())
    worker,thread=create_db_worker_in_thread(item_repo,file_mgr,enable_dedup=False,save_raw_image=False)
    pipeline=Pipeline(capture,ocr,worker,thread,tmp_db,item_repo,file_mgr,Widget(),None)
    try:
        ocr.start_loading()
        for _ in range(40): capture.capture_region(0,0,30,20,1,'region_ocr')
        pipeline.shutdown(); pipeline.shutdown()
        # Actual Windows NTFS/FULL-synchronous diagnostics drained all 40 jobs
        # in 28.813 s, with no orphan threads or lost results. This is a drain
        # correctness test, not a 10-second disk-performance guarantee.
        qtbot.waitUntil(lambda:pipeline.closed,timeout=60000 if sys.platform=='win32' else 10000)
        assert pipeline.closed
        assert not capture.isRunning() and not ocr.isRunning() and not thread.isRunning()
        with closing(sqlite3.connect(tmp_db._db_path)) as con:
            assert con.execute("SELECT COUNT(*) FROM items WHERE ocr_status='done' AND item_type='text' AND raw_image_path IS NULL").fetchone()[0]==40
            assert con.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert not list(Path(file_mgr._data_dir).glob('captures/**/*.png'))
    finally:
        # A failed assertion must still deliver the accepted work and shutdown
        # barriers before tmp_db's fixture closes SQLite beneath its writer.
        pipeline.ensure_shutdown()
