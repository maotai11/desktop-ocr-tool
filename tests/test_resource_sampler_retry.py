"""Live unpack observation retries only complete, freshly measured disk trees."""
import errno
import json
from hashlib import sha256
from itertools import pairwise
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import benchmark_resource_budget as sampler
from src.core import resource_budget as budget

UNKNOWN_CONTEXT = {'inspection_operation': 'OTHER', 'inspection_context_type': 'OTHER',
                   'inspection_context_operation': 'OTHER', 'inspection_filename_category': 'OTHER',
                   'inspection_filename_status': 'OTHER'}


@pytest.fixture
def clock(monkeypatch):
    state = SimpleNamespace(now=0., sleeps=[])
    def sleep(seconds):
        state.sleeps.append(seconds)
        state.now += seconds
    monkeypatch.setattr(sampler.time, 'monotonic', lambda: state.now)
    monkeypatch.setattr(sampler.time, 'sleep', sleep)
    return state


def test_changed_tree_is_remeasured_without_partial_totals(tmp_path, monkeypatch, clock):
    stable = tmp_path / 'stable.bin'; stable.write_bytes(b'x' * 5000)
    changing = tmp_path / 'changing.bin'; changing.write_bytes(b'x')
    monkeypatch.setattr(budget, 'sys', SimpleNamespace(platform='win32'))
    attempts = []
    def allocation(path):
        if path == changing:
            attempts.append(True)
            if len(attempts) <= 2:
                old = path.stat().st_size
                path.write_bytes(b'x' * (old + 100))
                return old
        return path.stat().st_size
    monkeypatch.setattr(budget, '_windows_allocated_bytes', allocation)
    measured = sampler.sample_managed_disk_usage(iter([changing, stable]))
    sizes = [path.stat() for path in (stable, changing)]
    expected = sum(max(info.st_size, getattr(info, 'st_blocks', 0) * 512) for info in sizes)
    assert measured.bytes == expected and measured.files == 2 and measured.directories == 0
    assert len(attempts) == 3 and clock.sleeps == [.02]


def test_external_retry_cannot_change_product_writer_admission(tmp_path, monkeypatch, clock):
    target = tmp_path / 'growing.bin'; target.write_bytes(b'x')
    monkeypatch.setattr(budget, 'sys', SimpleNamespace(platform='win32'))
    monkeypatch.setattr(budget, 'managed_storage_roots', lambda *a, **k: (target,))
    calls = []
    def allocation(path):
        size = path.stat().st_size
        calls.append(size)
        path.write_bytes(b'x' * (size + 1))
        return size
    monkeypatch.setattr(budget, '_windows_allocated_bytes', allocation)
    with pytest.raises(budget.ResourceMetadataChanged):
        with budget.disk_quota_guard(reserve_bytes=1):
            pytest.fail('An unstable product write must remain blocked')
    assert len(calls) == 2 and clock.sleeps == [] and budget._reserved_bytes == 0


@pytest.mark.parametrize('failure', [budget.ResourceInspectionError(),
    budget.ResourceInspectionError(native_error=2), budget.ResourceInspectionError(native_error=5),
    budget.ResourceBudgetExceeded(), PermissionError(), FileNotFoundError(), RuntimeError()])
def test_other_failures_are_never_retried(monkeypatch, clock, failure):
    calls = []
    def fail(roots):
        calls.append(tuple(roots))
        raise failure
    monkeypatch.setattr(sampler, 'managed_disk_usage', fail)
    with pytest.raises(type(failure)):
        sampler.sample_managed_disk_usage(['owned'])
    assert len(calls) == 1 and clock.sleeps == []


def test_continuous_metadata_changes_stop_after_fifty_whole_scans(monkeypatch, clock):
    calls = []
    def fail(roots):
        calls.append(tuple(roots))
        raise budget.ResourceMetadataChanged()
    monkeypatch.setattr(sampler, 'managed_disk_usage', fail)
    with pytest.raises(budget.ResourceMetadataChanged):
        sampler.sample_managed_disk_usage(iter(['one', 'two']))
    assert calls == [('one', 'two')] * 50 and clock.sleeps == [.02] * 49


@pytest.mark.parametrize('last_result', ['changed', 'stable'])
def test_whole_rescan_deadline_can_reject_before_attempt_limit(monkeypatch, clock, last_result):
    calls = []
    def scan(roots):
        calls.append(True)
        clock.now += 2.6
        if len(calls) == 1 or last_result == 'changed':
            raise budget.ResourceMetadataChanged()
        return budget.DiskUsage(10, 1, 0)
    monkeypatch.setattr(sampler, 'managed_disk_usage', scan)
    with pytest.raises(budget.ResourceMetadataChanged):
        sampler.sample_managed_disk_usage(['owned'])
    assert len(calls) == 2 and clock.sleeps == [.02]


def test_no_retry_starts_when_twenty_ms_wait_would_exhaust_deadline(monkeypatch, clock):
    calls = []
    def scan(roots):
        calls.append(True)
        clock.now = 4.99
        raise budget.ResourceMetadataChanged()
    monkeypatch.setattr(sampler, 'managed_disk_usage', scan)
    with pytest.raises(budget.ResourceMetadataChanged):
        sampler.sample_managed_disk_usage(['owned'])
    assert len(calls) == 1 and not clock.sleeps


def test_first_stable_scan_still_must_finish_within_deadline(monkeypatch, clock):
    calls = []
    def scan(roots):
        calls.append(True)
        clock.now += 5.2
        return budget.DiskUsage(10, 1, 0)
    monkeypatch.setattr(sampler, 'managed_disk_usage', scan)
    with pytest.raises(budget.ResourceMetadataChanged):
        sampler.sample_managed_disk_usage(['owned'])
    assert calls == [True] and not clock.sleeps


def test_observed_gap_includes_whole_rescan_work_and_wait(monkeypatch, clock):
    calls = []
    def scan(roots):
        calls.append(True)
        clock.now += .1
        if len(calls) == 1:
            raise budget.ResourceMetadataChanged()
        return budget.DiskUsage(7, 1, 0)
    monkeypatch.setattr(sampler, 'managed_disk_usage', scan)
    before = round(clock.now * 1000)
    assert sampler.sample_managed_disk_usage(['owned']).bytes == 7
    after = round(clock.now * 1000)
    gaps = sampler.observed_sample_gaps([{'elapsed_ms': before}, {'elapsed_ms': after}])
    assert gaps['minimum_ms'] == gaps['maximum_ms'] == gaps['mean_ms'] == 220


def test_unpack_stabilizes_after_fifth_scan_but_within_original_deadline(monkeypatch, clock):
    calls = []
    def scan(roots):
        calls.append(True)
        clock.now += .01
        if len(calls) <= 10:
            raise budget.ResourceMetadataChanged()
        return budget.DiskUsage(7, 1, 0)
    monkeypatch.setattr(sampler, 'managed_disk_usage', scan)
    assert sampler.sample_managed_disk_usage(['owned']).bytes == 7
    assert len(calls) == 11 and clock.sleeps == [.02] * 10
    assert clock.now < 1.


def test_failure_origin_distinguishes_real_node_change_from_scan_deadline(tmp_path, monkeypatch, clock):
    target = tmp_path / 'growing.bin'; target.write_bytes(b'x')
    monkeypatch.setattr(budget, 'sys', SimpleNamespace(platform='win32'))
    def allocation(path):
        size = path.stat().st_size
        path.write_bytes(b'x' * (size + 1))
        return size
    monkeypatch.setattr(budget, '_windows_allocated_bytes', allocation)
    with pytest.raises(budget.ResourceMetadataChanged) as caught:
        sampler.sample_managed_disk_usage([target])
    detail = sampler._inspection_failure_details(caught.value)
    assert detail == {'inspection_failure_site': 'NODE_CHANGED',
                      'inspection_attempts': 50, 'inspection_elapsed_ms': 980,
                      **UNKNOWN_CONTEXT, 'inspection_operation': 'NODE_CHANGED'}
    clock.now = 0
    def slow_stable(roots):
        clock.now += 5.2
        return budget.DiskUsage(1, 1, 0)
    monkeypatch.setattr(sampler, 'managed_disk_usage', slow_stable)
    with pytest.raises(budget.ResourceMetadataChanged) as caught:
        sampler.sample_managed_disk_usage([target])
    assert sampler._inspection_failure_details(caught.value) == {
        'inspection_failure_site': 'SCAN_DEADLINE', 'inspection_attempts': 1,
        'inspection_elapsed_ms': 5200, **UNKNOWN_CONTEXT}


def test_unknown_rethrown_origin_is_not_reported_as_stable_scan_deadline(monkeypatch, clock):
    def fail(roots):
        raise budget.ResourceMetadataChanged()
    monkeypatch.setattr(sampler, 'managed_disk_usage', fail)
    with pytest.raises(budget.ResourceMetadataChanged) as caught:
        sampler.sample_managed_disk_usage(['private-location'])
    detail = sampler._inspection_failure_details(caught.value)
    assert detail['inspection_failure_site'] == 'OTHER'
    assert set(detail) == {'inspection_failure_site','inspection_attempts','inspection_elapsed_ms',
                           *UNKNOWN_CONTEXT}


def test_stage_failure_retains_fixed_diagnostic_and_reaps_owner(tmp_path, monkeypatch, clock):
    closed = []
    class Process:
        def __init__(self, *args): pass
        def pids(self): return ()
        def close(self): closed.append(True)
    def slow_stable(roots):
        clock.now += 5.2
        return budget.DiskUsage(1, 1, 0)
    monkeypatch.setattr(sampler, 'NativeJobProcess', Process)
    monkeypatch.setattr(sampler, 'managed_disk_usage', slow_stable)
    report, samples = sampler.run_stage(tmp_path/'app.exe', 'cold_application', '--smoke-app', timeout=5)
    assert not samples and report['passed'] is False
    assert report['inspection_failure_site'] == 'SCAN_DEADLINE'
    assert report['inspection_attempts'] == 1 and report['inspection_elapsed_ms'] == 5200
    assert report['temporary_cleaned'] is True and closed == [True]
    assert str(tmp_path) not in str(report)


def test_stable_scan_can_use_measured_five_second_observation_budget(monkeypatch, clock):
    def scan(roots):
        clock.now += 1.047
        return budget.DiskUsage(7, 1, 0)
    monkeypatch.setattr(sampler, 'managed_disk_usage', scan)
    assert sampler.sample_managed_disk_usage(['owned']).bytes == 7
    assert clock.now == 1.047 and clock.sleeps == []


@pytest.mark.parametrize('native_error', [2, 3, 5, 32, 33])
def test_canonical_inspection_failure_retains_native_code_and_cleans_up(tmp_path, monkeypatch, clock, native_error):
    closed = []
    class Process:
        def __init__(self, *args): pass
        def pids(self): return ()
        def close(self): closed.append(True)
    def unavailable(roots):
        error = budget.ResourceInspectionError(native_error=native_error)
        error.args = ('PRIVATE_RAW_PATH_AND_TEXT',)
        raise error
    monkeypatch.setattr(sampler, 'NativeJobProcess', Process)
    monkeypatch.setattr(sampler, 'managed_disk_usage', unavailable)
    record, samples = sampler.run_stage(tmp_path/'app.exe', 'cold_application', '--smoke-app', timeout=5)
    assert record['passed'] is False and record['error_code'] == 'ResourceInspectionError'
    assert record['inspection_failure_site'] == 'OTHER'
    assert record['inspection_native_error'] == native_error
    assert not samples and clock.sleeps == [] and closed == [True]
    assert record['temporary_cleaned'] is True
    assert 'PRIVATE_RAW_PATH_AND_TEXT' not in str(record) and str(tmp_path) not in str(record)


@pytest.mark.parametrize('native_error', [True, -1, 0x100000000, 'PRIVATE_NATIVE_ERROR'])
def test_inspection_diagnostic_rejects_non_native_or_unbounded_codes(native_error):
    detail = sampler._inspection_failure_details(budget.ResourceInspectionError(native_error=native_error))
    assert detail == {'inspection_failure_site': 'OTHER', **UNKNOWN_CONTEXT}


def test_real_walk_failure_has_fixed_origin_without_private_path(tmp_path, monkeypatch):
    target = tmp_path/'owned-private.bin'; target.write_bytes(b'x')
    real_lstat = Path.lstat
    def denied(path, *args, **kwargs):
        if path == target:
            raise PermissionError('PRIVATE_WALK_ERROR')
        return real_lstat(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'lstat', denied)
        with pytest.raises(budget.ResourceInspectionError) as caught:
            budget.managed_disk_usage([target])
    detail = sampler._inspection_failure_details(caught.value)
    assert detail == {'inspection_failure_site': 'STORAGE_WALK', **UNKNOWN_CONTEXT,
                      'inspection_operation': 'INITIAL_LSTAT',
                      'inspection_context_type': 'PermissionError',
                      'inspection_context_operation': 'INITIAL_LSTAT'}


def test_real_directory_list_conversion_keeps_native_context_and_exactly_one_fresh_stat(tmp_path, monkeypatch):
    target = tmp_path/'inkpick-resource-private'/'temp'/'_MEI123'
    target.mkdir(parents=True)
    original = PermissionError(errno.EACCES, 'PRIVATE_MESSAGE', str(target))
    original.winerror = 5
    real_iterdir = Path.iterdir
    def denied(path):
        if path == target:
            raise original
        return real_iterdir(path)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'iterdir', denied)
        with pytest.raises(budget.ResourceInspectionError) as caught:
            budget.managed_disk_usage([target])
    assert caught.value.__context__ is original and caught.value.__suppress_context__ is True
    calls = []
    real_lstat = sampler.os.lstat
    def observed(filename):
        calls.append(filename)
        return real_lstat(filename)
    with monkeypatch.context() as patch:
        patch.setattr(sampler.os, 'lstat', observed)
        details = sampler._inspection_failure_details(caught.value)
    assert calls == [str(target)]
    assert details == {'inspection_failure_site': 'STORAGE_WALK',
                       'inspection_operation': 'MEASURE_OS_ERROR',
                       'inspection_context_type': 'PermissionError',
                       'inspection_context_operation': 'DIRECTORY_LIST',
                       'inspection_context_winerror': 5, 'inspection_context_errno': errno.EACCES,
                       'inspection_filename_category': 'ACTIVE_MEI',
                       'inspection_filename_status': 'PRESENT_DIR'}
    public = json.dumps(details)
    assert all(value not in public for value in ('PRIVATE_MESSAGE', str(tmp_path), '_MEI123', 'private'))
    assert caught.value.__context__ is original


def test_real_verification_stat_failure_retains_distinct_operation_after_guard_changes(tmp_path, monkeypatch):
    target = tmp_path/'private-verify.bin'; target.write_bytes(b'keep')
    original = PermissionError(errno.EACCES, 'PRIVATE_VERIFY_MESSAGE', str(target))
    original.winerror = 32
    real_lstat, calls = Path.lstat, []
    def verification_denied(path, *args, **kwargs):
        if path == target:
            calls.append(True)
            if len(calls) == 2:
                raise original
        return real_lstat(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'lstat', verification_denied)
        with pytest.raises(budget.ResourceInspectionError) as caught:
            budget.managed_disk_usage([target])
    assert len(calls) == 2 and caught.value.__context__ is original
    detail = sampler._inspection_failure_details(caught.value)
    assert detail['inspection_operation'] == 'MEASURE_OS_ERROR'
    assert detail['inspection_context_operation'] == 'VERIFY_LSTAT'
    assert detail['inspection_context_winerror'] == 32
    assert detail['inspection_filename_status'] == 'PRESENT_FILE'
    assert 'PRIVATE' not in json.dumps(detail) and str(tmp_path) not in json.dumps(detail)


def test_real_link_rejection_cannot_be_misclassified_as_old_measure_line(tmp_path, monkeypatch):
    target = tmp_path/'private-link-state.bin'; target.write_bytes(b'keep')
    real_lstat = Path.lstat
    def linked_mode(path, *args, **kwargs):
        info = real_lstat(path, *args, **kwargs)
        if path == target:
            return SimpleNamespace(st_mode=0o120777, st_ino=info.st_ino, st_dev=info.st_dev)
        return info
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'lstat', linked_mode)
        with pytest.raises(budget.ResourceInspectionError) as caught:
            budget.managed_disk_usage([target])
    detail = sampler._inspection_failure_details(caught.value)
    assert detail['inspection_failure_site'] == 'STORAGE_WALK'
    assert detail['inspection_operation'] == 'LINK_REJECTED'
    assert detail['inspection_context_type'] == 'OTHER'


@pytest.mark.parametrize('winerror,os_errno', [(True, True), (-1, -1),
    (0x100000000, 0x80000000), ('PRIVATE_WINERROR', 'PRIVATE_ERRNO')])
def test_context_rejects_bool_unbounded_and_private_error_codes(winerror, os_errno):
    original = OSError()
    original.winerror, original.errno = winerror, os_errno
    error = budget.ResourceInspectionError()
    error.__context__ = original
    details = sampler._inspection_failure_details(error)
    assert details['inspection_context_type'] == 'OSError'
    assert 'inspection_context_winerror' not in details and 'inspection_context_errno' not in details
    assert 'PRIVATE' not in json.dumps(details)


def test_context_cycle_and_eight_node_bound_do_not_query_unknown_filename(monkeypatch):
    root = budget.ResourceInspectionError()
    nodes = [RuntimeError('PRIVATE_CONTEXT') for _ in range(7)]
    root.__cause__ = nodes[0]
    for left, right in pairwise(nodes):
        left.__cause__ = right
    too_deep = PermissionError(errno.EACCES, 'PRIVATE_ERROR', 'PRIVATE_FILENAME')
    too_deep.winerror = 5
    nodes[-1].__cause__ = too_deep
    nodes[-1].__context__ = root
    def forbidden(*args, **kwargs):
        pytest.fail('An exception beyond eight nodes must not trigger a stat')
    with monkeypatch.context() as patch:
        patch.setattr(sampler.os, 'lstat', forbidden)
        detail = sampler._inspection_failure_details(root)
    assert detail == {'inspection_failure_site': 'OTHER', **UNKNOWN_CONTEXT}
    assert 'PRIVATE' not in json.dumps(detail)


def test_context_cycle_does_not_hide_a_reachable_native_failure():
    root = budget.ResourceInspectionError()
    link = RuntimeError('PRIVATE_CHAIN')
    original = PermissionError(errno.EACCES, 'PRIVATE')
    original.winerror = 32
    root.__cause__, link.__cause__, link.__context__ = link, root, original
    detail = sampler._inspection_failure_details(root)
    assert detail['inspection_context_type'] == 'PermissionError'
    assert detail['inspection_context_winerror'] == 32
    assert detail['inspection_context_errno'] == errno.EACCES
    assert 'PRIVATE' not in json.dumps(detail)


@pytest.mark.parametrize('filename', [None, '', 'x'*32769, Path('PRIVATE_PATH')],
                         ids=['none', 'empty', 'too-long', 'path-object'])
def test_fresh_stat_rejects_absent_nonstring_or_unbounded_filename(monkeypatch, filename):
    original = PermissionError()
    original.filename = filename
    error = budget.ResourceInspectionError()
    error.__context__ = original
    calls = []
    with monkeypatch.context() as patch:
        patch.setattr(sampler.os, 'lstat', lambda value: calls.append(value))
        detail = sampler._inspection_failure_details(error)
    assert calls == [] and detail['inspection_filename_status'] == 'OTHER'
    assert 'PRIVATE_PATH' not in json.dumps(detail)


def test_fresh_stat_requires_exact_string_not_a_subclass(monkeypatch):
    class Filename(str): pass
    original = PermissionError()
    original.filename = Filename('PRIVATE_FILENAME')
    error = budget.ResourceInspectionError()
    error.__context__ = original
    calls = []
    with monkeypatch.context() as patch:
        patch.setattr(sampler.os, 'lstat', lambda value: calls.append(value))
        detail = sampler._inspection_failure_details(error)
    assert not calls and detail['inspection_filename_status'] == 'OTHER'


@pytest.mark.parametrize('failure,status', [(FileNotFoundError(), 'ABSENT'),
    (PermissionError(), 'ACCESS_DENIED'), (OSError(), 'OTHER'), (RuntimeError('PRIVATE'), 'OTHER')])
def test_fresh_stat_failures_never_replace_original_inspection_or_leak(monkeypatch, failure, status):
    original = PermissionError(errno.EACCES, 'PRIVATE_MESSAGE', 'PRIVATE_FILENAME')
    error = budget.ResourceInspectionError()
    error.__context__ = original
    calls = []
    def failed(filename):
        calls.append(filename)
        raise failure
    with monkeypatch.context() as patch:
        patch.setattr(sampler.os, 'lstat', failed)
        details = sampler._inspection_failure_details(error)
    assert calls == ['PRIVATE_FILENAME']
    assert details['inspection_filename_status'] == status
    assert error.__context__ is original
    assert 'PRIVATE' not in json.dumps(details)


def test_successful_stage_has_no_failure_projection_or_fresh_diagnostic_stat(tmp_path, monkeypatch, clock):
    executable = tmp_path/'candidate.exe'; executable.write_bytes(b'fixture')
    closed = []
    class Process:
        polls = 0
        def __init__(self, exe, arguments, profile, temporary, source_commit):
            Path(arguments[1]).write_text(json.dumps({'passed': True, 'frozen': True, 'platform': 'win32',
                'version': sampler.APP_VERSION, 'executable_sha256': sha256(exe.read_bytes()).hexdigest(),
                'probe': 'application_lifecycle', 'shutdown_clean': True}), encoding='utf-8')
        def pids(self): return (1, 2)
        def exited(self):
            self.polls += 1
            return self.polls == 2
        def exit_code(self): return 0
        def accounting(self):
            return {'peak_job_private_bytes': 100, 'active_processes': 0,
                    'total_processes': 2, 'job_limit_bytes': 999999999}
        def close(self): closed.append(True)
    def forbidden(error):
        pytest.fail('Successful stages must not inspect exception filenames')
    monkeypatch.setattr(sampler, 'NativeJobProcess', Process)
    monkeypatch.setattr(sampler, '_windows_memory', lambda pid: (100, 100))
    monkeypatch.setattr(sampler, 'sample_managed_disk_usage', lambda roots: budget.DiskUsage(100, 1, 1))
    monkeypatch.setattr(sampler, '_inspection_context_details', forbidden)
    record, samples = sampler.run_stage(executable, 'cold_application', '--smoke-app', timeout=120)
    assert record['passed'] and len(samples) == 2 and record['temporary_cleaned']
    assert not any(key.startswith('inspection_') for key in record) and closed == [True]
