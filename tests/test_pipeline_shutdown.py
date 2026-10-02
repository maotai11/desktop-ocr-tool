"""Real Qt event dispatch + real SQLite/files; injected slow OCR only."""
import time
from types import SimpleNamespace
from PIL import Image
from PySide6.QtCore import QObject, Slot
from src.data.models import ItemCreateDTO
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
    ocr.start_loading()
    for _ in range(40): capture.capture_region(0,0,30,20,1,'region_ocr')
    pipeline.shutdown(); pipeline.shutdown()
    qtbot.waitUntil(lambda:pipeline.closed,timeout=10000)
    assert not capture.isRunning() and not ocr.isRunning() and not thread.isRunning()
    import sqlite3
    with sqlite3.connect(tmp_db._db_path) as con:
        assert con.execute("SELECT COUNT(*) FROM items WHERE ocr_status='done' AND item_type='text' AND raw_image_path IS NULL").fetchone()[0]==40
        assert con.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    assert not list(__import__('pathlib').Path(file_mgr._data_dir).glob('captures/**/*.png'))
