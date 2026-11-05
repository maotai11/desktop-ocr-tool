"""Trace only synthetic application processes; publish counts, never raw traces."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

MODES = ('--self-test', '--smoke-app', '--self-test-handwriting',
         '--self-test-handwriting-image', '--self-test-documents', '--self-test-comparison',
         '--self-test-shared-search')


def internet_calls(trace):
    return sum(bool(re.search(r'\bAF_INET6?\b', line)) for line in trace.splitlines())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args(argv)
    report = {'schema': 1, 'probe': 'runtime_network', 'platform': sys.platform,
              'source_commit': os.environ.get('GITHUB_SHA'), 'passed': False,
              'scope': 'SYNTHETIC_APP_PROCESS_TREE_SYSCALLS', 'internet_family_calls': 0,
              'cases_completed': 0, 'cases_passed': 0, 'raw_traces_published': False}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, sort_keys=True, indent=2) + '\n', encoding='utf-8')
    try:
        executable = shutil.which('strace')
        if sys.platform != 'linux' or executable is None:
            raise ValueError('System trace unavailable')
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix='inkpick-network-audit-') as temporary:
            for index, mode in enumerate(MODES):
                trace = Path(temporary) / f'trace-{index}.txt'
                probe = Path(temporary) / f'probe-{index}.json'
                child = subprocess.Popen([executable, '-f', '-e', 'trace=network', '-o',
                    str(trace), sys.executable, str(root / 'src/main.py'), mode, str(probe)],
                    cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    start_new_session=True)
                try:
                    code = child.wait(timeout=120)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait(timeout=10)
                    raise ValueError('Application tree failed to exit') from None
                if not trace.is_file() or trace.stat().st_size > 16 * 1024 * 1024:
                    raise ValueError('Invalid system trace')
                count = internet_calls(trace.read_text(encoding='utf-8', errors='replace'))
                report['internet_family_calls'] += count
                report['cases_completed'] += 1
                observed = json.loads(probe.read_text(encoding='utf-8'))
                if code == 0 and observed.get('passed') is True and count == 0:
                    report['cases_passed'] += 1
                else:
                    raise ValueError('Unexpected network or failed lifecycle')
        report['passed'] = report['cases_passed'] == len(MODES)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        report['error_code'] = 'RUNTIME_NETWORK_AUDIT_FAILED'
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, sort_keys=True, indent=2) + '\n', encoding='utf-8')
    print('Application network audit passed.' if report['passed'] else 'Application network audit failed.')
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
