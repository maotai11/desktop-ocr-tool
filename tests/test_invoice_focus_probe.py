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
                                    'schema', 'probe', 'schema_bool'])
def test_fresh_process_requires_all_typed_native_evidence_and_reaps(monkeypatch, tmp_path, invalid):
    payload = dict(schema=1, probe='invoice_native_focus', passed=True, dpi200_verified=True,
                   main_button_preserved_source=True, compact_button_preserved_source=True,
                   fixture_reaped=True, deactivation_detector_verified=True)
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
