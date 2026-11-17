"""A resource release receipt must reject absent, forged-type or over-cap data."""
from copy import deepcopy

import pytest

from scripts.benchmark_resource_budget import (ResourceGateError, observed_sample_gaps,
                                               validate_stage_measurements)


def measured():
    return {'samples': 100, 'peak_rss_bytes': 400_000_000,
            'peak_sampled_private_bytes': 450_000_000,
            'peak_job_private_bytes': 470_000_000, 'peak_disk_bytes': 800_000_000,
            'max_processes': 3, 'total_processes': 3, 'job_limit_bytes': 999_997_440,
            'bootloader_owned_before_resume': True, 'active_processes': 0, 'exit_code': 0,
            'observed_sample_gaps': {'count': 99, 'minimum_ms': 20, 'maximum_ms': 80,
                                     'mean_ms': 30., 'median_ms': 25, 'p95_ms': 50}}


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


def test_actual_gap_statistics_use_upper_median_and_nearest_rank_p95():
    assert observed_sample_gaps([{'elapsed_ms': value} for value in (0, 20, 75, 130, 140)]) == {
        'count': 4, 'minimum_ms': 10, 'maximum_ms': 55,
        'mean_ms': 35., 'median_ms': 55, 'p95_ms': 55}
    samples = [{'elapsed_ms': 0}]
    for gap in range(1, 21):
        samples.append({'elapsed_ms': samples[-1]['elapsed_ms']+gap})
    assert observed_sample_gaps(samples)['p95_ms'] == 19


def test_rare_long_inspection_gap_can_make_mean_exceed_p95():
    samples = [{'elapsed_ms': 0}]
    for gap in [20]*98+[1313]:
        samples.append({'elapsed_ms': samples[-1]['elapsed_ms']+gap})
    record = measured()
    record['observed_sample_gaps'] = observed_sample_gaps(samples)
    assert record['observed_sample_gaps']['mean_ms'] > record['observed_sample_gaps']['p95_ms']
    validate_stage_measurements(record)


@pytest.mark.parametrize('samples', [[{'elapsed_ms': True}, {'elapsed_ms': 20}],
                                   [{'elapsed_ms': 20}, {'elapsed_ms': 10}],
                                   [{}, {'elapsed_ms': 20}]])
def test_untyped_or_nonmonotonic_raw_times_cannot_produce_statistics(samples):
    with pytest.raises(ResourceGateError):
        observed_sample_gaps(samples)


@pytest.mark.parametrize('key,value', [
    ('count', 98), ('count', True), ('count', 99.),
    ('minimum_ms', -1), ('minimum_ms', True), ('minimum_ms', 20.),
    ('maximum_ms', -1), ('maximum_ms', False), ('maximum_ms', 80.),
    ('median_ms', -1), ('median_ms', True), ('median_ms', 25.),
    ('p95_ms', -1), ('p95_ms', True), ('p95_ms', 50.),
    ('mean_ms', True), ('mean_ms', '30'), ('mean_ms', float('nan')),
    ('mean_ms', float('inf')), ('mean_ms', -float('inf')),
    ('mean_ms', 19), ('mean_ms', 81), ('median_ms', 19), ('median_ms', 51),
    ('p95_ms', 81), ('p95_ms', 24), ('unexpected_field', 123),
])
def test_wrong_gap_types_counts_quantiles_or_nonfinite_values_cannot_qualify(key, value):
    record = measured()
    record['observed_sample_gaps'][key] = value
    with pytest.raises(ResourceGateError):
        validate_stage_measurements(record)


@pytest.mark.parametrize('gaps', [None, {}, False, [], {'count': 99}])
def test_absent_actual_gap_measurements_cannot_qualify(gaps):
    record = measured()
    record['observed_sample_gaps'] = gaps
    with pytest.raises(ResourceGateError):
        validate_stage_measurements(record)


def test_single_resource_sample_never_qualifies_as_gap_measurement():
    record = measured()
    record['samples'] = 1
    record['observed_sample_gaps'] = observed_sample_gaps([{'elapsed_ms': 0}])
    with pytest.raises(ResourceGateError):
        validate_stage_measurements(record)


def test_one_observed_gap_requires_all_statistics_to_equal_that_gap():
    record = measured()
    record['samples'] = 2
    record['observed_sample_gaps'] = observed_sample_gaps([{'elapsed_ms': 0}, {'elapsed_ms': 35}])
    validate_stage_measurements(record)
    record['observed_sample_gaps']['minimum_ms'] = 20
    with pytest.raises(ResourceGateError):
        validate_stage_measurements(record)
