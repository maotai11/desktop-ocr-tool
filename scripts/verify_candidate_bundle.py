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


def check_handwriting_probe(report, manifest, frozen, *, firewall=False):
    """Bind real native/Qt handwriting checks to this candidate, never mocks."""
    from src.handwriting.assets import ALPHABET_SHA256, MODEL_BYTES, MODEL_SHA256
    identity = manifest.get('handwriting', {})
    helper = identity.get('helper', {})
    source = helper.get('source', {})
    digest = lambda value: isinstance(value, str) and len(value) == 64 and all(
        char in '0123456789abcdef' for char in value)
    imports = helper.get('native_imports')
    if (identity.get('model_sha256') != MODEL_SHA256
            or identity.get('model_size_bytes') != MODEL_BYTES
            or identity.get('alphabet_sha256') != ALPHABET_SHA256
            or identity.get('label_count') != 11853
            or not digest(identity.get('validation_sha256'))
            or helper.get('schema') != 1 or helper.get('protocol') != 2
            or helper.get('platform') != 'win32' or helper.get('machine') != 'AMD64'
            or helper.get('name') != 'inkpick-handwriting.exe'
            or helper.get('crt') != 'MSVC /MT Release'
            or type(helper.get('size_bytes')) is not int or not 0 < helper['size_bytes'] <= 10000000
            or not digest(helper.get('sha256')) or not digest(source.get('sha256'))
            or source.get('upstream_commit') != '581faa8f6f15e4a7b21964be3a5ec36265c80e5b'
            or not isinstance(imports, list) or not imports
            or not {'kernel32.dll', 'bcrypt.dll'} <= set(imports)
            or not set(imports) <= {'kernel32.dll', 'bcrypt.dll', 'advapi32.dll', 'user32.dll', 'ntdll.dll'}):
        raise ValueError('Invalid handwriting build identity')
    expected = {'schema': 1, 'probe': 'handwriting_lifecycle', 'passed': True,
                'frozen': frozen, 'platform': 'win32', 'qt_platform': 'windows',
                'version': VERSION, 'clean_machine_verified': False, 'phase': 'completed',
                'executable_sha256': manifest['executable_sha256'] if frozen else None,
                'helper_sha256': helper['sha256'], 'model_sha256': MODEL_SHA256,
                'alphabet_sha256': ALPHABET_SHA256,
                'validation_sha256': identity['validation_sha256'],
                'helper_source_sha256': source['sha256'], 'helper_architecture': 'AMD64', 'helper_protocol': 2,
                'queue_latest_only': True, 'clipboard_roundtrip': True,
                'self_mime_preserved': True, 'helper_stopped': True, 'job_assigned': True,
                'firewall_gate_acknowledged': firewall,
                'accuracy_evidence': 'UPSTREAM_TRAINING_REPLAY_ONLY'}
    for key, value in expected.items():
        if type(report.get(key)) is not type(value) or report.get(key) != value:
            raise ValueError('Incomplete or mismatched handwriting probe: ' + key)
    if set(report) != set(expected) | {'cases'}:
        raise ValueError('Unexpected or transient data in completed handwriting evidence')
    alphabet_bytes = (ROOT / 'models/handwriting/alphabet.txt').read_bytes()
    if hashlib.sha256(alphabet_bytes).hexdigest() != ALPHABET_SHA256:
        raise ValueError('Verifier handwriting alphabet changed')
    alphabet = frozenset(alphabet_bytes.decode('utf-8'))
    cases = report.get('cases')
    labels = set('一二十己已巳臺灣龜鬱鑑靈壹貳參龘')
    if (not isinstance(cases, list) or len(cases) != len(labels)
            or any(not isinstance(case, dict) for case in cases)
            or {case.get('label') for case in cases} != labels):
        raise ValueError('Incomplete handwriting training-trace checks')
    for case in cases:
        values = case.get('candidates')
        if (set(case) != {'label', 'candidates', 'rank', 'passed'}
                or case['passed'] is not True or type(case['rank']) is not int or case['rank'] != 1
                or not isinstance(values, list) or not 1 <= len(values) <= 60
                or any(not isinstance(value, str) or value not in alphabet for value in values)
                or len(set(values)) != len(values) or values[0] != case['label']):
            raise ValueError('Handwriting expected-candidate check failed')


def check_handwriting_image_probe(report, manifest, frozen):
    from src.handwriting.candidates import is_cjk_scalar
    rec = manifest['model_profiles']['v6-small']['rec']
    expected = {'schema': 1, 'probe': 'handwriting_image_lifecycle', 'version': VERSION,
        'frozen': frozen, 'platform': 'win32', 'qt_platform': 'windows',
        'executable_sha256': manifest['executable_sha256'] if frozen else None,
        'helper_protocol': 2, 'passed': True, 'inference_completed': True,
        'worker_pid_verified': True, 'job_assigned': True, 'cancelled_and_reaped': True,
        'children_reaped': True, 'clean_machine_verified': False,
        'accuracy_evidence': 'UPSTREAM_TRAINING_RASTER_FUNCTIONAL_ONLY',
        'model_sha256': rec['sha256'], 'character_sha256': rec['character_sha256']}
    if (set(report) != set(expected) | {'candidates'} or any(
            type(report.get(key)) is not type(value) or report.get(key) != value
            for key,value in expected.items())):
        raise ValueError('Incomplete or mismatched handwriting image probe')
    values = report['candidates']
    if (not isinstance(values, list) or not 1 <= len(values) <= 2 or '一' not in values or
            any(not isinstance(value,str) or not is_cjk_scalar(value) for value in values)
            or len(set(values)) != len(values)):
        raise ValueError('Invalid handwriting image candidates')


def check_frozen_stress(report, manifest):
    expected = {'schema': 1, 'probe': 'model_runtime_stress', 'version': VERSION,
                'platform': 'win32', 'frozen': True, 'passed': True,
                'executable_sha256': manifest['executable_sha256'],
                'iterations_per_profile': 500, 'telemetry_env': '1',
                'clean_machine_verified': False}
    if any(type(report.get(key)) is not type(value) or report.get(key) != value
           for key, value in expected.items()):
        raise ValueError('Incomplete frozen runtime stress')
    profiles = report.get('profiles')
    if not isinstance(profiles, dict) or set(profiles) != set(MODEL_PROFILES):
        raise ValueError('Incomplete stress profile set')
    for profile, data in profiles.items():
        if (data.get('passed') is not True or type(data.get('iterations_completed')) is not int
                or data['iterations_completed'] != 500 or data.get('failures') != []):
            raise ValueError('Incomplete stress profile iterations')
        check_model_identity(data.get('identity'), profile, manifest['model_profiles'][profile])


def inspect_frozen_payload(path, model_profiles, source_files=None, validation_fixtures=None, handwriting=None):
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
    if handwriting is not None:
        from scripts.handwriting_bundle import MODEL_FILES
        allowed_model_data.update(MODEL_FILES)
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
    handwriting_evidence = None
    if handwriting is not None:
        from scripts.handwriting_bundle import inspect_payload
        handwriting_evidence = inspect_payload(reader, names, handwriting, source_files)
    return {'inspected': True, 'entry_count': len(names), 'entries': sorted(names),
            'handwriting': handwriting_evidence,
            'embedded_handwriting_verified': bool(handwriting_evidence and handwriting_evidence['verified']),
            'dlls': sorted(name for name in names if name.lower().endswith(('.dll', '.pyd'))),
            'required_payload': list(required), 'embedded_model_hashes_verified': True,
            'embedded_model_profiles': evidence,
            'onnx_allowlist_verified': True, 'onnx_assets': sorted(actual_onnx),
            'runtime_metadata': {'path': metadata_names[0], 'version': metadata['Version'],
                                 'sha256': hashlib.sha256(metadata_bytes).hexdigest()},
            'literal_ocr_fixtures': {'manifest_sha256': fixture_hash, 'images': fixture_hashes, 'verified': True},
            'system_dll_closure': 'NOT_A_CLEAN_HOST_CERTIFICATION'}


def verify_handwriting_source_distribution(archive, prefix, identity, source_files):
    """Check actual same-release corresponding-source/notices bytes."""
    from scripts.handwriting_bundle import SOURCE_SHA256, LICENSE_SHA256
    base = prefix + 'handwriting-source/'
    patch_hash = '120abca04044db3de43436d9d78c10a1a32a2b3130bd38dd10bd811f5574aa45'
    bsd_hash = '822856fc8cec7c88e90e182dacd8eca5306836fc09df817c9dbb1d6337f6a1cc'

    def checked(name, expected_hash, maximum, *, exact=None, source_path=None):
        info = archive.getinfo(base + name)
        if info.file_size > maximum or (exact is not None and info.file_size != exact):
            raise ValueError('Source distribution size mismatch: ' + name)
        digest = hashlib.sha256()
        with archive.open(base + name) as stream:
            for block in iter(lambda: stream.read(1048576), b''):
                digest.update(block)
        if digest.hexdigest() != expected_hash:
            raise ValueError('Source distribution hash mismatch: ' + name)
        if source_path and source_files.get(source_path) != {'sha256': expected_hash}:
            raise ValueError('Source distribution differs from committed source: ' + name)

    checked('tegaki-zh_TW/handwriting-zh_TW.xml', SOURCE_SHA256, 24523156, exact=24523156)
    checked('tegaki-zh_TW/COPYING-LGPL-2.1.txt', LICENSE_SHA256, 50000)
    checked('zinnia/vendor/zinnia/COPYING', bsd_hash, 10000,
            source_path='native/handwriting/vendor/zinnia/COPYING')
    checked('zinnia/patches/0001-bound-vertex-tree.patch', patch_hash, 10000,
            source_path='native/handwriting/patches/0001-bound-vertex-tree.patch')
    for name, source_path in (('MODEL_MANIFEST.json', 'models/handwriting/manifest.json'),
                              ('zinnia/UPSTREAM_MANIFEST.json', 'native/handwriting/UPSTREAM_MANIFEST.json')):
        info = archive.getinfo(base + name)
        if info.file_size > 100000:
            raise ValueError('Oversized source distribution manifest')
        raw = archive.read(base + name)
        if source_files.get(source_path) != {'sha256': hashlib.sha256(raw).hexdigest()}:
            raise ValueError('Source distribution manifest hash mismatch')
        value = _json(raw)
        if name == 'MODEL_MANIFEST.json':
            if value != {key: data for key, data in identity.items() if key not in ('helper', 'branding')}:
                raise ValueError('Source distribution model manifest mismatch')
        else:
            upstream = value
    helper_source = identity['helper']['source']
    files = helper_source['files']
    if (upstream.get('commit') != '581faa8f6f15e4a7b21964be3a5ec36265c80e5b'
            or upstream.get('feature_patch_sha256') != patch_hash
            or set(files) != {'vendor/' + name for name in upstream['files']} |
                             {'helper.cpp', 'model_and_strokes.h', 'CMakeLists.txt'}
            or hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest() != helper_source['sha256']):
        raise ValueError('Source distribution native-source identity mismatch')
    for name, digest in files.items():
        if PurePosixPath(name).is_absolute() or '..' in PurePosixPath(name).parts or '\\' in name:
            raise ValueError('Unsafe source distribution path')
        checked('zinnia/' + name, digest, 100000, source_path='native/handwriting/' + name)
    return {'verified': True, 'model_source_sha256': SOURCE_SHA256,
            'model_license_sha256': LICENSE_SHA256, 'engine_license_sha256': bsd_hash,
            'patch_sha256': patch_hash, 'native_source_sha256': helper_source['sha256']}


def verify_candidate(root, expected_commit, inspect_payload=False):
    root = Path(root)
    artifacts = root / 'artifacts'
    bundles = list(artifacts.glob(f'InkPick-v{VERSION}.zip'))
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
        source_distribution = verify_handwriting_source_distribution(
            archive, prefix, manifest.get('handwriting', {}), source.get('files', {}))
        name = manifest['executable_name']
        if name != f'InkPick-v{VERSION}.exe':
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
                                              manifest['ocr_validation_fixtures'],
                                              handwriting=manifest.get('handwriting')) if inspect_payload else
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
        report_name = 'frozen-handwriting.json'
        raw = archive.read(prefix + 'candidate-validation/' + report_name)
        if manifest['candidate_probes'][report_name] != {
                'sha256': hashlib.sha256(raw).hexdigest(), 'status': 'PASSED'}:
            raise ValueError('Embedded handwriting probe checksum mismatch')
        check_handwriting_probe(_json(raw), manifest, True)
        probes[report_name] = 'PASSED'
        report_name = 'frozen-handwriting-image.json'
        raw = archive.read(prefix + 'candidate-validation/' + report_name)
        if manifest['candidate_probes'][report_name] != {
                'sha256': hashlib.sha256(raw).hexdigest(), 'status': 'PASSED'}:
            raise ValueError('Embedded handwriting image probe checksum mismatch')
        check_handwriting_image_probe(_json(raw), manifest, True)
        probes[report_name] = 'PASSED'
    gate = _json((artifacts / 'firewall-validation/candidate-gate.json').read_bytes())
    if (gate.get('passed') is not True or gate.get('executable_sha256') != executable_hash.hexdigest()
            or gate.get('version') != VERSION or gate.get('clean_machine_verified') is not False):
        raise ValueError('Outbound-blocked gate mismatch')
    for name in probes:
        path = artifacts / 'firewall-validation' / name
        if name == 'frozen-handwriting.json':
            continue  # The separate gate binds both the outer EXE and extracted helper.
        if gate['probes'][name] != {'sha256': sha256_file(path), 'status': 'PASSED'}:
            raise ValueError('Outbound-blocked probe checksum mismatch')
        if name == 'frozen-handwriting-image.json':
            check_handwriting_image_probe(_json(path.read_bytes()), manifest, True)
        else:
            _check_probe(_json(path.read_bytes()), manifest, True)
    handwriting_path = artifacts / 'firewall-validation/frozen-handwriting.json'
    handwriting_gate = _json((artifacts / 'firewall-validation/handwriting-firewall-gate.json').read_bytes())
    expected_handwriting_gate = {
        'schema': 1, 'passed': True, 'helper_outbound_blocked': True,
        'executable_outbound_blocked': True, 'clean_machine_verified': False,
        'firewall_service_running': True, 'firewall_profiles_enabled': True,
        'helper_sha256': manifest['handwriting']['helper']['sha256'],
        'executable_sha256': executable_hash.hexdigest(), 'version': VERSION,
        'report_sha256': sha256_file(handwriting_path),
    }
    if any(type(handwriting_gate.get(key)) is not type(value) or handwriting_gate.get(key) != value
           for key, value in expected_handwriting_gate.items()):
        raise ValueError('Handwriting outbound-blocked gate mismatch')
    check_handwriting_probe(_json(handwriting_path.read_bytes()), manifest, True, firewall=True)
    for name in ('source-selftest.json', 'source-app-smoke.json'):
        _check_probe(_json((root / name).read_bytes()), manifest, False)
    check_handwriting_probe(_json((root / 'source-handwriting.json').read_bytes()), manifest, False)
    check_handwriting_image_probe(_json((root / 'source-handwriting-image.json').read_bytes()), manifest, False)
    stress_path = artifacts / 'firewall-validation/frozen-runtime-stress.json'
    check_frozen_stress(_json(stress_path.read_bytes()), manifest)
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
            'handwriting_probes': {'source': 'PASSED', 'frozen': 'PASSED',
                                   'outer_and_helper_outbound_blocked': 'PASSED'},
            'handwriting_source_distribution': source_distribution,
            'frozen_runtime_stress': {'status': 'PASSED', 'iterations_per_profile': 500,
                                     'sha256': sha256_file(stress_path)},
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
