import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from src.core import single_instance as instance


@pytest.fixture(autouse=True)
def release_lock_after_test():
    instance.release_instance_lock()
    yield
    instance.release_instance_lock()


def test_interprocess_lock_releases_on_normal_close_and_process_exit(tmp_path):
    # A real child process exercises the platform's lock, not a ctypes mock.
    environment = os.environ.copy()
    if os.name == 'posix':
        environment.update(TMPDIR=str(tmp_path), TEMP=str(tmp_path), TMP=str(tmp_path))
    code = ('from src.core.single_instance import acquire_instance_lock; import sys; '
            'print(acquire_instance_lock(), flush=True); sys.stdin.readline()')
    process = subprocess.Popen([sys.executable, '-c', code], env=environment,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == 'True'
        # Use another child so the private temp directory is identical.
        check = subprocess.run([sys.executable, '-c',
            'from src.core.single_instance import acquire_instance_lock; print(acquire_instance_lock())'],
            env=environment, capture_output=True, text=True, timeout=10, check=False)
        assert check.returncode == 0 and check.stdout.strip() == 'False'
        process.stdin.write('\n'); process.stdin.flush()
        process.wait(timeout=10)
        check = subprocess.run([sys.executable, '-c',
            ('from src.core.single_instance import acquire_instance_lock, release_instance_lock; '
             'print(acquire_instance_lock()); release_instance_lock(); print(acquire_instance_lock())')],
            env=environment, capture_output=True, text=True, timeout=10, check=False)
        assert check.returncode == 0 and check.stdout.splitlines() == ['True', 'True']
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)


@pytest.mark.skipif(os.name != 'posix', reason='POSIX lock path safety')
def test_posix_rejects_lock_file_symlink(tmp_path, monkeypatch):
    directory = tmp_path / f'desktop-ocr-{os.getuid()}'
    directory.mkdir(mode=0o700)
    target = tmp_path / 'target'
    target.write_text('do not touch')
    (directory / 'instance.lock').symlink_to(target)
    monkeypatch.setattr(instance.tempfile, 'gettempdir', lambda: str(tmp_path))
    with pytest.raises(OSError):
        instance.acquire_instance_lock()
    assert target.read_text() == 'do not touch'


@pytest.mark.skipif(os.name != 'posix', reason='POSIX lock path safety')
def test_posix_rejects_shared_lock_directory(tmp_path, monkeypatch):
    directory = tmp_path / f'desktop-ocr-{os.getuid()}'
    directory.mkdir(mode=0o700)
    directory.chmod(0o777)
    monkeypatch.setattr(instance.tempfile, 'gettempdir', lambda: str(tmp_path))
    with pytest.raises(PermissionError, match='Unsafe'):
        instance.acquire_instance_lock()


@pytest.mark.parametrize('already_exists', [False, True])
def test_windows_mutex_last_error_and_handle_lifetime(monkeypatch, already_exists):
    closed = []
    kernel = SimpleNamespace(CreateMutexW=lambda *args: 123,
                             CloseHandle=lambda handle: closed.append(handle) or True)
    monkeypatch.setattr(instance, '_windows_api', lambda: kernel)
    monkeypatch.setattr(instance.sys, 'platform', 'win32')
    monkeypatch.setattr(instance.ctypes, 'set_last_error', lambda value: None, raising=False)
    monkeypatch.setattr(instance.ctypes, 'get_last_error', lambda: 183 if already_exists else 0, raising=False)
    assert instance.acquire_instance_lock() is not already_exists
    if not already_exists:
        assert instance.acquire_instance_lock() is True
        assert closed == []
        instance.release_instance_lock()
    assert closed == [123]
    instance.release_instance_lock()
    assert closed == [123]


def test_smoke_startup_error_releases_lock_and_does_not_open_modal(qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from src import app
    from src.core import config, logger
    events = []
    monkeypatch.setattr(logger, 'setup_logger', lambda: None)
    cfg = SimpleNamespace(get=lambda *args, default=None: default,
                          get_data_directory=lambda: str(tmp_path / 'unwritable' / 'data'))
    monkeypatch.setattr(config, 'get_config', lambda: cfg)
    monkeypatch.setattr(instance, 'acquire_instance_lock', lambda: events.append('acquire') or True)
    monkeypatch.setattr(instance, 'release_instance_lock', lambda: events.append('release'))
    monkeypatch.setattr(app, '_setup_font', lambda *args: None)
    monkeypatch.setattr(QMessageBox, 'critical', lambda *args: pytest.fail('smoke opened a modal dialog'))
    report = tmp_path / 'smoke.json'
    assert app.main(smoke_report=report) == 1
    import json
    result = json.loads(report.read_text(encoding='utf-8'))
    assert result['passed'] is False and result['phase_a'] is False
    assert '無法寫入' in result['error']
    assert events == ['acquire', 'release']
