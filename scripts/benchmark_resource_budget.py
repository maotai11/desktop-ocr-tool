"""Windows-only external measurement, starting before the onefile bootloader.

The application is launched suspended, put in an owned native aggregate-memory
Job, and only then resumed. The host sampler is outside the measured budget.
"""
from __future__ import annotations

import argparse
import ctypes
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.resource_budget import (RESOURCE_LIMIT_BYTES, ResourceInspectionError,
                                      _windows_memory, managed_disk_usage)
from src.core.validation_report import write_validation_report
from src.core.version import APP_VERSION


class ResourceGateError(RuntimeError):
    pass


def _check(value, code):
    if not value:
        raise ResourceGateError(code)


def program_roots(executable):
    """Count delivered program files, excluding sibling candidate ZIP/evidence."""
    directory = executable.parent
    return [executable, *(directory/name for name in (
        'docs', 'licenses', 'handwriting-source', 'README.md', 'README.txt', 'OFFLINE_README.txt'))]


def isolated_environment(profile, temporary, job_name, source_commit=None):
    env = {key.upper(): value for key, value in os.environ.items()}
    for key in tuple(env):
        if key.upper().startswith(('PYTHON', 'QT_')):
            del env[key]
    system = Path(env['SYSTEMROOT'])
    env.update({'PATH': str(system/'System32')+';'+str(system),
                'USERPROFILE': str(profile), 'APPDATA': str(profile/'AppData/Roaming'),
                'LOCALAPPDATA': str(profile/'AppData/Local'), 'TEMP': str(temporary), 'TMP': str(temporary),
                'DESKTOP_OCR_HOME': str(profile), 'DESKTOP_OCR_RESOURCE_JOB': job_name,
                'ORT_DISABLE_TELEMETRY': '1'})
    if source_commit is not None:
        env['GITHUB_SHA'] = source_commit
    for name in ('APPDATA', 'LOCALAPPDATA'):
        Path(env[name]).mkdir(parents=True, exist_ok=True)
    return env


class NativeJobProcess:
    """Own the initial bootloader and all descendants from their first opcode."""
    def __init__(self, executable, arguments, profile, temporary, source_commit=None):
        from ctypes import wintypes
        _check(sys.platform == 'win32', 'WINDOWS_REQUIRED')

        class Basic(ctypes.Structure):
            _fields_ = [('user', ctypes.c_int64), ('job_user', ctypes.c_int64),
                        ('flags', wintypes.DWORD), ('minimum_ws', ctypes.c_size_t),
                        ('maximum_ws', ctypes.c_size_t), ('active', wintypes.DWORD),
                        ('affinity', ctypes.c_size_t), ('priority', wintypes.DWORD),
                        ('scheduling', wintypes.DWORD)]
        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in ('read_ops', 'write_ops', 'other_ops',
                                                           'read_bytes', 'write_bytes', 'other_bytes')]
        class Extended(ctypes.Structure):
            _fields_ = [('basic', Basic), ('io', IO), ('process_limit', ctypes.c_size_t),
                        ('job_limit', ctypes.c_size_t), ('peak_process', ctypes.c_size_t),
                        ('peak_job', ctypes.c_size_t)]
        class Accounting(ctypes.Structure):
            _fields_ = [(name, ctypes.c_int64) for name in ('user', 'kernel', 'period_user', 'period_kernel')]
            _fields_ += [(name, wintypes.DWORD) for name in ('faults', 'total', 'active', 'terminated')]
        class Pids(ctypes.Structure):
            _fields_ = [('assigned', wintypes.DWORD), ('count', wintypes.DWORD),
                        ('ids', ctypes.c_size_t*256)]
        class Startup(ctypes.Structure):
            _fields_ = [('cb', wintypes.DWORD), ('reserved', wintypes.LPWSTR),
                        ('desktop', wintypes.LPWSTR), ('title', wintypes.LPWSTR)]
            _fields_ += [(name, wintypes.DWORD) for name in ('x', 'y', 'width', 'height', 'columns', 'rows', 'fill', 'flags')]
            _fields_ += [('show', wintypes.WORD), ('reserved_count', wintypes.WORD),
                        ('reserved_bytes', ctypes.c_void_p), ('stdin', wintypes.HANDLE),
                        ('stdout', wintypes.HANDLE), ('stderr', wintypes.HANDLE)]
        class Information(ctypes.Structure):
            _fields_ = [('process', wintypes.HANDLE), ('thread', wintypes.HANDLE),
                        ('pid', wintypes.DWORD), ('tid', wintypes.DWORD)]

        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                                     wintypes.DWORD, ctypes.c_void_p]
        kernel.CreateProcessW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p,
                                          wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR,
                                          ctypes.POINTER(Startup), ctypes.POINTER(Information)]
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.ResumeThread.argtypes = [wintypes.HANDLE]
        kernel.ResumeThread.restype = wintypes.DWORD
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel, self.Extended, self.Accounting, self.Pids = kernel, Extended, Accounting, Pids
        self.job = self.process = self.thread = None
        self.pid = None
        self.name = 'Local\\InkPickResourceAudit-' + uuid4().hex
        ctypes.set_last_error(0)
        job = kernel.CreateJobObjectW(None, self.name)
        _check(bool(job), 'JOB_CREATE_FAILED')
        if ctypes.get_last_error() == 183:
            kernel.CloseHandle(job)
            raise ResourceGateError('JOB_NAME_COLLISION')
        self.job = job
        try:
            limits = Extended()
            limits.basic.flags = 0x200 | 0x2000  # Aggregate job memory + kill on last close.
            limits.job_limit = RESOURCE_LIMIT_BYTES-1
            _check(kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)),
                   'JOB_LIMIT_FAILED')
            env = isolated_environment(profile, temporary, self.name, source_commit)
            entries = [str(key)+'='+str(value) for key, value in sorted(env.items(), key=lambda x: x[0].upper())]
            _check(not any('\x00' in entry for entry in entries), 'INVALID_ENVIRONMENT')
            block = ctypes.create_unicode_buffer('\x00'.join(entries)+'\x00\x00')
            command = ctypes.create_unicode_buffer(subprocess.list2cmdline([str(executable), *arguments]))
            startup, info = Startup(), Information()
            startup.cb = ctypes.sizeof(startup)
            _check(kernel.CreateProcessW(str(executable), command, None, None, False, 0x4 | 0x400,
                                         ctypes.cast(block, ctypes.c_void_p), str(profile),
                                         ctypes.byref(startup), ctypes.byref(info)), 'PROCESS_CREATE_FAILED')
            self.process, self.thread, self.pid = info.process, info.thread, int(info.pid)
            _check(kernel.AssignProcessToJobObject(job, self.process), 'JOB_ASSIGN_BEFORE_RESUME_FAILED')
            _check(kernel.ResumeThread(self.thread) != 0xFFFFFFFF, 'PROCESS_RESUME_FAILED')
            kernel.CloseHandle(self.thread)
            self.thread = None
        except BaseException:
            self.close()
            raise

    def _query(self, code, structure):
        value = structure()
        _check(self.kernel.QueryInformationJobObject(self.job, code, ctypes.byref(value),
                                                     ctypes.sizeof(value), None), 'JOB_QUERY_FAILED')
        return value

    def accounting(self):
        limits, counts = self._query(9, self.Extended), self._query(1, self.Accounting)
        return {'peak_job_private_bytes': int(limits.peak_job), 'active_processes': int(counts.active),
                'total_processes': int(counts.total), 'job_limit_bytes': int(limits.job_limit)}

    def pids(self):
        value = self._query(3, self.Pids)
        _check(value.assigned == value.count and value.count <= 256, 'INCOMPLETE_JOB_PROCESS_LIST')
        return tuple(int(value.ids[index]) for index in range(value.count))

    def exited(self):
        status = self.kernel.WaitForSingleObject(self.process, 0)
        _check(status in (0, 258), 'PROCESS_WAIT_FAILED')
        return status == 0

    def exit_code(self):
        from ctypes import wintypes
        value = wintypes.DWORD()
        _check(self.kernel.GetExitCodeProcess(self.process, ctypes.byref(value)), 'PROCESS_EXIT_QUERY_FAILED')
        return int(value.value)

    def close(self):
        if self.job:
            self.kernel.TerminateJobObject(self.job, 1)
        if self.process:
            self.kernel.WaitForSingleObject(self.process, 5000)
        for name in ('thread', 'process', 'job'):
            handle = getattr(self, name, None)
            if handle:
                self.kernel.CloseHandle(handle)
                setattr(self, name, None)


PHASES = {'starting', 'models_loading', 'invoice_check', 'image_helper', 'bounded_raster', 'shutdown', 'complete'}
WORKLOAD_CHECKS = {'shared_small_engine_verified', 'three_manual_checks', 'only_current_row',
                   'no_automatic_capture', 'helper_inference_and_reap', 'bounded_raster_inference',
                   'no_history_or_capture_files', 'ownership_drained'}


def validate_stage_measurements(record):
    """Typed numeric evidence; absent samples or boundary equality never pass."""
    for key in ('samples', 'peak_rss_bytes', 'peak_sampled_private_bytes', 'peak_job_private_bytes',
                'peak_disk_bytes', 'max_processes', 'total_processes', 'job_limit_bytes'):
        _check(type(record.get(key)) is int and record[key] > 0, 'MISSING_RESOURCE_MEASUREMENT')
    for key in ('peak_rss_bytes', 'peak_sampled_private_bytes', 'peak_job_private_bytes',
                'peak_disk_bytes', 'job_limit_bytes'):
        _check(record[key] < RESOURCE_LIMIT_BYTES, 'RESOURCE_LIMIT_EXCEEDED')
    _check(record.get('bootloader_owned_before_resume') is True, 'BOOTLOADER_NOT_OWNED')
    _check(type(record.get('active_processes')) is int and record['active_processes'] == 0,
           'ORPHAN_PROCESSES_REMAIN')
    _check(type(record.get('exit_code')) is int and record['exit_code'] == 0, 'WORKLOAD_NONZERO_EXIT')
    _check(record['max_processes'] >= 2 and record['total_processes'] >= 2, 'BOOTLOADER_NOT_MEASURED')


def _fields(value, required):
    public = {'original_report_sha256', 'original_report_bytes', 'nonpublic_error_present'}
    _check(type(value) is dict and set(required) <= set(value) <= set(required) | public,
           'INCOMPLETE_RESOURCE_FIELDS')
    if 'nonpublic_error_present' in value:
        _check(value['nonpublic_error_present'] is False, 'PUBLIC_RESOURCE_ERROR_PRESENT')
    if 'original_report_sha256' in value:
        _check(isinstance(value['original_report_sha256'], str)
               and re.fullmatch('[0-9a-f]{64}', value['original_report_sha256']), 'INVALID_REPORT_DIGEST')
    if 'original_report_bytes' in value:
        _check(type(value['original_report_bytes']) is int and value['original_report_bytes'] > 0,
               'INVALID_REPORT_BYTES')
    return {key: item for key, item in value.items() if key not in public}


def validate_resource_report(report, *, version, executable_sha256, source_commit,
                             repository='maotai11/desktop-ocr-tool', run_id=None, run_attempt=None):
    """Strict same-candidate raw/public receipt validation; no Windows imports."""
    report = _fields(report, {'schema', 'probe', 'version', 'platform', 'passed', 'limit_bytes',
        'sample_interval_ms', 'source_commit', 'workflow', 'cold_os_cache_verified',
        'clean_machine_verified', 'stages', 'executable_sha256'})
    _check(type(report['schema']) is int and report['schema'] == 1
           and report['probe'] == 'resource_budget' and report['platform'] == 'win32'
           and report['version'] == version and report['passed'] is True, 'RESOURCE_IDENTITY_MISMATCH')
    _check(type(report['limit_bytes']) is int and report['limit_bytes'] == RESOURCE_LIMIT_BYTES
           and type(report['sample_interval_ms']) is int and report['sample_interval_ms'] == 20,
           'RESOURCE_POLICY_MISMATCH')
    _check(report['cold_os_cache_verified'] is False and report['clean_machine_verified'] is False,
           'UNSUPPORTED_RESOURCE_CLAIM')
    _check(isinstance(executable_sha256, str) and re.fullmatch('[0-9a-f]{64}', executable_sha256)
           and report['executable_sha256'] == executable_sha256, 'RESOURCE_EXE_MISMATCH')
    _check(isinstance(source_commit, str) and re.fullmatch('[0-9a-f]{40}', source_commit)
           and report['source_commit'] == source_commit, 'RESOURCE_SOURCE_MISMATCH')
    workflow = _fields(report['workflow'], {'repository', 'run_id', 'run_attempt', 'source_commit'})
    _check(workflow['repository'] == repository and workflow['source_commit'] == source_commit,
           'RESOURCE_WORKFLOW_MISMATCH')
    for key, expected in (('run_id', run_id), ('run_attempt', run_attempt)):
        observed = workflow[key]
        _check(observed is None or isinstance(observed, str) and observed.isdecimal() and int(observed) > 0,
               'RESOURCE_WORKFLOW_TYPE_MISMATCH')
        if expected is not None:
            _check(type(expected) is str and observed == expected, 'RESOURCE_WORKFLOW_ATTEMPT_MISMATCH')
    stages = _fields(report['stages'], {'cold_application', 'manual_invoice_and_helpers'})
    base = {'passed', 'samples', 'peak_rss_bytes', 'peak_sampled_private_bytes', 'peak_job_private_bytes',
        'peak_disk_bytes', 'max_processes', 'maximum_active_mei_directories', 'bootloader_owned_before_resume',
        'temporary_cleaned', 'exit_code', 'active_processes', 'total_processes', 'job_limit_bytes',
        'inner_report_sha256', 'inner_report_bytes'}
    manual = {'workload_checks', 'manual_rounds_completed', 'fields_each', 'large_source_pixels', 'native_capture_used'}
    for name, value in stages.items():
        stage = _fields(value, base | (manual if name == 'manual_invoice_and_helpers' else set()))
        validate_stage_measurements(stage)
        _check(stage['passed'] is True and stage['temporary_cleaned'] is True, 'INCOMPLETE_RESOURCE_CLEANUP')
        _check(type(stage['maximum_active_mei_directories']) is int
               and stage['maximum_active_mei_directories'] >= 1, 'EXTRACTED_PAYLOAD_NOT_MEASURED')
        _check(isinstance(stage['inner_report_sha256'], str)
               and re.fullmatch('[0-9a-f]{64}', stage['inner_report_sha256'])
               and type(stage['inner_report_bytes']) is int and 0 < stage['inner_report_bytes'] <= 1_000_000,
               'INVALID_INNER_REPORT_BINDING')
        _check(stage['total_processes'] >= stage['max_processes'], 'INCONSISTENT_PROCESS_ACCOUNTING')
        if name == 'manual_invoice_and_helpers':
            checks = _fields(stage['workload_checks'], WORKLOAD_CHECKS)
            _check(all(value is True for value in checks.values()) and stage['native_capture_used'] is True,
                   'INCOMPLETE_REAL_MANUAL_WORKLOAD')
            _check(type(stage['manual_rounds_completed']) is int and stage['manual_rounds_completed'] == 3
                   and type(stage['fields_each']) is int and stage['fields_each'] == 5
                   and type(stage['large_source_pixels']) is int and stage['large_source_pixels'] == 6_000_000,
                   'MANUAL_WORKLOAD_COUNT_MISMATCH')
            _check(stage['total_processes'] >= 3 and stage['max_processes'] >= 3, 'HELPER_NOT_MEASURED')


def run_stage(executable, name, flag, *, timeout, source_commit=None):
    work = tempfile.TemporaryDirectory(prefix='inkpick-resource-')
    directory = Path(work.name)
    profile, temporary = directory/'profile', directory/'temp'
    profile.mkdir(); temporary.mkdir()
    inner_path = profile/'workload.json'
    roots = [*program_roots(executable), temporary, profile]
    record = {'passed': False, 'samples': 0, 'peak_rss_bytes': 0, 'peak_sampled_private_bytes': 0,
              'peak_disk_bytes': 0, 'max_processes': 0, 'maximum_active_mei_directories': 0,
              'bootloader_owned_before_resume': False, 'temporary_cleaned': False}
    samples, process = [], None
    started = time.monotonic()
    try:
        process = NativeJobProcess(executable, [flag, str(inner_path)], profile, temporary, source_commit)
        record['bootloader_owned_before_resume'] = True
        while True:
            rss = private = 0
            pids = process.pids()
            for pid in pids:
                try:
                    resident, committed = _windows_memory(pid)
                except ResourceInspectionError:
                    _check(pid not in process.pids(), 'LIVE_PROCESS_MEMORY_UNAVAILABLE')
                    continue
                rss += resident; private += committed
            disk = managed_disk_usage(roots).bytes
            phase = 'starting'
            if inner_path.is_file():
                try:
                    value = json.loads(inner_path.read_text(encoding='utf-8'))
                    if value.get('phase') in PHASES:
                        phase = value['phase']
                except (ValueError, OSError):
                    pass
            samples.append({'elapsed_ms': round((time.monotonic()-started)*1000), 'rss_bytes': rss,
                            'private_bytes': private, 'disk_bytes': disk, 'processes': len(pids), 'phase': phase})
            record['samples'] += 1
            record['peak_rss_bytes'] = max(record['peak_rss_bytes'], rss)
            record['peak_sampled_private_bytes'] = max(record['peak_sampled_private_bytes'], private)
            record['peak_disk_bytes'] = max(record['peak_disk_bytes'], disk)
            record['max_processes'] = max(record['max_processes'], len(pids))
            meis = sum(1 for path in temporary.rglob('_MEI*') if path.is_dir())
            record['maximum_active_mei_directories'] = max(record['maximum_active_mei_directories'], meis)
            _check(max(rss, private, disk) < RESOURCE_LIMIT_BYTES, 'RESOURCE_LIMIT_EXCEEDED')
            _check(time.monotonic()-started < timeout, 'RESOURCE_STAGE_TIMEOUT')
            if process.exited():
                break
            time.sleep(.02)
        record['exit_code'] = process.exit_code()
        record.update(process.accounting())
        validate_stage_measurements(record)
        _check(0 < inner_path.stat().st_size <= 1_000_000, 'INNER_REPORT_SIZE_INVALID')
        inner_bytes = inner_path.read_bytes()
        inner = json.loads(inner_bytes)
        _check(inner.get('passed') is True and inner.get('frozen') is True and inner.get('platform') == 'win32',
               'UNQUALIFIED_INNER_REPORT')
        _check(inner.get('version') == APP_VERSION and inner.get('executable_sha256') == sha256(executable.read_bytes()).hexdigest(),
               'INNER_CANDIDATE_IDENTITY_MISMATCH')
        record['inner_report_sha256'] = sha256(inner_bytes).hexdigest()
        record['inner_report_bytes'] = len(inner_bytes)
        if name == 'manual_invoice_and_helpers':
            _check(inner.get('probe') == 'resource_invoice_workload' and inner.get('native_capture_used') is True,
                   'REAL_MANUAL_WORKLOAD_MISSING')
            _check(inner.get('source_commit') == source_commit, 'INNER_SOURCE_MISMATCH')
            _check(set(inner.get('checks', {})) == WORKLOAD_CHECKS and all(v is True for v in inner['checks'].values()),
                   'INCOMPLETE_MANUAL_WORKLOAD')
            _check(type(inner.get('manual_rounds_completed')) is int and inner['manual_rounds_completed'] == 3
                   and type(inner.get('fields_each')) is int and inner['fields_each'] == 5
                   and type(inner.get('large_source_pixels')) is int and inner['large_source_pixels'] == 6_000_000,
                   'MANUAL_WORKLOAD_COUNT_MISMATCH')
            _check(record['total_processes'] >= 3 and record['max_processes'] >= 3, 'HELPER_NOT_MEASURED')
            record['workload_checks'] = inner['checks']
            record['manual_rounds_completed'] = 3
            record['fields_each'] = 5
            record['large_source_pixels'] = 6_000_000
            record['native_capture_used'] = True
        else:
            _check(inner.get('probe') == 'application_lifecycle' and inner.get('shutdown_clean') is True,
                   'COLD_APPLICATION_LIFECYCLE_MISSING')
        record['passed'] = True
    except Exception as exc:
        record['error_code'] = str(exc) if isinstance(exc, ResourceGateError) else type(exc).__name__
    finally:
        if process is not None:
            process.close()
        try:
            work.cleanup()
            record['temporary_cleaned'] = not directory.exists()
        except OSError:
            record['error_code'] = 'TEMPORARY_CLEANUP_FAILED'
        if not record['temporary_cleaned']:
            record['passed'] = False
    return record, samples


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--executable', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--source-commit', default=os.environ.get('GITHUB_SHA'))
    args = parser.parse_args(argv)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    report = {'schema': 1, 'probe': 'resource_budget', 'version': APP_VERSION,
              'platform': sys.platform, 'passed': False, 'limit_bytes': RESOURCE_LIMIT_BYTES,
              'sample_interval_ms': 20, 'source_commit': args.source_commit,
              'workflow': {'repository': os.environ.get('GITHUB_REPOSITORY', 'maotai11/desktop-ocr-tool'),
                           'run_id': os.environ.get('GITHUB_RUN_ID'),
                           'run_attempt': os.environ.get('GITHUB_RUN_ATTEMPT'),
                           'source_commit': args.source_commit},
              'cold_os_cache_verified': False, 'clean_machine_verified': False, 'stages': {}}
    traces = {}
    try:
        _check(sys.platform == 'win32', 'WINDOWS_REQUIRED')
        _check(isinstance(args.source_commit, str) and re.fullmatch('[0-9a-f]{40}', args.source_commit),
               'SOURCE_COMMIT_REQUIRED')
        executable = args.executable.resolve()
        _check(executable.is_file() and executable.suffix.lower() == '.exe', 'CANDIDATE_EXE_REQUIRED')
        report['executable_sha256'] = sha256(executable.read_bytes()).hexdigest()
        for name, flag, timeout in (('cold_application', '--smoke-app', 120),
                                    ('manual_invoice_and_helpers', '--self-test-resources', 420)):
            report['stages'][name], traces[name] = run_stage(executable, name, flag, timeout=timeout,
                                                          source_commit=args.source_commit)
            write_validation_report(args.report, report)
            _check(report['stages'][name]['passed'] is True, 'RESOURCE_STAGE_FAILED')
        report['passed'] = True
        validate_resource_report(report, version=APP_VERSION,
                                 executable_sha256=report['executable_sha256'], source_commit=args.source_commit,
                                 run_id=os.environ.get('GITHUB_RUN_ID'),
                                 run_attempt=os.environ.get('GITHUB_RUN_ATTEMPT'))
    except Exception as exc:
        report['passed'] = False
        report['error_code'] = str(exc) if isinstance(exc, ResourceGateError) else type(exc).__name__
    write_validation_report(args.report, report)
    write_validation_report(args.report.with_suffix('.samples.json'), {'sample_interval_ms': 20, 'stages': traces})
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
