"""Audit the exact installed build/runtime inventory; never run inside the app."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def evaluate(raw, expected):
    dependencies = raw.get('dependencies')
    if not isinstance(dependencies, list):
        raise TypeError('Invalid audit result')
    observed, findings = {}, {}
    for item in dependencies:
        name = re.sub(r'[-_.]+', '-', item['name']).lower()
        if name in observed or item.get('skip_reason') or not isinstance(item.get('vulns'), list):
            raise ValueError('Incomplete audit result')
        observed[name] = item['version']
        for entry in item['vulns']:
            aliases = sorted({entry['id'], *entry.get('aliases', [])})
            findings[(name, entry['id'])] = {'package': name, 'version': item['version'],
                'id': entry['id'], 'aliases': aliases,
                'fix_versions': sorted(set(entry.get('fix_versions', [])))}
    if observed != expected:
        raise ValueError('Audit inventory differs')
    return list(findings.values())


def run(inventory, tool_python, report_path):
    inventory = Path(inventory)
    report_path = Path(report_path)
    report = {'schema': 1, 'probe': 'dependency_vulnerabilities', 'passed': False,
              'platform': sys.platform, 'source_commit': os.environ.get('GITHUB_SHA'),
              'scope': 'PYTHON_INSTALLED_DISTRIBUTIONS', 'scanner': 'pip-audit',
              'scanner_version': '2.10.1', 'packages_count': 0,
              'vulnerabilities_count': 0, 'findings': [],
              'audited_at_utc': datetime.now(timezone.utc).isoformat()}
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    try:
        payload = inventory.read_bytes()
        report['dependency_inventory_sha256'] = hashlib.sha256(payload).hexdigest()
        installed = json.loads(payload)['installed']
        if not isinstance(installed, dict) or not 1 <= len(installed) <= 256:
            raise ValueError('Invalid inventory')
        expected = {}
        for name, version in installed.items():
            if (not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', name)
                    or not isinstance(version, str)
                    or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.!+_-]*', version)):
                raise ValueError('Invalid distribution')
            key = re.sub(r'[-_.]+', '-', name).lower()
            if key in expected:
                raise ValueError('Duplicate distribution')
            expected[key] = version
        report['packages_count'] = len(expected)
        version = subprocess.run([str(tool_python), '-m', 'pip_audit', '--version'],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30, check=False)
        if version.returncode != 0 or version.stdout.decode('utf-8').strip() != 'pip-audit 2.10.1':
            raise ValueError('Unexpected scanner version')
        with tempfile.TemporaryDirectory(prefix='inkpick-audit-') as temporary:
            requirements = Path(temporary) / 'installed.txt'
            output = Path(temporary) / 'audit.json'
            requirements.write_text(''.join(f'{name}=={version}\n' for name, version in
                                          sorted(expected.items())), encoding='utf-8')
            result = subprocess.run([str(tool_python), '-m', 'pip_audit', '--requirement',
                str(requirements), '--no-deps', '--disable-pip', '--strict', '--format', 'json',
                '--output', str(output), '--progress-spinner', 'off'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=240, check=False)
            findings = evaluate(json.loads(output.read_text(encoding='utf-8')), expected)
            report['findings'] = findings
            report['vulnerabilities_count'] = len(findings)
            report['passed'] = result.returncode == 0 and not findings
            if not report['passed']:
                report['error_code'] = 'DEPENDENCY_AUDIT_FAILED'
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        report['error_code'] = 'DEPENDENCY_AUDIT_INCOMPLETE'
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print('Dependency audit passed.' if report['passed'] else 'Dependency audit failed or incomplete.')
    return 0 if report['passed'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', type=Path, required=True)
    parser.add_argument('--tool-python', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    return run(args.inventory, args.tool_python, args.report)


if __name__ == '__main__':
    raise SystemExit(main())
