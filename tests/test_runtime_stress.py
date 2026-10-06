import json

import pytest

from src.runtime_stress import run_runtime_stress


@pytest.mark.parametrize('iterations', [0,-1,5001,True,1.5])
def test_invalid_runtime_stress_limit_is_reported_without_inference(tmp_path, iterations):
    path = tmp_path / 'report.json'
    assert run_runtime_stress(path, iterations) == 1
    report = json.loads(path.read_text())
    assert not report['passed'] and '1..5000' in report['error']
    assert report['profiles'] == {}


def test_native_runtime_stress_exercises_both_bundled_profiles(tmp_path):
    path = tmp_path / 'report.json'
    assert run_runtime_stress(path, 3) == 0
    report = json.loads(path.read_text())
    assert report['passed'] and report['clean_machine_verified'] is False
    assert set(report['profiles']) == {'v6-small','v6-medium'}
    for profile, data in report['profiles'].items():
        assert data['passed'] and data['iterations_completed'] == 3
        assert data['identity']['profile'] == profile
        assert data['failures'] == [] and data['after_unload']['rss_bytes'] > 0
