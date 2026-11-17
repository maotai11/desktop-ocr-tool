"""Failure-only GUI locations retain no source text, paths or live frames."""
import json

import pytest

from src import gui_probe


def owned_function(source, filename='PRIVATE_FROZEN_SOURCE'):
    namespace = {}
    exec(compile(source, filename, 'exec'), gui_probe.__dict__, namespace)
    return namespace['_exercise_gui']


@pytest.fixture
def run_probe(qapp, tmp_path, monkeypatch):
    from src import invoice_focus_probe
    monkeypatch.setattr(invoice_focus_probe, 'verify_fresh_child', lambda app, directory: True)
    def complete(app, directory, checks):
        for key in checks:
            checks[key] = True
    monkeypatch.setattr(gui_probe, '_exercise_gui', complete)
    destinations = []
    def run():
        destination = tmp_path / f'probe-{len(destinations)}.json'
        destinations.append(destination)
        code = gui_probe.run_gui_probe(destination)
        return code, json.loads(destination.read_text()), destination.read_text()
    yield run
    assert gui_probe._GUI_FAILURE_RECORDER.get() is None


def test_success_has_no_location_or_frame_queries_and_keeps_sixteen_checks(run_probe, monkeypatch):
    calls = []
    def queried(depth):
        calls.append(depth)
        raise RuntimeError('Success must not inspect frames')
    def complete(app, directory, checks):
        gui_probe._require(True)
        gui_probe._until(lambda: True)
        for key in checks:
            checks[key] = True
    monkeypatch.setattr(gui_probe, '_gui_failure_frame', queried)
    monkeypatch.setattr(gui_probe, '_exercise_gui', complete)
    code, report, _ = run_probe()
    assert code == 0 and report['passed'] is True and len(report['checks']) == 16
    assert all(report['checks'].values()) and 'failure_location' not in report and not calls


def test_frozen_owned_invariant_records_exact_scalar_location_and_original_exception(run_probe, monkeypatch):
    function = owned_function('def _exercise_gui(app, directory, checks):\n    _require(False)\n')
    monkeypatch.setattr(gui_probe, '_exercise_gui', function)
    code, report, public = run_probe()
    assert code == 1 and report['passed'] is False
    assert report['failure_location'] == {'reason': 'INVARIANT_FALSE', 'function': '_exercise_gui', 'line': 2}
    assert 'PRIVATE_FROZEN_SOURCE' not in public and report['error_code'] == 'GUI_PROBE_CHECK_FAILED'


def test_first_requirement_survives_a_cleanup_requirement_overwrite(run_probe, monkeypatch):
    function = owned_function('def _exercise_gui(app, directory, checks):\n'
        '    try:\n        _require(False)\n    finally:\n        _require(False)\n')
    monkeypatch.setattr(gui_probe, '_exercise_gui', function)
    code, report, _ = run_probe()
    assert code == 1 and report['failure_location'] == {
        'reason': 'INVARIANT_FALSE', 'function': '_exercise_gui', 'line': 3}


def test_real_unsatisfied_wait_records_outer_owned_caller(run_probe, monkeypatch):
    function = owned_function('def _exercise_gui(app, directory, checks):\n    _until(lambda: False, 1)\n')
    monkeypatch.setattr(gui_probe, '_exercise_gui', function)
    code, report, _ = run_probe()
    assert code == 1 and report['failure_location'] == {
        'reason': 'WAIT_UNSATISFIED', 'function': '_exercise_gui', 'line': 2}


def test_unexpected_exception_uses_deepest_owned_known_site_and_redacts(run_probe, monkeypatch):
    private = RuntimeError('PRIVATE_MESSAGE /private/path customer value1000')
    monkeypatch.setattr(gui_probe, '_test_private_failure', private, raising=False)
    function = owned_function('def _exercise_gui(app, directory, checks):\n    raise _test_private_failure\n')
    monkeypatch.setattr(gui_probe, '_exercise_gui', function)
    code, report, public = run_probe()
    assert code == 1 and report['failure_location'] == {
        'reason': 'UNEXPECTED_EXCEPTION', 'function': '_exercise_gui', 'line': 2}
    assert all(value not in public for value in ('PRIVATE', '/private/path', 'customer', '1000'))


def test_matching_filename_cannot_impersonate_owned_module_globals(run_probe, monkeypatch):
    namespace = {'_require': gui_probe._require}
    exec(compile('def _exercise_gui(app, directory, checks):\n    _require(False)\n',
                 gui_probe.__file__, 'exec'), namespace)
    monkeypatch.setattr(gui_probe, '_exercise_gui', namespace['_exercise_gui'])
    code, report, _ = run_probe()
    assert code == 1 and report['failure_location'] == {
        'reason': 'INVARIANT_FALSE', 'function': 'OTHER', 'line': 0}


def test_swallowed_real_qt_callback_failure_cannot_turn_into_success_and_resets(run_probe, monkeypatch):
    function = owned_function('def _exercise_gui(app, directory, checks):\n'
        '    from PySide6.QtCore import QEventLoop, QTimer\n'
        '    loop = QEventLoop()\n'
        '    def callback():\n'
        '        try:\n'
        '            _require_owned_fixture("PRIVATE_OUTSIDE_FIXTURE", directory)\n'
        '        except ValueError:\n'
        '            pass\n'
        '        for key in checks:\n'
        '            checks[key] = True\n'
        '        loop.quit()\n'
        '    QTimer.singleShot(0, callback)\n'
        '    loop.exec()\n')
    with monkeypatch.context() as patch:
        patch.setattr(gui_probe, '_exercise_gui', function)
        code, report, public = run_probe()
    assert code == 1 and report['passed'] is False and all(report['checks'].values())
    assert report['failure_location']['reason'] == 'INVARIANT_FALSE'
    assert report['failure_location']['function'] == '_require_owned_fixture'
    assert 'PRIVATE_OUTSIDE_FIXTURE' not in public
    assert gui_probe._GUI_FAILURE_RECORDER.get() is None
    code, clean, _ = run_probe()
    assert code == 0 and clean['passed'] is True and 'failure_location' not in clean


def test_diagnostic_frame_failure_cannot_replace_original_value_error(monkeypatch):
    def unavailable(depth):
        raise RuntimeError('PRIVATE_DIAGNOSTIC')
    monkeypatch.setattr(gui_probe, '_gui_failure_frame', unavailable)
    with gui_probe._gui_failure_scope():
        with pytest.raises(ValueError, match='^GUI control evidence did not satisfy its invariant$'):
            gui_probe._require(False)
        assert gui_probe._GUI_FAILURE_RECORDER.get() == {
            'reason': 'INVARIANT_FALSE', 'function': 'OTHER', 'line': 0}
    assert gui_probe._GUI_FAILURE_RECORDER.get() is None


def test_metadata_early_return_restores_outer_context(qapp, tmp_path, monkeypatch):
    from src.core import validation_report
    outer = {'reason': 'INVARIANT_FALSE', 'function': 'OTHER', 'line': 0}
    token = gui_probe._GUI_FAILURE_RECORDER.set(outer)
    try:
        def unavailable(*args):
            raise OSError('PRIVATE_WRITE')
        monkeypatch.setattr(validation_report, 'write_validation_report', unavailable)
        assert gui_probe.run_gui_probe(tmp_path/'unwritten.json') == 1
        assert gui_probe._GUI_FAILURE_RECORDER.get() is outer
    finally:
        gui_probe._GUI_FAILURE_RECORDER.reset(token)


def test_interrupt_restores_context_and_does_not_change_interrupt_behavior(run_probe, monkeypatch):
    def interrupted(*args):
        raise KeyboardInterrupt('PRIVATE_INTERRUPT')
    monkeypatch.setattr(gui_probe, '_exercise_gui', interrupted)
    with pytest.raises(KeyboardInterrupt):
        run_probe()
    assert gui_probe._GUI_FAILURE_RECORDER.get() is None


def test_requirement_outside_owned_run_does_not_capture_frames_or_persist(monkeypatch):
    calls = []
    monkeypatch.setattr(gui_probe, '_gui_failure_frame', lambda depth: calls.append(depth))
    with pytest.raises(ValueError, match='^GUI control evidence did not satisfy its invariant$'):
        gui_probe._require(False)
    assert not calls and gui_probe._GUI_FAILURE_RECORDER.get() is None


def test_failure_vocab_is_exact_and_contains_only_existing_probe_functions():
    assert gui_probe.GUI_FAILURE_REASONS == {'INVARIANT_FALSE', 'WAIT_UNSATISFIED', 'UNEXPECTED_EXCEPTION'}
    assert gui_probe.GUI_FAILURE_FUNCTIONS == {'_require', '_removed_tools_absent', '_require_owned_fixture',
        '_until', '_replace', '_button', '_modal', '_escape', '_pointer', '_stroke', '_dispatch_for',
        '_context_action', '_exercise_manual_invoice_check', '_invoice_native_overlay_check',
        '_exercise_gui', 'run_gui_probe', 'OTHER'}
