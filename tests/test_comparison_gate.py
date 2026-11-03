"""Public evidence must retain failures without exporting compared content."""
from pathlib import Path

import pytest
import yaml

from scripts import prepare_public_validation as public
from scripts.audit_runtime_network import MODES


@pytest.mark.parametrize('name', ['unchanged_frames_skipped', 'changed_frames_resumed',
                                  'close_stops_polling', 'pin_toggle_available'])
def test_new_idle_and_window_checks_cannot_be_missing_or_false(name):
    from scripts import publish_prerelease as publisher
    from handwriting_release_fixtures import comparison_probe
    manifest = {'version': publisher.VERSION, 'executable_sha256': 'a' * 64}
    for value in (False, None):
        report = comparison_probe(publisher.VERSION, manifest['executable_sha256'])
        if value is None:
            del report['checks'][name]
        else:
            report['checks'][name] = value
            safe = public.safe_fields(report)
            assert safe['checks'][name] is False
            assert public.observed_result(safe) == 'INCONSISTENT_AS_RECORDED'
        with pytest.raises(publisher.PublicationError, match='checks'):
            publisher._check_comparison_probe(report, manifest, True)


def test_comparison_failure_is_visible_in_public_summary():
    report = public.safe_fields({'passed': True, 'probe': 'comparison_lifecycle',
        'fixture_kind': 'SYNTHETIC_COMPARISON_PAIRS',
        'checks': {'same_snapshot_pairs': False, 'workers_stopped': True},
        'a': 'PRIVATE_A', 'b': 'PRIVATE_B', 'image_path': 'PRIVATE_PATH'})
    assert public.observed_result(report) == 'INCONSISTENT_AS_RECORDED'
    assert report['checks']['same_snapshot_pairs'] is False
    assert not any(key in report for key in ('a', 'b', 'image_path'))


def test_comparison_runs_on_source_frozen_portable_and_network_paths():
    root = Path(__file__).resolve().parents[1]
    workflow = yaml.load((root / '.github/workflows/validate.yml').read_text(encoding='utf-8'), Loader=yaml.BaseLoader)
    steps = {step.get('id'): step for step in workflow['jobs']['validate']['steps']}
    assert '--self-test-comparison source-comparison.json' in steps['source_comparison']['run']
    assert '--self-test-comparison' in steps['firewall']['run']
    assert 'frozen-comparison.json' in steps['firewall']['run']
    assert not steps['source_comparison'].get('continue-on-error')
    portable = (root / 'scripts/validate_portable_profile.ps1').read_text(encoding='utf-8')
    assert "mode = '--self-test-comparison'; name = 'isolated-comparison.json'" in portable
    assert 'cancelled_results_ignored' in portable and 'workers_stopped' in portable
    assert '--self-test-comparison' in MODES and len(MODES) == 6
    sources = {name for name, _, _ in public.sources()}
    assert {'source-comparison.json', 'artifacts/firewall-validation/frozen-comparison.json',
            'artifacts/portable-validation/isolated-comparison.json'} <= sources
    assert public.source_step('source-comparison.json') == 'source_comparison'
