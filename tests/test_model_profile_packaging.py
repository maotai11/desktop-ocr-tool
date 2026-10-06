"""Reject incomplete offline profile payloads and profile probe evidence."""
import copy
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from model_profile_fixtures import fixture_profiles, fixture_validation, profile_report

from scripts import build
from scripts.verify_candidate_bundle import (
    check_model_profiles,
    check_profile_manifests,
    inspect_frozen_payload,
)


def bundle_manifest(profiles):
    return {'models': profiles['v6-small'], 'model_profiles': profiles,
            'default_model_profile': 'v6-small', 'ocr_validation_fixtures': fixture_validation()[0]}


def corrupt_profile(report, defect):
    secondary = report['model_profiles']['v6-medium']
    if defect == 'missing_profile': del report['model_profiles']['v6-medium']
    elif defect == 'failed_profile': secondary['passed'] = False
    elif defect == 'missing_role': del secondary['models']['cls']
    elif defect == 'model_hash': secondary['models']['rec']['sha256'] = '0' * 64
    elif defect == 'model_path': secondary['models']['rec']['path'] = 'models/wrong.onnx'
    elif defect == 'model_size': secondary['models']['rec']['size_bytes'] += 1
    elif defect == 'model_version': secondary['models']['rec']['version'] = 'unverified'
    elif defect == 'ocr_text': secondary['ocr_result']['text'] = 'wrong result'
    elif defect == 'ocr_status': secondary['ocr_result']['status'] = 'error'
    elif defect == 'ocr_engine': secondary['ocr_result']['engine'] = 'other-engine'
    elif defect == 'provenance': secondary['ocr_result']['model_version'] = 'unknown'
    elif defect == 'swapped_profile': secondary['model_identity']['profile'] = 'v6-small'
    elif defect == 'runtime_version': secondary['model_identity']['runtime_version'] = 'unverified'
    elif defect == 'recognition_batch': secondary['model_identity']['recognition_batch'] = 6
    elif defect == 'recognition_width': secondary['model_identity']['max_recognition_width'] = 99999
    elif defect == 'decoder_hash': secondary['model_identity']['recognition']['decoder_sha256'] = '0' * 64
    elif defect == 'provider': secondary['model_identity']['recognition']['providers'] = ['CUDAExecutionProvider']
    elif defect == 'space_index': secondary['model_identity']['recognition']['space_index'] = 0
    elif defect == 'blank_index': secondary['model_identity']['recognition']['blank_index'] = 1
    elif defect == 'input_type': secondary['model_identity']['recognition']['input_type'] = 'tensor(float16)'
    elif defect == 'input_shape': secondary['model_identity']['recognition']['input_shape'][1] = 4
    elif defect == 'output_shape': secondary['model_identity']['recognition']['output_shape'][-1] = 9
    elif defect == 'missing_identity': del secondary['model_identity']
    elif defect == 'missing_fixture': secondary['fixtures'].pop()
    elif defect == 'fixture_hash': secondary['fixtures'][0]['image_sha256'] = '0' * 64
    elif defect == 'literal_fixture': secondary['fixtures'][1]['result']['text'] = 'NT$1,234.00'
    elif defect == 'fixture_provenance': secondary['fixtures'][0]['result']['model_version'] = 'unknown'
    elif defect == 'default_result': report['ocr_result']['text'] = 'wrong default'
    elif defect == 'v6_dynamic_height': report['model_profiles']['v6-small']['model_identity']['recognition']['input_shape'][2] = '?'
    else: raise AssertionError(defect)


PROFILE_DEFECTS = ['missing_profile', 'failed_profile', 'missing_role', 'model_hash', 'model_path',
                   'model_size', 'model_version', 'ocr_text', 'ocr_status', 'ocr_engine',
                   'provenance', 'swapped_profile', 'runtime_version', 'recognition_batch',
                   'recognition_width', 'decoder_hash', 'provider', 'space_index', 'blank_index',
                   'input_type', 'input_shape', 'output_shape', 'missing_identity',
                   'default_result', 'v6_dynamic_height', 'missing_fixture', 'fixture_hash',
                   'literal_fixture', 'fixture_provenance']


@pytest.mark.parametrize('defect', PROFILE_DEFECTS)
def test_profile_gate_rejects_secondary_and_identity_regressions(defect):
    profiles, _ = fixture_profiles()
    report = profile_report(profiles)
    corrupt_profile(report, defect)
    with pytest.raises(ValueError):
        check_model_profiles(report, bundle_manifest(profiles))


def test_profile_gate_ignores_only_machine_specific_absolute_paths():
    profiles, _ = fixture_profiles()
    report = profile_report(profiles)
    for result in report['model_profiles'].values():
        for info in result['models'].values(): info['absolute_path'] = 'C:/different/extraction/' + info['path']
    for info in report['models'].values(): info['absolute_path'] = '/source/' + info['path']
    check_model_profiles(report, bundle_manifest(profiles))
    report['model_profiles']['v6-medium']['models']['rec']['unrecorded_metadata'] = True
    with pytest.raises(ValueError):
        check_model_profiles(report, bundle_manifest(profiles))


@pytest.mark.parametrize('defect', ['missing_profile', 'unknown_profile', 'missing_role', 'path_escape',
                                  'missing_size', 'missing_decoder', 'wrong_classes'])
def test_profile_manifest_contract_requires_complete_safe_offline_assets(defect):
    profiles, _ = fixture_profiles()
    if defect == 'missing_profile': del profiles['v6-medium']
    elif defect == 'unknown_profile': profiles['automatic-download'] = profiles['v6-medium']
    elif defect == 'missing_role': del profiles['v6-medium']['cls']
    elif defect == 'path_escape': profiles['v6-medium']['rec']['path'] = 'models/../../outside.onnx'
    elif defect == 'missing_size': del profiles['v6-medium']['rec']['size_bytes']
    elif defect == 'missing_decoder': del profiles['v6-medium']['rec']['decoder_sha256']
    elif defect == 'wrong_classes': profiles['v6-medium']['rec']['output_classes'] += 1
    with pytest.raises(ValueError):
        check_profile_manifests(profiles)


@pytest.mark.parametrize('defect', ['none', 'missing_secondary', 'tampered_secondary', 'missing_lock'])
def test_build_preflight_verifies_profile_assets_before_packaging(tmp_path, defect):
    profiles, assets = fixture_profiles()
    for name, data in assets.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    if defect == 'missing_secondary': (tmp_path / 'models/rec-v6-medium.onnx').unlink()
    if defect == 'tampered_secondary': (tmp_path / 'models/rec-v6-medium.onnx').write_bytes(b'corrupt')
    if defect == 'missing_lock': (tmp_path / 'models/models-v6-medium.lock.json').unlink()
    if defect == 'none':
        build.verify_models(tmp_path)
        assert build.bundled_model_profiles(tmp_path) == profiles
    else:
        with pytest.raises((OSError, ValueError)):
            build.verify_models(tmp_path)


@pytest.mark.parametrize('defect', ['missing_profile', 'model_hash', 'decoder_hash', 'provenance'])
def test_build_candidate_gate_requires_profile_probe_evidence(tmp_path, monkeypatch, defect):
    profiles, _ = fixture_profiles()
    monkeypatch.setattr(build, 'bundled_model_profiles', lambda _: copy.deepcopy(profiles))
    monkeypatch.setattr(build, 'bundled_validation_fixtures', lambda _: fixture_validation()[0])
    paths = build.resolve_release_paths(tmp_path)
    build.reset_output_dirs(paths)
    paths['out_exe'].write_bytes(b'fake-executable')
    report = dict(profile_report(profiles), schema=2, probe='ocr_database',
                  passed=True, frozen=True, platform='win32', qt_platform='windows',
                  version=build.APP_VERSION, executable_sha256=build.sha256_file(paths['out_exe']),
                  clean_machine_verified=False, telemetry_env='1', database_integrity='ok',
                  checks={'qt': True, 'models': True, 'ocr': True, 'database': True})
    corrupt_profile(report, defect)
    monkeypatch.setattr(build, '_run_probe', lambda *args: report)
    with pytest.raises(RuntimeError, match='model-profile gate'):
        build.validate_candidate(paths)


@pytest.mark.parametrize('source_hash', ['correct', 'missing', 'wrong'])
def test_frozen_profile_locks_are_bound_to_source_manifest(monkeypatch, source_hash):
    from PyInstaller.archive import readers
    profiles, data = fixture_profiles()
    data.update({'python312.dll': b'python', 'qwindows.dll': b'qt',
                 'onnxruntime_pybind11_state.pyd': b'ort'})
    source_files = {name: {'sha256': hashlib.sha256(raw).hexdigest()} for name, raw in data.items()}
    if source_hash == 'missing': del source_files['models/models-v6-medium.lock.json']
    if source_hash == 'wrong': source_files['models/models-v6-medium.lock.json']['sha256'] = '0' * 64

    class Reader:
        def __init__(self, _): self.toc = dict.fromkeys(data)
        def extract(self, name): return data[name]

    monkeypatch.setattr(readers, 'CArchiveReader', Reader)
    if source_hash == 'correct':
        assert inspect_frozen_payload('unused.exe', profiles, source_files, fixture_validation()[0])['inspected'] is True
    else:
        with pytest.raises(ValueError, match='source hash mismatch'):
            inspect_frozen_payload('unused.exe', profiles, source_files, fixture_validation()[0])


@pytest.mark.parametrize('defect', ['none', 'missing_profile', 'model_hash', 'decoder_hash',
                                  'input_shape', 'provenance', 'provider', 'v6_dynamic_height',
                                  'missing_fixture', 'literal_fixture'])
def test_windows_profile_validator_helpers(tmp_path, defect):
    """Exercise real PowerShell on Windows CI without launching an EXE or claiming host acceptance."""
    powershell = shutil.which('pwsh') or shutil.which('powershell')
    if not powershell:
        pytest.skip('PowerShell is unavailable; Windows CI executes these helper contracts')
    profiles, _ = fixture_profiles()
    report = profile_report(profiles)
    if defect != 'none': corrupt_profile(report, defect)
    (tmp_path / 'manifest.json').write_text(json.dumps(bundle_manifest(profiles)), encoding='utf-8')
    (tmp_path / 'report.json').write_text(json.dumps(report), encoding='utf-8')
    script = (Path(build.ROOT) / 'scripts/validate_windows_bundle.ps1').read_text(encoding='utf-8')
    helpers = script.split('# Both offline profiles', 1)[1].split('$summary = [ordered]@{', 1)[0]
    harness = tmp_path / 'test-profile-gate.ps1'
    harness.write_text("$ErrorActionPreference = 'Stop'\nSet-StrictMode -Version Latest\n# Both offline profiles" + helpers + "\n"
                       "$manifest = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json\n"
                       "$report = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'report.json') -Raw -Encoding UTF8 | ConvertFrom-Json\n"
                       "Assert-ProfileManifests $manifest\nAssert-ModelProfileProbes $report $manifest\n", encoding='utf-8')
    process = subprocess.run([powershell, '-NoProfile', '-NonInteractive', '-File', str(harness)],
                             capture_output=True, text=True, timeout=30, check=False)
    assert (process.returncode == 0) is (defect == 'none'), process.stdout + process.stderr


@pytest.mark.parametrize('unexpected', [
    'rapidocr_onnxruntime/models/ch_PP-OCRv4_rec_infer.onnx',
    'rapidocr_onnxruntime/models/ch_PP-OCRv4_det_infer.onnx',
    'models/rec/pp-ocrv4_rec.onnx',
    'models/unused-new-model.ONNX',
    'models/models-v4.lock.json',
    'models/validation/unapproved.png',
    'other/unreferenced.onnx',
])
def test_frozen_payload_rejects_every_unreferenced_model_asset(monkeypatch, unexpected):
    from PyInstaller.archive import readers
    profiles, data = fixture_profiles()
    data.update({'python312.dll': b'python', 'qwindows.dll': b'qt',
                 'onnxruntime_pybind11_state.pyd': b'ort', unexpected: b'must-not-ship'})

    class Reader:
        def __init__(self, _): self.toc = dict.fromkeys(data)
        def extract(self, name): return data[name]

    monkeypatch.setattr(readers, 'CArchiveReader', Reader)
    with pytest.raises(ValueError, match='allowlist mismatch'):
        inspect_frozen_payload('unused.exe', profiles, validation_fixtures=fixture_validation()[0])


def test_frozen_payload_reports_exact_allowed_onnx_assets(monkeypatch):
    from PyInstaller.archive import readers
    profiles, data = fixture_profiles()
    data.update({'python312.dll': b'python', 'qwindows.dll': b'qt',
                 'onnxruntime_pybind11_state.pyd': b'ort'})

    class Reader:
        def __init__(self, _): self.toc = dict.fromkeys(data)
        def extract(self, name): return data[name]

    monkeypatch.setattr(readers, 'CArchiveReader', Reader)
    report = inspect_frozen_payload('unused.exe', profiles, validation_fixtures=fixture_validation()[0])
    assert report['onnx_allowlist_verified'] is True
    assert report['onnx_assets'] == sorted({info['path'] for models in profiles.values() for info in models.values()})


@pytest.mark.parametrize('path', ['MODELS/rec-v6-small.onnx', '../outside', 'C:/outside', 'directory/./entry'])
def test_frozen_payload_rejects_ambiguous_or_unsafe_paths(monkeypatch, path):
    from PyInstaller.archive import readers
    profiles, data = fixture_profiles()
    data[path] = b'bad'

    class Reader:
        def __init__(self, _): self.toc = dict.fromkeys(data)
        def extract(self, name): return data[name]

    monkeypatch.setattr(readers, 'CArchiveReader', Reader)
    with pytest.raises(ValueError, match='frozen payload entry'):
        inspect_frozen_payload('unused.exe', profiles, validation_fixtures=fixture_validation()[0])


def test_profile_manifests_reject_conflicting_shared_asset_identity():
    profiles, _ = fixture_profiles()
    profiles['v6-medium']['det']['sha256'] = '0' * 64
    with pytest.raises(ValueError, match='Conflicting shared model asset'):
        check_profile_manifests(profiles)


def test_both_locked_profiles_run_without_vendor_default_weights(tmp_path):
    """Use the real runtime with its defaults removed, not mocked inference results."""
    import os
    import sys
    from importlib.util import find_spec

    package = Path(next(iter(find_spec('rapidocr_onnxruntime').submodule_search_locations)))
    relocated = tmp_path / 'rapidocr_onnxruntime'
    shutil.copytree(package, relocated, ignore=shutil.ignore_patterns('models', '__pycache__'))
    assert not list(relocated.rglob('*.onnx'))
    script = r'''
import json
import socket
import sys
from pathlib import Path
import cv2
import rapidocr_onnxruntime as vendor
from src.ocr.engine import OcrEngine
from src.ocr.model_validator import MODEL_PROFILES

assert Path(vendor.__file__).resolve().parent == Path.cwd() / 'rapidocr_onnxruntime'
assert not (Path(vendor.__file__).parent / 'models').exists()
def blocked(*args, **kwargs):
    raise AssertionError('Runtime attempted to access the network')
socket.socket.connect = blocked
socket.socket.connect_ex = blocked
socket.create_connection = blocked
root = Path(sys.argv[1])
fixtures = json.loads((root / 'models/validation/fixtures.json').read_text(encoding='utf-8'))
results = {}
for profile in MODEL_PROFILES:
    engine = OcrEngine(model_profile=profile)
    engine.load()
    assert engine.is_ready()
    for case in fixtures['cases']:
        result = engine.run_ocr(cv2.imread(str(root / 'models/validation' / case['file'])))
        assert result['text'] == case['expected'][profile], (profile, case['id'], result)
        assert result['status'] in ('done', 'needs_review')
    results[profile] = 'PASSED_WITHOUT_VENDOR_MODELS'
print(json.dumps(results))
'''
    environment = dict(os.environ, PYTHONPATH=str(build.ROOT), ORT_DISABLE_TELEMETRY='1',
                       QT_QPA_PLATFORM='offscreen')
    process = subprocess.run([sys.executable, '-c', script, str(build.ROOT)], cwd=tmp_path,
                             env=environment, capture_output=True, text=True, timeout=180, check=False)
    assert process.returncode == 0, process.stdout + process.stderr
    assert json.loads(process.stdout) == {'v6-small': 'PASSED_WITHOUT_VENDOR_MODELS',
                                        'v6-medium': 'PASSED_WITHOUT_VENDOR_MODELS'}


def test_powershell_profile_constants_match_runtime():
    from src.ocr.model_validator import DEFAULT_MODEL_PROFILE, MODEL_PROFILES
    script = (build.ROOT / 'scripts/validate_windows_bundle.ps1').read_text(encoding='utf-8')
    expected = "$requiredProfiles = @(" + ', '.join(repr(profile) for profile in MODEL_PROFILES) + ')'
    assert expected in script
    assert "$defaultProfile = '" + DEFAULT_MODEL_PROFILE + "'" in script
