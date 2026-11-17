"""Removed feature switches cannot launch a legacy worker or write evidence."""
from pathlib import Path
import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize('flag', ['--self-test-comparison', '--self-test-shared-search'])
def test_removed_feature_cli_is_rejected_before_startup(tmp_path, flag):
    root = Path(__file__).resolve().parents[1]
    report = tmp_path / 'must-not-exist.json'
    profile = tmp_path / 'must-not-create-profile'
    env = os.environ.copy()
    env.update(DESKTOP_OCR_HOME=str(profile), QT_QPA_PLATFORM='offscreen')
    child = subprocess.run([sys.executable, str(root / 'src/main.py'), flag, str(report)],
                           cwd=root, env=env, capture_output=True, timeout=20)
    assert child.returncode == 2
    assert flag.encode() in child.stderr and b'unrecognized arguments' in child.stderr
    assert not report.exists() and not profile.exists()
