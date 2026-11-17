"""Public inspection failures expose bounded facts without qualifying a run."""
import json

import pytest

from resource_release_fixtures import receipt
from scripts import prepare_public_validation as public
from scripts.benchmark_resource_budget import ResourceGateError, validate_resource_report


def failed_report():
    value = receipt(public.VERSION, 'c' * 64, 'a' * 40)
    value['passed'] = False
    value['stages']['cold_application']['passed'] = False
    return value


def test_failed_stage_retains_only_fixed_inspection_facts():
    value = failed_report()
    stage = value['stages']['cold_application']
    facts = {'inspection_failure_site': 'STORAGE_WALK', 'inspection_operation': 'MEASURE_OS_ERROR',
             'inspection_context_operation': 'DIRECTORY_LIST', 'inspection_context_type': 'PermissionError',
             'inspection_context_winerror': 5, 'inspection_context_errno': 13,
             'inspection_filename_category': 'ACTIVE_MEI', 'inspection_filename_status': 'ABSENT',
             'inspection_native_error': 5, 'inspection_attempts': 1, 'inspection_elapsed_ms': 200}
    stage.update(facts, error_code='PRIVATE_ERROR', filename='PRIVATE_PATH',
                 inspection_unknown='PRIVATE_UNKNOWN', inner_failure={'text': 'PRIVATE_OCR'})
    clean = public.safe_fields(value)
    projected = clean['stages']['cold_application']
    assert {key: projected[key] for key in facts} == facts
    assert projected['nonpublic_error_present'] is True
    assert 'PRIVATE_' not in json.dumps(clean)
    assert public.observed_result(clean) == 'FAIL_AS_RECORDED'
    with pytest.raises(ResourceGateError):
        validate_resource_report(clean, version=public.VERSION, executable_sha256='c' * 64,
                                 source_commit='a' * 40)


@pytest.mark.parametrize('key', sorted(public.INSPECTION_ENUMS))
@pytest.mark.parametrize('bad', ['PRIVATE_PATH', True, 1, {}, []], ids=['private', 'bool', 'int', 'dict', 'list'])
def test_inspection_enum_rejects_nonfixed_values(key, bad):
    value = failed_report()
    value['stages']['cold_application'][key] = bad
    clean = public.safe_fields(value)
    stage = clean['stages']['cold_application']
    assert key not in stage
    assert stage['invalid_allowlisted_fields'] == [key]
    assert 'PRIVATE_PATH' not in json.dumps(clean)
    assert public.observed_result(clean) == 'INVALID_FIELDS'


@pytest.mark.parametrize('key,maximum', sorted(public.INSPECTION_NUMBERS.items()))
def test_inspection_numbers_use_exact_bounded_integers(key, maximum):
    class CustomInt(int):
        pass
    for number in (0, maximum):
        value = failed_report()
        value['stages']['cold_application'][key] = number
        clean = public.safe_fields(value)
        assert clean['stages']['cold_application'][key] == number
        assert public.observed_result(clean) == 'FAIL_AS_RECORDED'
    for bad in (True, -1, maximum + 1, 1.0, 'PRIVATE_NUMBER', CustomInt(1)):
        value = failed_report()
        value['stages']['cold_application'][key] = bad
        clean = public.safe_fields(value)
        assert key not in clean['stages']['cold_application']
        assert 'PRIVATE_NUMBER' not in json.dumps(clean)
        assert public.observed_result(clean) == 'INVALID_FIELDS'


@pytest.mark.parametrize('location', ['success_stage', 'missing_passed', 'other_probe', 'root', 'nested_check'])
def test_inspection_facts_cannot_enter_success_or_unrelated_reports(location):
    value = receipt(public.VERSION, 'c' * 64, 'a' * 40)
    target = value['stages']['cold_application']
    if location == 'missing_passed':
        del target['passed']
    elif location == 'other_probe':
        value['probe'] = 'application_lifecycle'
        target['passed'] = False
    elif location == 'root':
        target = value
    elif location == 'nested_check':
        target['checks'] = {'cold_application': {'passed': False}}
        target = target['checks']['cold_application']
    target['inspection_context_winerror'] = 5
    clean = public.safe_fields(value)
    assert public.observed_result(clean) == 'INVALID_FIELDS'
    assert '"inspection_context_winerror": 5' not in json.dumps(clean)


def test_success_schema_unchanged_and_all_owned_error_types_are_allowlisted():
    from scripts.benchmark_resource_budget import _OS_CONTEXT_TYPES
    assert set(_OS_CONTEXT_TYPES.values()) <= public.INSPECTION_ENUMS['inspection_context_type']
    value = receipt(public.VERSION, 'c' * 64, 'a' * 40)
    clean = public.safe_fields(value)
    assert 'inspection_' not in json.dumps(clean)
    validate_resource_report(clean, version=public.VERSION, executable_sha256='c' * 64,
                             source_commit='a' * 40, run_id='1234', run_attempt='1')
    assert public.observed_result(clean) == 'PASS_AS_RECORDED_NOT_OVERALL_APPROVAL'
