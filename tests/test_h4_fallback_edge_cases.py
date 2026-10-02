"""Explicit offline load failures, genuine predict failure and routing provenance."""
import numpy as np
from unittest.mock import MagicMock
from src.ocr.providers.paddleocr_v5_provider import PaddleOCRv5Provider
from src.ocr.engine import OcrEngine
from src.ocr.secondary_engine import SecondaryEngineBase


def test_missing_package_returns_failed(monkeypatch):
    monkeypatch.setattr('src.ocr.providers.paddleocr_v5_provider._PADDLEOCR_AVAILABLE',False)
    assert PaddleOCRv5Provider().recognize(np.zeros((20,20,3),np.uint8))['status']=='failed'


def test_no_models_cannot_implicitly_download(monkeypatch):
    monkeypatch.setattr('src.ocr.providers.paddleocr_v5_provider._PADDLEOCR_AVAILABLE',True)
    constructor=MagicMock()
    monkeypatch.setattr('src.ocr.providers.paddleocr_v5_provider.PaddleOCR',constructor)
    provider=PaddleOCRv5Provider()
    assert provider._ensure_loaded() is False
    assert 'Offline' in provider._load_error
    constructor.assert_not_called()
    assert provider.is_available() is False


def test_constructor_failure_records_error(monkeypatch,tmp_path):
    monkeypatch.setattr('src.ocr.providers.paddleocr_v5_provider._PADDLEOCR_AVAILABLE',True)
    constructor=MagicMock(side_effect=OSError('model damaged'))
    monkeypatch.setattr('src.ocr.providers.paddleocr_v5_provider.PaddleOCR',constructor)
    provider=PaddleOCRv5Provider(text_detection_model_dir=str(tmp_path),text_recognition_model_dir=str(tmp_path))
    assert provider._ensure_loaded() is False and 'damaged' in provider._load_error
    assert constructor.call_args.kwargs['device']=='cpu'


def test_real_predict_error_branch(monkeypatch):
    monkeypatch.setattr('src.ocr.providers.paddleocr_v5_provider._PADDLEOCR_AVAILABLE',True)
    p=PaddleOCRv5Provider(); p._engine=MagicMock()
    p._engine.predict.side_effect=ValueError('invalid input')
    result=p.recognize(np.zeros((20,20,3),np.uint8))
    assert result['status']=='failed' and 'invalid input' in result['error']


def test_fallback_on_primary_exception_records_actual_provider(monkeypatch):
    class Provider(SecondaryEngineBase):
        name='fixture_secondary'
        def is_available(self): return True
        def recognize(self,image,mode='screen'):
            return dict(text='臺灣',confidence=.95,status='done',detail=[],elapsed_ms=1,model_version='sha256:real')
    engine=OcrEngine(enable_second_pass=False);engine._ready=True
    engine._engine=MagicMock(side_effect=RuntimeError('primary crashed'))
    engine.set_secondary_engine(Provider());engine.configure(enable_secondary_engine=True)
    result=engine.run_ocr(np.zeros((960,960,3),np.uint8))
    assert result['text']=='臺灣' and result['engine']=='fixture_secondary'
    assert result['model_version']=='sha256:real'


def test_failed_candidate_never_overwrites_good_result():
    assert not OcrEngine._is_better(dict(text='bad',confidence=1,status='failed'),dict(text='good',confidence=.5,status='done'))
