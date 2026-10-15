"""Runner fail-closed counterexamples, with fake Jobs and no model imports."""
import io
import os
import sys
from types import SimpleNamespace
import pytest
from scripts import run_bounded_pytest as runner
from scripts import bounded_test_job


@pytest.mark.parametrize('arguments',[['-n2'],['-n','2'],['--numprocesses=2'],['--numprocesses','2'],['--dist=load'],['-p','xdist.plugin'],['-o','addopts=-n2']])
def test_parallel_model_lanes_rejected(arguments):
    assert runner.parallel_arguments(arguments)


def test_child_clears_host_addopts_and_disables_config_addopts_before_pytest(monkeypatch):
    seen=[]
    monkeypatch.setattr(runner.sys,'stdin',SimpleNamespace(buffer=io.BytesIO(b'START\n')))
    monkeypatch.setattr(runner.sys,'platform','win32')  # Don't modify this test parent's rlimits.
    monkeypatch.setenv('PYTEST_ADDOPTS','-n2')
    monkeypatch.setitem(sys.modules,'pytest',SimpleNamespace(main=lambda args:seen.append(args) or 0))
    assert runner.child(256,10,['--collect-only','tests'])==0
    assert 'PYTEST_ADDOPTS' not in os.environ
    assert seen==[['-o','addopts=','--collect-only','tests']]


@pytest.mark.parametrize('assigned',[False,True])
def test_job_assignment_failure_kills_and_reaps_direct_child_before_test_import(monkeypatch,tmp_path,assigned):
    instances=[]
    class FailedJob:
        def __init__(self,*args,**kwargs):
            self.handle=1 if assigned else None;self.closed=False;instances.append(self)
        def assign(self,pid):raise OSError('simulated assignment failure')
        def terminate_all(self):raise OSError('simulated job termination failure')
        def close(self):self.closed=True
    monkeypatch.setattr(bounded_test_job,'BoundedTestJob',FailedJob)
    monkeypatch.setattr(runner.sys,'platform','win32')
    marker=tmp_path/'imported.txt';test=tmp_path/'test_unstarted.py'
    test.write_text(f'from pathlib import Path\nPath({str(marker)!r}).write_text("bad")\n')
    result=runner.run_phase([str(test)],'failure',tmp_path,256,10)
    assert not result['passed'] and result['error_type']=='OSError'
    assert result['direct_child_reaped'] and not marker.exists()
    assert instances[0].closed
