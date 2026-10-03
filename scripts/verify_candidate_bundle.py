"""Verify same-run Windows assets and emit a small, transferable evidence file.

The actual ZIP/EXE bytes are checked on the build runner. This does not certify
a clean Windows host or complete Windows system-DLL compatibility.
"""
import argparse
import hashlib
import json
import os
import stat
from pathlib import Path, PurePosixPath, PureWindowsPath
import tempfile
import zipfile
import xml.etree.ElementTree as ET


VERSION = '1.6.2-rc.2'


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
                or report.get('checks') != {'qt': True, 'models': True, 'ocr': True, 'database': True}
                or set(report.get('models', {})) != {'det', 'rec', 'cls'}
                or any(report['models'][k]['sha256'] != manifest['models'][k]['sha256']
                       for k in ('det', 'rec', 'cls'))):
            raise ValueError('OCR/model probe mismatch')
    elif report.get('probe') == 'application_lifecycle':
        if (report.get('phase_a') is not True or report.get('engine_ready') is not True
                or report.get('shutdown_clean') is not True
                or report.get('threads_stopped') !=
                {'capture': True, 'ocr': True, 'database': True, 'hotkeys': True}):
            raise ValueError('Incomplete lifecycle probe')
    else:
        raise ValueError('Unknown probe kind')


def inspect_frozen_payload(path, models):
    from PyInstaller.archive.readers import CArchiveReader
    reader = CArchiveReader(str(path))
    names = {name.replace('\\', '/'): name for name in reader.toc}
    lower = [name.lower() for name in names]
    required = ('python312.dll', 'qwindows.dll', 'onnxruntime_pybind11_state.pyd')
    for name in required:
        if not any(value.endswith('/' + name) or value == name for value in lower):
            raise ValueError(f'Frozen dependency absent: {name}')
    for info in models.values():
        key = names.get(info['path'].replace('\\', '/'))
        if not key or hashlib.sha256(reader.extract(key)).hexdigest() != info['sha256']:
            raise ValueError('Frozen ONNX asset hash mismatch')
    return {'inspected': True, 'entry_count': len(names), 'entries': sorted(names),
            'dlls': sorted(name for name in names if name.lower().endswith(('.dll', '.pyd'))),
            'required_payload': list(required), 'embedded_model_hashes_verified': True,
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
            payload = (inspect_frozen_payload(executable, manifest['models']) if inspect_payload else
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
