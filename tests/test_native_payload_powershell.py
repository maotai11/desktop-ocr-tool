"""Exercise native scan policy/path/process helpers without scanning host data."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/validate_native_payload.ps1'


def powershell():
    command = (os.environ.get('INKPICK_TEST_POWERSHELL') or shutil.which('pwsh')
               or shutil.which('powershell'))
    if not command:
        pytest.skip('PowerShell unavailable; Windows CI executes native policy contracts')
    return command


def run_helpers(tmp_path, body, fixture=None, timeout=20):
    source = SCRIPT.read_text(encoding='utf-8')
    helpers = source.split('# Native payload helper functions:', 1)[1].split(
        '# End native payload helper functions.', 1)[0]
    (tmp_path / 'fixture.json').write_text(json.dumps(fixture), encoding='utf-8')
    harness = tmp_path / 'native-policy.ps1'
    harness.write_text(
        "$ErrorActionPreference = 'Stop'\nSet-StrictMode -Version Latest\n"
        '# Native payload helper functions:' + helpers + '\n'
        "$fixture = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'fixture.json') -Raw -Encoding UTF8 | ConvertFrom-Json\n"
        "Write-Output 'INKPICK_NATIVE_HELPERS_ENTERED'\n" + body, encoding='utf-8')
    environment = dict(os.environ, DOTNET_GCHeapHardLimit='0x10000000')
    for key in ('XDG_CACHE_HOME', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME'):
        directory = tmp_path / key.lower()
        directory.mkdir(exist_ok=True)
        environment[key] = str(directory)
    result = subprocess.run(
        [powershell(), '-NoProfile', '-NonInteractive', '-File', str(harness)],
        text=True, capture_output=True, timeout=timeout, check=False, env=environment)
    assert 'INKPICK_NATIVE_HELPERS_ENTERED' in result.stdout.splitlines(), result.stdout + result.stderr
    return result


@pytest.mark.parametrize('case', [
    'ready', 'passive', 'service_off', 'antivirus_off', 'engine_zero', 'signature_zero',
    'signature_old', 'signature_future', 'samples_allowed', 'maps_enabled',
    'missing_field', 'string_boolean',
])
def test_native_defender_readiness_fails_closed(tmp_path, case):
    status = {'AMServiceEnabled': True, 'AntivirusEnabled': True, 'AMEngineVersion': '1.1.26090.2',
                  'AntivirusSignatureVersion': '1.451.99.0', 'AntivirusSignatureLastUpdated': '2026-11-05T10:00:00Z',
                  'AMRunningMode': 'Normal', 'RealTimeProtectionEnabled': True}
    preference = {'SubmitSamplesConsent': 2, 'MAPSReporting': 0}
    if case == 'passive':
        status.update(AMRunningMode='Passive Mode', RealTimeProtectionEnabled=False)
    elif case == 'service_off':
        status['AMServiceEnabled'] = False
    elif case == 'antivirus_off':
        status['AntivirusEnabled'] = False
    elif case == 'engine_zero':
        status['AMEngineVersion'] = '0.0.0.0'
    elif case == 'signature_zero':
        status['AntivirusSignatureVersion'] = '0.0.0.0'
    elif case == 'signature_old':
        status['AntivirusSignatureLastUpdated'] = '2026-11-01T10:00:00Z'
    elif case == 'signature_future':
        status['AntivirusSignatureLastUpdated'] = '2026-11-05T13:00:00Z'
    elif case == 'samples_allowed':
        preference['SubmitSamplesConsent'] = 1
    elif case == 'maps_enabled':
        preference['MAPSReporting'] = 2
    elif case == 'missing_field':
        del status['AntivirusSignatureLastUpdated']
    elif case == 'string_boolean':
        status['AMServiceEnabled'] = 'true'
    result = run_helpers(tmp_path,
        "$now = [DateTime]::Parse('2026-11-05T12:00:00Z').ToUniversalTime()\n"
        '$ready = Assert-NativeDefenderReadiness $fixture.status $fixture.preference $now\n'
        "$ready | ConvertTo-Json -Compress\n", {'status': status, 'preference': preference})
    assert (result.returncode == 0) is (case in ('ready', 'passive')), result.stdout + result.stderr
    if result.returncode == 0:
        report = json.loads(result.stdout.splitlines()[-1])
        assert report['sample_submission_consent'] == 2 and report['maps_reporting'] == 0
        assert report['real_time_protection_enabled'] is (case == 'ready')
        assert report['scanner_version'] == status['AMEngineVersion']


@pytest.mark.parametrize('case', [
    'valid', 'traversal', 'absolute', 'extra_file', 'missing_file', 'duplicate',
    'directory', 'unapproved_root', 'backslash', 'symlink',
])
def test_native_scan_scope_is_exact_and_has_no_redirects(tmp_path, case):
    root = tmp_path / 'scan'
    files = ['candidate/tool.exe', 'payload/pkg/native.pyd']
    for name in files:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'MZ synthetic bytes')
    paths = list(files)
    if case == 'traversal':
        paths[1] = 'payload/pkg/../native.pyd'
    elif case == 'absolute':
        paths[1] = str(root / files[1])
    elif case == 'extra_file':
        (root / 'unexpected.json').write_text('{}', encoding='utf-8')
    elif case == 'missing_file':
        (root / files[1]).unlink()
    elif case == 'duplicate':
        paths.append(files[1])
    elif case == 'directory':
        paths[1] = 'payload/pkg'
    elif case == 'unapproved_root':
        paths[1] = 'other/native.pyd'
    elif case == 'backslash':
        paths[1] = 'payload\\pkg\\native.pyd'
    elif case == 'symlink':
        (root / files[1]).unlink()
        target = tmp_path / 'outside.pyd'
        target.write_bytes(b'outside')
        try:
            (root / files[1]).symlink_to(target)
        except OSError:
            pytest.skip('Host does not permit symlink fixtures')
    result = run_helpers(tmp_path,
        '$count = Assert-NativeScanScope $fixture.root $fixture.paths\n'
        "Write-Output ('COUNT=' + $count)\n", {'root': str(root), 'paths': paths})
    assert (result.returncode == 0) is (case == 'valid'), result.stdout + result.stderr
    if case == 'valid':
        assert 'COUNT=2' in result.stdout.splitlines()


@pytest.mark.parametrize('case', ['newest', 'foreign_signer', 'invalid_signature', 'missing'])
def test_native_scanner_requires_trusted_signature_and_latest_version(tmp_path, case):
    program_data = tmp_path / 'program-data'
    program_files = tmp_path / 'program-files'
    for version in ('4.18.9.0', '4.18.10.0'):
        path = program_data / 'Microsoft/Windows Defender/Platform' / version / 'MpCmdRun.exe'
        path.parent.mkdir(parents=True)
        if case != 'missing':
            path.write_bytes(b'not executed')
    program_files.mkdir()
    fixture = {'program_data': str(program_data), 'program_files': str(program_files),
                   'status': 'HashMismatch' if case == 'invalid_signature' else 'Valid',
                   'subject': 'CN=Publisher, O=Other Company, C=US' if case == 'foreign_signer'
                   else 'CN=Microsoft Windows Publisher, O=Microsoft Corporation, C=US'}
    result = run_helpers(tmp_path,
        'function Get-AuthenticodeSignature { param([string]$LiteralPath)\n'
        'return [pscustomobject]@{ Status=$fixture.status; SignerCertificate=[pscustomobject]@{Subject=$fixture.subject} }\n}\n'
        '$env:ProgramData = $fixture.program_data\n$env:ProgramFiles = $fixture.program_files\n'
        '$selected = Get-NativeDefenderScanner\nWrite-Output $selected\n', fixture)
    assert (result.returncode == 0) is (case == 'newest'), result.stdout + result.stderr
    if case == 'newest':
        assert '4.18.10.0' in result.stdout.splitlines()[-1]


def test_native_process_roundtrips_literal_arguments_without_shell(tmp_path):
    arguments = ['', 'a b', 'quote"inside', 'trailing\\', 'two\\\\"quote', '中文',
                 '$HOME', '`echo hello`', '$(echo nope)', 'line\nnew']
    output = tmp_path / 'arguments.json'
    code = 'import json,sys; open(sys.argv[1],"w",encoding="utf-8").write(json.dumps(sys.argv[2:]))'
    result = run_helpers(tmp_path,
        '$code = Invoke-NativeBoundedProcess $fixture.program ([string[]]$fixture.arguments) '
        "(Join-Path $PSScriptRoot 'process') 5\nif ($code -ne 0) { throw 'PROCESS_FAILED' }\n",
        {'program': sys.executable, 'arguments': ['-c', code, str(output), *arguments]})
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(output.read_text(encoding='utf-8')) == arguments


def test_native_process_timeout_terminates_owned_work(tmp_path):
    started = time.monotonic()
    result = run_helpers(tmp_path,
        '$code = Invoke-NativeBoundedProcess $fixture.program ([string[]]$fixture.arguments) '
        "(Join-Path $PSScriptRoot 'process') 1\n",
        {'program': sys.executable, 'arguments': ['-c', 'import time; time.sleep(30)']}, timeout=10)
    assert result.returncode != 0
    assert 'NATIVE_PROCESS_TIMEOUT' in result.stderr
    assert time.monotonic() - started < 8


def test_native_threat_metadata_only_counts_the_target_scope(tmp_path):
    root, executable = str(tmp_path / 'scan'), str(tmp_path / 'tool.exe')
    fixture = {'root': root, 'executable': executable, 'detections': [
        {'DetectionID': 'unrelated', 'Resources': [str(tmp_path / 'unrelated.bin')]},
        {'DetectionID': 'candidate', 'Resources': [f'file:_{executable}']},
        {'DetectionID': 'payload', 'Resources': [f'file:_{root}{os.sep}payload{os.sep}native.dll']},
        {'DetectionID': 'payload', 'Resources': [f'file:_{root}{os.sep}payload{os.sep}native.dll']},
    ]}
    result = run_helpers(tmp_path,
        '$ids = Get-NativeScopedThreatIds $fixture.detections $fixture.root $fixture.executable\n'
        "@($ids | Sort-Object) | ConvertTo-Json -Compress\n", fixture)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.splitlines()[-1]) == ['candidate', 'payload']


def test_native_runner_failure_cannot_leave_a_stale_success_report(tmp_path):
    # Invalid bytes fail before any antivirus launch on Windows; Linux refuses
    # the runner altogether. Neither platform executes the candidate file.
    executable = tmp_path / 'not executable.exe'
    executable.write_bytes(b'MZ invalid archive')
    output = tmp_path / 'native-payload.json'
    output.write_text(json.dumps({'schema': 1, 'probe': 'native_payload_audit', 'passed': True}), encoding='utf-8')
    result = subprocess.run(
        [powershell(), '-NoProfile', '-NonInteractive', '-File', str(SCRIPT),
         '-Executable', str(executable), '-Output', str(output), '-SourceCommit', 'a' * 40,
         '-PythonExecutable', sys.executable],
        text=True, capture_output=True, timeout=20, check=False,
        env=dict(os.environ, DOTNET_GCHeapHardLimit='0x10000000'))
    assert result.returncode != 0
    assert json.loads(output.read_text(encoding='utf-8-sig'))['passed'] is False
    assert str(tmp_path) not in result.stdout + result.stderr
    assert not list(tmp_path.glob('*.stdout'))
