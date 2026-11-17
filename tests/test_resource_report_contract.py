"""Resource publication uses the exact measured candidate and typed evidence."""
from copy import deepcopy

import pytest

from scripts.benchmark_resource_budget import (ResourceGateError, WORKLOAD_CHECKS,
                                                validate_resource_report)


def receipt():
    base = {'passed': True, 'samples': 50, 'peak_rss_bytes': 500_000_000,
            'peak_sampled_private_bytes': 550_000_000, 'peak_job_private_bytes': 570_000_000,
            'peak_disk_bytes': 800_000_000, 'max_processes': 3, 'total_processes': 4,
            'maximum_active_mei_directories': 2, 'bootloader_owned_before_resume': True,
            'temporary_cleaned': True, 'exit_code': 0, 'active_processes': 0,
            'job_limit_bytes': 999_997_440, 'inner_report_sha256': 'b'*64, 'inner_report_bytes': 1024}
    manual = deepcopy(base)
    manual.update(workload_checks=dict.fromkeys(WORKLOAD_CHECKS, True), manual_rounds_completed=3,
                  fields_each=5, large_source_pixels=6_000_000, native_capture_used=True)
    return {'schema': 1, 'probe': 'resource_budget', 'version': '1.8.0-rc.19', 'platform': 'win32',
            'passed': True, 'limit_bytes': 1_000_000_000, 'sample_interval_ms': 20,
            'source_commit': 'a'*40, 'executable_sha256': 'c'*64,
            'workflow': {'repository': 'maotai11/desktop-ocr-tool', 'run_id': '12345',
                         'run_attempt': '1', 'source_commit': 'a'*40},
            'cold_os_cache_verified': False, 'clean_machine_verified': False,
            'stages': {'cold_application': base, 'manual_invoice_and_helpers': manual}}


def check(value):
    validate_resource_report(value, version='1.8.0-rc.19', executable_sha256='c'*64,
                             source_commit='a'*40, run_id='12345', run_attempt='1')


def test_actual_policy_receipt_and_known_public_auxiliary_fields_can_qualify():
    value = receipt()
    check(value)
    value['nonpublic_error_present'] = False
    value['original_report_sha256'] = 'f'*64
    value['original_report_bytes'] = 1000
    value['stages']['nonpublic_error_present'] = False
    value['stages']['manual_invoice_and_helpers']['workload_checks']['nonpublic_error_present'] = False
    check(value)


@pytest.mark.parametrize('key,value', [
    ('schema', True), ('platform', 'linux'), ('version', '1.8.0-rc.18'), ('probe', 'other'),
    ('passed', 1), ('limit_bytes', 1_073_741_824), ('sample_interval_ms', 100),
    ('source_commit', 'd'*40), ('executable_sha256', 'd'*64),
    ('cold_os_cache_verified', True), ('clean_machine_verified', True),
    ('nonpublic_error_present', True), ('error_code', 'ignored-error'),
])
def test_wrong_identity_limits_or_unsupported_claims_cannot_qualify(key, value):
    report = receipt(); report[key] = value
    with pytest.raises(ResourceGateError): check(report)


@pytest.mark.parametrize('key,value', [('repository', 'other/repo'), ('source_commit', 'd'*40),
                                     ('run_id', 12345), ('run_id', '23456'), ('run_attempt', '2')])
def test_wrong_workflow_or_attempt_cannot_qualify(key, value):
    report = receipt(); report['workflow'][key] = value
    with pytest.raises(ResourceGateError): check(report)


@pytest.mark.parametrize('stage', ['cold_application', 'manual_invoice_and_helpers'])
@pytest.mark.parametrize('key,value', [
    ('temporary_cleaned', False), ('passed', 1), ('maximum_active_mei_directories', 0),
    ('maximum_active_mei_directories', True), ('inner_report_sha256', 'not-hash'),
    ('inner_report_bytes', 0), ('error_code', 'ignored'), ('active_processes', 1),
    ('peak_job_private_bytes', 1_000_000_000), ('samples', None),
])
def test_each_stage_requires_actual_measurements_and_cleanup(stage, key, value):
    report = receipt(); report['stages'][stage][key] = value
    with pytest.raises(ResourceGateError): check(report)


@pytest.mark.parametrize('key,value', [('native_capture_used', False), ('manual_rounds_completed', 3.0),
                                     ('fields_each', 5.0), ('large_source_pixels', 6_000_000.0),
                                     ('max_processes', 2), ('total_processes', 2)])
def test_manual_stage_requires_real_helper_and_exact_workload_counts(key, value):
    report = receipt(); report['stages']['manual_invoice_and_helpers'][key] = value
    with pytest.raises(ResourceGateError): check(report)


@pytest.mark.parametrize('key', sorted(WORKLOAD_CHECKS))
def test_every_real_manual_workload_check_is_required(key):
    report = receipt(); report['stages']['manual_invoice_and_helpers']['workload_checks'][key] = False
    with pytest.raises(ResourceGateError): check(report)


def test_removed_stage_or_unknown_extra_stage_cannot_be_hidden():
    report = receipt(); report['stages'].pop('cold_application')
    with pytest.raises(ResourceGateError): check(report)
    report = receipt(); report['stages']['unmeasured'] = {}
    with pytest.raises(ResourceGateError): check(report)
