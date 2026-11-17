"""The resource workload and external runner reject incomplete real evidence."""
from hashlib import sha256
import json
from pathlib import Path

import numpy as np
import pytest

from scripts import benchmark_resource_budget as gate
from src.core.version import APP_VERSION
from src.invoice_check.rows import detect_active_row
from src.resource_probe import (WorkloadFailure, _require_manual_snapshot,
                                _require_synthetic_values, _run_bounded_raster_checks,
                                _run_checked_image_helper, _raster_error_reason, fixture_image)


@pytest.mark.parametrize('scale', [1, 2])
def test_resource_target_has_exactly_one_selected_row_at_both_scales(qapp, scale):
    import cv2
    image, invoice, table, overrides, columns = fixture_image()
    pixels = np.asarray(image.constBits()).reshape(image.height(), image.width(), 4)
    x, y, width, height = table
    target = pixels[y:y+height, x:x+width, :3].copy()
    if scale != 1:
        target = cv2.resize(target, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
    selected = detect_active_row(target)
    assert selected['row'] == [46*scale, 95*scale]
    assert selected['header'] == [scale, 45*scale]
    assert image.width() <= 1000  # Also fits a normal 1024px CI display.
    assert len(overrides) == len(columns) == 5
    assert invoice[0]+invoice[2] < table[0]


def test_program_roots_do_not_count_unrelated_ci_candidate_zip(tmp_path):
    exe = tmp_path/'InkPick.exe'; exe.write_bytes(b'MZ')
    (tmp_path/'candidate.zip').write_bytes(b'not an application runtime file')
    roots = gate.program_roots(exe)
    assert exe in roots and tmp_path not in roots
    assert not any(path.suffix == '.zip' for path in roots)
    assert tmp_path/'handwriting-source' in roots


def test_child_environment_is_isolated_without_repurposing_home(tmp_path, monkeypatch):
    monkeypatch.setenv('SystemRoot', str(tmp_path/'Windows'))
    monkeypatch.setenv('PYTHONPATH', 'must-not-inherit')
    monkeypatch.setenv('QT_PLUGIN_PATH', 'must-not-inherit')
    monkeypatch.setenv('HOME', 'keep-host-home')
    env = gate.isolated_environment(tmp_path/'profile', tmp_path/'temp', 'Local\\owned-job', 'a'*40)
    assert 'PYTHONPATH' not in env and 'QT_PLUGIN_PATH' not in env
    assert env['HOME'] == 'keep-host-home'
    assert env['DESKTOP_OCR_HOME'] == str(tmp_path/'profile')
    assert env['GITHUB_SHA'] == 'a'*40
    assert env['DESKTOP_OCR_RESOURCE_JOB'] == 'Local\\owned-job'
    assert env['PATH'] == str(tmp_path/'Windows'/'System32')+';'+str(tmp_path/'Windows')


def fake_inner(executable, profile, source, *, manual):
    doc = {'passed': True, 'platform': 'win32', 'frozen': True, 'version': APP_VERSION,
           'executable_sha256': sha256(executable.read_bytes()).hexdigest()}
    if manual:
        doc.update(probe='resource_invoice_workload', native_capture_used=True, source_commit=source,
                   manual_rounds_completed=3, fields_each=5, large_source_pixels=6_000_000,
                   checks=dict.fromkeys(('shared_small_engine_verified', 'three_manual_checks',
                   'only_current_row', 'no_automatic_capture', 'helper_inference_and_reap',
                   'bounded_raster_inference', 'no_history_or_capture_files', 'ownership_drained'), True))
    else:
        doc.update(probe='application_lifecycle', shutdown_clean=True)
    return doc


@pytest.fixture
def simulated_stage(tmp_path, monkeypatch):
    executable = tmp_path/'candidate.exe'; executable.write_bytes(b'MZ synthetic test-only executable')
    changes, closed = {}, []
    class Process:
        def __init__(self, exe, arguments, profile, temporary, source):
            self.exit_queries = 0
            doc = fake_inner(exe, profile, source, manual=arguments[0]=='--self-test-resources')
            for key, value in changes.items():
                if key == 'checks':
                    doc['checks'].update(value)
                else:
                    doc[key] = value
            Path(arguments[1]).write_text(json.dumps(doc))
        def pids(self): return (101, 102, 103)
        def exited(self):
            self.exit_queries += 1
            return self.exit_queries >= 2
        def exit_code(self): return 0
        def accounting(self):
            return {'peak_job_private_bytes': 70_000_000, 'active_processes': 0,
                    'total_processes': 3, 'job_limit_bytes': 999_997_440}
        def close(self): closed.append(True)
    monkeypatch.setattr(gate, 'NativeJobProcess', Process)
    monkeypatch.setattr(gate, '_windows_memory', lambda pid: (16_000_000, 20_000_000))
    return executable, changes, closed


@pytest.mark.parametrize('name,flag', [('cold_application', '--smoke-app'),
                                      ('manual_invoice_and_helpers', '--self-test-resources')])
def test_complete_native_measurements_and_workload_qualify(simulated_stage, name, flag):
    exe, _, closed = simulated_stage
    report, samples = gate.run_stage(exe, name, flag, timeout=5, source_commit='a'*40)
    assert report['passed'] is True and report['temporary_cleaned'] is True
    assert report['samples'] == len(samples) == 2
    assert report['observed_sample_gaps'] == gate.observed_sample_gaps(samples)
    assert report['peak_rss_bytes'] == 48_000_000
    assert report['peak_sampled_private_bytes'] == 60_000_000
    assert closed == [True]


@pytest.mark.parametrize('change', [
    {'frozen': False}, {'platform': 'linux'}, {'passed': 1}, {'source_commit': 'b'*40},
    {'native_capture_used': False}, {'manual_rounds_completed': 2}, {'manual_rounds_completed': 3.0},
    {'fields_each': 5.0}, {'large_source_pixels': 6_000_000.0},
    {'checks': {'only_current_row': False}}, {'checks': {'helper_inference_and_reap': 1}},
    {'checks': {'no_automatic_capture': None}},
])
def test_incomplete_workload_is_rejected_and_native_owner_closed(simulated_stage, change):
    exe, changes, closed = simulated_stage
    changes.update(change)
    report, _ = gate.run_stage(exe, 'manual_invoice_and_helpers', '--self-test-resources',
                               timeout=5, source_commit='a'*40)
    assert report['passed'] is False and report['temporary_cleaned'] is True
    assert report.get('error_code') and closed == [True]


def test_sampler_private_or_disk_excess_fails_without_accepting_inner_success(simulated_stage, monkeypatch):
    exe, _, closed = simulated_stage
    monkeypatch.setattr(gate, '_windows_memory', lambda pid: (16_000_000, 400_000_000))
    report, _ = gate.run_stage(exe, 'manual_invoice_and_helpers', '--self-test-resources',
                               timeout=5, source_commit='a'*40)
    assert report['passed'] is False and report['error_code'] == 'RESOURCE_LIMIT_EXCEEDED'
    assert report['temporary_cleaned'] is True and closed == [True]


def test_workload_exiting_before_second_measurement_cannot_supply_observed_gaps(simulated_stage, monkeypatch):
    exe, _, closed = simulated_stage
    monkeypatch.setattr(gate.NativeJobProcess, 'exited', lambda self: True)
    report, samples = gate.run_stage(exe, 'cold_application', '--smoke-app', timeout=5, source_commit='a'*40)
    assert len(samples) == report['samples'] == 1
    assert report['observed_sample_gaps']['count'] == 0
    assert report['passed'] is False and report['error_code'] == 'INSUFFICIENT_RESOURCE_SAMPLE_GAPS'
    assert report['temporary_cleaned'] is True and closed == [True]


def synthetic_checks():
    return [{'key': key, 'status': 'review',
             'invoice': {'value': 'PRIVATE_SYNTHETIC_VALUE', 'ambiguous': True},
             'row': {'value': 'PRIVATE_SYNTHETIC_VALUE', 'ambiguous': False}}
            for key in ('net_amount', 'tax_amount', 'issue_date', 'invoice_number', 'seller_tax_id')]


def test_successful_synthetic_predicates_do_not_add_diagnostic_report_keys():
    from types import SimpleNamespace
    report = {}
    _require_manual_snapshot(SimpleNamespace(state='ready', last_result={}), [True]*2, report)
    _require_synthetic_values(synthetic_checks(), report)
    assert report == {}


@pytest.mark.parametrize('defect', ['different', 'missing', 'fewer_fields'])
def test_failed_five_field_predicate_preserves_error_and_only_safe_scalar_details(defect):
    checks, report = synthetic_checks(), {}
    if defect == 'different':
        checks[0]['row']['value'] = 'OTHER_PRIVATE_VALUE'
        checks[0]['status'] = 'different'
    elif defect == 'missing':
        checks[0]['invoice']['value'] = None
        checks[0]['status'] = 'missing'
    else:
        checks.pop()
    with pytest.raises(WorkloadFailure, match='^SYNTHETIC_VALUES_DIFFER$'):
        _require_synthetic_values(checks, report)
    diagnostic = report['failure_diagnostics']
    assert diagnostic['kind'] == 'SYNTHETIC_VALUES_DIFFER'
    assert set(diagnostic['fields']) == {check['key'] for check in synthetic_checks()}
    assert all(type(item) is bool for field in diagnostic['fields'].values()
               for key, item in field.items() if key != 'status')
    assert 'PRIVATE' not in json.dumps(report)
    if defect == 'different':
        assert diagnostic['fields']['net_amount']['normalized_equal'] is False
    elif defect == 'missing':
        assert diagnostic['fields']['net_amount']['invoice_value_present'] is False
    else:
        assert diagnostic['fields']['seller_tax_id']['status'] == 'unknown'


def test_failed_manual_snapshot_reports_only_fixed_state_and_counts():
    from types import SimpleNamespace
    report = {}
    with pytest.raises(WorkloadFailure, match='^MANUAL_SNAPSHOT_FAILED$'):
        _require_manual_snapshot(SimpleNamespace(state='error', last_result=None), [True], report)
    assert report == {'failure_diagnostics': {'kind': 'MANUAL_SNAPSHOT_FAILED',
                      'controller_state': 'error', 'has_result': False, 'capture_count': 1}}


@pytest.mark.parametrize('statuses', [('done', 'done', 'done'), ('done', 'needs_review', 'done'),
                                     ('done', 'failed'), ('done', 'done', 'failed')])
def test_same_large_source_finishes_all_three_paths_before_raster_check_passes(qapp, monkeypatch, statuses):
    from PySide6.QtCore import QObject, QTimer, Signal
    class Worker(QObject):
        document_ocr_done = Signal(str, int, str, object)
        def __init__(self):
            super().__init__()
            self.busy = False
            self.calls = []
        def queue_document_ocr(self, token, page, field, image):
            return self.submit('generic', token, page, field, image)
        def queue_invoice_ocr(self, token, page, field, image):
            return self.submit('invoice', token, page, field, image)
        def submit(self, kind, token, page, field, image):
            assert not self.busy
            status = statuses[len(self.calls)]
            self.busy = True
            self.calls.append((kind, np.shares_memory(image, backing), image.shape, image.nbytes))
            def finish():
                self.busy = False
                self.document_ocr_done.emit(token, page, field, {'status': status})
            QTimer.singleShot(0, finish)
            return True
    monkeypatch.setattr('src.core.resource_budget.memory_preflight', lambda **_: None)
    worker = Worker()
    backing = np.full((2000, 3000, 3), 255, np.uint8)
    report = {}
    if 'failed' in statuses:
        with pytest.raises(WorkloadFailure, match='^BOUNDED_RASTER_FAILED$'):
            _run_bounded_raster_checks(qapp, worker, backing, report)
        assert report == {'failure_diagnostics': {'kind': 'RASTER_FAILURE',
            'raster_case': 'wide_invoice' if len(statuses) == 2 else 'max_canvas', 'completed_status': 'failed',
            'raster_error_reason': 'NONE'}}
    else:
        assert _run_bounded_raster_checks(qapp, worker, backing, report) is True
        assert report == {}
    assert [call[0] for call in worker.calls] == ['generic', 'invoice', 'invoice'][:len(statuses)]
    assert all(call[1:] == (True, (2000, 3000, 3), 18_000_000) for call in worker.calls[:2])
    if len(worker.calls) == 3:
        assert worker.calls[-1][1:] == (True, (2448, 2448, 3), 2448*2448*3)
    assert not worker.busy


def test_helper_failure_keeps_known_stage_bools_without_candidates_or_error_text(tmp_path):
    path = tmp_path/'owned-helper.json'
    path.write_text(json.dumps({'probe': 'handwriting_image_lifecycle',
        'error': 'IMAGE_PROBE_RUNTIME_FAILURE', 'passed': False, 'inference_completed': True,
        'failure_reason': 'TIMEOUT',
        'worker_pid_verified': True, 'job_assigned': True, 'cancelled_and_reaped': False,
        'children_reaped': True, 'candidates': ['PRIVATE_OCR_TEXT', '一'], 'private_path': str(tmp_path)}))
    report = {}
    with pytest.raises(WorkloadFailure, match='^REAL_IMAGE_HELPER_FAILED$'):
        _run_checked_image_helper(lambda _: 1, path, report)
    diagnostic = report['failure_diagnostics']
    assert diagnostic['helper_report_status'] == 'COMPLETE'
    assert diagnostic['helper_error'] == 'IMAGE_PROBE_RUNTIME_FAILURE'
    assert diagnostic['helper_failure_reason'] == 'TIMEOUT'
    assert diagnostic['helper_checks']['passed'] is False
    assert diagnostic['helper_checks']['inference_completed'] is True
    assert diagnostic['helper_checks']['cancelled_and_reaped'] is False
    assert diagnostic['expected_candidate_present'] is True
    assert 'PRIVATE' not in json.dumps(report) and str(tmp_path) not in json.dumps(report)


@pytest.mark.parametrize('payload,status', [(None, 'ABSENT'), ('{invalid', 'INVALID_JSON'),
                                           ('[]', 'INVALID_SHAPE'), ('x'*1_048_577, 'TOO_LARGE')],
                         ids=['absent', 'malformed_json', 'non_object', 'oversize'])
def test_helper_failure_receipt_is_bounded_and_malformed_cases_remain_failed(tmp_path, payload, status):
    path = tmp_path/'owned-helper.json'
    if payload is not None:
        path.write_text(payload)
    report = {}
    with pytest.raises(WorkloadFailure, match='^REAL_IMAGE_HELPER_FAILED$'):
        _run_checked_image_helper(lambda _: 1, path, report)
    assert report['failure_diagnostics']['helper_report_status'] == status


def test_successful_helper_adds_no_failure_diagnostic(tmp_path):
    report = {}
    _run_checked_image_helper(lambda _: 0, tmp_path/'unused.json', report)
    assert report == {}


@pytest.mark.parametrize('args,expected', [
    ((), 'PROBE_DEADLINE'), (('手寫辨識逾時，請重試',), 'TIMEOUT'),
    (('無法安全啟動手寫引擎',), 'JOB_ASSIGNMENT_FAILED'),
    (('手寫引擎版本或回傳格式不符，請更新完整免安裝資料夾',), 'PROTOCOL_INVALID'),
    (('PRIVATE/secret/path/手寫辨識逾時，請重試',), 'OTHER'), (('PRIVATE_VALUE',), 'OTHER'),
    (({'private': 'PRIVATE_VALUE'},), 'OTHER'),
])
def test_helper_reason_uses_exact_existing_message_allowlist_only(args, expected):
    from src.handwriting_image_probe import _image_failure_reason
    assert _image_failure_reason(args) == expected


def test_unknown_helper_reason_never_escapes_collector_or_changes_failure(tmp_path):
    path = tmp_path/'owned-helper.json'
    path.write_text(json.dumps({'probe': 'handwriting_image_lifecycle',
        'error': 'IMAGE_PROBE_RUNTIME_FAILURE', 'passed': False,
        'failure_reason': 'PRIVATE_VALUE/'+str(tmp_path)}))
    report = {}
    with pytest.raises(WorkloadFailure, match='^REAL_IMAGE_HELPER_FAILED$'):
        _run_checked_image_helper(lambda _: 1, path, report)
    diagnostic = report['failure_diagnostics']
    assert diagnostic['helper_failure_reason'] == 'OTHER'
    assert diagnostic['helper_error'] == 'IMAGE_PROBE_RUNTIME_FAILURE'
    assert diagnostic['helper_checks']['passed'] is False
    assert 'PRIVATE' not in json.dumps(report) and str(tmp_path) not in json.dumps(report)


@pytest.mark.parametrize('error,expected', [
    ('文字辨識工作量超限，請分段框選；原圖保留供重試', 'RECOGNITION_WORK_BUDGET'),
    ('切片 16/20 失敗: 文字辨識工作量超限，請分段框選；原圖保留供重試', 'RECOGNITION_WORK_BUDGET'),
    ('文字區塊過多，請縮小框選範圍後重試', 'CROP_COUNT_LIMIT'),
    ('文字行過長或過細，請分段框選；原圖保留供重試', 'CROP_WIDTH_LIMIT'),
    ('PRIVATE/path/文字辨識工作量超限，請分段框選；原圖保留供重試', 'OTHER'),
    ('切片 16/20 失敗: PRIVATE/path/value', 'OTHER'),
    ('切片 16/99 失敗: 文字辨識工作量超限，請分段框選；原圖保留供重試', 'OTHER'),
    ({'private': 'PRIVATE_VALUE'}, 'OTHER'), (None, 'NONE'),
])
def test_raster_failure_reason_never_copies_unrecognized_error_text(error, expected):
    assert _raster_error_reason({'error': error}) == expected
