"""Acceptance-gate unit contracts, not a Windows clean-machine certification."""
import hashlib
import json
import subprocess
import sys
import zipfile

import pytest

from scripts import build
from scripts.record_dependency_inventory import inventory_wheels
from src.core.validation_report import write_validation_report


@pytest.mark.parametrize('fail', [False, True])
def test_smoke_integrity_connection_closes_on_success_and_failure(tmp_path, monkeypatch, fail):
    from src.core import validation_report
    events = []

    class Connection:
        def execute(self, statement):
            assert statement == 'PRAGMA integrity_check'
            if fail:
                raise RuntimeError('injected verification failure')
            return self

        def fetchone(self):
            return ('ok',)

        def close(self):
            events.append('closed')

    def connect(database_uri, **kwargs):
        assert database_uri.endswith('/test.db?mode=ro')
        assert kwargs == {'uri': True}
        return Connection()

    monkeypatch.setattr(validation_report.sqlite3, 'connect', connect)
    if fail:
        with pytest.raises(RuntimeError, match='injected'):
            validation_report.database_integrity(tmp_path / 'test.db')
    else:
        assert validation_report.database_integrity(tmp_path / 'test.db') == 'ok'
    assert events == ['closed']


@pytest.mark.parametrize('version', ['../victim', '..', '1.2.3/../../data', '', '1.2.3\\data'])
def test_release_version_cannot_escape_outputs(tmp_path, monkeypatch, version):
    monkeypatch.setattr(build, 'APP_VERSION', version)
    with pytest.raises(ValueError, match='Invalid release version'):
        build.resolve_release_paths(tmp_path)


def test_cross_build_refusal_does_not_erase_existing_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr(build.sys, 'platform', 'linux')
    monkeypatch.setattr(build, 'ROOT', tmp_path)
    marker = tmp_path / 'artifacts' / 'build' / 'keep.txt'
    marker.parent.mkdir(parents=True)
    marker.write_text('previous build')
    assert build.main() == 1
    assert marker.read_text() == 'previous build'


def test_reset_refuses_symlink_outputs_before_removing_anything(tmp_path):
    paths = build.resolve_release_paths(tmp_path)
    paths['artifacts_dir'].mkdir()
    paths['dist_dir'].mkdir()
    marker = paths['dist_dir'] / 'keep.txt'
    marker.write_text('previous build')
    target = tmp_path / 'outside'
    target.mkdir()
    try:
        paths['release_dir'].symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip('OS does not permit unprivileged test symlinks')
    with pytest.raises(RuntimeError, match='symlink'):
        build.reset_output_dirs(paths)
    assert marker.exists()


def test_version_hook_bakes_build_version(tmp_path, monkeypatch):
    monkeypatch.setattr(build, 'APP_VERSION', '2.3.4-test.5')
    paths = build.resolve_release_paths(tmp_path)
    build.reset_output_dirs(paths)
    build.write_version_hook(paths)
    source = paths['version_hook'].read_text()
    assert 'os.environ["DESKTOP_OCR_VERSION"] = "2.3.4-test.5"' in source
    assert str(paths['version_hook']) in build.pyinstaller_command(tmp_path, paths)


def valid_reports(paths):
    common = {'schema': 2, 'passed': True, 'frozen': True, 'platform': 'win32', 'qt_platform': 'windows',
                  'version': build.APP_VERSION, 'executable_sha256': build.sha256_file(paths['out_exe']),
                  'clean_machine_verified': False, 'database_integrity': 'ok'}
    return {
        '--self-test': dict(common, probe='ocr_database', telemetry_env='1',
                            models=json.loads((build.ROOT / 'models/models.lock.json').read_text()),
                            checks={'qt': True, 'models': True, 'ocr': True, 'database': True}),
        '--smoke-app': dict(common, probe='application_lifecycle', phase_a=True,
                            engine_ready=True, shutdown_clean=True,
                            threads_stopped={'capture': True, 'ocr': True, 'database': True, 'hotkeys': True}),
    }


@pytest.mark.parametrize('defect', ['none', 'wrong_hash', 'not_frozen', 'wrong_version',
                                  'offscreen', 'model_hash', 'thread_running', 'claimed_clean'])
def test_exact_executable_candidate_gate(tmp_path, monkeypatch, defect):
    paths = build.resolve_release_paths(tmp_path)
    build.reset_output_dirs(paths)
    paths['out_exe'].write_bytes(b'candidate-executable')
    reports = valid_reports(paths)
    if defect == 'wrong_hash': reports['--self-test']['executable_sha256'] = '0' * 64
    if defect == 'not_frozen': reports['--self-test']['frozen'] = False
    if defect == 'wrong_version': reports['--self-test']['version'] = '0.0.0'
    if defect == 'offscreen': reports['--smoke-app']['qt_platform'] = 'offscreen'
    if defect == 'model_hash': reports['--self-test']['models']['rec']['sha256'] = '0' * 64
    if defect == 'thread_running': reports['--smoke-app']['threads_stopped']['ocr'] = False
    if defect == 'claimed_clean': reports['--smoke-app']['clean_machine_verified'] = True
    def run(executable, mode, report_path):
        write_validation_report(report_path, reports[mode])
        return reports[mode]
    monkeypatch.setattr(build, '_run_probe', run)
    if defect == 'none':
        assert set(build.validate_candidate(paths)) == {'frozen-selftest.json', 'frozen-app-smoke.json'}
    else:
        with pytest.raises(RuntimeError):
            build.validate_candidate(paths)


def test_source_identity_covers_dirty_file_bytes(tmp_path, monkeypatch):
    path = tmp_path / 'source.py'
    path.write_text('old')
    def git(args, **kwargs):
        command = args[1:]
        if command == ['rev-parse', 'HEAD']: return b'a' * 40 + b'\n'
        if command == ['rev-parse', 'HEAD^{tree}']: return b'b' * 40 + b'\n'
        if command[0] == 'ls-files': return b'source.py\0'
        return b' M source.py\n'
    monkeypatch.setattr(build.subprocess, 'check_output', git)
    before = build.source_manifest(tmp_path)
    path.write_text('new')
    after = build.source_manifest(tmp_path)
    assert before['commit'] == after['commit'] == 'a' * 40
    assert before['head_tree'] == 'b' * 40
    assert before['working_tree_sha256'] != after['working_tree_sha256']
    assert before['dirty'] is True


def test_package_contains_provenance_and_explicit_remaining_gates(tmp_path):
    paths = build.resolve_release_paths(tmp_path)
    build.reset_output_dirs(paths)
    paths['out_exe'].write_bytes(b'candidate-executable')
    source = {'commit': 'a' * 40, 'head_tree': 'b' * 40, 'working_tree_sha256': 'c' * 64,
                  'dirty': True, 'files': {'src/main.py': {'sha256': 'd' * 64}}}
    build.package_release(paths, source=source)
    manifest = json.loads((paths['release_dir'] / 'BUILD_MANIFEST.json').read_text())
    assert manifest['executable_sha256'] == hashlib.sha256(b'candidate-executable').hexdigest()
    assert manifest['source']['working_tree_sha256'] == 'c' * 64
    assert manifest['source_manifest_sha256'] == build.sha256_file(paths['release_dir'] / 'SOURCE_MANIFEST.json')
    assert manifest['candidate_probes'] == 'NOT_RUN'
    for key in ('clean_machine_gate', 'mixed_dpi_gate', 'dependency_hash_lock', 'dependency_license_review'):
        assert manifest[key] == 'NOT_RUN'
    with zipfile.ZipFile(paths['zip_path']) as archive:
        names = archive.namelist()
        assert any(name.endswith('/Validate-Candidate.ps1') for name in names)
        assert any(name.endswith('/SOURCE_MANIFEST.json') for name in names)


def test_atomic_report_does_not_replace_previous_report_on_invalid_values(tmp_path):
    path = tmp_path / 'report.json'
    write_validation_report(path, {'passed': False})
    with pytest.raises(ValueError):
        write_validation_report(path, {'passed': True, 'value': float('nan')})
    assert json.loads(path.read_text()) == {'passed': False}
    assert list(tmp_path.iterdir()) == [path]


def wheel(directory, filename, name='Example', version='1.0'):
    path = directory / filename
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('example-1.0.dist-info/METADATA', f'Name: {name}\nVersion: {version}\n')
    return path


def test_wheel_inventory_records_exact_input_hashes_and_not_trust(tmp_path):
    path = wheel(tmp_path, 'example-1.0-py3-none-any.whl')
    [entry] = inventory_wheels(tmp_path)
    assert entry['sha256'] == build.sha256_file(path)
    assert entry['name'] == 'Example'
    assert entry['license_review'] == 'NOT_RUN'


def test_wheel_inventory_rejects_empty_or_duplicate_distributions(tmp_path):
    with pytest.raises(ValueError, match='empty'):
        inventory_wheels(tmp_path)
    wheel(tmp_path, 'example-1.0-py3-none-any.whl', name='Example_pkg')
    wheel(tmp_path, 'example-2.0-py3-none-any.whl', name='example-pkg', version='2.0')
    with pytest.raises(ValueError, match='Duplicate'):
        inventory_wheels(tmp_path)


@pytest.mark.parametrize('args', [['--self-test'], ['--smoke-app'],
                                  ['--self-test', 'x', '--smoke-app', 'y'], ['--unknown']])
def test_invalid_probe_cli_never_starts_application(args):
    process = subprocess.run([sys.executable, 'src/main.py', *args],
                             capture_output=True, text=True, timeout=10, cwd=build.ROOT, check=False)
    assert process.returncode == 2
    assert 'error:' in process.stderr
