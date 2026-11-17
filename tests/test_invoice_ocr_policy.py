"""Invoice canvas tests exercise real orchestration, never claim OCR accuracy."""
from threading import Event, get_ident
from types import SimpleNamespace

import numpy as np
import pytest

from src.core.resource_budget import ResourceBudgetExceeded
from src.invoice_check import compare_fields, extract_invoice_fields, extract_row_fields
from src.ocr.engine import OcrEngine
from src.workers.ocr_worker import OcrWorker


def source(height=49,width=620):
    return np.broadcast_to(np.arange(width,dtype=np.uint16).astype(np.uint8)[None,:,None],(height,width,3)).copy()


def ready(monkeypatch, values=(('1000',.99),), **kwargs):
    engine = OcrEngine(**kwargs)
    engine._ready = True
    engine._model_version = 'owned-synthetic'
    resets=[]
    engine._engine = SimpleNamespace(text_rec=SimpleNamespace(reset_budget=lambda:resets.append(True)))
    monkeypatch.setattr('src.core.resource_budget.memory_preflight',lambda **kwargs:None)
    monkeypatch.setattr('src.ocr.engine.enhance_for_ocr',lambda im,**kwargs:np.bitwise_xor(im,1))
    calls=[]
    values=iter(values)
    def recognize(image):
        calls.append(image.shape)
        text,confidence=next(values)
        # Geometry of a real source-space word on the 620x49 invoice row.
        box=[[30,324],[330,324],[330,384],[30,384]]
        return [[box,text,confidence]]
    monkeypatch.setattr(engine,'_do_ocr_array',recognize)
    return engine,calls,resets


def test_invoice_high_confidence_is_not_forced_review_and_preserves_source_box(monkeypatch):
    engine,calls,resets=ready(monkeypatch)
    result=engine.run_invoice_ocr(source())
    assert result['status']=='done' and result['detail'][0]['raw_text']=='1000'
    assert np.allclose(result['detail'][0]['box'],[[10,10],[110,10],[110,30],[10,30]])
    assert result['preprocessing']['strategy']=='invoice_whole_region'
    assert result['coordinate_transform']['padding_xy']==[0,294]
    assert len(calls)==len(resets)==1 and calls[0]==(736,1860,3)


def test_explicit_invoice_status_allows_typed_match_and_real_difference(monkeypatch):
    engine,_,_=ready(monkeypatch,values=(('1000',.99),('1001',.99)))
    invoice=engine.run_invoice_ocr(source())
    row=engine.run_invoice_ocr(source())
    overrides={'net_amount':[0,0,1,1]}
    left=extract_invoice_fields(invoice['detail'],(620,49),overrides)
    same=extract_row_fields(invoice['detail'],(620,49),overrides=overrides)
    different=extract_row_fields(row['detail'],(620,49),overrides=overrides)
    assert compare_fields(left,same)[0]['status']=='matched'
    assert compare_fields(left,different)[0]['status']=='different'


@pytest.mark.parametrize('shape,expected', [((49,620),'overlapping_tiles'),((50,180),'bounded_small_region')])
def test_generic_ocr_keeps_existing_tile_review_path(monkeypatch,shape,expected):
    engine,_,_=ready(monkeypatch,values=tuple(('1000',.99) for _ in range(20)))
    generic=engine.run_ocr(source(*shape))
    assert generic['status']=='needs_review'
    assert generic['preprocessing']['strategy']==expected


@pytest.mark.parametrize('first,second',[(('1000',.4),('1000',.99)),(('1000',.7),('1001',.99))])
def test_weak_first_and_conflicting_retry_remain_review(monkeypatch,first,second):
    engine,calls,resets=ready(monkeypatch,values=(first,second))
    result=engine.run_invoice_ocr(source())
    assert result['status']=='needs_review' and result['warnings']
    assert len(calls)==2 and len(resets)==1
    assert result['hypotheses']['first_pass'][0]['raw_text']==first[0]
    if first[0]!=second[0]:
        assert result['detail'][0]['raw_text']==first[0]
        assert result['detail'][0]['alternatives'][0]['raw_text']==second[0]
        assert np.allclose(result['detail'][0]['alternatives'][0]['box'],result['detail'][0]['box'])


def test_cancel_and_resource_rejection_do_not_enter_native_recognition(monkeypatch):
    engine,calls,_=ready(monkeypatch)
    cancelled=Event();cancelled.set()
    assert engine.run_invoice_ocr(source(),cancel_event=cancelled)['status']=='cancelled'
    def reject(**kwargs):raise ResourceBudgetExceeded('memory')
    monkeypatch.setattr('src.core.resource_budget.memory_preflight',reject)
    assert engine.run_invoice_ocr(source())['status']=='failed'
    oversized=np.broadcast_to(np.uint8(0),(4001,4000,3))
    assert engine.run_invoice_ocr(oversized)['status']=='failed'
    assert not calls


def test_pathological_long_canvas_is_bounded_and_downscale_is_review(monkeypatch):
    engine,calls,_=ready(monkeypatch)
    result=engine.run_invoice_ocr(source(1,100000))
    assert result['status']=='needs_review'
    assert len(calls)==1
    h,w,_=calls[0]
    assert h*w<=4_000_000 and max(h,w)<=2000


def test_invoice_and_document_share_worker_slot_and_cancel_ack(qtbot):
    entered,release=Event(),Event()
    owners=[]
    class Engine:
        def set_progress_callback(self,callback):pass
        def is_ready(self):return True
        def run_invoice_ocr(self,image,mode,*,cancel_event):
            owners.append(get_ident());entered.set()
            assert release.wait(3)
            return {'text':'1000','status':'done'}
        def run_ocr(self,*args):raise AssertionError('Invoice used generic tiling route')
    worker=OcrWorker(Engine());results=[]
    worker.document_ocr_done.connect(lambda *args:results.append(args))
    cancel=Event()
    try:
        assert worker.queue_invoice_ocr('invoice',0,'row',source(),cancel_event=cancel)
        qtbot.waitUntil(entered.is_set)
        assert not worker.queue_document_ocr('document',0,'field',source())
        assert not worker.queue_invoice_ocr('second',0,'row',source())
        cancel.set();release.set()
        qtbot.waitUntil(lambda:bool(results))
        assert results[0][3]['status']=='cancelled'
        assert not worker._document_pending and owners[0]!=get_ident()
    finally:
        release.set();worker.stop();assert worker.wait(5000)


def test_invoice_small_roi_keeps_real_status_and_padding_transform(monkeypatch):
    engine,calls,_=ready(monkeypatch)
    def recognize(image):
        calls.append(image.shape)
        return [[[[128,323],[428,323],[428,383],[128,383]],'1000',.99]]
    monkeypatch.setattr(engine,'_do_ocr_array',recognize)
    result=engine.run_invoice_ocr(source(50,180))
    assert result['status']=='done' and calls==[(736,736,3)]
    assert np.allclose(result['detail'][0]['box'],[[10,10],[110,10],[110,30],[10,30]])
    assert result['coordinate_transform']['padding_xy']==[98,293]


def test_native_completion_after_cancel_never_exposes_invoice_text(monkeypatch):
    engine,_,_=ready(monkeypatch)
    cancelled=Event()
    def recognize(image):
        cancelled.set()
        return [[[[30,324],[330,324],[330,384],[30,384]],'1000',.99]]
    monkeypatch.setattr(engine,'_do_ocr_array',recognize)
    result=engine.run_invoice_ocr(source(),cancel_event=cancelled)
    assert result['status']=='cancelled' and result['text']=='' and not result['detail']
