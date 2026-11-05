"""Shared-search evidence is mandatory, private-free and same-candidate bound."""
import copy
from pathlib import Path

import pytest
import yaml

from handwriting_release_fixtures import shared_search_probe
from scripts import prepare_public_validation as public
from scripts import publish_prerelease as publisher
from scripts.audit_runtime_network import MODES
from scripts.verify_candidate_bundle import _check_probe, check_shared_search_probe
from src.shared_search_probe import CHECK_NAMES


@pytest.mark.parametrize('name', CHECK_NAMES)
@pytest.mark.parametrize('value', [False, None])
def test_shared_search_checks_cannot_be_false_or_missing(name, value):
    report = shared_search_probe(publisher.VERSION, 'a' * 64)
    if value is None:
        del report['checks'][name]
    else:
        report['checks'][name] = value
        safe = public.safe_fields(report)
        assert safe['checks'][name] is False
        assert public.observed_result(safe) == 'INCONSISTENT_AS_RECORDED'
    with pytest.raises(ValueError, match='checks'):
        check_shared_search_probe(report, {'version': publisher.VERSION, 'executable_sha256': 'a' * 64}, True)


@pytest.mark.parametrize('key,value', [('overlay_capture_verified', False), ('overlay_capture_verified', 'True'),
    ('schema', True), ('executable_sha256', 'b' * 64), ('frozen', False), ('version', '0.0.0'),
    ('platform', 'linux'), ('qt_platform', 'offscreen'), ('ocr_accuracy_tested', True)])
def test_native_overlay_identity_is_required_for_windows_release(key, value):
    report = shared_search_probe(publisher.VERSION, 'a' * 64)
    report[key] = value
    with pytest.raises(ValueError, match='identity'):
        check_shared_search_probe(report, {'version': publisher.VERSION, 'executable_sha256': 'a' * 64}, True)


def test_windows_failed_native_proof_remains_visible_without_exporting_content():
    report = shared_search_probe(publisher.VERSION, 'a' * 64)
    report.update(overlay_capture_verified=False, text='PRIVATE_WORDS', image_path='PRIVATE_SCREEN',
                  detail=[{'text': 'PRIVATE_DETAIL'}])
    safe = public.safe_fields(report)
    assert public.observed_result(safe) == 'INCONSISTENT_AS_RECORDED'
    assert not any(key in safe for key in ('text', 'detail', 'image_path'))
    linux = copy.deepcopy(report)
    linux.update(platform='linux', qt_platform='offscreen', frozen=False, executable_sha256=None)
    assert public.observed_result(public.safe_fields(linux)) == 'PASS_AS_RECORDED_NOT_OVERALL_APPROVAL'


@pytest.mark.parametrize('value', [False, None])
def test_shared_search_worker_shutdown_cannot_be_false_or_missing(value):
    report = dict(schema=2, probe='application_lifecycle', passed=True, frozen=False,
        platform='win32', qt_platform='windows', version=publisher.VERSION,
        executable_sha256=None, clean_machine_verified=False, database_integrity='ok',
        phase_a=True, engine_ready=True, shutdown_clean=True,
        threads_stopped=dict(capture=True, ocr=True, database=True, hotkeys=True,
                             history_maintenance=True, shared_search=True))
    if value is None:
        del report['threads_stopped']['shared_search']
    else:
        report['threads_stopped']['shared_search'] = value
    with pytest.raises(ValueError):
        _check_probe(report, {'version': publisher.VERSION}, False)


def test_shared_search_runs_on_source_build_firewall_portable_and_network_routes():
    root = Path(__file__).resolve().parents[1]
    workflow = yaml.load((root / '.github/workflows/validate.yml').read_text(encoding='utf-8'),
                         Loader=yaml.BaseLoader)
    steps = {step.get('id'): step for step in workflow['jobs']['validate']['steps']}
    assert steps['source_shared_search']['run'] == 'python src/main.py --self-test-shared-search source-shared-search.json'
    assert not steps['source_shared_search'].get('continue-on-error')
    assert '--self-test-shared-search' in MODES and len(MODES) == 7
    for name in ('scripts/build.py', 'scripts/validate_windows_bundle.ps1', 'scripts/validate_portable_profile.ps1'):
        assert '--self-test-shared-search' in (root / name).read_text()
    for name in ('scripts/validate_windows_bundle.ps1', 'scripts/validate_portable_profile.ps1'):
        assert 'Assert-SharedSearchProbe' in (root / name).read_text()
    guard = (root / 'scripts/assert_shared_search_probe.ps1').read_text()
    assert 'overlay_capture_verified' in guard
    assert all(name in guard for name in CHECK_NAMES)
    expected = {'source-shared-search.json', 'artifacts/candidate-validation/frozen-shared-search.json',
                'artifacts/firewall-validation/frozen-shared-search.json',
                'artifacts/portable-validation/isolated-shared-search.json'}
    assert expected <= {name for name, _, _ in public.sources()}
    assert public.source_step('source-shared-search.json') == 'source_shared_search'
