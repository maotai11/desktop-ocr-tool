"""Actual paired crop/controller/serial queue probe, with fixed non-private reports."""
import json

from src import comparison_probe


def test_actual_comparison_lifecycle_uses_fixed_regions_and_preserves_clipboard(qapp, tmp_path):
    from src.comparison import capture

    screen_factory = capture.mss.mss
    previous = qapp.clipboard().text()
    path = tmp_path / 'comparison.json'
    assert comparison_probe.run_comparison_probe(path) == 0
    report = json.loads(path.read_text())
    assert report['passed'] is True
    assert report['checks'] == dict.fromkeys(comparison_probe.CHECK_NAMES, True)
    assert report['probe'] == 'comparison_lifecycle'
    assert report['model_inference_tested'] is False
    assert report['ocr_accuracy_tested'] is False
    assert report['clean_machine_verified'] is False
    assert capture.mss.mss is screen_factory
    assert qapp.clipboard().text() == previous
    assert '甲' not in path.read_text()


def test_capture_failure_replaces_old_pass_without_private_diagnostics(qapp, tmp_path, monkeypatch):
    from src.comparison import capture

    def unavailable(*args):
        raise OSError('C:/Users/PRIVATE_PERSON/SECRET_FILE.png')

    screen_factory = capture.mss.mss
    monkeypatch.setattr(capture, 'capture_pair', unavailable)
    path = tmp_path / 'comparison.json'
    path.write_text('{"passed":true,"private":"stale report"}')
    assert comparison_probe.run_comparison_probe(path) == 1
    report = json.loads(path.read_text())
    assert report['passed'] is False
    assert report['error_code'] == 'COMPARISON_PROBE_CHECK_FAILED'
    assert not report['checks']['same_snapshot_pairs']
    assert capture.mss.mss is screen_factory
    assert all(value not in path.read_text() for value in ('PRIVATE_PERSON', 'SECRET_FILE', 'stale report'))


def test_cli_dispatches_comparison_before_starting_normal_application(tmp_path, monkeypatch):
    from src import main

    called = []
    monkeypatch.setattr(comparison_probe, 'run_comparison_probe', lambda path: called.append(path) or 0)
    path = tmp_path / 'comparison.json'
    assert main.run(['--self-test-comparison', str(path)]) == 0
    assert called == [str(path)]
