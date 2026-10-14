"""Build-only transport/installed-tool fixes; no network calls in these tests."""
import hashlib
import io
import os
from pathlib import Path
import ssl
import subprocess
import sys
import urllib.error
import urllib.request

import pytest

from scripts import prepare_handwriting_model as models
from scripts import build_handwriting_helper as build_helper
from scripts.build_handwriting_helper import merge_command_environment, vs_environment_command


def fake_download(monkeypatch, events):
    payload = b'fixed official archive test bytes'
    monkeypatch.setattr(models, 'ARCHIVE_BYTES', len(payload))
    monkeypatch.setattr(models, 'ARCHIVE_SHA256', hashlib.sha256(payload).hexdigest())
    calls, delays = [], []

    class Opener:
        def open(self, source, timeout):
            assert source == models.SOURCE and source.startswith('https://')
            assert timeout == 30
            calls.append(source)
            event = events.pop(0)
            if isinstance(event, BaseException):
                raise event
            return io.BytesIO(payload if event == 'valid' else event)

    monkeypatch.setattr(models.urllib.request, 'build_opener', lambda *handlers: Opener())
    monkeypatch.setattr(models.time, 'sleep', delays.append)
    return payload, calls, delays


def test_transient_redirect_reset_retries_then_verifies_exact_bytes(monkeypatch):
    payload, calls, delays = fake_download(monkeypatch, [
        urllib.error.URLError(ConnectionResetError(10054, 'test reset')), 'valid'])
    assert models.download_archive() == payload
    assert len(calls) == 2 and delays == [1]


def test_incomplete_transport_retries_without_accepting_partial_data(monkeypatch):
    payload, calls, delays = fake_download(monkeypatch, [b'partial', 'valid'])
    assert models.download_archive() == payload
    assert len(calls) == 2 and delays == [1]


def test_transport_attempts_are_bounded(monkeypatch):
    _, calls, delays = fake_download(monkeypatch, [ConnectionResetError()] * 3)
    with pytest.raises(ConnectionResetError):
        models.download_archive()
    assert len(calls) == 3 and delays == [1, 2]


def test_streaming_monotonic_deadline_rejects_before_read(monkeypatch):
    _, calls, delays = fake_download(monkeypatch, ['valid'] * 3)
    clock = iter([0, 121, 200, 321, 400, 521])
    monkeypatch.setattr(models.time, 'monotonic', lambda: next(clock))
    with pytest.raises(TimeoutError, match='download deadline exceeded'):
        models.download_archive()
    assert len(calls) == 3 and delays == [1, 2]


@pytest.mark.parametrize('code', [408, 429, 500, 502, 503, 504])
def test_only_transient_http_status_retried(monkeypatch, code):
    error = urllib.error.HTTPError(models.SOURCE, code, 'test', {}, None)
    payload, calls, delays = fake_download(monkeypatch, [error, 'valid'])
    assert models.download_archive() == payload
    assert len(calls) == 2 and delays == [1]


@pytest.mark.parametrize('error', [
    urllib.error.URLError(ssl.SSLCertVerificationError('test certificate')),
    urllib.error.URLError(ssl.SSLError('test TLS protocol error')),
    urllib.error.HTTPError(models.SOURCE, 403, 'test', {}, None),
    urllib.error.HTTPError(models.SOURCE, 404, 'test', {}, None),
])
def test_security_and_permanent_failures_are_not_retried(monkeypatch, error):
    _, calls, delays = fake_download(monkeypatch, [error])
    with pytest.raises(type(error)):
        models.download_archive()
    assert len(calls) == 1 and not delays


@pytest.mark.parametrize('extra', [b'', b'x'])
def test_integrity_failure_never_retried(monkeypatch, extra):
    bad = b'?' * len(b'fixed official archive test bytes') + extra
    _, calls, delays = fake_download(monkeypatch, [bad])
    with pytest.raises(ValueError, match='hash/size mismatch'):
        models.download_archive()
    assert len(calls) == 1 and not delays


def test_https_redirect_cannot_downgrade_transport():
    handler = models.HttpsOnlyRedirect()
    request = urllib.request.Request(models.SOURCE)
    with pytest.raises(ValueError, match='retain HTTPS'):
        handler.redirect_request(request, None, 302, 'test', {}, 'http://example.com/archive')
    redirect = handler.redirect_request(request, None, 302, 'test', {}, 'https://example.com/archive')
    assert redirect.full_url.startswith('https://')
    assert handler.max_redirections == 3


def test_cmd_raw_command_preserves_quoted_installed_path():
    cmd = r'C:\Windows\System32\cmd.exe'
    setup = r'C:\Program Files\Microsoft Visual Studio\18\Enterprise\Common7\Tools\VsDevCmd.bat'
    result = vs_environment_command(cmd, setup)
    assert result == f'"{cmd}" /d /u /s /c "call "{setup}" -no_logo -arch=x64 -host_arch=x64 >nul && set"'
    assert '\\"' not in result


def test_vs_environment_path_case_replaces_original_path():
    env = {'PATH': 'old-path'}
    merge_command_environment(env, 'Path=new-compiler-path\nINCLUDE=C:\\含字=value\n=C:=C:\\ignored\n')
    assert env == {'PATH': 'new-compiler-path', 'INCLUDE': 'C:\\含字=value'}


def test_compiler_environment_uses_new_case_insensitive_path(monkeypatch):
    monkeypatch.setattr(build_helper.sys, 'platform', 'win32')
    monkeypatch.setenv('ProgramFiles(x86)', r'C:\Program Files (x86)')
    monkeypatch.setenv('SystemRoot', r'C:\Windows')
    monkeypatch.setenv('PATH', 'old-path')
    monkeypatch.setattr(build_helper.shutil, 'which', lambda *args, **kwargs: None)
    calls = []

    def output(command, **kwargs):
        calls.append((command, kwargs))
        if len(calls) == 1:
            return 'C:\\Program Files\\Microsoft Visual Studio\\18\\Enterprise\n'
        assert isinstance(command, str) and '\\"' not in command
        assert kwargs['encoding'] == 'utf-16le' and kwargs['timeout'] == 30
        return 'Path=compiler-path\nINCLUDE=C:\\含字\n'

    monkeypatch.setattr(build_helper.subprocess, 'check_output', output)
    env = build_helper.compiler_environment()
    assert env['PATH'] == 'compiler-path' and env['INCLUDE'] == 'C:\\含字'
    assert len(calls) == 2


@pytest.mark.parametrize('character', list('"%&|<>^!\r\n'))
def test_compiler_path_rejects_shell_expansion(character):
    with pytest.raises(ValueError, match='Unsafe installed compiler path'):
        vs_environment_command(r'C:\Windows\System32\cmd.exe', 'C:\\bad' + character + 'path.bat')


@pytest.mark.skipif(sys.platform != 'win32', reason='Actual CMD path-with-spaces quote execution')
def test_windows_cmd_executes_installed_style_path_with_spaces(tmp_path):
    directory = tmp_path / 'compiler setup with spaces'
    directory.mkdir()
    setup = directory / 'VsDevCmd.bat'
    setup.write_text('@echo off\nset "INKPICK_COMPILER_QUOTE_TEST=path-with-spaces-ok"\n', encoding='ascii')
    cmd = Path(os.environ['SystemRoot']) / 'System32/cmd.exe'
    output = subprocess.check_output(vs_environment_command(cmd, setup), executable=str(cmd),
                                     timeout=5, encoding='utf-16le')
    assert 'INKPICK_COMPILER_QUOTE_TEST=path-with-spaces-ok' in output.splitlines()
