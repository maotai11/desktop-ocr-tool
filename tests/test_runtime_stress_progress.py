"""Stress progress must survive interruption without adding model calls."""
from copy import deepcopy
import hashlib
import json
import math
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from src import runtime_stress


@pytest.fixture
def controlled_stress(tmp_path, monkeypatch):
    root = tmp_path/'models'/'validation'
    root.mkdir(parents=True)
    cases = []
    truths = {}
    for index in range(3):
        pixel = (index+1)*20
        image = np.full((3, 4, 3), pixel, np.uint8)
        ok, encoded = cv2.imencode('.png', image)
        assert ok
        data = encoded.tobytes()
        name = f'fixture{index}.png'
        (root/name).write_bytes(data)
        truth = f'fixture-{index}'
        truths[pixel] = truth
        cases.append({'id': f'case-{index}', 'file': name,
                      'image_sha256': hashlib.sha256(data).hexdigest(), 'ground_truth': truth})
    (root/'fixtures.json').write_text(json.dumps({'cases': cases}))
    profiles = ('v6-small', 'v6-medium')
    state = SimpleNamespace(loads=[], calls=[], snapshots=[], failure=None, wrong_at=None, write_failure=False)

    class Engine:
        def __init__(self, model_profile):
            self.profile = model_profile
            self.ready = False
            self.model_identity = {'profile': model_profile}
            self._model_version = model_profile
        def is_ready(self): return self.ready
        def load(self):
            state.loads.append(self.profile)
            if state.failure == 'load':
                raise RuntimeError('synthetic load failure')
            self.ready = True
        def run_ocr(self, image):
            profile_calls = sum(profile == self.profile for profile, _ in state.calls)
            if state.failure in ('inference', 'cancel') and profile_calls == 12:
                if state.failure == 'cancel':
                    raise KeyboardInterrupt()
                raise RuntimeError('synthetic inference failure')
            pixel = int(image[0, 0, 0])
            state.calls.append((self.profile, pixel))
            wrong = state.wrong_at == len(state.calls)
            return {'text': 'wrong' if wrong else truths[pixel], 'status': 'done'}

    import src.ocr.engine
    import src.ocr.model_validator
    import psutil
    monkeypatch.setattr(src.ocr.engine, 'OcrEngine', Engine)
    monkeypatch.setattr(src.ocr.model_validator, 'MODEL_PROFILES', profiles)
    monkeypatch.setattr(src.ocr.model_validator, 'model_root', lambda: tmp_path)
    monkeypatch.setattr(psutil, 'Process', lambda: SimpleNamespace(
        memory_info=lambda: SimpleNamespace(rss=10_000_000, private=8_000_000),
        num_threads=lambda: 2, num_handles=lambda: 5, num_fds=lambda: 5))
    original = runtime_stress.write_validation_report
    def write(path, report):
        state.snapshots.append(deepcopy(report))
        if state.write_failure and len(state.snapshots) == 1:
            raise OSError('synthetic report failure')
        original(path, report)
    monkeypatch.setattr(runtime_stress, 'write_validation_report', write)
    return state


def test_start_load_and_every_ten_iterations_are_saved_without_extra_inference(controlled_stress, tmp_path):
    state = controlled_stress
    path = tmp_path/'report.json'
    assert runtime_stress.run_runtime_stress(path, 23) == 0
    assert state.loads == ['v6-small', 'v6-medium']
    assert state.calls == [(profile, ((index % 3)+1)*20)
                           for profile in state.loads for index in range(23)]
    # Two load saves, 1/10/20/final iteration, unload, once per profile;
    # one final aggregate write. No per-inference report-writing loop.
    assert len(state.snapshots) == 15
    for profile in state.loads:
        snapshots = [snapshot['profiles'][profile] for snapshot in state.snapshots
                     if profile in snapshot['profiles']]
        assert snapshots[0]['iterations_completed'] == 0 and snapshots[0]['engine_ready'] is False
        assert snapshots[1]['iterations_completed'] == 0 and snapshots[1]['engine_ready'] is True
        assert [record['iterations_completed'] for record in snapshots[:7]] == [0, 0, 1, 10, 20, 23, 23]
        assert set(snapshots[-1]['checkpoints']) == {'1', '10', '20', '23'}
        assert all(type(record['elapsed_seconds']) is float and math.isfinite(record['elapsed_seconds'])
                   and record['elapsed_seconds'] >= 0 for record in snapshots)
        assert all(left['elapsed_seconds'] <= right['elapsed_seconds'] for left, right in zip(snapshots, snapshots[1:]))
        for record in snapshots[2:]:
            assert set(record['latency_ms']) == {'p50', 'p95', 'p99'}
            assert all(math.isfinite(value) and value >= 0 for value in record['latency_ms'].values())
    final = json.loads(path.read_text())
    assert final['passed'] and all(record['iterations_completed'] == 23 for record in final['profiles'].values())


@pytest.mark.parametrize('failure,count,ready', [('load', 0, False), ('inference', 12, True), ('cancel', 12, True)])
def test_error_and_cancellation_save_last_completed_count(controlled_stress, tmp_path, failure, count, ready):
    state = controlled_stress
    state.failure = failure
    path = tmp_path/'report.json'
    if failure == 'cancel':
        with pytest.raises(KeyboardInterrupt):
            runtime_stress.run_runtime_stress(path, 23)
    else:
        assert runtime_stress.run_runtime_stress(path, 23) == 1
    final = json.loads(path.read_text())
    record = final['profiles']['v6-small']
    assert final['passed'] is False and record['passed'] is False
    assert record['iterations_completed'] == count and record['engine_ready'] is ready
    assert len(state.calls) == count and state.loads == ['v6-small']
    if count:
        assert set(record['latency_ms']) == {'p50', 'p95', 'p99'}
    assert math.isfinite(record['elapsed_seconds']) and record['elapsed_seconds'] >= 0


def test_mismatch_is_retained_and_prevents_aggregate_pass(controlled_stress, tmp_path):
    state = controlled_stress
    state.wrong_at = 7
    path = tmp_path/'report.json'
    assert runtime_stress.run_runtime_stress(path, 11) == 1
    final = json.loads(path.read_text())
    assert len(state.calls) == 22
    assert final['profiles']['v6-small']['iterations_completed'] == 11
    assert final['profiles']['v6-small']['failures'][0]['iteration'] == 7
    assert final['profiles']['v6-small']['passed'] is False
    assert final['profiles']['v6-medium']['passed'] is True


def test_report_write_failure_stops_before_any_model_call_and_cannot_leave_stale_pass(controlled_stress, tmp_path):
    state = controlled_stress
    state.write_failure = True
    path = tmp_path/'report.json'
    path.write_text(json.dumps({'passed': True}))
    assert runtime_stress.run_runtime_stress(path, 23) == 1
    final = json.loads(path.read_text())
    assert final['passed'] is False
    assert final['profiles']['v6-small']['iterations_completed'] == 0
    assert state.loads == state.calls == []
    assert len(state.snapshots) == 2


def test_default_remains_five_hundred_per_profile_with_bounded_checkpoint_writes(controlled_stress, tmp_path):
    state = controlled_stress
    path = tmp_path/'report.json'
    assert runtime_stress.run_runtime_stress(path) == 0
    final = json.loads(path.read_text())
    assert final['iterations_per_profile'] == 500
    assert len(state.calls) == 1000
    assert all(record['iterations_completed'] == 500 for record in final['profiles'].values())
    assert len(state.snapshots) == 109
    assert all(snapshot['passed'] is False for snapshot in state.snapshots[:-1])
    assert state.snapshots[-1]['passed'] is True
