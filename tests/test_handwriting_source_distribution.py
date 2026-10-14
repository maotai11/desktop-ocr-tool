"""Real pinned source/license bytes, independent of small publication fixtures."""
import copy
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

from scripts.build_handwriting_helper import source_identity
from scripts.verify_candidate_bundle import verify_handwriting_source_distribution

ROOT = Path(__file__).resolve().parents[1]
PREFIX = 'release-v1.8.0-rc.1/'
BASE = PREFIX + 'handwriting-source/'


@pytest.fixture(scope='module')
def real_source_bytes():
    native = ROOT / 'native/handwriting'
    identity = json.loads((ROOT / 'models/handwriting/manifest.json').read_text())
    identity['helper'] = {'source': source_identity(ROOT)}
    paths = {'MODEL_MANIFEST.json': ROOT / 'models/handwriting/manifest.json',
             'tegaki-zh_TW/handwriting-zh_TW.xml': ROOT / 'models/handwriting/source/handwriting-zh_TW.xml',
             'tegaki-zh_TW/COPYING-LGPL-2.1.txt': ROOT / 'models/handwriting/source/COPYING-LGPL-2.1.txt',
             'zinnia/UPSTREAM_MANIFEST.json': native / 'UPSTREAM_MANIFEST.json',
             'zinnia/patches/0001-bound-vertex-tree.patch': native / 'patches/0001-bound-vertex-tree.patch'}
    paths.update({'zinnia/' + name: native / name for name in identity['helper']['source']['files']})
    data = {BASE + name: path.read_bytes() for name, path in paths.items()}
    files = {path.relative_to(ROOT).as_posix(): {'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
             for path in paths.values()}
    return identity, data, files


def test_same_release_archive_contains_exact_pinned_model_source_and_licenses(real_source_bytes):
    identity, data, files = real_source_bytes
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw in data.items(): archive.writestr(name, raw)
    with zipfile.ZipFile(io.BytesIO(buffer.getvalue())) as archive:
        report = verify_handwriting_source_distribution(archive, PREFIX, identity, files)
    assert report['verified'] is True
    assert report['model_source_sha256'] == identity['source_sha256']
    assert report['model_license_sha256'] == identity['license_sha256']


@pytest.mark.parametrize('defect', ['missing_xml', 'wrong_xml', 'wrong_lgpl', 'wrong_bsd', 'wrong_patch',
                                   'wrong_helper_source', 'wrong_model_manifest', 'wrong_committed_hash'])
def test_source_distribution_rejects_missing_changed_or_unbound_bytes(real_source_bytes, defect):
    identity, original, original_files = real_source_bytes
    identity, data, files = copy.deepcopy(identity), dict(original), copy.deepcopy(original_files)
    targets = {'wrong_xml': 'tegaki-zh_TW/handwriting-zh_TW.xml',
               'wrong_lgpl': 'tegaki-zh_TW/COPYING-LGPL-2.1.txt',
               'wrong_bsd': 'zinnia/vendor/zinnia/COPYING',
               'wrong_patch': 'zinnia/patches/0001-bound-vertex-tree.patch',
               'wrong_helper_source': 'zinnia/helper.cpp', 'wrong_model_manifest': 'MODEL_MANIFEST.json'}
    if defect == 'missing_xml': del data[BASE + 'tegaki-zh_TW/handwriting-zh_TW.xml']
    elif defect == 'wrong_committed_hash': files['native/handwriting/helper.cpp'] = {'sha256': '0' * 64}
    else: data[BASE + targets[defect]] = b'changed-source-fixture'
    class Reader:
        def getinfo(self, name): return SimpleNamespace(file_size=len(data[name]))
        def read(self, name): return data[name]
        def open(self, name): return io.BytesIO(data[name])
    with pytest.raises((ValueError, KeyError)):
        verify_handwriting_source_distribution(Reader(), PREFIX, identity, files)
