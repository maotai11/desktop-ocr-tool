"""Windows-only external measurement, starting before the onefile bootloader.

The application is launched suspended, put in an owned native aggregate-memory
Job, and only then resumed. The host sampler is outside the measured budget.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import math
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.resource_budget import (
    RESOURCE_LIMIT_BYTES,
    ResourceInspectionError,
    ResourceMetadataChanged,
    _windows_memory,
    managed_disk_usage,
)
from src.core.validation_report import write_validation_report
from src.core.version import APP_VERSION


class ResourceGateError(RuntimeError):
    pass


def _check(value, code):
    if not value:
        raise ResourceGateError(code)


def sample_managed_disk_usage(roots):
    """External sampling only: restart a changed tree, never reuse partial totals.

    Product writer admission still fails after its bounded per-node query. The
    external observer permits up to fifty fresh scans within five seconds when
    extraction changes metadata, with the usual 20 ms wait between attempts.
    No permissions, unknown inspection failures or quota errors are retried.
    """
    roots = tuple(roots)
    started = time.monotonic()
    deadline = started + 5.
    def rejected(error, attempts):
        # Scalar failure context only; no tracing or changes to admission.
        error.resource_scan_attempts = attempts
        error.resource_scan_elapsed_ms = max(0, round((time.monotonic()-started)*1000))
        return error
    for attempt in range(50):
        if attempt and time.monotonic() >= deadline:
            raise rejected(ResourceMetadataChanged(), attempt)
        try:
            measured = managed_disk_usage(roots)
        except ResourceMetadataChanged as exc:
            if attempt == 49 or time.monotonic() + .02 >= deadline:
                rejected(exc, attempt + 1)
                raise
            time.sleep(.02)
            continue
        if time.monotonic() >= deadline:
            raise rejected(ResourceMetadataChanged(), attempt + 1)
        return measured
    raise ResourceMetadataChanged()


_OS_CONTEXT_TYPES = {kind: kind.__name__ for kind in (
    OSError, BlockingIOError, BrokenPipeError, ChildProcessError, ConnectionError,
    ConnectionAbortedError, ConnectionRefusedError, ConnectionResetError,
    FileExistsError, FileNotFoundError, InterruptedError, IsADirectoryError,
    NotADirectoryError, PermissionError, ProcessLookupError, TimeoutError)}


def _owned_inspection_operation(error):
    """Only known source operations; do not export filenames or traceback data."""
    operation = 'OTHER'
    tb = error.__traceback__
    while tb is not None:
        code = tb.tb_frame.f_code
        if code.co_filename == _windows_memory.__code__.co_filename:
            known = {
                '_measure_storage_node': {83: 'NODE_TYPE_REJECTED', 84: 'WINDOWS_FILE_ALLOCATION',
                    86: 'DIRECTORY_LIST', 87: 'VERIFY_LSTAT', 90: 'NODE_CHANGED', 97: 'NODE_CHANGED'},
                'managed_disk_usage': {114: 'ROOT_RESOLVE', 116: 'ROOT_RESOLVE',
                    124: 'INITIAL_LSTAT', 128: 'INITIAL_MISSING_UNCONFIRMED', 132: 'INITIAL_LSTAT',
                    137: 'LINK_REJECTED', 139: 'MEASURE_STORAGE',
                    151: 'POST_MEASURE_MISSING_UNCONFIRMED', 157: 'MEASURE_OS_ERROR'},
            }.get(code.co_name, {}).get(tb.tb_lineno)
            if known is not None:
                operation = known
        tb = tb.tb_next
    return operation


def _inspection_context_details(error):
    """Bounded failure-only projection; at most one fresh stat, no source text."""
    result = {'inspection_operation': 'OTHER', 'inspection_context_type': 'OTHER',
              'inspection_context_operation': 'OTHER', 'inspection_filename_category': 'OTHER',
              'inspection_filename_status': 'OTHER'}
    try:
        result['inspection_operation'] = _owned_inspection_operation(error)
        pending, seen, chain = [error], set(), []
        while pending and len(chain) < 8:
            node = pending.pop(0)
            if id(node) in seen:
                continue
            seen.add(id(node)); chain.append(node)
            pending.extend(value for value in (node.__cause__, node.__context__) if value is not None)
        selected = next((node for node in chain[1:] if type(node) in _OS_CONTEXT_TYPES),
                        chain[1] if len(chain) > 1 else None)
        if selected is None:
            return result
        result['inspection_context_type'] = _OS_CONTEXT_TYPES.get(type(selected), 'OTHER')
        result['inspection_context_operation'] = _owned_inspection_operation(selected)
        if not isinstance(selected, OSError):
            return result
        for name, maximum in (('winerror', 0xFFFFFFFF), ('errno', 0x7FFFFFFF)):
            value = getattr(selected, name, None)
            if type(value) is int and 0 <= value <= maximum:
                result['inspection_context_' + name] = value
        filename = selected.filename
        if type(filename) is not str or not 0 < len(filename) <= 32768:
            return result
        parts = tuple(part.casefold() for part in Path(filename).parts)
        for index, part in enumerate(parts):
            if part.startswith('inkpick-resource-'):
                owned = parts[index + 1:]
                if owned[:1] == ('profile',):
                    result['inspection_filename_category'] = 'PROFILE'
                elif owned[:1] == ('temp',) and any(value.startswith('_mei') for value in owned[1:]):
                    result['inspection_filename_category'] = 'ACTIVE_MEI'
                break
        try:
            mode = os.lstat(filename).st_mode
            result['inspection_filename_status'] = ('PRESENT_FILE' if stat.S_ISREG(mode)
                else 'PRESENT_DIR' if stat.S_ISDIR(mode) else 'OTHER')
        except FileNotFoundError:
            result['inspection_filename_status'] = 'ABSENT'
        except PermissionError:
            result['inspection_filename_status'] = 'ACCESS_DENIED'
        except (OSError, ValueError):
            pass
    except BaseException:  # noqa: BLE001, S110 - preserve the original failed stage, including diagnostic faults
        # Even a malformed diagnostic exception must preserve the failed stage.
        pass
    return result


def _inspection_failure_details(error):
    """Failure-only, fixed origin and bounded scalar counters; never traceback text."""
    site = 'OTHER'
    tb = error.__traceback__
    while tb is not None and tb.tb_next is not None:
        tb = tb.tb_next
    if tb is not None:
        code = tb.tb_frame.f_code
        if code.co_filename == __file__ and code.co_name == 'sample_managed_disk_usage':
            site = 'SCAN_DEADLINE'
        elif code.co_filename == _windows_memory.__code__.co_filename:
            site = {
                '_measure_storage_node': ('NODE_CHANGED' if isinstance(error, ResourceMetadataChanged)
                                          else 'NODE_INSPECTION'),
                'managed_disk_usage': 'STORAGE_WALK',
                '_windows_allocated_bytes': 'WINDOWS_FILE_ALLOCATION',
                '_windows_memory': 'WINDOWS_PROCESS_MEMORY',
            }.get(code.co_name, 'OTHER')
    result = {'inspection_failure_site': site}
    for source, target, maximum in (
            ('resource_scan_attempts', 'inspection_attempts', 50),
            ('resource_scan_elapsed_ms', 'inspection_elapsed_ms', 1_000_000_000),
            ('native_error', 'inspection_native_error', 0xFFFFFFFF)):
        value = getattr(error, source, None)
        if type(value) is int and 0 <= value <= maximum:
            result[target] = value
    result.update(_inspection_context_details(error))
    return result


_INNER_FAILURE_CODES = frozenset({
    'WORKLOAD_TIMEOUT', 'MODEL_LOAD_FAILED', 'PRIMARY_SCREEN_REQUIRED',
    'SYNTHETIC_TARGET_SCREEN_TOO_SMALL', 'MANUAL_SNAPSHOT_FAILED', 'SYNTHETIC_VALUES_DIFFER',
    'ADJACENT_ROW_ENTERED', 'REAL_IMAGE_HELPER_FAILED', 'RASTER_QUEUE_REJECTED',
    'BOUNDED_RASTER_FAILED', 'REPORT_WRITE_FAILED', 'OCR_SHUTDOWN_FAILED',
    'RuntimeError', 'ValueError', 'KeyError', 'TypeError', 'IndexError', 'AttributeError',
    'MemoryError', 'ResourceBudgetExceeded', 'ResourceInspectionError', 'ResourceMetadataChanged',
    'OSError', 'FileNotFoundError', 'PermissionError', 'JSONDecodeError', 'OperationalError',
    'ImportError', 'ModuleNotFoundError', 'SystemError',
})


def _inner_failure_receipt(path):
    """Capture the first failed run before cleanup, without copying free-form data."""
    def unique(pairs):
        output = {}
        for key, value in pairs:
            if key in output:
                raise ValueError('duplicate')
            output[key] = value
        return output
    def invalid_constant(value):
        raise ValueError('nonfinite')
    def fixed(value, allowed, default='OTHER'):
        return value if type(value) is str and value in allowed else default
    def boolean(value):
        return value if type(value) is bool else None
    def counter(value, maximum):
        return value if type(value) is int and 0 <= value <= maximum else None
    try:
        with path.open('rb') as stream:
            raw = stream.read(1_000_001)
    except FileNotFoundError:
        return {'read_status': 'ABSENT'}
    except OSError:
        return {'read_status': 'READ_FAILED'}
    if len(raw) > 1_000_000:
        return {'read_status': 'READ_LIMIT_EXCEEDED'}
    try:
        value = json.loads(raw, object_pairs_hook=unique, parse_constant=invalid_constant)
    except (ValueError, UnicodeError, RecursionError):
        return {'read_status': 'INVALID_JSON'}
    if type(value) is not dict:
        return {'read_status': 'INVALID_SHAPE'}
    checks = value.get('checks')
    checks = checks if type(checks) is dict else {}
    result = {
        'read_status': 'COMPLETE',
        'probe': fixed(value.get('probe'), {'resource_invoice_workload', 'application_lifecycle'}),
        'phase': fixed(value.get('phase'), PHASES),
        'error_code': ('NONE' if 'error_code' not in value else
                       fixed(value['error_code'], _INNER_FAILURE_CODES, 'OTHER_ERROR')),
        'checks': {key: boolean(checks.get(key)) for key in sorted(WORKLOAD_CHECKS)},
    }
    for key in ('passed', 'frozen', 'native_capture_used', 'shutdown_clean'):
        result[key] = boolean(value.get(key))
    for key, maximum in (('manual_rounds_completed', 3), ('fields_each', 5),
                         ('large_source_pixels', 16_000_000)):
        result[key] = counter(value.get(key), maximum)
    diagnostic = value.get('failure_diagnostics')
    if type(diagnostic) is dict:
        kind = fixed(diagnostic.get('kind'), {'MANUAL_SNAPSHOT_FAILED', 'SYNTHETIC_VALUES_DIFFER', 'REAL_IMAGE_HELPER_FAILED', 'RASTER_FAILURE'})
        if kind == 'MANUAL_SNAPSHOT_FAILED':
            result['failure_diagnostics'] = {
                'kind': kind,
                'controller_state': fixed(diagnostic.get('controller_state'), {
                    'idle','capturing','reading','verifying','ready','error','cancelling','closed'}, 'unknown'),
                'has_result': boolean(diagnostic.get('has_result')),
                'capture_count': counter(diagnostic.get('capture_count'), 1000),
            }
        elif kind == 'SYNTHETIC_VALUES_DIFFER':
            fields = diagnostic.get('fields')
            fields = fields if type(fields) is dict else {}
            clean = {}
            for key in ('net_amount','tax_amount','issue_date','invoice_number','seller_tax_id'):
                field = fields.get(key)
                field = field if type(field) is dict else {}
                clean[key] = {'status': fixed(field.get('status'), {
                    'matched','different','review','missing'}, 'unknown')}
                for name in ('invoice_value_present','row_value_present','normalized_equal',
                             'invoice_ambiguous','row_ambiguous'):
                    clean[key][name] = boolean(field.get(name))
            result['failure_diagnostics'] = {'kind': kind, 'fields': clean}
        elif kind == 'REAL_IMAGE_HELPER_FAILED':
            checks = diagnostic.get('helper_checks')
            checks = checks if type(checks) is dict else {}
            code = diagnostic.get('helper_return_code')
            result['failure_diagnostics'] = {
                'kind': kind,
                'helper_report_status': fixed(diagnostic.get('helper_report_status'), {
                    'COMPLETE','ABSENT','READ_FAILED','TOO_LARGE','INVALID_JSON','INVALID_SHAPE'}),
                'helper_error': fixed(diagnostic.get('helper_error'), {
                    'NONE','IMAGE_PROBE_ASSET_FAILURE','IMAGE_PROBE_RUNTIME_FAILURE'}),
                'helper_failure_reason': fixed(diagnostic.get('helper_failure_reason'), {
                    'NONE','PROBE_DEADLINE','TIMEOUT','ASSETS_UNAVAILABLE','JOB_ASSIGNMENT_FAILED',
                    'IPC_WRITE_FAILED','PROTOCOL_INVALID','OUTPUT_LIMIT','PROCESS_START_FAILED',
                    'PROCESS_EXITED','RECOGNITION_FAILED'}),
                'helper_return_code': code if type(code) is int and -2**31 <= code < 2**31 else None,
                'helper_checks': {key: boolean(checks.get(key)) for key in (
                    'passed','inference_completed','worker_pid_verified','job_assigned',
                    'cancelled_and_reaped','children_reaped')},
                'expected_candidate_present': boolean(diagnostic.get('expected_candidate_present')),
            }
        elif kind == 'RASTER_FAILURE':
            result['failure_diagnostics'] = {
                'kind': kind,
                'raster_case': fixed(diagnostic.get('raster_case'), {'generic','wide_invoice','max_canvas'}),
                'completed_status': fixed(diagnostic.get('completed_status'), {
                    'NONE','done','needs_review','failed','cancelled'}),
                'raster_error_reason': fixed(diagnostic.get('raster_error_reason', 'NONE'), {
                    'RECOGNITION_WORK_BUDGET','CROP_COUNT_LIMIT','CROP_WIDTH_LIMIT','INVALID_CROP',
                    'TILE_COUNT_LIMIT','TILE_PIXEL_LIMIT','MEMORY_BUDGET','RESOURCE_INSPECTION','OTHER','NONE'}),
            }
    return result


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


def observed_sample_gaps(samples):
    """Actual completion gaps from rounded monotonic milliseconds.

    Median uses the upper middle sample; p95 uses the nearest-rank quantile.
    The requested sleep is separate from inspection and scheduling delays.
    """
    _check(all(type(sample) is dict and type(sample.get('elapsed_ms')) is int
               for sample in samples), 'INVALID_RESOURCE_SAMPLE_TIMESTAMPS')
    gaps = [right['elapsed_ms']-left['elapsed_ms'] for left, right in zip(samples, samples[1:])]
    _check(all(gap >= 0 for gap in gaps), 'NONMONOTONIC_RESOURCE_SAMPLES')
    if not gaps:
        return {'count': 0, 'minimum_ms': 0, 'maximum_ms': 0,
                'mean_ms': 0., 'median_ms': 0, 'p95_ms': 0}
    ordered = sorted(gaps)
    return {'count': len(gaps), 'minimum_ms': ordered[0], 'maximum_ms': ordered[-1],
            'mean_ms': round(sum(gaps)/len(gaps), 3), 'median_ms': ordered[len(gaps)//2],
            'p95_ms': ordered[min(len(gaps)-1, (len(gaps)*95+99)//100-1)]}


def validate_observed_sample_gaps(gaps, samples):
    gaps = _fields(gaps, {'count', 'minimum_ms', 'maximum_ms', 'mean_ms', 'median_ms', 'p95_ms'})
    _check(type(samples) is int and samples >= 2 and type(gaps['count']) is int
           and gaps['count'] == samples-1, 'INSUFFICIENT_RESOURCE_SAMPLE_GAPS')
    _check(all(type(gaps[key]) is int and gaps[key] >= 0
               for key in ('minimum_ms', 'maximum_ms', 'median_ms', 'p95_ms')),
           'INVALID_RESOURCE_SAMPLE_GAPS')
    minimum, maximum, mean = gaps['minimum_ms'], gaps['maximum_ms'], gaps['mean_ms']
    _check(type(mean) in (int, float) and minimum <= mean <= maximum
           and (type(mean) is int or math.isfinite(mean))
           and minimum <= gaps['median_ms'] <= gaps['p95_ms'] <= maximum,
           'INCONSISTENT_RESOURCE_SAMPLE_GAPS')
    if gaps['count'] == 1:
        _check(minimum == maximum == mean == gaps['median_ms'] == gaps['p95_ms'],
               'INCONSISTENT_SINGLE_RESOURCE_SAMPLE_GAP')


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
    validate_observed_sample_gaps(record.get('observed_sample_gaps'), record['samples'])


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
        'inner_report_sha256', 'inner_report_bytes', 'observed_sample_gaps'}
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
            disk = sample_managed_disk_usage(roots).bytes
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
        record['observed_sample_gaps'] = observed_sample_gaps(samples)
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
        if isinstance(exc, ResourceInspectionError):
            record.update(_inspection_failure_details(exc))
        record['inner_failure'] = _inner_failure_receipt(inner_path)
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
        record['observed_sample_gaps'] = observed_sample_gaps(samples)
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
              # Requested sleep only. Per-stage observed gaps include the
              # complete process/disk inspection cost and scheduler delays.
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
