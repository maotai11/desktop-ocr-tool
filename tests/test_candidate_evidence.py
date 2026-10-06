"""Small-evidence verifier checks actual ZIP/EXE/probe bytes before publishing."""
import hashlib
import json
import zipfile

import pytest
from model_profile_fixtures import fixture_profiles, fixture_validation, profile_report

from scripts.verify_candidate_bundle import VERSION, sha256_file, verify_candidate


def encoded(value):
    return json.dumps(value).encode()


@pytest.fixture
def candidate(tmp_path):
    artifacts = tmp_path / 'artifacts'
    artifacts.mkdir()
    firewall = artifacts / 'firewall-validation'
    firewall.mkdir()
    source = {'commit': 'a' * 40, 'head_tree': 'b' * 40, 'dirty': False}
    source_bytes = encoded(source)
    exe = b'fake-exe-byte-fixture'
    profiles, _ = fixture_profiles()
    models = profiles['v6-small']
    common = {'schema': 2, 'passed': True, 'frozen': True, 'platform': 'win32',
              'qt_platform': 'windows', 'version': VERSION,
              'clean_machine_verified': False, 'database_integrity': 'ok',
              'executable_sha256': hashlib.sha256(exe).hexdigest()}
    probes = {
        'frozen-selftest.json': dict(common, probe='ocr_database', telemetry_env='1',
                                    **profile_report(profiles), checks={'qt': True, 'models': True, 'ocr': True, 'database': True}),
        'frozen-app-smoke.json': dict(common, probe='application_lifecycle', phase_a=True,
                                     engine_ready=True, shutdown_clean=True,
                                     threads_stopped={'capture': True, 'ocr': True, 'database': True, 'hotkeys': True}),
    }
    manifest = {'schema': 2, 'platform': 'win32', 'version': VERSION,
                'executable_name': f'DesktopOCRTool-v{VERSION}.exe',
                'executable_sha256': hashlib.sha256(exe).hexdigest(), 'models': models,
                'default_model_profile': 'v6-small', 'model_profiles': profiles,
                'ocr_validation_fixtures': fixture_validation()[0],
                'source_manifest_sha256': hashlib.sha256(source_bytes).hexdigest(),
                'source': source,
                'candidate_probes': {name: {'status': 'PASSED', 'sha256': hashlib.sha256(encoded(value)).hexdigest()}
                                     for name, value in probes.items()}}
    contents = {'BUILD_MANIFEST.json': encoded(manifest), 'SOURCE_MANIFEST.json': source_bytes,
                manifest['executable_name']: exe,
                **{'candidate-validation/' + name: encoded(value) for name, value in probes.items()}}

    def write_bundle():
        path = artifacts / f'DesktopOCRTool-v{VERSION}.zip'
        with zipfile.ZipFile(path, 'w') as archive:
            for name, raw in contents.items():
                archive.writestr(f'release-v{VERSION}/' + name, raw)
        path.with_suffix('.zip.sha256').write_text(sha256_file(path) + '  ' + path.name, encoding='ascii')
        return path

    write_bundle()
    for name, value in probes.items():
        (firewall / name).write_bytes(encoded(value))
        source_name = 'source-selftest.json' if name == 'frozen-selftest.json' else 'source-app-smoke.json'
        (tmp_path / source_name).write_bytes(encoded(dict(value, frozen=False, executable_sha256=None)))
    gate = {'passed': True, 'version': VERSION, 'executable_sha256': manifest['executable_sha256'],
            'clean_machine_verified': False,
            'probes': {name: {'status': 'PASSED', 'sha256': sha256_file(firewall / name)} for name in probes}}
    (firewall / 'candidate-gate.json').write_bytes(encoded(gate))
    (tmp_path / 'test-results.xml').write_text(
        '<testsuites><testsuite failures="0" errors="0"><testcase name="one"/></testsuite></testsuites>')
    return tmp_path, manifest, contents, write_bundle


def test_verified_evidence_binds_actual_zip_and_exe(candidate):
    root, _, _, _ = candidate
    report = verify_candidate(root, 'a' * 40)
    assert report['passed'] and report['source_commit'] == 'a' * 40
    assert report['bundle_sha256'] == sha256_file(root / 'artifacts' / report['bundle_filename'])
    assert report['executable_sha256'] == hashlib.sha256(b'fake-exe-byte-fixture').hexdigest()
    assert report['frozen_payload']['inspected'] is False
    assert report['clean_machine_verified'] is False


@pytest.mark.parametrize('defect', ['commit', 'exe', 'probe', 'source', 'zip', 'firewall', 'tests'])
def test_metadata_never_accepts_mismatched_bytes_or_failed_gates(candidate, defect):
    root, manifest, contents, write_bundle = candidate
    if defect == 'exe': contents[manifest['executable_name']] += b'changed'
    if defect == 'probe': contents['candidate-validation/frozen-selftest.json'] += b' '
    if defect == 'source': contents['SOURCE_MANIFEST.json'] += b' '
    write_bundle()
    if defect == 'zip':
        (root / 'artifacts' / f'DesktopOCRTool-v{VERSION}.zip.sha256').write_text('0' * 64)
    if defect == 'firewall':
        (root / 'artifacts/firewall-validation/candidate-gate.json').write_bytes(encoded({'passed': False}))
    if defect == 'tests':
        (root / 'test-results.xml').write_text('<testsuites><testsuite failures="1" errors="0"/></testsuites>')
    with pytest.raises(ValueError):
        verify_candidate(root, 'c' * 40 if defect == 'commit' else 'a' * 40)


def test_verifier_rejects_dirty_source_even_when_manifest_agrees(candidate):
    root, manifest, contents, write_bundle = candidate
    source = json.loads(contents['SOURCE_MANIFEST.json'])
    source['dirty'] = True
    contents['SOURCE_MANIFEST.json'] = encoded(source)
    manifest['source'] = source
    manifest['source_manifest_sha256'] = hashlib.sha256(contents['SOURCE_MANIFEST.json']).hexdigest()
    contents['BUILD_MANIFEST.json'] = encoded(manifest)
    write_bundle()
    with pytest.raises(ValueError, match='manifest mismatch'):
        verify_candidate(root, 'a' * 40)


def test_verifier_rejects_missing_testcases(candidate):
    root, _, _, _ = candidate
    (root / 'test-results.xml').write_text('<testsuites/>')
    with pytest.raises(ValueError, match='No Windows test evidence'):
        verify_candidate(root, 'a' * 40)


@pytest.mark.parametrize('kind', ['duplicate', 'symlink'])
def test_verifier_rejects_ambiguous_or_redirected_archive_members(candidate, kind):
    import stat
    root, _, _, _ = candidate
    path = root / 'artifacts' / f'DesktopOCRTool-v{VERSION}.zip'
    with zipfile.ZipFile(path, 'a') as archive:
        if kind == 'duplicate':
            with pytest.warns(UserWarning, match='Duplicate'):
                archive.writestr(f'release-v{VERSION}/SOURCE_MANIFEST.json', b'{}')
        else:
            info = zipfile.ZipInfo(f'release-v{VERSION}/redirect')
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, '../outside')
    path.with_suffix('.zip.sha256').write_text(sha256_file(path))
    with pytest.raises(ValueError, match='bundle member'):
        verify_candidate(root, 'a' * 40)


@pytest.mark.parametrize('defect', ['none', 'missing_python', 'model_hash', 'secondary_hash',
                                  'secondary_missing', 'secondary_lock_missing', 'secondary_lock_changed',
                                  'secondary_size', 'duplicate_normalized', 'metadata_missing',
                                  'metadata_version', 'fixture_missing', 'fixture_hash', 'fixture_manifest_missing'])
def test_payload_inspector_checks_actual_runtime_and_both_profile_bytes(monkeypatch, defect):
    from PyInstaller.archive import readers

    from scripts.verify_candidate_bundle import inspect_frozen_payload
    profiles, assets = fixture_profiles()
    data = {'python312.dll': b'python', 'PySide6/qwindows.dll': b'qt',
            'onnxruntime/onnxruntime_pybind11_state.pyd': b'ort', **assets}
    if defect == 'metadata_missing': del data['rapidocr_onnxruntime-1.4.4.dist-info/METADATA']
    if defect == 'metadata_version': data['rapidocr_onnxruntime-1.4.4.dist-info/METADATA'] = b'Name: rapidocr-onnxruntime\nVersion: 9.0\n'
    if defect == 'fixture_missing': del data['models/validation/amount.png']
    if defect == 'fixture_hash': data['models/validation/amount.png'] = b'changed pixels'
    if defect == 'fixture_manifest_missing': del data['models/validation/fixtures.json']
    if defect == 'missing_python': del data['python312.dll']
    if defect == 'model_hash': data['models/rec-v6-small.onnx'] = b'changed'
    if defect == 'secondary_hash': data['models/rec-v6-medium.onnx'] = b'changed'
    if defect == 'secondary_missing': del data['models/rec-v6-medium.onnx']
    if defect == 'secondary_lock_missing': del data['models/models-v6-medium.lock.json']
    if defect == 'secondary_lock_changed': data['models/models-v6-medium.lock.json'] = b'{}'
    if defect == 'secondary_size':
        profiles['v6-medium']['rec']['size_bytes'] += 1
        data['models/models-v6-medium.lock.json'] = encoded(profiles['v6-medium'])
    if defect == 'duplicate_normalized': data['models\\rec-v6-medium.onnx'] = data['models/rec-v6-medium.onnx']

    class Reader:
        def __init__(self, _): self.toc = dict.fromkeys(data)
        def extract(self, name): return data[name]

    monkeypatch.setattr(readers, 'CArchiveReader', Reader)
    if defect == 'none':
        result = inspect_frozen_payload('unused-fixture.exe', profiles, validation_fixtures=fixture_validation()[0])
        assert result['embedded_model_hashes_verified'] is True
        assert set(result['embedded_model_profiles']) == {'v6-small', 'v6-medium'}
        assert result['runtime_metadata']['version'] == '1.4.4'
        assert result['literal_ocr_fixtures']['verified'] is True
    else:
        with pytest.raises(ValueError):
            inspect_frozen_payload('unused-fixture.exe', profiles, validation_fixtures=fixture_validation()[0])


def test_source_manifest_excludes_only_untracked_generated_probe_outputs(tmp_path, monkeypatch):
    from scripts import build
    (tmp_path / 'source.py').write_text('code')
    (tmp_path / 'source-selftest.json').write_text('{}')
    (tmp_path / 'unexpected.py').write_text('unknown code')
    unexpected = False

    def git(args, **kwargs):
        command = args[1:]
        if command == ['rev-parse', 'HEAD']: return b'a' * 40 + b'\n'
        if command == ['rev-parse', 'HEAD^{tree}']: return b'b' * 40 + b'\n'
        if command == ['ls-files', '-z', '--cached']: return b'source.py\0'
        if command[0] == 'ls-files':
            return b'source.py\0source-selftest.json\0' + (b'unexpected.py\0' if unexpected else b'')
        return b'?? source-selftest.json\n' + (b'?? unexpected.py\n' if unexpected else b'')

    monkeypatch.setattr(build.subprocess, 'check_output', git)
    before = build.source_manifest(tmp_path)
    assert before['dirty'] is False and 'source-selftest.json' not in before['files']
    assert before['excluded_generated_validation_files'] == ['source-selftest.json']
    unexpected = True
    after = build.source_manifest(tmp_path)
    assert after['dirty'] is True and 'unexpected.py' in after['files']


@pytest.mark.parametrize('stage', ['embedded', 'firewall', 'source'])
@pytest.mark.parametrize('defect', ['missing_secondary', 'secondary_hash', 'secondary_ocr', 'secondary_identity'])
def test_same_run_evidence_requires_secondary_at_every_probe_stage(candidate, stage, defect):
    root, manifest, contents, write_bundle = candidate
    embedded = 'candidate-validation/frozen-selftest.json'
    if stage == 'embedded': raw = contents[embedded]
    else:
        path = (root / 'artifacts/firewall-validation/frozen-selftest.json' if stage == 'firewall'
                else root / 'source-selftest.json')
        raw = path.read_bytes()
    report = json.loads(raw)
    if defect == 'missing_secondary': del report['model_profiles']['v6-medium']
    elif defect == 'secondary_hash': report['model_profiles']['v6-medium']['models']['rec']['sha256'] = '0' * 64
    elif defect == 'secondary_ocr': report['model_profiles']['v6-medium']['ocr_result']['text'] = 'incorrect'
    elif defect == 'secondary_identity': report['model_profiles']['v6-medium']['model_identity']['runtime_version'] = 'wrong'
    updated = encoded(report)
    if stage == 'embedded':
        contents[embedded] = updated
        manifest['candidate_probes']['frozen-selftest.json']['sha256'] = hashlib.sha256(updated).hexdigest()
        contents['BUILD_MANIFEST.json'] = encoded(manifest)
        write_bundle()
    else:
        path.write_bytes(updated)
        if stage == 'firewall':
            gate_path = root / 'artifacts/firewall-validation/candidate-gate.json'
            gate = json.loads(gate_path.read_bytes())
            gate['probes']['frozen-selftest.json']['sha256'] = hashlib.sha256(updated).hexdigest()
            gate_path.write_bytes(encoded(gate))
    with pytest.raises(ValueError):
        verify_candidate(root, 'a' * 40)
