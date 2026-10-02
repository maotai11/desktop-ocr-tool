"""Optional provider CONTRACT tests. No claim of native Paddle integration."""
import numpy as np
import pytest
from unittest.mock import MagicMock
from src.ocr.providers import create_provider, list_known_providers
from src.ocr.providers.paddleocr_v5_provider import PaddleOCRv5Provider
from src.ocr.secondary_engine import NullSecondaryEngine


def page(text='臺灣', score=.9):
    return dict(rec_polys=[[[0,0],[40,0],[40,20],[0,20]]], rec_texts=[text], rec_scores=[score])


def test_factory_canonical_name():
    assert isinstance(create_provider('paddleocr_v5'), PaddleOCRv5Provider)
    assert isinstance(create_provider('paddleocr_v5_mobile'), NullSecondaryEngine)
    assert isinstance(create_provider('unknown'), NullSecondaryEngine)
    assert 'paddleocr_v5' in list_known_providers()


@pytest.mark.parametrize('score,status',[(.95,'done'),(.6,'needs_review')])
def test_mapping_confidence(score,status):
    result=PaddleOCRv5Provider()._map_result([page(score=score)],20)
    assert result['text']=='臺灣' and result['status']==status
    assert result['elapsed_ms']==20 and result['engine']=='paddleocr_v5'


def test_filtered_rec_polys_aligns_not_dt_polys():
    raw=page();raw['dt_polys']=np.zeros((2,4,2))
    result=PaddleOCRv5Provider()._map_result([raw],0)
    assert result['detail'][0]['box']==raw['rec_polys'][0]


@pytest.mark.parametrize('raw',[None,[],[None],[dict(rec_polys=[],rec_texts=[],rec_scores=[])]])
def test_empty_results(raw):
    result=PaddleOCRv5Provider()._map_result(raw,0)
    assert result['text']=='' and result['status']=='done'


@pytest.mark.parametrize('raw',[[[]],[object()],[dict(rec_polys=[],rec_texts=['lost'],rec_scores=[.9])]])
def test_unknown_and_misaligned_contract_is_not_silent_empty(raw):
    with pytest.raises(ValueError): PaddleOCRv5Provider()._map_result(raw,0)


def test_numpy_bgr_input_and_predict_contract(monkeypatch):
    monkeypatch.setattr('src.ocr.providers.paddleocr_v5_provider._PADDLEOCR_AVAILABLE',True)
    provider=PaddleOCRv5Provider(); model=MagicMock(); provider._engine=model
    model.predict.return_value=iter([page()])
    image=np.zeros((30,40,3),np.uint8)
    result=provider.recognize(image)
    assert model.predict.call_args.kwargs['input'] is image
    assert result['text']=='臺灣'
