"""Real script entry must validate resource metadata without pytest's import path."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from scripts import prepare_public_validation as public
from tests.resource_release_fixtures import receipt


@pytest.mark.parametrize('kind,expected', [
    ('valid', 'PASS_AS_RECORDED_NOT_OVERALL_APPROVAL'),
    ('failed', 'FAIL_AS_RECORDED'),
    ('false-success', 'INCONSISTENT_AS_RECORDED'),
])
def test_direct_entry_without_site_or_pythonpath_validates_resource_report(tmp_path, kind, expected):
    root, outside = tmp_path/'checkout', tmp_path/'outside'
    root.mkdir(); outside.mkdir()
    source = Path(__file__).resolve().parents[1]
    for relative in ('scripts/prepare_public_validation.py', 'scripts/benchmark_resource_budget.py',
                     'src/core/resource_budget.py', 'src/core/validation_report.py', 'src/core/version.py'):
        target = root/relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source/relative, target)
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=root, stderr=subprocess.PIPE, timeout=20)
    git('init', '-q', '--template=')
    git('add', 'scripts', 'src')
    git('-c', 'user.name=Validation fixture', '-c', 'user.email=validation@example.invalid',
        '-c', 'commit.gpgsign=false', 'commit', '-qm', 'Synthetic validation checkout')
    commit = git('rev-parse', 'HEAD').decode('ascii').strip()
    payload = receipt(public.VERSION, 'e'*64, commit)
    if kind == 'failed':
        payload['passed'] = False
    elif kind == 'false-success':
        payload['stages']['manual_invoice_and_helpers']['peak_job_private_bytes'] = 1_000_000_000
    payload['private_diagnostic'] = 'PRIVATE_RECOGNIZED_TEXT_AND_PATH'
    (root/'artifacts').mkdir()
    (root/'artifacts/resource-budget.json').write_text(json.dumps(payload), encoding='utf-8')
    env = dict(os.environ)
    env.pop('PYTHONPATH', None); env.pop('PYTHONHOME', None)
    outcome = 'failure' if kind == 'failed' else 'success'
    env.update(VALIDATION_STEP_RESULTS=json.dumps({'resource_budget': {'outcome': outcome, 'conclusion': outcome}}),
               VALIDATION_JOB_STATUS=outcome)
    result = subprocess.run([sys.executable, '-S', str(root/'scripts/prepare_public_validation.py'),
                             '--expected-commit', commit, '--platform', 'Windows'],
                            cwd=outside, env=env, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr.decode('utf-8', errors='replace')
    raw = (root/'artifacts/public-validation/summary.json').read_bytes()
    report = json.loads(raw)
    evidence = next(item for item in report['evidence'] if item['source'] == 'artifacts/resource-budget.json')
    assert evidence['observed_result'] == expected
    assert evidence['allowlisted']['source_commit'] == commit
    assert report['release_approved'] is False
    assert b'PRIVATE_RECOGNIZED_TEXT_AND_PATH' not in raw
    assert (root/'artifacts/public-validation/summary.sha256').read_text(encoding='ascii').split()[0] == hashlib.sha256(raw).hexdigest()
