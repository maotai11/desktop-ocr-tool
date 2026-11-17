"""Failed GUI locations stay fixed, private and unable to qualify a candidate."""
import json

import pytest

from scripts import prepare_public_validation as public
from scripts import publish_prerelease as publisher
from scripts.verify_candidate_bundle import check_gui_probe
from src import gui_probe


def report(passed=False):
    return dict(schema=1, probe='gui_feature_lifecycle', passed=passed,
        invoice_focus_native_required=True, invoice_focus_native_verified=passed,
        version=publisher.VERSION, frozen=True, platform='win32', qt_platform='windows',
        executable_sha256='a' * 64, clean_machine_verified=False,
        fixture_kind='SYNTHETIC_GUI_CONTROLS', ocr_accuracy_tested=False,
        model_inference_tested=False, checks=dict.fromkeys(gui_probe.GUI_CHECK_NAMES, passed))


def location(**changes):
    return dict(reason='INVARIANT_FALSE', function='_invoice_native_overlay_check', line=424) | changes


@pytest.mark.parametrize('name', ['source-gui.json', 'frozen-gui.json',
    'firewall/frozen-gui.json', 'isolated-gui.json'])
def test_failed_gui_location_survives_public_export_without_qualifying(name):
    value = report()
    value.update(failure_location=location(), error_code='PRIVATE_MESSAGE',
                 traceback='PRIVATE_TRACE', filename='PRIVATE_PATH', text='PRIVATE_OCR')
    clean = json.loads(publisher.public_validation_report(name, json.dumps(value).encode()))
    assert clean['failure_location'] == location()
    assert clean['nonpublic_error_present'] is True
    assert 'PRIVATE_' not in json.dumps(clean)
    assert public.observed_result(clean) == 'FAIL_AS_RECORDED'
    with pytest.raises(ValueError):
        check_gui_probe(clean, {'version': publisher.VERSION, 'executable_sha256': 'a' * 64}, True)


@pytest.mark.parametrize('bad', [None, 'PRIVATE_PATH', [], {},
    location(filename='PRIVATE_PATH'), location(reason='PRIVATE_REASON'),
    location(function='PRIVATE_FUNCTION'), location(line=True), location(line=-1),
    location(line=0), location(line=1_000_001), location(line=1.0),
    location(function='OTHER', line=1)],
    ids=['none', 'text', 'list', 'empty', 'extra', 'reason', 'function', 'bool',
         'negative', 'zero-known', 'large', 'float', 'nonzero-other'])
def test_location_shape_types_and_values_reject_private_or_nonfixed_data(bad):
    value = report()
    value['failure_location'] = bad
    clean = public.safe_fields(value)
    assert 'failure_location' not in clean
    assert clean['invalid_allowlisted_fields'] == ['failure_location']
    assert 'PRIVATE_' not in json.dumps(clean)
    assert public.observed_result(clean) == 'INVALID_FIELDS'


def test_exact_builtin_types_and_numeric_boundaries():
    class CustomInt(int):
        pass
    class CustomStr(str):
        pass
    class CustomDict(dict):
        pass
    for bad in (location(line=CustomInt(1)), location(reason=CustomStr('INVARIANT_FALSE')),
                location(function=CustomStr('_exercise_gui')), CustomDict(location())):
        value = report()
        value['failure_location'] = bad
        assert public.observed_result(public.safe_fields(value)) == 'INVALID_FIELDS'
    for known in (location(line=1), location(line=1_000_000), location(function='OTHER', line=0)):
        value = report()
        value['failure_location'] = known
        assert public.safe_fields(value)['failure_location'] == known


@pytest.mark.parametrize('context', ['success', 'missing-passed', 'false-number',
    'other-probe', 'nested-check', 'nested-report'])
def test_location_cannot_enter_success_or_unrelated_contexts(context):
    value = report()
    target = value
    if context == 'success':
        value = target = report(True)
    elif context == 'missing-passed':
        del value['passed']
    elif context == 'false-number':
        value['passed'] = 0
    elif context == 'other-probe':
        value['probe'] = 'application_lifecycle'
    elif context == 'nested-check':
        target = value['checks']
    elif context == 'nested-report':
        value['probes'] = {'frozen-gui.json': report()}
        target = value['probes']['frozen-gui.json']
    target['failure_location'] = location()
    clean = public.safe_fields(value)
    assert '"line": 424' not in json.dumps(clean)
    assert public.observed_result(clean) == 'INVALID_FIELDS'


def test_success_schema_and_all_mandatory_checks_remain_unchanged():
    value = report(True)
    clean = public.safe_fields(value)
    assert 'failure_location' not in clean
    assert len(gui_probe.GUI_CHECK_NAMES) == 16
    check_gui_probe(value, {'version': publisher.VERSION, 'executable_sha256': 'a' * 64}, True)
    assert set(clean['checks']) == set(gui_probe.GUI_CHECK_NAMES) | {'nonpublic_error_present'}
    assert public.observed_result(clean) == 'PASS_AS_RECORDED_NOT_OVERALL_APPROVAL'


def test_producer_and_public_fixed_vocabularies_match():
    assert public.GUI_FAILURE_FUNCTIONS == gui_probe.GUI_FAILURE_FUNCTIONS
    assert public.GUI_FAILURE_REASONS == gui_probe.GUI_FAILURE_REASONS
