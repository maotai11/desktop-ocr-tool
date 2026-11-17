"""Exact visual memoization is bounded, model-bound and lasts one tiled operation."""
from types import SimpleNamespace

import numpy as np
import pytest

from src.ocr.bounded_recognizer import BoundedRecognizer
from src.ocr.engine import OcrEngine

CTX=('v6-small','a'*64,'b'*64)


class Recognizer:
    def __init__(self,text_size=0):
        self.rec_batch_num=6
        self.rec_image_shape=[3,48,320]
        self.session=SimpleNamespace(session=object())
        self.postprocess_op=SimpleNamespace(character=['blank','a',' '])
        self.calls=[]
        self.text_size=text_size
    def __call__(self,crops,*args,**kwargs):
        self.calls.append((list(crops),args,kwargs))
        return [[str(int(crop[0,0,0]))+'x'*self.text_size,.9] for crop in crops],.01


def crop(value=0,h=48,w=40):
    return np.full((h,w,3),value,np.uint8)


def bounded(**kwargs):
    raw=Recognizer(**kwargs)
    return raw,BoundedRecognizer(raw,model_context=CTX)


def test_reuse_keeps_order_each_position_and_returned_mutation_isolated():
    raw,guard=bounded()
    first,second=crop(1),crop(2)
    with guard.exact_raster_operation(CTX):
        initial=guard([first,second],False)
        initial[0][0][0]='changed'
        result=guard([second,first,second],False)
        assert [row[0] for row in result[0]]==['2','1','2']
        assert result[0][0] is not result[0][2]
        result[0][0][0]='mutated'
        assert guard([second])[0][0][0]=='2'
        assert len(raw.calls)==1 and guard.total_width==640 and guard.total_crops==6
        assert not any(isinstance(value,np.ndarray) for pair in guard._operation['entries'].items() for value in pair)
    summary=guard.last_exact_operation
    assert summary['cache_hits']==4 and summary['native_miss_crops']==2
    assert summary['cleared'] and guard._operation is None
    with guard.exact_raster_operation(CTX):
        guard([first])
    assert len(raw.calls)==2


def test_logical_limit_still_counts_cached_positions():
    raw,guard=bounded()
    with guard.exact_raster_operation(CTX):
        for _ in range(512):guard([crop()])
        assert guard.total_crops==512 and guard.total_width==320 and len(raw.calls)==1
        with pytest.raises(ValueError):guard([crop()])
        assert not guard._operation['entries'] and not guard._operation['enabled']


def test_native_unique_width_limit_is_not_raised_by_cache():
    raw,guard=bounded()
    with guard.exact_raster_operation(CTX):
        for value in range(16):guard([crop(value,h=1,w=85)])
        assert guard.total_width==65280
        guard([crop(0,h=1,w=85)])
        assert guard.total_width==65280
        with pytest.raises(ValueError):guard([crop(99)])
        assert len(raw.calls)==16 and not guard._operation['entries']


def test_entry_and_output_bytes_limits_are_hard():
    raw,guard=bounded()
    with guard.exact_raster_operation(CTX):
        for value in range(129):guard([crop(value)])
        assert len(guard._operation['entries'])==128
        guard([crop(128)])
        assert len(raw.calls)==130
    assert guard.last_exact_operation['max_entries']==128
    raw,guard=bounded(text_size=2000)
    with guard.exact_raster_operation(CTX):
        for value in range(50):guard([crop(value)])
        assert guard._operation['output_bytes']<=65536
        assert len(guard._operation['entries'])<50
    assert guard.last_exact_operation['max_output_bytes']<=65536


@pytest.mark.parametrize('mutation',['batch','shape','session','decoder','context'])
def test_changed_model_or_batch_disables_reuse(mutation):
    raw,guard=bounded()
    context=CTX if mutation!='context' else ('v6-medium','c'*64,'d'*64)
    with guard.exact_raster_operation(context):
        guard([crop()])
        if mutation=='batch':raw.rec_batch_num=2
        elif mutation=='shape':raw.rec_image_shape=[3,48,640]
        elif mutation=='session':raw.session.session=object()
        elif mutation=='decoder':raw.postprocess_op.character[1]='b'
        guard([crop()])
        assert len(raw.calls)==2 and not guard._operation['entries']


def test_large_noncontiguous_word_boxes_and_unknown_backend_keep_native_path():
    for image,options in [(crop(h=256,w=344),{}),(crop(w=80)[:,::2],{}),(crop(),{'return_word_box':True})]:
        raw,guard=bounded()
        with guard.exact_raster_operation(CTX):
            guard([image],**options);guard([image],**options)
            assert len(raw.calls)==2 and not guard._operation['entries']
    raw=Recognizer();guard=BoundedRecognizer(raw)
    with guard.exact_raster_operation(CTX):
        guard([crop()]);guard([crop()])
        assert len(raw.calls)==2


def test_reset_and_exception_clear_immediately_preserving_original_error():
    raw,guard=bounded()
    with guard.exact_raster_operation(CTX):
        guard([crop()]);guard.reset_budget()
        assert guard._operation is None
        guard([crop()])
    assert len(raw.calls)==2
    raw,guard=bounded()
    error=RuntimeError('backend original error')
    class Broken(Recognizer):
        def __call__(self,*args,**kwargs):raise error
    broken=Broken();guard=BoundedRecognizer(broken,model_context=CTX)
    with guard.exact_raster_operation(CTX):
        with pytest.raises(RuntimeError) as caught:guard([crop()])
        assert caught.value is error and not guard._operation['entries']


def test_unknown_output_is_not_cached_or_retried():
    class Unknown(Recognizer):
        def __call__(self,*args,**kwargs):
            self.calls.append(True)
            return {'unknown':'unchanged'}
    raw=Unknown();guard=BoundedRecognizer(raw,model_context=CTX)
    with guard.exact_raster_operation(CTX):
        assert guard([crop()])=={'unknown':'unchanged'}
        assert guard([crop()])=={'unknown':'unchanged'}
        assert len(raw.calls)==2 and not guard._operation['entries']


def test_engine_only_opens_verified_multitile_operation(monkeypatch):
    from contextlib import contextmanager
    contexts=[]
    class Guard:
        def reset_budget(self):pass
        @contextmanager
        def exact_raster_operation(self,context):
            contexts.append(context)
            yield
    engine=OcrEngine();engine._ready=True;engine._engine=SimpleNamespace(text_rec=Guard())
    engine._recognizer_model_context=CTX;engine.model_identity={'profile':engine.model_profile}
    monkeypatch.setattr(engine,'_run_single_ocr',lambda *args,**kwargs:{'status':'done'})
    monkeypatch.setattr(engine,'_run_tiles',lambda *args,**kwargs:{'status':'needs_review'})
    monkeypatch.setattr(engine,'_run_invoice_region',lambda *args,**kwargs:{'status':'done'})
    image=crop(h=200,w=200)
    for tiles in ([],[{}],[{},{}]):
        monkeypatch.setattr('src.ocr.engine.plan_ocr_tiles',lambda *args,tiles=tiles:tiles)
        engine.run_ocr(image)
    engine.run_invoice_ocr(image)
    assert contexts==[CTX]
    engine.model_profile='v6-medium'
    engine.run_ocr(image)
    assert contexts==[CTX]


def test_error_clears_cache_but_never_refunds_consumed_native_budget():
    raw,guard=bounded()
    with guard.exact_raster_operation(CTX):
        for value in range(16):guard([crop(value,h=1,w=85)])
        with pytest.raises(ValueError):guard([crop(99)])
        assert guard.total_width==65280 and guard.total_crops==16
        with pytest.raises(ValueError):guard([crop(0,h=1,w=85)])
        assert len(raw.calls)==16 and guard.total_width==65280


def test_empty_and_contradicting_backend_results_never_become_cached_guesses():
    class Contradicting(Recognizer):
        def __call__(self,crops,*args,**kwargs):
            self.calls.append(True)
            if not crops:raise RuntimeError('original empty error')
            return [(str(index),.9) for index in range(len(crops))],.01
    raw=Contradicting();guard=BoundedRecognizer(raw,model_context=CTX)
    with guard.exact_raster_operation(CTX):
        result=guard([crop(),crop()])
        assert result[0]==[('0',.9),('1',.9)]
        assert not guard._operation['entries'] and not guard._operation['enabled']
    with guard.exact_raster_operation(CTX):
        with pytest.raises(RuntimeError,match='original empty error'):guard([])
        assert not guard._operation['entries']
