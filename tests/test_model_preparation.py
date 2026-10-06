import hashlib
import io
import json

import pytest

from scripts.prepare_models import OFFICIAL_PREFIX, prepare


def fixture(root, data=b'official-model'):
    (root / 'models').mkdir()
    info = {'path': 'models/rec/v6.onnx', 'size_bytes': len(data),
            'sha256': hashlib.sha256(data).hexdigest(), 'source': OFFICIAL_PREFIX+'PP-OCRv6/rec/model.onnx'}
    path = root / 'models/models.lock.json'
    path.write_text(json.dumps({'rec': info}))
    return path, info


def test_verified_download_is_atomic_and_no_repeat_network(tmp_path, monkeypatch):
    fixture(tmp_path)
    calls = []
    monkeypatch.setattr('urllib.request.urlopen', lambda url, timeout: calls.append(url) or io.BytesIO(b'official-model'))
    prepare(tmp_path); prepare(tmp_path)
    assert len(calls) == 1
    assert (tmp_path / 'models/rec/v6.onnx').read_bytes() == b'official-model'
    assert not list((tmp_path / 'models/rec').glob('.model-*'))


@pytest.mark.parametrize('payload', [b'changed-model!', b'short', b'way-too-long-model-file'])
def test_wrong_download_never_becomes_asset(tmp_path, monkeypatch, payload):
    fixture(tmp_path)
    monkeypatch.setattr('urllib.request.urlopen', lambda *a, **kw: io.BytesIO(payload))
    with pytest.raises(ValueError): prepare(tmp_path)
    assert not (tmp_path / 'models/rec/v6.onnx').exists()
    assert not list((tmp_path / 'models/rec').glob('.model-*'))


def test_existing_tampered_asset_is_not_silently_overwritten(tmp_path, monkeypatch):
    fixture(tmp_path); p = tmp_path / 'models/rec/v6.onnx'; p.parent.mkdir(); p.write_bytes(b'changed')
    monkeypatch.setattr('urllib.request.urlopen', lambda *a, **kw: pytest.fail('network accessed'))
    with pytest.raises(ValueError, match='Existing'): prepare(tmp_path)
    assert p.read_bytes() == b'changed'


@pytest.mark.parametrize('field,value', [('path','../outside.onnx'),('source','https://untrusted.example/model')])
def test_model_escape_or_untrusted_source_rejected(tmp_path, monkeypatch, field, value):
    path, info = fixture(tmp_path); info[field] = value; path.write_text(json.dumps({'rec': info}))
    monkeypatch.setattr('urllib.request.urlopen', lambda *a, **kw: pytest.fail('network accessed'))
    with pytest.raises(ValueError): prepare(tmp_path)
