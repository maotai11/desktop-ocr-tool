"""The canonical failed workload is diagnosed before its owned files disappear."""
import json
from pathlib import Path

import pytest

from scripts import benchmark_resource_budget as gate
from tests.test_resource_workload import simulated_stage  # noqa: F401


@pytest.mark.parametrize('raw,status', [
    (b'{', 'INVALID_JSON'), (b'[]', 'INVALID_SHAPE'),
    (b'{"passed":false,"passed":true}', 'INVALID_JSON'),
    (b'{"private":NaN}', 'INVALID_JSON'),
    (b'x' * 1_000_001, 'READ_LIMIT_EXCEEDED'),
], ids=['malformed','non_object','duplicate','nonfinite','oversize'])
def test_bad_or_oversize_inner_report_never_escapes_fixed_failure(tmp_path, raw, status):
    path = tmp_path/'inner.json'; path.write_bytes(raw)
    assert gate._inner_failure_receipt(path) == {'read_status': status}


def test_missing_and_unreadable_inner_reports_are_distinct_and_private(tmp_path, monkeypatch):
    path = tmp_path/'absent'
    assert gate._inner_failure_receipt(path) == {'read_status': 'ABSENT'}
    original_open = Path.open
    def denied(observed, *args, **kwargs):
        if observed == path:
            raise PermissionError('PRIVATE_PATH_OR_MESSAGE')
        return original_open(observed, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'open', denied)
        assert gate._inner_failure_receipt(path) == {'read_status': 'READ_FAILED'}


@pytest.mark.parametrize('kind', ['MANUAL_SNAPSHOT_FAILED','SYNTHETIC_VALUES_DIFFER',
                                  'REAL_IMAGE_HELPER_FAILED','RASTER_FAILURE'])
def test_known_diagnostics_keep_only_fixed_states_and_typed_scalars(tmp_path, kind):
    private = 'PRIVATE_RECOGNIZED_TEXT_OR_PATH'
    diagnostic = {'kind': kind, 'raw': private, 'controller_state': private,
        'has_result': private, 'capture_count': True, 'helper_report_status':'COMPLETE',
        'helper_error': private, 'helper_failure_reason': private, 'helper_return_code': True,
        'helper_checks': {'passed': False, 'inference_completed': True, 'raw': private},
        'expected_candidate_present': private, 'raster_case': 'max_canvas', 'completed_status':'failed',
        'raster_error_reason': private,
        'fields': {key: {'status':'review','invoice_value_present':True, 'row_value_present':False,
            'normalized_equal':False, 'invoice_ambiguous': private, 'row_ambiguous':False, 'value':private}
            for key in ('net_amount','tax_amount','issue_date','invoice_number','seller_tax_id')}}
    value = {'probe':'resource_invoice_workload','phase':'complete','error_code':private,
             'passed':False,'manual_rounds_completed':True,'fields_each':5,'large_source_pixels':6_000_000,
             'checks':{'only_current_row':True,'three_manual_checks':private,'unknown':private},
             'failure_diagnostics':diagnostic,'raw':private}
    path=tmp_path/'inner.json';path.write_text(json.dumps(value))
    report=gate._inner_failure_receipt(path)
    assert report['read_status']=='COMPLETE' and report['error_code']=='OTHER_ERROR'
    assert report['manual_rounds_completed'] is None and report['fields_each']==5
    assert set(report['checks'])==gate.WORKLOAD_CHECKS
    assert report['checks']['only_current_row'] is True and report['checks']['three_manual_checks'] is None
    assert report['failure_diagnostics']['kind']==kind
    assert private not in json.dumps(report)


def test_unknown_diagnostic_shape_cannot_trigger_membership_error(tmp_path):
    path=tmp_path/'inner.json'
    path.write_text(json.dumps({'error_code': [], 'phase': {}, 'failure_diagnostics': {'kind': []}}))
    report=gate._inner_failure_receipt(path)
    assert report['error_code']=='OTHER_ERROR' and report['phase']=='OTHER'
    assert 'failure_diagnostics' not in report


def test_canonical_exit_failure_preserves_first_inner_receipt_then_reaps(simulated_stage, monkeypatch):
    exe, changes, closed=simulated_stage
    changes.update({'passed':False,'error_code':'BOUNDED_RASTER_FAILED','manual_rounds_completed':3,
                    'failure_diagnostics':{'kind':'RASTER_FAILURE','raster_case':'max_canvas','completed_status':'failed'}})
    monkeypatch.setattr(gate.NativeJobProcess,'exit_code',lambda self:1)
    report,_=gate.run_stage(exe,'manual_invoice_and_helpers','--self-test-resources',timeout=5,source_commit='a'*40)
    assert report['passed'] is False and report['error_code']=='WORKLOAD_NONZERO_EXIT'
    inner=report['inner_failure']
    assert inner['error_code']=='BOUNDED_RASTER_FAILED' and inner['manual_rounds_completed']==3
    assert inner['failure_diagnostics']=={'kind':'RASTER_FAILURE','raster_case':'max_canvas',
        'completed_status':'failed','raster_error_reason':'NONE'}
    assert report['temporary_cleaned'] is True and closed==[True]


def test_success_keeps_original_report_schema(simulated_stage):
    exe,_,closed=simulated_stage
    report,_=gate.run_stage(exe,'manual_invoice_and_helpers','--self-test-resources',timeout=5,source_commit='a'*40)
    assert report['passed'] is True and 'inner_failure' not in report
    assert 'inspection_failure_site' not in report and closed==[True]


@pytest.mark.parametrize('reason', ['NONE','PROBE_DEADLINE','TIMEOUT','ASSETS_UNAVAILABLE',
    'JOB_ASSIGNMENT_FAILED','IPC_WRITE_FAILED','PROTOCOL_INVALID','OUTPUT_LIMIT',
    'PROCESS_START_FAILED','PROCESS_EXITED','RECOGNITION_FAILED','OTHER'])
def test_known_helper_reason_survives_without_original_message(tmp_path, reason):
    path=tmp_path/'inner.json'
    path.write_text(json.dumps({'failure_diagnostics':{'kind':'REAL_IMAGE_HELPER_FAILED',
        'helper_failure_reason':reason,'message':'PRIVATE_ABORT_MESSAGE'}}))
    report=gate._inner_failure_receipt(path)
    assert report['failure_diagnostics']['helper_failure_reason']==reason
    assert 'PRIVATE_ABORT_MESSAGE' not in json.dumps(report)


@pytest.mark.parametrize('reason', ['RECOGNITION_WORK_BUDGET','CROP_COUNT_LIMIT','CROP_WIDTH_LIMIT',
    'INVALID_CROP','TILE_COUNT_LIMIT','TILE_PIXEL_LIMIT','MEMORY_BUDGET','RESOURCE_INSPECTION','OTHER','NONE'])
def test_known_raster_reason_retained_without_private_error(tmp_path, reason):
    path=tmp_path/'inner.json'
    path.write_text(json.dumps({'failure_diagnostics':{'kind':'RASTER_FAILURE',
        'raster_error_reason':reason,'error':'PRIVATE_OCR_VALUE_OR_PATH'}}))
    report=gate._inner_failure_receipt(path)
    assert report['failure_diagnostics']['raster_error_reason']==reason
    assert 'PRIVATE_OCR_VALUE_OR_PATH' not in json.dumps(report)
