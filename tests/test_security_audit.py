"""A failed, skipped, mismatched or vulnerable dependency audit cannot pass."""
import json
from pathlib import Path

import pytest

from scripts.audit_dependencies import evaluate, run


def test_audit_matches_complete_inventory_and_keeps_identified_findings():
    raw = {'dependencies': [{'name': 'Pillow', 'version': '12.1.1', 'vulns': [
        {'id': 'GHSA-test-only', 'aliases': ['CVE-2026-TEST'], 'fix_versions': ['13.0.0']}]}]}
    findings = evaluate(raw, {'pillow': '12.1.1'})
    assert findings[0]['package'] == 'pillow'
    assert findings[0]['fix_versions'] == ['13.0.0']
    assert evaluate({'dependencies': [{'name': 'Pillow', 'version': '13.0.0', 'vulns': []}]},
                    {'pillow': '13.0.0'}) == []


@pytest.mark.parametrize('dependency', [
    [], [{'name': 'pillow', 'version': '12.1.1', 'vulns': []}],
    [{'name': 'pillow', 'version': '13.0.0', 'skip_reason': 'not found', 'vulns': []}],
    [{'name': 'pillow', 'version': '13.0.0', 'vulns': []}] * 2,
])
def test_audit_rejects_missing_wrong_skipped_or_duplicate_packages(dependency):
    with pytest.raises(ValueError):
        evaluate({'dependencies': dependency}, {'pillow': '13.0.0'})


def test_collection_or_scanner_failure_is_not_zero_vulnerabilities(tmp_path, monkeypatch):
    inventory = tmp_path / 'inventory.json'
    inventory.write_text(json.dumps({'installed': {'pillow': '13.0.0'}}))
    output = tmp_path / 'audit.json'
    output.write_text('{"passed": true}')
    def unavailable(*args, **kwargs):
        assert json.loads(output.read_text())['passed'] is False
        raise OSError('PRIVATE_SCANNER_PATH')
    monkeypatch.setattr('scripts.audit_dependencies.subprocess.run', unavailable)
    assert run(inventory, 'missing-scanner', output) == 1
    report = json.loads(output.read_text())
    assert not report['passed'] and report['error_code'] == 'DEPENDENCY_AUDIT_INCOMPLETE'
    assert 'PRIVATE_' not in output.read_text()


def test_security_scanner_runs_before_tests_and_build_with_no_ignored_results():
    import yaml
    root = Path(__file__).resolve().parents[1]
    workflow = yaml.load((root / '.github/workflows/validate.yml').read_text(encoding='utf-8'), Loader=yaml.BaseLoader)
    steps = workflow['jobs']['validate']['steps']
    ids = [item.get('id') for item in steps]
    assert ids.index('installed_inventory') < ids.index('security_audit') < ids.index('tests') < ids.index('build')
    audit = steps[ids.index('security_audit')]
    assert not audit.get('continue-on-error') and '--inventory artifacts/dependency-inventory.json' in audit['run']
    assert not audit.get('if')


def test_actual_ort_sessions_do_not_spin_after_inference():
    from rapidocr_onnxruntime.utils.infer_engine import OrtInferSession

    from src.ocr.engine import OcrEngine
    before = OrtInferSession.__dict__['_init_sess_opts']
    engine = OcrEngine(enable_second_pass=False)
    engine.load()
    backend = engine._engine
    for wrapper in (backend.text_det.infer, backend.text_cls.infer, backend.text_rec.recognizer.session):
        options = wrapper.session.get_session_options()
        assert options.get_session_config_entry('session.intra_op.allow_spinning') == '0'
        assert options.get_session_config_entry('session.inter_op.allow_spinning') == '0'
    assert OrtInferSession.__dict__['_init_sess_opts'] is before


def test_ort_factory_restores_on_load_failure():
    from rapidocr_onnxruntime.utils.infer_engine import OrtInferSession

    from src.ocr.session_options import non_spinning_sessions
    before = OrtInferSession.__dict__['_init_sess_opts']
    with pytest.raises(RuntimeError), non_spinning_sessions():
        assert OrtInferSession.__dict__['_init_sess_opts'] is not before
        raise RuntimeError('load failed')
    assert OrtInferSession.__dict__['_init_sess_opts'] is before


def test_supported_opencv_has_no_legacy_openexr_and_video_backends_are_disabled():
    import re

    import cv2

    from src.runtime_policy import configure_runtime
    configure_runtime()
    assert re.search(r'OpenEXR:\s+NO\b', cv2.getBuildInformation())
    assert not cv2.videoio_registry.hasBackend(cv2.CAP_FFMPEG)
    assert not cv2.videoio_registry.hasBackend(cv2.CAP_GSTREAMER)


def test_system_trace_distinguishes_local_ipc_and_internet_families():
    from scripts.audit_runtime_network import internet_calls
    assert internet_calls('socket(AF_UNIX, SOCK_STREAM, 0) = 3\n') == 0
    assert internet_calls('socket(AF_INET, SOCK_STREAM, 0) = 3\nsocket(AF_INET6, SOCK_DGRAM, 0) = 4') == 2
