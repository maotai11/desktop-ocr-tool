"""Additional typed-evidence checks from the independent integration review."""
import pytest

from handwriting_release_fixtures import shared_search_probe
from scripts.verify_candidate_bundle import check_shared_search_probe
from src.core.version import APP_VERSION


def evidence(frozen=True):
    manifest = {'version': APP_VERSION, 'executable_sha256': 'a' * 64}
    return manifest, shared_search_probe(APP_VERSION, manifest['executable_sha256'], frozen)


@pytest.mark.parametrize('key', ['stale_rows_discarded', 'blue_follow_stable', 'capture_overlay_hidden'])
@pytest.mark.parametrize('value', ['true', 1, None])
def test_critical_evidence_rejects_truthy_values_and_null(key, value):
    manifest, report = evidence()
    report['checks'][key] = value
    with pytest.raises(ValueError):
        check_shared_search_probe(report, manifest, True)


@pytest.mark.parametrize('key', ['error', 'error_code', 'traceback'])
def test_error_diagnostic_cannot_coexist_with_claimed_success(key):
    manifest, report = evidence()
    report[key] = 'FAILURE'
    with pytest.raises(ValueError):
        check_shared_search_probe(report, manifest, True)


def test_unknown_success_check_does_not_replace_the_fixed_contract():
    manifest, report = evidence()
    report['checks']['unverified_extra'] = True
    with pytest.raises(ValueError):
        check_shared_search_probe(report, manifest, True)


def test_source_probe_cannot_claim_a_frozen_executable_hash():
    manifest, report = evidence(False)
    report['executable_sha256'] = manifest['executable_sha256']
    with pytest.raises(ValueError):
        check_shared_search_probe(report, manifest, False)
