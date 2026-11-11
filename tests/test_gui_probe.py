"""The finite GUI report is earned by controls and survives failure safely."""
import json
import os
from contextlib import contextmanager
from pathlib import Path

import pytest
from PySide6.QtCore import QMimeData

from src import gui_probe


@pytest.fixture(autouse=True)
def release_owned_clipboard(qapp):
    yield
    # The offscreen platform retains Python-owned rich QMimeData until app
    # destruction. Release this test's fixture before interpreter teardown.
    qapp.clipboard().clear()


@pytest.mark.parametrize('lexical_root', [False, True], ids=['canonical-root', 'lexical-parent-alias'])
def test_real_gui_control_matrix_and_worker_shutdown(qapp, tmp_path, monkeypatch, lexical_root):
    if lexical_root:
        original_temporary_directory = gui_probe.tempfile.TemporaryDirectory
        @contextmanager
        def aliased_temporary_directory(*args, **kwargs):
            with original_temporary_directory(*args, **kwargs) as directory:
                alias = Path(directory) / 'alias'
                alias.mkdir()
                yield str(alias / '..')
        monkeypatch.setattr(gui_probe.tempfile, 'TemporaryDirectory', aliased_temporary_directory)
    monkeypatch.setenv('DESKTOP_OCR_HOME', str(tmp_path / 'unrelated-profile'))
    monkeypatch.setenv('GITHUB_SHA', 'a' * 40)
    unrelated = tmp_path / 'unrelated-profile'
    unrelated.mkdir()
    sentinel = unrelated / 'keep.txt'
    sentinel.write_text('unrelated profile', encoding='utf-8')
    data = QMimeData()
    data.setText('original clipboard')
    data.setHtml('<b>original clipboard</b>')
    qapp.clipboard().setMimeData(data)
    destination = tmp_path / 'gui-report.json'
    assert gui_probe.run_gui_probe(destination) == 0
    report = json.loads(destination.read_text(encoding='utf-8'))
    assert report['passed'] is True
    assert set(report['checks']) == set(gui_probe.GUI_CHECK_NAMES)
    assert all(value is True for value in report['checks'].values())
    assert report['probe'] == 'gui_feature_lifecycle'
    assert report['fixture_kind'] == 'SYNTHETIC_GUI_CONTROLS'
    assert report['source_commit'] == 'a' * 40
    assert report['qt_platform'] == qapp.platformName()
    assert report['model_inference_tested'] is False
    assert report['ocr_accuracy_tested'] is False
    assert report['clean_machine_verified'] is False
    assert qapp.clipboard().text() == 'original clipboard'
    assert qapp.clipboard().mimeData().html() == '<b>original clipboard</b>'
    assert list(unrelated.iterdir()) == [sentinel]


def test_fixture_path_containment_still_rejects_external_file(tmp_path):
    owned_root = (tmp_path / 'owned').resolve()
    owned_root.mkdir()
    internal = owned_root / 'fixture.png'
    internal.write_bytes(b'owned fixture')
    external = tmp_path / 'external.png'
    external.write_bytes(b'external fixture')
    gui_probe._require_owned_fixture(internal, owned_root)
    with pytest.raises(ValueError):
        gui_probe._require_owned_fixture(external, owned_root)
    with pytest.raises(ValueError):
        gui_probe._require_owned_fixture(owned_root / '..' / external.name, owned_root)


def test_failure_replaces_old_pass_cleans_owned_files_and_redacts(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv('DESKTOP_OCR_HOME', str(tmp_path / 'existing-home'))
    monkeypatch.setenv('GITHUB_SHA', 'not a commit')
    destination = tmp_path / 'report.json'
    destination.write_text('{"passed":true}', encoding='utf-8')
    created = []
    def fail(app, directory, checks):
        created.append(directory)
        (directory / 'private.txt').write_text('PRIVATE PROBE CONTENT', encoding='utf-8')
        raise ValueError('PRIVATE PROBE CONTENT at ' + str(directory))
    monkeypatch.setattr(gui_probe, '_exercise_gui', fail)
    assert gui_probe.run_gui_probe(destination) == 1
    output = destination.read_text(encoding='utf-8')
    report = json.loads(output)
    assert report['passed'] is False
    assert report['error_code'] == 'GUI_PROBE_CHECK_FAILED'
    assert report['checks']['temporary_files_cleaned'] is True
    assert report['checks']['windows_and_workers_closed'] is False
    assert 'source_commit' not in report
    assert 'PRIVATE' not in output and str(created[0]) not in output
    assert not created[0].exists()
    assert Path(os.environ['DESKTOP_OCR_HOME']) == tmp_path / 'existing-home'


def test_interrupted_editor_control_cannot_claim_later_actions_passed(qapp, tmp_path, monkeypatch):
    original = gui_probe._button
    def broken_save(parent, label):
        if label == '儲存':
            raise RuntimeError('Synthetic save-control failure')
        return original(parent, label)
    monkeypatch.setattr(gui_probe, '_button', broken_save)
    destination = tmp_path / 'failure.json'
    assert gui_probe.run_gui_probe(destination) == 1
    checks = json.loads(destination.read_text(encoding='utf-8'))['checks']
    assert checks['history_search_tags_and_pin'] is True
    assert checks['editor_save_and_history_refresh'] is False
    assert checks['ocr_selection_copy_explicit'] is False
    assert checks['windows_and_workers_closed'] is True
    assert checks['temporary_files_cleaned'] is True


def test_capture_probe_detects_drag_state_surviving_escape(qapp, tmp_path, monkeypatch):
    from src.ui.capture_overlay import CaptureOverlay
    # Reintroduce the actual stale-drag defect while retaining the real Qt input
    # and pipeline owner routes, rather than substituting the report itself.
    monkeypatch.setattr(CaptureOverlay, '_reset_drag', lambda self: None)
    destination = tmp_path / 'stale-drag.json'
    assert gui_probe.run_gui_probe(destination) == 1
    checks = json.loads(destination.read_text(encoding='utf-8'))['checks']
    assert checks['launcher_actions_and_capture_cancel'] is True
    assert checks['capture_cancel_restart_ignores_old_gesture'] is False
    assert checks['windows_and_workers_closed'] is True
    assert checks['temporary_files_cleaned'] is True


def test_shutdown_probe_detects_late_launcher_resurrection(qapp, tmp_path, monkeypatch):
    from src.ui.widget import FloatingWidget
    original = FloatingWidget.toggle_visibility

    def stale_toggle(widget):
        if widget._ui_closing:
            widget.show()
        else:
            original(widget)

    monkeypatch.setattr(FloatingWidget, 'toggle_visibility', stale_toggle)
    destination = tmp_path / 'late-launcher.json'
    assert gui_probe.run_gui_probe(destination) == 1
    checks = json.loads(destination.read_text(encoding='utf-8'))['checks']
    assert checks['minimized_windows_restore'] is True
    assert checks['capture_cancel_restart_ignores_old_gesture'] is True
    assert checks['tray_pause_and_quit_route'] is True
    assert checks['shutdown_rejects_late_tool_entries'] is False
    assert checks['windows_and_workers_closed'] is True
    assert checks['temporary_files_cleaned'] is True
