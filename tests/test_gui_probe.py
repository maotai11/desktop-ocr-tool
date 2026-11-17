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


@pytest.mark.parametrize('defect', ['replacement_not_saved', 'cleanup_bypassed'])
def test_settings_probe_rejects_lost_rules_and_unformatted_copy(qapp, tmp_path, monkeypatch, defect):
    if defect == 'replacement_not_saved':
        from src.core.config import ConfigManager
        original = ConfigManager.set
        original_update = ConfigManager.update_section

        def lose_replacement(cfg, *keys_and_value):
            if keys_and_value[:2] == ('clipboard', 'copy_replace_commas'):
                keys_and_value = (*keys_and_value[:-1], '')
            return original(cfg, *keys_and_value)

        monkeypatch.setattr(ConfigManager, 'set', lose_replacement)
        def lose_atomic_replacement(cfg, section, values):
            values = dict(values)
            if section == 'clipboard' and 'copy_replace_commas' in values:
                values['copy_replace_commas'] = ''
            return original_update(cfg, section, values)
        monkeypatch.setattr(ConfigManager, 'update_section', lose_atomic_replacement)
    else:
        from src.clipboard import writer
        monkeypatch.setattr(writer, 'prepare_copy_text', lambda text, cfg=None: text)
    destination = tmp_path / 'settings-failure.json'
    assert gui_probe.run_gui_probe(destination) == 1
    checks = json.loads(destination.read_text(encoding='utf-8'))['checks']
    assert checks['settings_cancel_and_apply'] is False
    assert checks['windows_and_workers_closed'] is True
    assert checks['temporary_files_cleaned'] is True


def test_document_gui_probe_rejects_unwanted_cleanup_of_template_copy(qapp, tmp_path, monkeypatch):
    from src.clipboard.writer import copy_text
    from src.ui.document_template_window import DocumentTemplateWindow
    # Template copy deliberately preserves the same literal text as its preview.
    def unwanted_cleanup(window):
        if window._copy_button.isEnabled():
            copy_text(window._output.toPlainText(), window._cfg)
    monkeypatch.setattr(DocumentTemplateWindow, '_copy_results', unwanted_cleanup)
    destination = tmp_path / 'document-copy-failure.json'
    assert gui_probe.run_gui_probe(destination) == 1
    checks = json.loads(destination.read_text(encoding='utf-8'))['checks']
    assert checks['settings_cancel_and_apply'] is True
    assert checks['document_import_template_zoom_clear'] is False
    assert checks['windows_and_workers_closed'] is True
    assert checks['temporary_files_cleaned'] is True


@pytest.mark.parametrize('defect', ['mode_not_saved', 'cleanup_not_saved'])
def test_launcher_copy_probe_rejects_controls_that_do_not_persist(qapp, tmp_path, monkeypatch, defect):
    from src.ui import copy_preferences
    if defect == 'mode_not_saved':
        monkeypatch.setattr(copy_preferences, 'set_copy_mode', lambda *args: True)
    else:
        def discard_draft(dialog):
            dialog.preferences_saved.emit()
            dialog.accept()
        monkeypatch.setattr(copy_preferences.CopyCleanupDialog, '_save', discard_draft)
    destination = tmp_path / 'quick-copy-failure.json'
    assert gui_probe.run_gui_probe(destination) == 1
    checks = json.loads(destination.read_text(encoding='utf-8'))['checks']
    assert checks['settings_cancel_and_apply'] is True
    assert checks['launcher_copy_controls'] is False
    assert checks['windows_and_workers_closed'] is True
    assert checks['temporary_files_cleaned'] is True


@pytest.mark.parametrize('defect', ['launcher', 'tray', 'worker_owner', 'packaged_module', 'cli'])
def test_removed_comparison_contract_rejects_retained_entry_or_implementation(qapp, tmp_path, monkeypatch, defect):
    from src.ui.widget import FloatingWidget
    from src.ui.tray_manager import TrayManager
    if defect in ('launcher', 'worker_owner'):
        from PySide6.QtWidgets import QPushButton
        original = FloatingWidget.__init__
        def retained_widget(widget, *args, **kwargs):
            original(widget, *args, **kwargs)
            if defect == 'launcher':
                QPushButton('A/B 核對', widget)
            else:
                widget._shared_search_controller = object()
        monkeypatch.setattr(FloatingWidget, '__init__', retained_widget)
    elif defect == 'tray':
        original = TrayManager._build_menu
        def retained_tray(tray):
            original(tray)
            tray._tray.contextMenu().addAction('整列搜尋')
        monkeypatch.setattr(TrayManager, '_build_menu', retained_tray)
    elif defect == 'packaged_module':
        import importlib.util
        from importlib.machinery import ModuleSpec
        original = importlib.util.find_spec
        monkeypatch.setattr(importlib.util, 'find_spec', lambda name, *args, **kwargs:
                            ModuleSpec(name, loader=None) if name == 'src.comparison_probe'
                            else original(name, *args, **kwargs))
    else:
        from src import main
        original = main._build_parser
        def retired_cli():
            parser = original()
            parser.add_argument('--self-test-comparison')
            return parser
        monkeypatch.setattr(main, '_build_parser', retired_cli)
    destination = tmp_path / 'removed-feature-failure.json'
    assert gui_probe.run_gui_probe(destination) == 1
    checks = json.loads(destination.read_text(encoding='utf-8'))['checks']
    assert checks['handwriting_stroke_candidate_copy'] is True
    assert checks['removed_comparison_tools_absent'] is False
    assert checks['windows_and_workers_closed'] is True
    assert checks['temporary_files_cleaned'] is True
