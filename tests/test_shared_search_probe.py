"""Bounded real-controller search proof, without private screens or model claims."""
import json
import os
from types import SimpleNamespace

from src import shared_search_probe as probe


def test_cli_shared_search_probe_keeps_private_state_and_proves_logical_lifecycle(qapp, tmp_path):
    from src.comparison import shared_capture
    provider = shared_capture.mss.mss
    before_clipboard = qapp.clipboard().text()
    before_home = os.environ.get('DESKTOP_OCR_HOME')
    report_path = tmp_path / 'shared.json'
    assert probe.run_shared_search_probe(report_path) == 0
    report = json.loads(report_path.read_text(encoding='utf-8'))
    assert report['passed'] is True and report['checks'] == dict.fromkeys(probe.CHECK_NAMES, True)
    assert report['probe'] == 'shared_search_lifecycle'
    assert report['model_inference_tested'] is False and report['ocr_accuracy_tested'] is False
    assert report['clean_machine_verified'] is False
    if report['platform'] == 'win32':
        assert report['overlay_capture_verified'] is True
    else:
        assert report['overlay_capture_verified'] is False
    assert shared_capture.mss.mss is provider
    assert qapp.clipboard().text() == before_clipboard
    assert os.environ.get('DESKTOP_OCR_HOME') == before_home
    assert not any(word in report_path.read_text(encoding='utf-8') for word in ('訂單AB', '新捲動', '過期列'))


def test_capture_failure_replaces_old_success_without_private_paths(qapp, tmp_path, monkeypatch):
    from src.comparison import shared_capture
    factory = shared_capture.mss.mss
    def unavailable(*args):
        raise OSError('C:/Users/PRIVATE_SEARCH/SECRET_SCREEN.png')
    monkeypatch.setattr(shared_capture, 'capture_shared', unavailable)
    path = tmp_path / 'shared.json'
    path.write_text('{"passed": true, "text": "old PRIVATE_WORDS"}')
    assert probe.run_shared_search_probe(path) == 1
    report = json.loads(path.read_text(encoding='utf-8'))
    assert report['passed'] is False and report['error_code'] == 'SHARED_SEARCH_PROBE_CHECK_FAILED'
    assert not any(word in path.read_text(encoding='utf-8') for word in ('PRIVATE_SEARCH', 'SECRET_SCREEN', 'PRIVATE_WORDS'))
    assert shared_capture.mss.mss is factory


def test_cli_dispatches_shared_search_before_normal_application(tmp_path, monkeypatch):
    from src import main
    called = []
    monkeypatch.setattr(probe, 'run_shared_search_probe', lambda path: called.append(path) or 0)
    path = tmp_path / 'shared.json'
    assert main.run(['--self-test-shared-search', str(path)]) == 0
    assert called == [str(path)]


def test_windows_run_requires_actual_native_pixel_proof(qapp, tmp_path, monkeypatch):
    from src import shared_search_windows_probe as native
    monkeypatch.setattr(probe, 'sys', SimpleNamespace(platform='win32'))
    monkeypatch.setattr(probe, '_logical_checks', lambda app: dict.fromkeys(probe.CHECK_NAMES, True))
    monkeypatch.setattr(native, 'verify_overlay_capture', lambda app: False)
    path = tmp_path / 'native-refused.json'
    assert probe.run_shared_search_probe(path) == 1
    report = json.loads(path.read_text(encoding='utf-8'))
    assert report['overlay_capture_verified'] is False and report['passed'] is False
    assert report['error_code'] == 'SHARED_SEARCH_PROBE_CHECK_FAILED'
