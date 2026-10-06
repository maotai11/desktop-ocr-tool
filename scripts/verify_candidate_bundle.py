"""Verify same-run Windows assets and emit a small, transferable evidence file.

The actual ZIP/EXE bytes are checked on the build runner. This does not certify
a clean Windows host or complete Windows system-DLL compatibility.
"""
import argparse
import hashlib
import json
import os
import stat
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from email.parser import BytesParser
from pathlib import Path, PurePosixPath, PureWindowsPath

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.version import APP_VERSION as VERSION
from src.ocr.model_validator import DEFAULT_MODEL_PROFILE, MODEL_PROFILES

MODEL_ROLES = {'det', 'rec', 'cls'}


def portable_models(models):
    """Compare lock content without machine-specific extraction locations."""
    if not isinstance(models, dict) or set(models) != MODEL_ROLES:
        raise ValueError('Model manifest must contain det, rec and cls')
    if any(not isinstance(info, dict) for info in models.values()):
        raise ValueError('Invalid model manifest entry')
    return {role: {key: value for key, value in info.items() if key != 'absolute_path'}
            for role, info in models.items()}


def check_profile_manifests(profiles):
    if not isinstance(profiles, dict) or set(profiles) != set(MODEL_PROFILES):
        raise ValueError('Both bundled model profiles are required')
    seen_assets = {}
    for profile, models in profiles.items():
        for role, info in portable_models(models).items():
            path = info.get('path')
            digest = info.get('sha256')
            if (not isinstance(path, str) or '\\' in path
                    or PureWindowsPath(path).drive or PurePosixPath(path).is_absolute()
                    or '..' in PurePosixPath(path).parts or PurePosixPath(path).as_posix() != path
                    or not path.startswith('models/') or not path.endswith('.onnx')
                    or not isinstance(digest, str) or len(digest) != 64
                    or any(char not in '0123456789abcdef' for char in digest)
                    or type(info.get('size_bytes')) is not int or info['size_bytes'] <= 0
                    or not isinstance(info.get('version'), str) or not info['version']):
                raise ValueError(f'Incomplete model manifest: {profile}/{role}')
            if path in seen_assets and seen_assets[path] != info:
                raise ValueError(f'Conflicting shared model asset: {path}')
            seen_assets[path] = info
        rec = models['rec']
        if (not isinstance(rec.get('character_sha256'), str)
                or len(rec['character_sha256']) != 64
                or not isinstance(rec.get('decoder_sha256'), str)
                or len(rec['decoder_sha256']) != 64
                or any(char not in '0123456789abcdef' for char in rec['decoder_sha256'])
                or any(char not in '0123456789abcdef' for char in rec['character_sha256'])
                or type(rec.get('character_count')) is not int or rec['character_count'] <= 0
                or type(rec.get('output_classes')) is not int
                or rec['output_classes'] != rec['character_count'] + 2):
            raise ValueError(f'Incomplete recognition metadata: {profile}')


def check_validation_fixtures(fixtures):
    if not isinstance(fixtures, dict) or not isinstance(fixtures.get('cases'), list):
        raise ValueError('Missing literal OCR validation fixtures')  # noqa: TRY004 - reject malformed evidence uniformly
    cases = fixtures['cases']
    if (len(cases) != 3 or any(not isinstance(case, dict) for case in cases)
            or {case.get('id') for case in cases} != {'traditional', 'amount', 'date_api'}):
        raise ValueError('Incomplete literal OCR fixture cases')
    for case in cases:
        name, digest = case.get('file'), case.get('image_sha256')
        if (not isinstance(name, str) or '/' in name or '\\' in name or ':' in name
                or name in ('.', '..') or not name.endswith('.png')
                or not isinstance(digest, str) or len(digest) != 64
                or any(char not in '0123456789abcdef' for char in digest)
                or not isinstance(case.get('ground_truth'), str) or not case['ground_truth']
                or case.get('expected') != {profile: case['ground_truth'] for profile in MODEL_PROFILES}):
            raise ValueError('Invalid literal OCR fixture manifest')
    if len({case['file'] for case in cases}) != len(cases):
        raise ValueError('Duplicate literal OCR fixture path')


def canonical_model_version(profile, models):
    return profile + ';' + ';'.join(f'{role}:{info["version"]}:{info["sha256"]}'
                                   for role, info in sorted(models.items()))


def check_fixture_results(results, fixtures, profile, models):
    if not isinstance(results, list) or len(results) != len(fixtures['cases']):
        raise ValueError(f'Incomplete literal fixture results: {profile}')
    expected = {case['id']: case for case in fixtures['cases']}
    if any(not isinstance(result, dict) for result in results) or {r.get('id') for r in results} != set(expected):
        raise ValueError(f'Mismatched literal fixture results: {profile}')
    for observed in results:
        case = expected[observed['id']]
        ocr = observed.get('result')
        if (observed.get('image_sha256') != case['image_sha256']
                or observed.get('ground_truth') != case['ground_truth']
                or not isinstance(ocr, dict) or ocr.get('text') != case['ground_truth']
                or ocr.get('status') not in ('done', 'needs_review')
                or ocr.get('engine') != 'rapidocr_onnxruntime'
                or ocr.get('model_version') != canonical_model_version(profile, models)):
            raise ValueError(f'Literal OCR fixture/provenance mismatch: {profile}/{case["id"]}')


def expected_model_identity(profile, models):
    rec = models['rec']
    return {'profile': profile, 'runtime': 'rapidocr_onnxruntime', 'runtime_version': '1.4.4',
            'recognition': {key: rec[key] for key in
                            ('character_sha256', 'character_count', 'output_classes', 'decoder_sha256')} | {
                'providers': ['CPUExecutionProvider'], 'recognition_shape': [3, 48, 320],
                'input_type': 'tensor(float)', 'blank_index': 0,
                'space_index': rec['output_classes'] - 1},
            'recognition_batch': 1, 'max_recognition_width': 4096}


def check_model_identity(identity, profile, models):
    expected = expected_model_identity(profile, models)
    if not isinstance(identity, dict) or set(identity) != set(expected):
        raise ValueError(f'Incomplete model identity: {profile}')
    recognition = identity.get('recognition')
    if (not isinstance(recognition, dict)
            or set(recognition) != set(expected['recognition']) | {'input_shape', 'output_shape'}):
        raise ValueError(f'Incomplete recognition identity: {profile}')
    input_shape, output_shape = recognition['input_shape'], recognition['output_shape']
    if (not isinstance(input_shape, list) or len(input_shape) != 4 or input_shape[1] != 3
            or input_shape[2] != 48
            or not isinstance(output_shape, list) or len(output_shape) != 3
            or output_shape[-1] != models['rec']['output_classes']):
        raise ValueError(f'Recognition tensor shape mismatch: {profile}')
    portable_identity = dict(identity, recognition={key: value for key, value in recognition.items()
                                                  if key not in ('input_shape', 'output_shape')})
    if portable_identity != expected:
        raise ValueError(f'Model-profile runtime/decoder identity mismatch: {profile}')


def check_model_profiles(report, manifest):
    profiles = manifest.get('model_profiles')
    check_profile_manifests(profiles)
    fixtures = manifest.get('ocr_validation_fixtures')
    check_validation_fixtures(fixtures)
    if manifest.get('default_model_profile') != DEFAULT_MODEL_PROFILE:
        raise ValueError('Unexpected default model profile')
    default_models = portable_models(profiles[DEFAULT_MODEL_PROFILE])
    if (portable_models(manifest.get('models')) != default_models
            or portable_models(report.get('models')) != default_models):
        raise ValueError('Default model manifest mismatch')
    results = report.get('model_profiles')
    if not isinstance(results, dict) or set(results) != set(MODEL_PROFILES):
        raise ValueError('Both model-profile probes are required')
    for profile, models in profiles.items():
        result = results[profile]
        if (not isinstance(result, dict) or result.get('passed') is not True
                or portable_models(result.get('models')) != portable_models(models)):
            raise ValueError(f'Model-profile identity mismatch: {profile}')
        check_model_identity(result.get('model_identity'), profile, models)
        ocr = result.get('ocr_result')
        if (not isinstance(ocr, dict) or not isinstance(ocr.get('text'), str)
                or ''.join(ocr['text'].split()) != 'DesktopOCR12345'
                or ocr.get('status') not in ('done', 'needs_review')
                or ocr.get('engine') != 'rapidocr_onnxruntime'
                or ocr.get('model_version') != canonical_model_version(profile, models)):
            raise ValueError(f'Model-profile OCR mismatch: {profile}')
        check_fixture_results(result.get('fixtures'), fixtures, profile, models)
    if report.get('ocr_result') != results[DEFAULT_MODEL_PROFILE]['ocr_result']:
        raise ValueError('Default OCR result mismatch')


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _json(data):
    return json.loads(data.decode('utf-8-sig'))


def _check_probe(report, manifest, frozen):
    if (report.get('passed') is not True or report.get('schema') != 2
            or report.get('frozen') is not frozen or report.get('platform') != 'win32'
            or report.get('qt_platform') != 'windows' or report.get('version') != VERSION
            or report.get('clean_machine_verified') is not False
            or report.get('database_integrity') != 'ok'):
        raise ValueError('Incomplete or mismatched Windows probe')
    if frozen and report.get('executable_sha256') != manifest['executable_sha256']:
        raise ValueError('Probe executable hash mismatch')
    if report.get('probe') == 'ocr_database':
        if (report.get('telemetry_env') != '1'
                or report.get('checks') != {'qt': True, 'models': True, 'ocr': True, 'database': True}):
            raise ValueError('OCR/model probe mismatch')
        check_model_profiles(report, manifest)
    elif report.get('probe') == 'application_lifecycle':
        if (report.get('phase_a') is not True or report.get('engine_ready') is not True
                or report.get('shutdown_clean') is not True
                or report.get('threads_stopped') !=
                {'capture': True, 'ocr': True, 'database': True, 'hotkeys': True}):
            raise ValueError('Incomplete lifecycle probe')
    else:
        raise ValueError('Unknown probe kind')


def inspect_frozen_payload(path, model_profiles, source_files=None, validation_fixtures=None):
    from PyInstaller.archive.readers import CArchiveReader
    check_profile_manifests(model_profiles)
    reader = CArchiveReader(str(path))
    names = {}
    casefolded = set()
    for name in reader.toc:
        normalized = name.replace('\\', '/')
        if normalized.casefold() in casefolded:
            raise ValueError('Duplicate normalized frozen payload entry')
        if (PureWindowsPath(normalized).drive or PurePosixPath(normalized).is_absolute()
                or '..' in PurePosixPath(normalized).parts
                or PurePosixPath(normalized).as_posix() != normalized):
            raise ValueError('Unsafe frozen payload entry')
        casefolded.add(normalized.casefold())
        names[normalized] = name
    allowed_onnx = {info['path'] for models in model_profiles.values() for info in models.values()}
    actual_onnx = {name for name in names if name.lower().endswith('.onnx')}
    if actual_onnx != allowed_onnx:
        raise ValueError('Frozen ONNX allowlist mismatch: missing=' + str(sorted(allowed_onnx - actual_onnx))
                         + ', unexpected=' + str(sorted(actual_onnx - allowed_onnx)))
    if 'rapidocr_onnxruntime/config.yaml' not in names:
        raise ValueError('Frozen RapidOCR runtime config absent')
    lower = [name.lower() for name in names]
    required = ('python312.dll', 'qwindows.dll', 'onnxruntime_pybind11_state.pyd')
    for name in required:
        if not any(value.endswith('/' + name) or value == name for value in lower):
            raise ValueError(f'Frozen dependency absent: {name}')
    metadata_names = [name for name in names if name.endswith('/METADATA')
                      and PurePosixPath(name).parent.name.lower().replace('-', '_').startswith('rapidocr_onnxruntime_')
                      and PurePosixPath(name).parent.name.endswith('.dist-info')]
    if len(metadata_names) != 1:
        raise ValueError('Frozen RapidOCR package metadata absent or ambiguous')
    metadata_bytes = reader.extract(names[metadata_names[0]])
    metadata = BytesParser().parsebytes(metadata_bytes)
    if (metadata.get('Name', '').lower().replace('_', '-') != 'rapidocr-onnxruntime'
            or metadata.get('Version') != '1.4.4'):
        raise ValueError('Frozen RapidOCR runtime version mismatch')
    check_validation_fixtures(validation_fixtures)
    fixture_lock_path = 'models/validation/fixtures.json'
    allowed_model_data = allowed_onnx | {'models/' + name for name in MODEL_PROFILES.values()} | {
        fixture_lock_path, *('models/validation/' + case['file'] for case in validation_fixtures['cases'])}
    actual_model_data = {name for name in names if name.lower().startswith('models/')}
    if actual_model_data != allowed_model_data:
        raise ValueError('Frozen model-data allowlist mismatch')
    if fixture_lock_path not in names:
        raise ValueError('Frozen literal OCR fixture manifest absent')
    fixture_bytes = reader.extract(names[fixture_lock_path])
    fixture_hash = hashlib.sha256(fixture_bytes).hexdigest()
    if _json(fixture_bytes) != validation_fixtures:
        raise ValueError('Frozen literal OCR fixture manifest mismatch')
    if source_files is not None and source_files.get(fixture_lock_path) != {'sha256': fixture_hash}:
        raise ValueError('Frozen literal OCR fixture source hash mismatch')
    fixture_hashes = {}
    for case in validation_fixtures['cases']:
        key = names.get('models/validation/' + case['file'])
        if not key or hashlib.sha256(reader.extract(key)).hexdigest() != case['image_sha256']:
            raise ValueError(f'Frozen literal OCR fixture hash mismatch: {case["id"]}')
        fixture_hashes[case['id']] = case['image_sha256']
    evidence = {}
    for profile, models in model_profiles.items():
        lock_path = 'models/' + MODEL_PROFILES[profile]
        lock_key = names.get(lock_path)
        if not lock_key:
            raise ValueError(f'Frozen model-profile manifest absent: {profile}')
        lock_bytes = reader.extract(lock_key)
        lock_hash = hashlib.sha256(lock_bytes).hexdigest()
        if _json(lock_bytes) != portable_models(models):
            raise ValueError(f'Frozen model-profile manifest mismatch: {profile}')
        if source_files is not None and source_files.get(lock_path) != {'sha256': lock_hash}:
            raise ValueError(f'Frozen model-profile source hash mismatch: {profile}')
        hashes = {}
        for role, info in models.items():
            key = names.get(info['path'])
            if not key:
                raise ValueError(f'Frozen ONNX asset absent: {profile}/{role}')
            data = reader.extract(key)
            if (hashlib.sha256(data).hexdigest() != info['sha256']
                    or len(data) != info['size_bytes']):
                raise ValueError(f'Frozen ONNX asset hash/size mismatch: {profile}/{role}')
            hashes[role] = info['sha256']
        evidence[profile] = {'manifest_path': lock_path, 'manifest_sha256': lock_hash,
                             'model_hashes': hashes, 'verified': True}
    return {'inspected': True, 'entry_count': len(names), 'entries': sorted(names),
            'dlls': sorted(name for name in names if name.lower().endswith(('.dll', '.pyd'))),
            'required_payload': list(required), 'embedded_model_hashes_verified': True,
            'embedded_model_profiles': evidence,
            'onnx_allowlist_verified': True, 'onnx_assets': sorted(actual_onnx),
            'runtime_metadata': {'path': metadata_names[0], 'version': metadata['Version'],
                                 'sha256': hashlib.sha256(metadata_bytes).hexdigest()},
            'literal_ocr_fixtures': {'manifest_sha256': fixture_hash, 'images': fixture_hashes, 'verified': True},
            'system_dll_closure': 'NOT_A_CLEAN_HOST_CERTIFICATION'}


def verify_candidate(root, expected_commit, inspect_payload=False):
    root = Path(root)
    artifacts = root / 'artifacts'
    bundles = list(artifacts.glob(f'DesktopOCRTool-v{VERSION}.zip'))
    if len(bundles) != 1:
        raise ValueError('Expected exactly one current candidate ZIP')
    bundle = bundles[0]
    bundle_hash = sha256_file(bundle)
    if bundle.with_suffix('.zip.sha256').read_text(encoding='ascii').split()[0] != bundle_hash:
        raise ValueError('Bundle checksum mismatch')
    prefix = f'release-v{VERSION}/'
    with zipfile.ZipFile(bundle) as archive:
        if len(set(archive.namelist())) != len(archive.namelist()):
            raise ValueError('Duplicate bundle member')
        if any(stat.S_ISLNK(info.external_attr >> 16) for info in archive.infolist()):
            raise ValueError('Symlink bundle member')
        for name in archive.namelist():
            if ('..' in PurePosixPath(name).parts or PurePosixPath(name).is_absolute()
                    or PureWindowsPath(name).drive or '\\' in name):
                raise ValueError('Unsafe bundle member')
        manifest = _json(archive.read(prefix + 'BUILD_MANIFEST.json'))
        source_bytes = archive.read(prefix + 'SOURCE_MANIFEST.json')
        source = _json(source_bytes)
        if (manifest.get('schema') != 2 or manifest.get('platform') != 'win32'
                or manifest.get('version') != VERSION or source.get('commit') != expected_commit
                or source.get('dirty') is not False
                or manifest.get('source') != {key: value for key, value in source.items() if key != 'files'}
                or manifest.get('source_manifest_sha256') != hashlib.sha256(source_bytes).hexdigest()):
            raise ValueError('Source/version manifest mismatch')
        if manifest.get('default_model_profile') != DEFAULT_MODEL_PROFILE:
            raise ValueError('Unexpected default model profile')
        check_profile_manifests(manifest.get('model_profiles'))
        check_validation_fixtures(manifest.get('ocr_validation_fixtures'))
        if portable_models(manifest.get('models')) != portable_models(manifest['model_profiles'][DEFAULT_MODEL_PROFILE]):
            raise ValueError('Default model manifest mismatch')
        name = manifest['executable_name']
        if name != f'DesktopOCRTool-v{VERSION}.exe':
            raise ValueError('Unexpected executable name')
        executable_hash = hashlib.sha256()
        with tempfile.TemporaryDirectory(prefix='ocr-payload-verify-') as directory:
            executable = Path(directory) / name
            with archive.open(prefix + name) as stream, executable.open('wb') as output:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    executable_hash.update(chunk)
                    output.write(chunk)
            if executable_hash.hexdigest() != manifest['executable_sha256']:
                raise ValueError('EXE checksum mismatch')
            payload = (inspect_frozen_payload(executable, manifest['model_profiles'], source.get('files', {}),
                                              manifest['ocr_validation_fixtures']) if inspect_payload else
                       {'inspected': False, 'system_dll_closure': 'NOT_RUN'})
            executable_size = executable.stat().st_size
        probes = {}
        for report_name in ('frozen-selftest.json', 'frozen-app-smoke.json'):
            raw = archive.read(prefix + 'candidate-validation/' + report_name)
            if manifest['candidate_probes'][report_name] != {
                    'sha256': hashlib.sha256(raw).hexdigest(), 'status': 'PASSED'}:
                raise ValueError('Embedded probe checksum mismatch')
            _check_probe(_json(raw), manifest, True)
            probes[report_name] = 'PASSED'
    gate = _json((artifacts / 'firewall-validation/candidate-gate.json').read_bytes())
    if (gate.get('passed') is not True or gate.get('executable_sha256') != executable_hash.hexdigest()
            or gate.get('version') != VERSION or gate.get('clean_machine_verified') is not False):
        raise ValueError('Outbound-blocked gate mismatch')
    for name in probes:
        path = artifacts / 'firewall-validation' / name
        if gate['probes'][name] != {'sha256': sha256_file(path), 'status': 'PASSED'}:
            raise ValueError('Outbound-blocked probe checksum mismatch')
        _check_probe(_json(path.read_bytes()), manifest, True)
    for name in ('source-selftest.json', 'source-app-smoke.json'):
        _check_probe(_json((root / name).read_bytes()), manifest, False)
    suites = ET.parse(root / 'test-results.xml').getroot()
    if not list(suites.iter('testcase')):
        raise ValueError('No Windows test evidence')
    if any(int(s.get('failures', 0)) or int(s.get('errors', 0)) for s in suites.iter('testsuite')):
        raise ValueError('Windows tests failed')
    skipped = [node.get('name') for node in suites.iter('testcase') if node.find('skipped') is not None]
    return {'schema': 1, 'passed': True, 'version': VERSION, 'source_commit': expected_commit,
            'source_tree': source['head_tree'], 'bundle_filename': bundle.name,
            'bundle_sha256': bundle_hash, 'bundle_bytes': bundle.stat().st_size,
            'executable_filename': manifest['executable_name'],
            'executable_sha256': executable_hash.hexdigest(), 'executable_bytes': executable_size,
            'source_manifest_sha256': hashlib.sha256(source_bytes).hexdigest(),
            'source_manifest': source, 'build_manifest': manifest, 'frozen_payload': payload,
            'windows_tests': {'collected': len(list(suites.iter('testcase'))), 'skipped': skipped},
            'model_profiles': {profile: 'PASSED' for profile in MODEL_PROFILES},
            'source_probes': 'PASSED', 'frozen_probes': probes,
            'outbound_blocked_frozen_probes': 'PASSED',
            'workflow_run_id': os.environ.get('GITHUB_RUN_ID'),
            'workflow_run_attempt': os.environ.get('GITHUB_RUN_ATTEMPT'),
            'workflow': {'repository': os.environ.get('GITHUB_REPOSITORY'),
                         'run_id': os.environ.get('GITHUB_RUN_ID'),
                         'run_attempt': os.environ.get('GITHUB_RUN_ATTEMPT'),
                         'source_commit': expected_commit},
            'clean_machine_verified': False, 'mixed_dpi_gate': 'NOT_RUN'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifact-root', type=Path, required=True)
    parser.add_argument('--expected-commit', required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--inspect-payload', action='store_true')
    args = parser.parse_args(argv)
    report = verify_candidate(args.artifact_root, args.expected_commit, args.inspect_payload)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
