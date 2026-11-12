"""Windows regression for the actual CI stress supervisor; no model execution."""
from __future__ import annotations

import ctypes
import json
from pathlib import Path
import shutil
import subprocess
import sys
import textwrap
import time

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='Real Windows process-tree regression')

# Every fixture has its own deadline, in addition to the supervisor and test Job.
FIXTURE = r'''
import json, os, subprocess, sys, time
from pathlib import Path
root = Path(__file__).resolve().parent
mode = sys.argv[1]
deadline = time.monotonic() + 45
def wait_file(path):
    while not path.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError('Synthetic fixture deadline')
        time.sleep(.02)
if mode == 'sentinel':
    time.sleep(45)
    raise SystemExit(0)
if mode == 'child':
    (root / 'child-ready').write_text(str(os.getpid()))
    wait_file(root / 'release')
    if sys.argv[2] in ('hang', 'mutant'):
        while time.monotonic() < deadline:
            time.sleep(.05)
    raise SystemExit(0)
child = subprocess.Popen([sys.executable, __file__, 'child', mode])
try:
    wait_file(root / 'child-ready')
    (root / 'pids.pending').write_text(json.dumps({'parent': os.getpid(), 'child': child.pid}))
    (root / 'pids.pending').replace(root / 'pids.json')
    wait_file(root / 'release')
    if mode in ('hang', 'mutant'):
        while time.monotonic() < deadline:
            time.sleep(.05)
    child.wait(timeout=5)
    if mode == 'exit7':
        raise SystemExit(7)
    report = {'passed': True, 'iterations_per_profile': 500, 'frozen': True,
              'executable_sha256': 'a' * 64, 'version': 'synthetic', 'profiles': {
                  name: {'passed': True, 'iterations_completed': 500, 'identity': {'profile': name}}
                  for name in ('v6-small', 'v6-medium')}}
    (root / 'report.json').write_text('{' if mode == 'malformed' else json.dumps(report))
finally:
    if child.poll() is None:
        child.kill()
    child.wait(timeout=5)
'''


def _stress_block(mutant):
    workflow = yaml.load((ROOT / '.github/workflows/validate.yml').read_text(encoding='utf-8'),
                         Loader=yaml.BaseLoader)
    step = next(step for step in workflow['jobs']['validate']['steps'] if step.get('id') == 'firewall')
    marker = "$stressReport = Join-Path $PWD 'artifacts/firewall-validation/frozen-runtime-stress.json'\n"
    block = textwrap.dedent(step['run'].split(marker, 1)[1].rsplit('\n} finally {', 1)[0])
    command = "$stressProcess = Start-Process -FilePath $exe -ArgumentList @('--stress-ocr', $stressReport) -PassThru"
    assert block.count(command) == block.count('WaitForExit(900000)') == 1
    # Preserve the actual production supervisor. Capture its real SafeHandle at
    # fixture launch; no Process method or cleanup behavior is mocked.
    block = block.replace(command, '$stressProcess = Start-Process -FilePath $exe '
                          '-ArgumentList $fixtureArguments -PassThru; $ownedHandle = $stressProcess.SafeHandle')
    block = block.replace('WaitForExit(900000)', 'WaitForExit(5000)')
    if mutant:
        assert block.count('$stressProcess.Kill($true)') == 1
        block = block.replace('$stressProcess.Kill($true)', '$stressProcess.Kill()')
    return block


def _ps_literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def _wait_for(path, process, seconds=15):
    deadline = time.monotonic() + seconds
    while not path.is_file():
        assert process.poll() is None, 'Harness exited before the real parent/child fixture was ready'
        if time.monotonic() >= deadline:
            pytest.fail('Synthetic parent/child startup exceeded its deadline')
        time.sleep(.02)


def _windows_handles():
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    return kernel


def _run_case(tmp_path, case):
    pwsh = shutil.which('pwsh')
    assert pwsh, 'Windows CI must provide PowerShell 7 for Kill(entireProcessTree)'
    sys.path.insert(0, str(ROOT))
    try:
        from scripts.bounded_test_job import BoundedTestJob
    finally:
        sys.path.pop(0)
    fixture = tmp_path / 'synthetic process.py'
    fixture.write_text(FIXTURE, encoding='utf-8')
    block = _stress_block(case == 'mutant')
    # Windows Start-Process joins ArgumentList; quote through the same Windows
    # command-line convention as subprocess rather than concatenating raw paths.
    arguments = subprocess.list2cmdline([str(fixture), case])
    harness = tmp_path / 'stress cleanup.ps1'
    harness.write_text(
        "$ErrorActionPreference = 'Stop'\nSet-StrictMode -Version Latest\n"
        "$startupDeadline = [DateTime]::UtcNow.AddSeconds(10)\n"
        "while (-not (Test-Path -LiteralPath 'start')) {\n"
        "  if ([DateTime]::UtcNow -gt $startupDeadline) { throw 'Synthetic start deadline' }\n"
        "  Start-Sleep -Milliseconds 20\n}\n"
        f'$exe = {_ps_literal(sys.executable)}\n$fixtureArguments = {_ps_literal(arguments)}\n'
        "$stressReport = Join-Path $PWD 'report.json'\n"
        "$manifest = [pscustomobject]@{ version='synthetic'; executable_sha256=('a' * 64) }\n"
        "$ownedHandle = $null\n$failure = $null\ntry {\n" + block + "\n"
        "} catch { $failure = $_.Exception.Message } finally {\n"
        "  [ordered]@{ failure=$failure; disposed=($null -ne $ownedHandle -and $ownedHandle.IsClosed) } | "
        "ConvertTo-Json -Compress | Write-Output\n}\n"
        "if ($null -ne $failure) { exit 1 }\n", encoding='utf-8')
    job = BoundedTestJob(memory_mib=512, active_process_limit=8, job_memory_mib=1024)
    kernel = _windows_handles()
    handles, harness_process, sentinel = {}, None, None
    try:
        # Assignment precedes the 'start' handshake, so all fixture descendants
        # inherit this *test safety* Job. It stays open during the observations.
        harness_process = subprocess.Popen([pwsh, '-NoProfile', '-NonInteractive', '-File', str(harness)],
                                           cwd=tmp_path, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        job.assign(harness_process.pid)
        sentinel = subprocess.Popen([sys.executable, str(fixture), 'sentinel'], cwd=tmp_path)
        (tmp_path / 'start').touch()
        _wait_for(tmp_path / 'pids.json', harness_process)
        pids = json.loads((tmp_path / 'pids.json').read_text())
        assert len(set(pids.values())) == 2 and sentinel.pid not in pids.values()
        for name, pid in pids.items():
            handles[name] = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE, held across exit/PID reuse
            assert handles[name], f'Cannot observe real owned {name} process: {ctypes.get_last_error()}'
        (tmp_path / 'release').touch()
        stdout, stderr = harness_process.communicate(timeout=20)
        observations = json.loads(stdout.strip().splitlines()[-1])
        assert observations['disposed'] is True, stdout + stderr
        # Do this before closing the safety Job: otherwise the test itself could
        # hide an orphan left by production cleanup, especially in the mutant.
        assert kernel.WaitForSingleObject(handles['parent'], 5000) == 0, stdout + stderr
        child_wait = kernel.WaitForSingleObject(handles['child'], 0 if case == 'mutant' else 5000)
        assert child_wait == (258 if case == 'mutant' else 0), stdout + stderr  # WAIT_TIMEOUT / WAIT_OBJECT_0
        assert sentinel.poll() is None, 'Cleanup touched an unrelated sentinel process'
        if case == 'success':
            assert harness_process.returncode == 0 and observations['failure'] is None, stdout + stderr
        else:
            assert harness_process.returncode != 0 and observations['failure'], stdout + stderr
            if case in ('hang', 'mutant'):
                assert 'Frozen stress timed out' in observations['failure']
            elif case == 'exit7':
                assert 'Frozen stress failed' in observations['failure']
    finally:
        # No PID/name sweeps: only this assigned Job and the Popen-owned sentinel.
        try:
            job.terminate_all()
        finally:
            job.close()
            try:
                if harness_process is not None:
                    if harness_process.poll() is None:
                        harness_process.kill()
                    harness_process.communicate(timeout=5)
            finally:
                try:
                    if sentinel is not None:
                        if sentinel.poll() is None:
                            sentinel.kill()
                        sentinel.wait(timeout=5)
                finally:
                    try:
                        assert all(kernel.WaitForSingleObject(handle, 5000) == 0
                                   for handle in handles.values() if handle), 'Owned fixture remained after Job cleanup'
                    finally:
                        for handle in handles.values():
                            if handle:
                                kernel.CloseHandle(handle)


@pytest.mark.parametrize('case', ['hang', 'success', 'exit7', 'malformed', 'mutant'])
def test_frozen_stress_process_tree_cleanup(tmp_path, case):
    _run_case(tmp_path, case)
