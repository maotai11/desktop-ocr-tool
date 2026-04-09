# -*- coding: utf-8 -*-
from src.ocr.engine import OcrEngine
from src.workers.ocr_worker import OcrWorker


def test_start_loading_configures_wrapper_progress_callback(qapp, monkeypatch):
    engine = OcrEngine()
    started = []

    monkeypatch.setattr(OcrWorker, "start", lambda self: started.append(self._mode))

    worker = OcrWorker(engine)
    worker.start_loading()

    assert started == ["load"]
    assert callable(engine._progress_callback)
