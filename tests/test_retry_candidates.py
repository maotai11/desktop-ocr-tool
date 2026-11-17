"""Visual alternatives add review evidence without stealing detector passes."""
from copy import deepcopy
from types import SimpleNamespace

import numpy as np
import pytest

from src.ocr.engine import OcrEngine
from src.ocr.retry_candidates import collect_retry_candidates, validated_alternatives


def raw(text='100.00', confidence=.7, x=20, y=20, w=110, h=24):
    return [[[x,y],[x+w,y],[x+w,y+h],[x,y+h]],text,confidence]


def snapshot(*rows):
    return OcrEngine._snapshot_results(list(rows))


def image(h=160,w=200):
    return np.broadcast_to(np.arange(w,dtype=np.uint8)[None,:,None],(h,w,3)).copy()


def ready(monkeypatch, outputs, short_side=160, limit=2):
    engine=OcrEngine(max_image_short_side=short_side,max_ocr_passes=limit)
    engine._ready=True;engine._model_version='test-model'
    calls=[];outputs=iter(outputs)
    def forbidden(*_):
        raise AssertionError('An additional recognizer-only pass was started')
    engine._engine=SimpleNamespace(text_rec=forbidden)
    def infer(array):
        calls.append(array.shape)
        return deepcopy(next(outputs))
    monkeypatch.setattr(engine,'_do_ocr_array',infer)
    return engine,calls


def test_differing_visual_pass_is_explicit_evidence_not_auto_correction(monkeypatch):
    first=[raw(),raw('ABC',.99,y=70)]
    second=[raw('700.00',.999),raw('ABC',.99,y=70)]
    engine,calls=ready(monkeypatch,[first,second])
    result=engine.run_ocr(image())
    assert len(calls)==2 and result['text']=='100.00\n\nABC'
    assert result['status']=='needs_review'
    detail=result['detail'][0]
    assert detail['text']==detail['raw_text']=='100.00' and detail['confidence']==.7
    assert detail['alternatives']==[{'text':'700.00','raw_text':'700.00','confidence':.999,
        'source':'visual_retry','pass_name':'second_pass','box':second[0][0]}]
    assert 'alternatives' not in result['detail'][1]
    assert first[0][1]=='100.00' and 'recognizer_only' not in result['hypotheses']['pass_metadata']['second_pass']


def test_full_second_detector_can_still_recover_an_unseen_disjoint_line(monkeypatch):
    first=[raw('first',.7)]
    second=[raw('first',.99),raw('recovered second line',.99,y=85)]
    source=image();source[90:96,30:120]=254
    engine,calls=ready(monkeypatch,[first,second])
    result=engine.run_ocr(source)
    assert len(calls)==2 and 'recovered second line' in result['text']
    assert len(result['hypotheses']['second_pass'])==2


def test_two_baselines_inside_one_box_and_a_grid_bridge_keep_whole_retry(monkeypatch):
    source=np.full((160,200,3),255,np.uint8)
    source[30:36,30:120]=0;source[45:51,30:120]=0;source[30:51,130]=0
    first=[raw('first',.7,x=20,y=20,w=120,h=45)]
    second=[raw('first',.95,x=20,y=27,w=120,h=10),raw('second',.95,x=20,y=42,w=120,h=12)]
    engine,calls=ready(monkeypatch,[first,second])
    result=engine.run_ocr(source)
    assert len(calls)==2
    assert [r['raw_text'] for r in result['hypotheses']['second_pass']]==['first','second']
    assert 'alternatives' not in result['detail'][0]


def test_candidate_metadata_failure_cannot_block_existing_recovery(monkeypatch):
    engine,calls=ready(monkeypatch,[[raw('first')],[raw('first',.99),raw('recovered',.99,y=85)]])
    monkeypatch.setattr('src.ocr.engine.collect_retry_candidates',
        lambda *_:(_ for _ in ()).throw(ValueError('synthetic review metadata failure')))
    result=engine.run_ocr(image())
    assert len(calls)==2 and 'recovered' in result['text']
    assert result['hypotheses']['pass_metadata']['second_pass']['outcome']=='completed'


@pytest.mark.parametrize('limit',[1,2,3])
def test_original_pass_limit_is_unchanged_and_no_extra_recognizer_call(monkeypatch,limit):
    engine,calls=ready(monkeypatch,[[raw()],[raw('700',.99)],[raw('800',.99)]],limit=limit)
    result=engine.run_ocr(image())
    assert len(calls)==limit and result['text']=='100.00'


def test_candidate_own_geometry_maps_to_original_coordinates(monkeypatch):
    engine,_=ready(monkeypatch,[[raw(x=40,y=40,w=200,h=40)],
        [raw('700.00',.99,x=42,y=40,w=200,h=40)]],short_side=320)
    result=engine.run_ocr(image())
    detail=result['detail'][0]
    assert detail['box']==[[20.,20.],[120.,20.],[120.,40.],[20.,40.]]
    assert detail['alternatives'][0]['box']==[[21.,20.],[121.,20.],[121.,40.],[21.,40.]]


def test_small_region_tile_candidates_keep_source_coordinates(monkeypatch):
    from src.ocr.preprocessor import plan_ocr_tiles
    source=image(20,100);tile=plan_ocr_tiles(source.shape,160)[0];px,py=tile['padding_xy']
    engine,calls=ready(monkeypatch,[[raw(x=px+15,y=py+6,w=120,h=30)],
        [raw('700.00',.99,x=px+18,y=py+6,w=120,h=30)]])
    result=engine.run_ocr(source);detail=result['detail'][0]
    assert len(calls)==2 and detail['box']==[[5.,2.],[45.,2.],[45.,12.],[5.,12.]]
    assert detail['alternatives'][0]['box']==[[6.,2.],[46.,2.],[46.,12.],[6.,12.]]


@pytest.mark.parametrize('candidate',[
    [raw('partial',.99,w=40)],
    [raw('first fragment',.99,w=60),raw('second fragment',.99,x=80,w=50)],
    [raw('expanded',.99,w=200)],
])
def test_partial_split_or_expanded_readings_are_not_line_alternatives(candidate):
    assert collect_retry_candidates(snapshot(raw()),snapshot(*candidate),'second_pass')==[]


def test_merged_reading_cannot_be_offered_to_two_separate_fields():
    first=snapshot(raw('100',x=0,w=50),raw('200',x=60,w=50))
    assert collect_retry_candidates(first,snapshot(raw('100200',.99,x=0,w=110)),'second_pass')==[]


def test_equal_text_at_separate_positions_is_not_globally_replaced():
    evidence=collect_retry_candidates(snapshot(raw('100'),raw('100',y=80)),
        snapshot(raw('700',.99),raw('100',.99,y=80)),'second_pass')
    assert len(evidence)==1 and evidence[0]['box']==raw()[0]


@pytest.mark.parametrize('bad',[None,{},[{'raw_text':'x','box':None,'confidence':.9}],
    [{'raw_text':'x','box':[[float('nan'),0]]*4,'confidence':.9}],
    [{'raw_text':'x','box':[[0,0]]*4,'confidence':.9}]])
def test_malformed_candidate_evidence_cannot_create_suggestions(bad):
    assert collect_retry_candidates(snapshot(raw()),bad,'second_pass')==[]


@pytest.mark.parametrize('bad',[None, {}, ['invalid'], [{'alternatives':None}],
    [{'original_text':'100.00','box':[[1],[2,3]],'alternatives':[{}]}],
    [{'original_text':'100.00','box':raw()[0],'alternatives':[None]}]])
def test_malformed_attached_evidence_cannot_fail_valid_ocr(monkeypatch,bad):
    engine,calls=ready(monkeypatch,[[raw()],[raw('700.00',.99)]])
    monkeypatch.setattr('src.ocr.engine.collect_retry_candidates',lambda *_:bad)
    result=engine.run_ocr(image())
    assert len(calls)==2 and result['status']=='needs_review' and result['text']=='100.00'
    assert 'alternatives' not in result['detail'][0]


def test_malformed_nested_tile_candidates_cannot_fail_valid_ocr(monkeypatch):
    source=image(20,100)
    from src.ocr.preprocessor import plan_ocr_tiles
    tile=plan_ocr_tiles(source.shape,160)[0];px,py=tile['padding_xy']
    engine,_=ready(monkeypatch,[])
    row={'text':'100.00','raw_text':'100.00','confidence':.99,
         'box':raw(x=px+15,y=py+6,w=120,h=30)[0],'alternatives':[None,{'box':'malformed'}]}
    monkeypatch.setattr(engine,'_run_single_ocr',lambda *_args,**_kwargs:
        {'text':'100.00','detail':[row],'status':'done','engine':'test','model_version':'test'})
    result=engine.run_ocr(source)
    assert result['status']=='needs_review' and result['text']=='100.00'
    assert 'alternatives' not in result['detail'][0]


def test_original_coordinates_drop_bad_metadata_without_losing_primary_or_valid_candidate():
    good={'raw_text':'700','confidence':.99,'box':raw(x=42,y=40,w=200,h=40)[0],
          'source':'visual_retry','pass_name':'second_pass'}
    result={'text':'100','detail':[{'text':'100','box':raw(x=40,y=40,w=200,h=40)[0],
            'alternatives':[None,dict(good,box=[[float('nan'),0]]*4),good]}]}
    before=deepcopy(result)
    mapped=OcrEngine._original_coordinates(result,(160,200),(320,400))
    assert mapped['text']=='100' and mapped['detail'][0]['box'][0]==[20.,20.]
    assert len(mapped['detail'][0]['alternatives'])==1
    assert mapped['detail'][0]['alternatives'][0]['box'][0]==[21.,20.]
    assert result['detail'][0]['box']==before['detail'][0]['box']


@pytest.mark.parametrize('changes',[{'source':'unknown'}, {'pass_name':'idle'},
    {'confidence':True}, {'confidence':float('inf')}, {'raw_text':7}, {'raw_text':'x'*2049},
    {'box':[[True,0],[1,0],[1,1],[0,1]]}, {'box':[['0',0],[1,0],[1,1],[0,1]]}])
def test_candidate_validation_rejects_invalid_provenance_and_values(changes):
    good={'raw_text':'700','confidence':.99,'box':raw()[0],
          'source':'visual_retry','pass_name':'second_pass'}
    assert validated_alternatives([good|changes])==[]
