"""Strict parent/child evidence and ownership cleanup, independent of Windows CI."""
from pathlib import Path

import pytest

from src import invoice_focus_probe as probe


class Child:
    returncode = 0
    waited = False
    def poll(self): return self.returncode
    def wait(self, timeout): self.waited = True; return self.returncode


@pytest.mark.parametrize('invalid', [None, 'passed', 'dpi200_verified',
                                    'main_button_preserved_source',
                                    'compact_button_preserved_source', 'fixture_reaped', 'deactivation_detector_verified',
                                    'noncheck_native_edit_verified', 'schema', 'probe', 'schema_bool'])
def test_fresh_process_requires_all_typed_native_evidence_and_reaps(monkeypatch, tmp_path, invalid):
    payload = dict(schema=1, probe='invoice_native_focus', passed=True, dpi200_verified=True,
                   main_button_preserved_source=True, compact_button_preserved_source=True,
                   fixture_reaped=True, deactivation_detector_verified=True, noncheck_native_edit_verified=True)
    if invalid == 'schema_bool':
        payload['schema'] = True
    elif invalid:
        payload[invalid] = 1 if invalid not in ('schema', 'probe') else 'invalid'
    child = Child()
    jobs, commands = [], []
    class Job:
        closed = False
        assigned = None
        def __init__(self): jobs.append(self)
        def assign(self, value): self.assigned = value
        def close(self): self.closed = True
    def launch(command, **kwargs):
        commands.append((command, kwargs))
        return child
    monkeypatch.setattr(probe, '_resume', lambda child: None)
    monkeypatch.setattr(probe.sys, 'platform', 'win32')
    monkeypatch.setattr(probe, '_ChildJob', Job)
    monkeypatch.setattr(probe.subprocess, 'Popen', launch)
    monkeypatch.setattr(probe, '_pump', lambda app, predicate, timeout: predicate())
    monkeypatch.setattr(probe, '_read', lambda path: payload)
    assert probe.verify_fresh_child(object(), tmp_path) is (invalid is None)
    assert jobs[0].assigned is child and jobs[0].closed and child.waited
    command, options = commands[0]
    assert command[-2] == '--self-test-invoice-focus'
    assert Path(command[-1]).parent == tmp_path
    assert options['env']['QT_SCALE_FACTOR'] == '2'


def test_timeout_terminates_only_owned_job_and_reaps(monkeypatch, tmp_path):
    child = Child()
    events = []
    class Job:
        def assign(self, value): assert value is child
        def close(self): events.append('owned-job-closed')
    monkeypatch.setattr(probe, '_resume', lambda child: None)
    monkeypatch.setattr(probe.sys, 'platform', 'win32')
    monkeypatch.setattr(probe, '_ChildJob', Job)
    monkeypatch.setattr(probe.subprocess, 'Popen', lambda *a, **kw: child)
    monkeypatch.setattr(probe, '_pump', lambda *a: False)
    assert not probe.verify_fresh_child(object(), tmp_path)
    assert events == ['owned-job-closed'] and child.waited


def test_nonwindows_cannot_claim_native_focus_proof(monkeypatch, tmp_path):
    monkeypatch.setattr(probe.sys, 'platform', 'linux')
    assert not probe.verify_fresh_child(None, tmp_path)
    path = tmp_path / 'report.json'
    assert probe.run_focus_probe(path) == 1
    report = probe._read(path)
    assert report['passed'] is False and report['dpi200_verified'] is False


def test_source_arm_ack_retries_after_windows_replace_sharing_failure(monkeypatch, tmp_path):
    protocol = probe._SourceProtocol(tmp_path / 'source.json')
    attempts, published = [], []
    monkeypatch.setattr(probe, '_read', lambda path: {'command': 'arm'})
    def write(path, state):
        attempts.append(True)
        if len(attempts) == 1:
            raise PermissionError('owned fixture sharing failure')
        published.append(dict(state))
    monkeypatch.setattr(probe, '_write', write)
    assert not protocol.poll()
    assert protocol.state['armed'] and protocol.dirty and not published
    assert not protocol.poll()
    assert published[-1]['armed'] is True and not protocol.dirty
    assert protocol.state['deactivations'] == 0 and protocol.state['selected'] is True


def test_source_deactivation_failure_is_retried_without_resetting_evidence(monkeypatch, tmp_path):
    protocol = probe._SourceProtocol(tmp_path / 'source.json')
    protocol.state['armed'] = True
    monkeypatch.setattr(probe, '_write', lambda *args: (_ for _ in ()).throw(PermissionError()))
    protocol.deactivated()
    assert protocol.dirty and protocol.state['deactivations'] == 1
    assert protocol.state['selected'] is False
    published = []
    monkeypatch.setattr(probe, '_write', lambda path, state: published.append(dict(state)))
    monkeypatch.setattr(probe, '_read', lambda path: {'command': 'arm'})
    protocol.poll()
    assert published[-1]['deactivations'] == 1 and published[-1]['selected'] is False
    assert not protocol.dirty


def test_native_probe_moves_real_button_into_768px_desktop_without_resize():
    import ctypes
    from ctypes import wintypes
    rectangles = {1: [294, 10, 1006, 1009], 2: [322, 929, 794, 981]}
    class Function:
        def __init__(self, callback): self.callback = callback
        def __call__(self, *args): return self.callback(*args)
    class Widget:
        def __init__(self, handle): self.handle = handle
        def winId(self): return self.handle
    def get_rect(handle, pointer):
        rect = ctypes.cast(pointer, ctypes.POINTER(wintypes.RECT)).contents
        rect.left, rect.top, rect.right, rect.bottom = rectangles[handle]
        return True
    positions = []
    def position(handle, after, x, y, width, height, flags):
        assert handle == 1 and width == height == 0
        assert flags & 0x1 and flags & 0x10  # NOSIZE + NOACTIVATE
        positions.append((x, y))
        dx, dy = x - rectangles[1][0], y - rectangles[1][1]
        for rect in rectangles.values():
            rect[0] += dx; rect[2] += dx; rect[1] += dy; rect[3] += dy
        return True
    class User: pass
    user = User()
    user.GetWindowRect = Function(get_rect)
    user.SetWindowPos = Function(position)
    probe._fit_probe_button(Widget(1), Widget(2), user, ctypes, wintypes, (0, 0, 1024, 720))
    assert positions and rectangles[2][1:] == [652, 794, 704]
    assert rectangles[1][3] - rectangles[1][1] == 999


def test_stop_command_sharing_failure_cannot_bypass_owned_kill_and_reap(monkeypatch, tmp_path):
    events = []
    class Job:
        def close(self): events.append('kill-owned-job')
    class Process:
        def poll(self): return 0 if events else None
        def wait(self, timeout): events.append('reap-owned-child')
    monkeypatch.setattr(probe, '_write', lambda *args: (_ for _ in ()).throw(PermissionError()))
    monkeypatch.setattr(probe, '_pump', lambda *args: False)
    assert probe._retire_fixture(None, tmp_path / 'owned.json', Process(), Job())
    assert events == ['kill-owned-job', 'reap-owned-child']
