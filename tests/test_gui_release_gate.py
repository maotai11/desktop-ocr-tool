"""GUI control evidence cannot be replaced with a top-level success flag."""
import json
from pathlib import Path

import pytest
import yaml

from scripts import prepare_public_validation as public
from scripts import publish_prerelease as publisher
from scripts.audit_runtime_network import MODES
from scripts.verify_candidate_bundle import check_gui_probe
from src import gui_probe, main


def gui_report(frozen=True):
    return dict(schema=1, probe='gui_feature_lifecycle', passed=True,
        version=publisher.VERSION, frozen=frozen, platform='win32', qt_platform='windows',
        executable_sha256='a' * 64 if frozen else None, clean_machine_verified=False,
        fixture_kind='SYNTHETIC_GUI_CONTROLS', ocr_accuracy_tested=False,
        model_inference_tested=False, checks=dict.fromkeys(gui_probe.GUI_CHECK_NAMES, True))


@pytest.mark.parametrize('name', gui_probe.GUI_CHECK_NAMES)
@pytest.mark.parametrize('defect', ['false', 'missing', 'nonbool'])
def test_every_gui_control_effect_is_mandatory(name, defect):
    report = gui_report()
    if defect == 'missing':
        del report['checks'][name]
    else:
        report['checks'][name] = False if defect == 'false' else 1
    with pytest.raises(ValueError, match='checks'):
        check_gui_probe(report, {'version': publisher.VERSION, 'executable_sha256': 'a' * 64}, True)
    if defect == 'false':
        assert public.observed_result(public.safe_fields(report)) == 'INCONSISTENT_AS_RECORDED'


@pytest.mark.parametrize('key,value', [('schema', True), ('passed', 1), ('version', '0.0.0'),
    ('frozen', False), ('executable_sha256', 'b' * 64), ('platform', 'linux'),
    ('qt_platform', 'offscreen'), ('model_inference_tested', True), ('clean_machine_verified', True)])
def test_gui_report_is_bound_to_candidate_and_limited_claims(key, value):
    report = gui_report()
    report[key] = value
    with pytest.raises(ValueError, match='identity'):
        check_gui_probe(report, {'version': publisher.VERSION, 'executable_sha256': 'a' * 64}, True)


def test_gui_dispatch_occurs_before_regular_app_start(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(gui_probe, 'run_gui_probe', lambda path: called.append(path) or 0)
    report = str(tmp_path / 'gui.json')
    assert main.run(['--self-test-gui', report]) == 0
    assert called == [report]


def test_gui_routes_are_mandatory_and_public_metadata_drops_content():
    root = Path(__file__).resolve().parents[1]
    workflow = yaml.load((root / '.github/workflows/validate.yml').read_text(encoding='utf-8'),
                         Loader=yaml.BaseLoader)
    steps = {step.get('id'): step for step in workflow['jobs']['validate']['steps']}
    assert steps['source_gui']['run'] == 'python src/main.py --self-test-gui source-gui.json'
    assert not steps['source_gui'].get('continue-on-error')
    assert '--self-test-gui' in MODES and len(MODES) == 8
    for path in ('scripts/build.py', 'scripts/validate_windows_bundle.ps1', 'scripts/validate_portable_profile.ps1'):
        assert '--self-test-gui' in (root / path).read_text(encoding='utf-8')
    guard = (root / 'scripts/assert_gui_probe.ps1').read_text(encoding='utf-8')
    assert all(key in guard for key in gui_probe.GUI_CHECK_NAMES)
    names = {name for name, _, _ in public.sources()}
    assert {'source-gui.json', 'artifacts/candidate-validation/frozen-gui.json',
            'artifacts/firewall-validation/frozen-gui.json',
            'artifacts/portable-validation/isolated-gui.json'} <= names
    report = gui_report()
    report.update(text='PRIVATE_CONTENT', screenshot='PRIVATE_PATH', errors=['PRIVATE_ERROR'])
    safe = json.loads(publisher.public_validation_report('source-gui.json', json.dumps(report).encode()))
    assert set(safe['checks']) == set(gui_probe.GUI_CHECK_NAMES) | {'nonpublic_error_present'}
    assert 'PRIVATE' not in json.dumps(safe)
