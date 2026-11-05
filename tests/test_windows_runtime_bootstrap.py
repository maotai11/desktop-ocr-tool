"""Bootstrap trust/extraction contracts; fake child calls are not Windows proof."""
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import tarfile
import subprocess
import sys
import ssl

import pytest

from scripts import prepare_windows_runtime as runtime


def archive(tmp_path, members):
    path = tmp_path / 'fixture.tar.gz'
    with tarfile.open(path, 'w:gz') as output:
        for name, content, kind in members:
            item = tarfile.TarInfo(name)
            item.type = kind
            if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
                item.linkname = '../../outside'
            item.size = len(content) if kind == tarfile.REGTYPE else 0
            output.addfile(item, io.BytesIO(content) if item.size else None)
    lock = {'archive_size': path.stat().st_size, 'archive_sha256': runtime.file_digest(path)}
    return path, lock


def member(name, content=b'content', kind=tarfile.REGTYPE):
    return name, content, kind


@pytest.mark.parametrize('name', ['other/x', '/python/x', 'python/../escape', 'python/C:/evil',
    'python/x:stream', 'python/\\server\\share', 'python/NUL.txt', 'python/CONIN$',
    'python/COM1', 'python/COM¹.txt', 'python/end.', 'python/end ', 'python/a//b'])
def test_unsafe_archive_paths_rejected_before_any_file_is_created(tmp_path, name):
    path, lock = archive(tmp_path, [member('python/valid.txt'), member(name)])
    target = tmp_path / 'runtime'
    with pytest.raises(ValueError):
        runtime.extract_archive(path, target, lock)
    assert not target.exists()


@pytest.mark.parametrize('members', [
    [member('python/a'), member('python/A')],
    [member('python/Dir/a'), member('python/dir/b')],
    [member('python/file'), member('python/file/child')],
    [member('python/file/child'), member('python/file')],
    [member('python/a', kind=tarfile.SYMTYPE)],
    [member('python/a', kind=tarfile.LNKTYPE)],
    [member('python/a', kind=tarfile.FIFOTYPE)],
    [member('python')],
])
def test_links_duplicates_aliases_and_type_collisions_fail_before_extract(tmp_path, members):
    path, lock = archive(tmp_path, members)
    target = tmp_path / 'runtime'
    with pytest.raises(ValueError):
        runtime.extract_archive(path, target, lock)
    assert not target.exists()


@pytest.mark.parametrize('limit,value', [('MAX_MEMBERS', 1), ('MAX_FILE_BYTES', 3), ('MAX_TOTAL_BYTES', 7)])
def test_archive_limits_are_applied_before_writing(tmp_path, monkeypatch, limit, value):
    path, lock = archive(tmp_path, [member('python/a', b'1234'), member('python/b', b'5678')])
    monkeypatch.setattr(runtime, limit, value)
    with pytest.raises(ValueError):
        runtime.extract_archive(path, tmp_path / 'runtime', lock)
    assert not (tmp_path / 'runtime').exists()


def test_hash_is_verified_before_parsing_untrusted_archive(tmp_path):
    path = tmp_path / 'bad.tar.gz'
    path.write_bytes(b'not a tar archive')
    with pytest.raises(ValueError, match='hash or size'):
        runtime.extract_archive(path, tmp_path / 'runtime', {'archive_size': path.stat().st_size,
            'archive_sha256': '0' * 64})


def test_regular_archive_strips_exact_python_prefix(tmp_path):
    path, lock = archive(tmp_path, [member('python', kind=tarfile.DIRTYPE),
                                   member('python/DLLs', kind=tarfile.DIRTYPE),
                                   member('python/DLLs/test.pyd', b'MZ' + b'x' * 64)])
    root = tmp_path / 'runtime'
    runtime.extract_archive(path, root, lock)
    assert (root / 'DLLs/test.pyd').read_bytes() == b'MZ' + b'x' * 64
    assert not (root / 'python').exists()


@pytest.mark.parametrize('defect', ['url', 'provider', 'python_version', 'openssl_version',
                                  'archive_sha256', 'file_hash', 'file_size', 'relative', 'case_alias'])
def test_lock_rejects_unapproved_identity_or_malformed_native_map(tmp_path, defect):
    lock = runtime.load_runtime_lock()
    if defect in ('provider', 'python_version', 'openssl_version', 'archive_sha256'):
        lock[defect] = 'unapproved'
    elif defect == 'url': lock['archive_url'] = 'https://example.com/attacker.tar.gz'
    elif defect == 'file_hash': lock['files']['python.exe']['sha256'] = 'invalid'
    elif defect == 'file_size': lock['files']['python.exe']['size_bytes'] = True
    elif defect == 'relative': lock['files']['../python.exe'] = lock['files'].pop('python.exe')
    elif defect == 'case_alias': lock['files']['PYTHON.EXE'] = deepcopy(lock['files']['python.exe'])
    path = tmp_path / 'lock.json'
    path.write_text(json.dumps(lock))
    with pytest.raises(ValueError):
        runtime.load_runtime_lock(path)


def test_every_native_file_is_measured_and_only_wheel_owned_area_is_mutable(tmp_path):
    core, pip = b'MZ' + b'core' * 20, b'MZ' + b'pip' * 20
    for name, data in [('python.exe', core), ('Lib/site-packages/pip/tool.exe', pip)]:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    lock = {'files': {name: {'sha256': hashlib.sha256(data).hexdigest(), 'size_bytes': len(data)}
                     for name, data in [('python.exe', core), ('Lib/site-packages/pip/tool.exe', pip)]}}
    assert len(runtime.verify_native_files(tmp_path, lock)) == 2
    (tmp_path / 'Lib/site-packages/pip/tool.exe').write_bytes(b'MZnew wheel launcher')
    runtime.verify_native_files(tmp_path, lock, immutable_only=True)
    with pytest.raises(ValueError): runtime.verify_native_files(tmp_path, lock)
    (tmp_path / 'python.exe').write_bytes(core + b'changed')
    with pytest.raises(ValueError): runtime.verify_native_files(tmp_path, lock, immutable_only=True)


@pytest.mark.parametrize('defect', ['python_version', 'openssl_version', 'hashlib_ok', 'executable', 'ssl_module'])
def test_smoke_requires_exact_runtime_library_and_executable(tmp_path, monkeypatch, defect):
    root = tmp_path.resolve()
    result = {'python_version': '3.12.16', 'openssl_version': '3.5.9',
        'openssl_library': 'OpenSSL 3.5.9 29 Sep 2026', 'hashlib_ok': True,
        'executable': str(root / 'python.exe'), 'prefix': str(root), 'base_prefix': str(root),
        'ssl_module': str(root / 'DLLs/_ssl.pyd'), 'hashlib_module': str(root / 'DLLs/_hashlib.pyd')}
    if defect == 'hashlib_ok': result[defect] = False
    else: result[defect] = 'wrong runtime'
    monkeypatch.setattr(runtime.subprocess, 'run', lambda *a, **k: SimpleNamespace(stdout=json.dumps(result).encode()))
    with pytest.raises(ValueError): runtime.smoke_runtime(root, runtime.load_runtime_lock())


def test_smoke_program_executes_in_real_isolated_host_process():
    # Exercise the actual child source; mocked subprocess responses hide syntax failures.
    completed = subprocess.run([sys.executable, '-I', '-c', runtime.SMOKE],
                               capture_output=True, timeout=30, check=True)
    result = json.loads(completed.stdout)
    assert result['python_version'] == '.'.join(map(str, sys.version_info[:3]))
    assert result['hashlib_ok'] is True
    assert result['openssl_version'] == '.'.join(map(str, ssl.OPENSSL_VERSION_INFO[:3]))
    assert result['openssl_library'] == ssl.OPENSSL_VERSION
    assert set(result) == {'python_version', 'openssl_version', 'openssl_library',
                          'executable', 'prefix', 'base_prefix', 'ssl_module',
                          'hashlib_module', 'hashlib_ok'}


def test_existing_destination_replaces_stale_pass_without_deleting_user_files(tmp_path):
    target = tmp_path / 'existing'
    target.mkdir()
    keep = target / 'keep.txt'
    keep.write_text('keep me')
    report = tmp_path / 'report.json'
    report.write_text('{"passed":true}')
    with pytest.raises(ValueError):
        runtime.prepare(target, report, environment={'RUNNER_TEMP': str(tmp_path)})
    assert keep.read_text() == 'keep me'
    assert json.loads(report.read_bytes())['passed'] is False


def test_prepare_runs_only_verified_destination_python_and_exports_after_checks(tmp_path, monkeypatch):
    data = b'MZ' + b'fake runtime' * 10
    path, minimal = archive(tmp_path, [member('python/python.exe', data)])
    lock = dict(runtime.IDENTITY, **minimal, files={'python.exe': {
        'sha256': hashlib.sha256(data).hexdigest(), 'size_bytes': len(data)}})
    monkeypatch.setattr(runtime, 'load_runtime_lock', lambda: lock)
    monkeypatch.setattr(runtime, 'download_archive', lambda destination, _: destination.write_bytes(path.read_bytes()))
    calls = []
    monkeypatch.setattr(runtime, 'smoke_runtime', lambda root, _: calls.append(('smoke', root)))
    def run(arguments, **kwargs):
        calls.append(('run', arguments, kwargs))
        assert arguments[0] == str(tmp_path / 'runtime/python.exe') and arguments[1] == '-I'
        assert kwargs['env']['PIP_NO_INDEX'] == '1'
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(runtime.subprocess, 'run', run)
    path_file, env_file = tmp_path / 'github-path', tmp_path / 'github-env'
    path_file.write_text('existing\n'); env_file.write_text('EXISTING=1\n')
    result = runtime.prepare(tmp_path / 'runtime', tmp_path / 'inventory.json', environment={
        'RUNNER_TEMP': str(tmp_path), 'GITHUB_ACTIONS': 'true', 'GITHUB_PATH': str(path_file),
        'GITHUB_ENV': str(env_file), 'GITHUB_SHA': 'a' * 40})
    assert result['passed'] and result['environment_exported'] and result['ensurepip_completed']
    assert len(calls) == 3 and calls[0][0] == 'smoke'
    assert '-m' in calls[1][1] and 'ensurepip' in calls[1][1]
    assert path_file.read_text().splitlines() == ['existing', str(tmp_path / 'runtime'), str(tmp_path / 'runtime/Scripts')]
    assert 'pythonLocation=' + str(tmp_path / 'runtime') in env_file.read_text()
    assert result['source_commit'] == 'a' * 40 and str(tmp_path) not in json.dumps(result)


def test_main_preflight_failure_cannot_leave_stale_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime.sys, 'platform', 'linux')
    report = tmp_path / 'report.json'
    report.write_text('{"passed":true}')
    assert runtime.main(['--destination', str(tmp_path / 'runtime'), '--report', str(report)]) == 1
    assert json.loads(report.read_bytes()) == {'schema': 1, 'probe': 'windows_runtime_preparation',
        'platform': 'win32', 'passed': False, 'error_code': 'WINDOWS_RUNTIME_PREPARATION_FAILED'}


def test_main_preserves_earned_preparation_failure_flags(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime.sys, 'platform', 'win32')
    report = tmp_path / 'report.json'
    report.write_text('{"passed":true}')
    earned = {'schema': 1, 'probe': 'windows_runtime_preparation', 'platform': 'win32',
              'passed': False, 'download_verified': True, 'extraction_verified': True,
              'runtime_version_verified': True, 'ensurepip_completed': False,
              'error_code': 'WINDOWS_RUNTIME_PREPARATION_FAILED'}
    def failed_prepare(destination, report_path):
        assert json.loads(report_path.read_bytes())['passed'] is False
        report_path.write_text(json.dumps(earned))
        raise ValueError('private diagnostic must not be exported')
    monkeypatch.setattr(runtime, 'prepare', failed_prepare)
    assert runtime.main(['--destination', str(tmp_path / 'runtime'), '--report', str(report)]) == 1
    assert json.loads(report.read_bytes()) == earned


def test_main_lock_failure_before_prepare_report_still_replaces_old_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime.sys, 'platform', 'win32')
    report = tmp_path / 'report.json'
    report.write_text('{"passed":true}')
    def bad_prepare(*args):
        raise ValueError('lock preflight failed')
    monkeypatch.setattr(runtime, 'prepare', bad_prepare)
    assert runtime.main(['--destination', str(tmp_path / 'runtime'), '--report', str(report)]) == 1
    assert json.loads(report.read_bytes())['passed'] is False


def test_post_ensurepip_launchers_are_tools_not_immutable_runtime(tmp_path, monkeypatch):
    data = b'MZ' + b'fake runtime' * 10
    path, minimal = archive(tmp_path, [member('python/python.exe', data)])
    lock = dict(runtime.IDENTITY, **minimal, files={'python.exe': {
        'sha256': hashlib.sha256(data).hexdigest(), 'size_bytes': len(data)}})
    monkeypatch.setattr(runtime, 'load_runtime_lock', lambda: lock)
    monkeypatch.setattr(runtime, 'download_archive', lambda destination, _: destination.write_bytes(path.read_bytes()))
    monkeypatch.setattr(runtime, 'smoke_runtime', lambda *args: None)
    target = tmp_path / 'runtime'
    def ensurepip(arguments, **kwargs):
        if 'ensurepip' in arguments:
            scripts = target / 'Scripts'
            scripts.mkdir()
            for name in ('pip.exe', 'pip3.exe', 'pip3.12.exe'):
                (scripts / name).write_bytes(b'MZgenerated console launcher')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(runtime.subprocess, 'run', ensurepip)
    result = runtime.prepare(target, tmp_path / 'inventory.json', environment={'RUNNER_TEMP': str(tmp_path)})
    assert result['passed'] and result['ensurepip_completed']
    assert (target / 'Scripts/pip.exe').exists()
    # Initial extraction remains exact; tool exclusion is only post-ensurepip.
    with pytest.raises(ValueError):
        runtime.verify_native_files(target, lock)
    (target / 'DLLs').mkdir()
    (target / 'DLLs/unknown.pyd').write_bytes(b'MZunapproved DLL')
    with pytest.raises(ValueError):
        runtime.verify_native_files(target, lock, immutable_only=True)
