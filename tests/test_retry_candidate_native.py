"""Actual pinned models keep full-pass inference and literal fixture results.

A strict review threshold exercises the existing second whole-image pass.
These synthetic cases are not a handwriting accuracy or resource benchmark.
"""
import gc
import hashlib
import json
from pathlib import Path

import pytest

from src.ocr.model_validator import model_root


@pytest.fixture(scope='module')
def native_engine():
    root=model_root();lock=json.loads((root/'models/models.lock.json').read_text(encoding='utf-8'))
    if any(not (root/entry['path']).is_file() for entry in lock.values()):
        pytest.skip('Pinned offline OCR assets are not installed in this environment')
    from src.ocr.engine import OcrEngine
    engine=OcrEngine(confidence_accept=1.,enable_second_pass=True,max_ocr_passes=2)
    engine.load()
    yield engine
    engine._engine=None;gc.collect()


@pytest.mark.parametrize('name',['traditional','amount','date_api'])
def test_real_full_passes_keep_text_and_reuse_the_verified_session(native_engine,monkeypatch,name):
    import cv2
    import numpy as np
    root=model_root()/'models/validation'
    case=next(c for c in json.loads((root/'fixtures.json').read_text(encoding='utf-8'))['cases'] if c['id']==name)
    data=(root/case['file']).read_bytes();assert hashlib.sha256(data).hexdigest()==case['image_sha256']
    recognizer=native_engine._engine.text_rec;session=recognizer.recognizer.session.session
    original=native_engine._do_ocr_array;calls=[]
    def infer(image):
        calls.append(image.shape)
        return original(image)
    monkeypatch.setattr(native_engine,'_do_ocr_array',infer)
    result=native_engine.run_ocr(cv2.imdecode(np.frombuffer(data,np.uint8),cv2.IMREAD_COLOR))
    assert result['text']==case['ground_truth'] and result['status']=='needs_review'
    assert len(calls)==2
    hs=result['hypotheses'];parts=[hs,*[t['hypotheses'] for t in hs.get('tiles',[])]]
    meta=next(p['pass_metadata']['second_pass'] for p in parts if 'second_pass' in p.get('pass_metadata',{}))
    assert meta['variant']=='clahe_denoise' and 'recognizer_only' not in meta
    assert native_engine._engine.text_rec is recognizer and recognizer.recognizer.session.session is session
    assert session.get_providers()==['CPUExecutionProvider']


@pytest.mark.parametrize('ansi_encoding',['cp1252','cp950'])
def test_native_fixture_reading_survives_windows_ansi_defaults(native_engine,monkeypatch,ansi_encoding):
    """Run the actual fixture path under Windows defaults, with no fake OCR."""
    fixture=model_root()/'models/validation/fixtures.json'
    with pytest.raises(UnicodeDecodeError):
        fixture.read_bytes().decode(ansi_encoding)
    original_read=Path.read_text
    def ansi_read(path,encoding=None,errors=None):
        return original_read(path,encoding=encoding or ansi_encoding,errors=errors)
    monkeypatch.setattr(Path,'read_text',ansi_read)
    test_real_full_passes_keep_text_and_reuse_the_verified_session(native_engine,monkeypatch,'traditional')
