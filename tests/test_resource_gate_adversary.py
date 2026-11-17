"""A resource release receipt must reject absent, forged-type or over-cap data."""
from copy import deepcopy

import pytest

from scripts.benchmark_resource_budget import ResourceGateError, validate_stage_measurements


def measured():
    return {'samples': 100, 'peak_rss_bytes': 400_000_000,
            'peak_sampled_private_bytes': 450_000_000,
            'peak_job_private_bytes': 470_000_000, 'peak_disk_bytes': 800_000_000,
            'max_processes': 3, 'total_processes': 3, 'job_limit_bytes': 999_997_440,
            'bootloader_owned_before_resume': True, 'active_processes': 0, 'exit_code': 0}


def test_complete_strict_measurement_can_qualify():
    validate_stage_measurements(measured())


@pytest.mark.parametrize('key', ['samples', 'peak_rss_bytes', 'peak_sampled_private_bytes',
                                'peak_job_private_bytes', 'peak_disk_bytes', 'max_processes',
                                'total_processes', 'job_limit_bytes'])
@pytest.mark.parametrize('value', [None, True, 0, -1, '400000000', 400_000_000.0])
def test_missing_or_wrong_type_numbers_cannot_qualify(key, value):
    record = measured()
    record[key] = value
    with pytest.raises(ResourceGateError):
        validate_stage_measurements(record)


@pytest.mark.parametrize('key', ['peak_rss_bytes', 'peak_sampled_private_bytes',
                                'peak_job_private_bytes', 'peak_disk_bytes', 'job_limit_bytes'])
@pytest.mark.parametrize('value', [1_000_000_000, 1_000_000_001])
def test_equality_or_excess_is_rejected_for_each_actual_metric(key, value):
    record = measured()
    record[key] = value
    with pytest.raises(ResourceGateError):
        validate_stage_measurements(record)


@pytest.mark.parametrize('change', [
    {'bootloader_owned_before_resume': False}, {'bootloader_owned_before_resume': 1},
    {'active_processes': 1}, {'active_processes': False},
    {'exit_code': 1}, {'exit_code': False}, {'max_processes': 1}, {'total_processes': 1},
])
def test_unowned_bootloader_or_unreaped_work_cannot_qualify(change):
    record = deepcopy(measured())
    record.update(change)
    with pytest.raises(ResourceGateError):
        validate_stage_measurements(record)
