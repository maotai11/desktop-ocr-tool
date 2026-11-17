"""The resource workload and external runner reject incomplete real evidence."""
from hashlib import sha256
import json
from pathlib import Path

import numpy as np
import pytest

from scripts import benchmark_resource_budget as gate
from src.core.version import APP_VERSION
from src.invoice_check.rows import detect_active_row
from src.resource_probe import fixture_image


@pytest.mark.parametrize('scale', [1, 2])
def test_resource_target_has_exactly_one_selected_row_at_both_scales(qapp, scale):
    import cv2
    image, invoice, table, overrides, columns = fixture_image()
    pixels = np.asarray(image.constBits()).reshape(image.height(), image.width(), 4)
    x, y, width, height = table
    target = pixels[y:y+height, x:x+width, :3].copy()
    if scale != 1:
        target = cv2.resize(target, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
    selected = detect_active_row(target)
    assert selected['row'] == [46*scale, 95*scale]
    assert selected['header'] == [scale, 45*scale]
    assert image.width() <= 1000  # Also fits a normal 1024px CI display.
    assert len(overrides) == len(columns) == 5
    assert invoice[0]+invoice[2] < table[0]


def test_program_roots_do_not_count_unrelated_ci_candidate_zip(tmp_path):
    exe = tmp_path/'InkPick.exe'; exe.write_bytes(b'MZ')
    (tmp_path/'candidate.zip').write_bytes(b'not an application runtime file')
    roots = gate.program_roots(exe)
    assert exe in roots and tmp_path not in roots
    assert not any(path.suffix == '.zip' for path in roots)
    assert tmp_path/'handwriting-source' in roots


def test_child_environment_is_isolated_without_repurposing_home(tmp_path, monkeypatch):
    monkeypatch.setenv('SystemRoot', str(tmp_path/'Windows'))
    monkeypatch.setenv('PYTHONPATH', 'must-not-inherit')
    monkeypatch.setenv('QT_PLUGIN_PATH', 'must-not-inherit')
    monkeypatch.setenv('HOME', 'keep-host-home')
    env = gate.isolated_environment(tmp_path/'profile', tmp_path/'temp', 'Local\\owned-job', 'a'*40)
    assert 'PYTHONPATH' not in env and 'QT_PLUGIN_PATH' not in env
    assert env['HOME'] == 'keep-host-home'
    assert env['DESKTOP_OCR_HOME'] == str(tmp_path/'profile')
    assert env['GITHUB_SHA'] == 'a'*40
    assert env['DESKTOP_OCR_RESOURCE_JOB'] == 'Local\\owned-job'
    assert env['PATH'] == str(tmp_path/'Windows'/'System32')+';'+str(tmp_path/'Windows')


def fake_inner(executable, profile, source, *, manual):
    doc = {'passed': True, 'platform': 'win32', 'frozen': True, 'version': APP_VERSION,
           'executable_sha256': sha256(executable.read_bytes()).hexdigest()}
    if manual:
        doc.update(probe='resource_invoice_workload', native_capture_used=True, source_commit=source,
                   manual_rounds_completed=3, fields_each=5, large_source_pixels=6_000_000,
                   checks=dict.fromkeys(('shared_small_engine_verified', 'three_manual_checks',
                   'only_current_row', 'no_automatic_capture', 'helper_inference_and_reap',
                   'bounded_raster_inference', 'no_history_or_capture_files', 'ownership_drained'), True))
    else:
        doc.update(probe='application_lifecycle', shutdown_clean=True)
    return doc


@pytest.fixture
def simulated_stage(tmp_path, monkeypatch):
    executable = tmp_path/'candidate.exe'; executable.write_bytes(b'MZ synthetic test-only executable')
    changes, closed = {}, []
    class Process:
        def __init__(self, exe, arguments, profile, temporary, source):
            doc = fake_inner(exe, profile, source, manual=arguments[0]=='--self-test-resources')
            for key, value in changes.items():
                if key == 'checks':
                    doc['checks'].update(value)
                else:
                    doc[key] = value
            Path(arguments[1]).write_text(json.dumps(doc))
        def pids(self): return (101, 102, 103)
        def exited(self): return True
        def exit_code(self): return 0
        def accounting(self):
            return {'peak_job_private_bytes': 70_000_000, 'active_processes': 0,
                    'total_processes': 3, 'job_limit_bytes': 999_997_440}
        def close(self): closed.append(True)
    monkeypatch.setattr(gate, 'NativeJobProcess', Process)
    monkeypatch.setattr(gate, '_windows_memory', lambda pid: (16_000_000, 20_000_000))
    return executable, changes, closed


@pytest.mark.parametrize('name,flag', [('cold_application', '--smoke-app'),
                                      ('manual_invoice_and_helpers', '--self-test-resources')])
def test_complete_native_measurements_and_workload_qualify(simulated_stage, name, flag):
    exe, _, closed = simulated_stage
    report, samples = gate.run_stage(exe, name, flag, timeout=5, source_commit='a'*40)
    assert report['passed'] is True and report['temporary_cleaned'] is True
    assert report['samples'] == len(samples) == 1
    assert report['peak_rss_bytes'] == 48_000_000
    assert report['peak_sampled_private_bytes'] == 60_000_000
    assert closed == [True]


@pytest.mark.parametrize('change', [
    {'frozen': False}, {'platform': 'linux'}, {'passed': 1}, {'source_commit': 'b'*40},
    {'native_capture_used': False}, {'manual_rounds_completed': 2}, {'manual_rounds_completed': 3.0},
    {'fields_each': 5.0}, {'large_source_pixels': 6_000_000.0},
    {'checks': {'only_current_row': False}}, {'checks': {'helper_inference_and_reap': 1}},
    {'checks': {'no_automatic_capture': None}},
])
def test_incomplete_workload_is_rejected_and_native_owner_closed(simulated_stage, change):
    exe, changes, closed = simulated_stage
    changes.update(change)
    report, _ = gate.run_stage(exe, 'manual_invoice_and_helpers', '--self-test-resources',
                               timeout=5, source_commit='a'*40)
    assert report['passed'] is False and report['temporary_cleaned'] is True
    assert report.get('error_code') and closed == [True]


def test_sampler_private_or_disk_excess_fails_without_accepting_inner_success(simulated_stage, monkeypatch):
    exe, _, closed = simulated_stage
    monkeypatch.setattr(gate, '_windows_memory', lambda pid: (16_000_000, 400_000_000))
    report, _ = gate.run_stage(exe, 'manual_invoice_and_helpers', '--self-test-resources',
                               timeout=5, source_commit='a'*40)
    assert report['passed'] is False and report['error_code'] == 'RESOURCE_LIMIT_EXCEEDED'
    assert report['temporary_cleaned'] is True and closed == [True]
