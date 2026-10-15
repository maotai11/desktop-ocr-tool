"""Export only allowlisted review metadata; raw gates/bundles stay runner-local.

This exporter does not approve a Release and does not sanitize a deliverable ZIP.
No raw report, XML, log, EXE or ZIP bytes are copied into the public directory.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = 'artifacts/public-validation'
VERSION = '1.8.0-rc.1'  # same fixed internal candidate as the existing gate
MAX_REPORT = 16 * 1024 * 1024
MAX_BINARY = 2 * 1024 * 1024 * 1024
STEP_IDS = ('checkout', 'python', 'linux_dependencies', 'wheels', 'wheel_inventory',
    'install', 'dependency_check', 'installed_inventory', 'models', 'handwriting_model',
    'helper_build', 'helper_probe', 'tests', 'source_selftest', 'source_app',
    'source_handwriting', 'source_image', 'build', 'firewall', 'bundle_verify')
WINDOWS_ONLY = {'build', 'firewall', 'bundle_verify'}
LINUX_ONLY = {'linux_dependencies'}
OUTCOMES = {'success', 'failure', 'cancelled', 'skipped'}
BOOLS = frozenset(('passed frozen clean_machine_verified inference_completed worker_pid_verified '
    'job_assigned cancelled_and_reaped children_reaped queue_latest_only clipboard_roundtrip '
    'self_mime_preserved helper_stopped firewall_gate_acknowledged helper_outbound_blocked '
    'executable_outbound_blocked firewall_service_running firewall_profiles_enabled started '
    'timed_out output_limit_exceeded direct_child_reaped owned_tree_exited '
    'implicit_pytest_addopts_cleared qt models ocr database dirty verified phase_a engine_ready shutdown_clean capture hotkeys').split())
NUMBERS = frozenset(('schema helper_protocol iterations_per_profile iterations_completed '
    'exit_code generation kind status expected_status training_rank rank elapsed_seconds '
    'wall_seconds timeout_seconds memory_limit_mib peak_sampled_direct_child_rss_kib '
    'rss_sampling_ms unexpected_children_at_test_exit collected executable_bytes bundle_bytes '
    'size_bytes label_count model_size_bytes source_size_bytes stdout_bytes character_count output_classes').split())
HASHES = frozenset(('sha256 executable_sha256 bundle_sha256 model_sha256 character_sha256 '
    'helper_sha256 helper_source_sha256 alphabet_sha256 validation_sha256 '
    'source_manifest_sha256 report_sha256 working_tree_sha256 decoder_sha256').split())
COMMITS = {'commit', 'source_commit', 'head_tree', 'source_tree', 'upstream_commit'}
ENUMS = {
    'platform': {'linux', 'win32'}, 'qt_platform': {'offscreen', 'windows'},
    'probe': {'ocr_database', 'application_lifecycle', 'handwriting_lifecycle',
              'handwriting_image_lifecycle', 'handwriting_native_helper', 'model_runtime_stress'},
    'phase': {'completed', 'waiting_firewall', 'starting', 'shutdown'},
    'status': {'PASSED', 'FAILED', 'NOT_RUN', 'RECORDED_NOT_A_TRUSTED_LOCK'},
    'source_probes': {'PASSED', 'FAILED', 'NOT_RUN'},
    'outbound_blocked_frozen_probes': {'PASSED', 'FAILED', 'NOT_RUN'},
    'mixed_dpi_gate': {'NOT_RUN', 'PASSED', 'FAILED'},
    'network_observation': {'NOT_RUN'}, 'license_review': {'NOT_RUN'},
    'database_integrity': {'ok'},
    'helper_architecture': {'AMD64', 'x86_64'},
    'accuracy_evidence': {'UPSTREAM_TRAINING_REPLAY_ONLY',
        'UPSTREAM_TRAINING_RASTER_FUNCTIONAL_ONLY',
        'UPSTREAM_TRAINING_REPLAY_AND_SYNTHETIC_ROBUSTNESS_ONLY'},
    'memory_limit_kind': {'Windows shared Job private allocation', 'POSIX per-process RLIMIT_AS'},
}
CONTAINERS = {'checks', 'phases', 'collection', 'execution', 'model_profiles', 'profiles',
    'v6-small', 'v6-medium', 'source_manifest', 'build_manifest', 'handwriting', 'helper',
    'source', 'model_identity', 'identity', 'recognition', 'det', 'rec', 'cls', 'frozen_runtime_stress', 'handwriting_probes', 'frozen_probes',
    'frozen-selftest.json', 'frozen-app-smoke.json', 'frozen-handwriting.json',
    'frozen-handwriting-image.json', 'probes', 'candidate_probes', 'threads_stopped', 'dependency_inventory', 'job_before_cleanup', 'job_after_cleanup'}
STATUSES = {'PASSED', 'FAILED', 'NOT_RUN'}


def strict_json(data):
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result: raise ValueError('duplicate')
            result[key] = value
        return result
    def constant(value): raise ValueError('nonfinite')
    return json.loads(data, object_pairs_hook=pairs, parse_constant=constant)


def safe_number(value):
    return (type(value) is int and -2**32 <= value <= 2**53) or (type(value) is float and math.isfinite(value) and -2**32 <= value <= 2**53)


def safe_fields(value, depth=0, context=None):
    """Unknown keys/free text are never copied. Invalid known fields get a code."""
    if not isinstance(value, dict) or depth > 8: raise ValueError('shape')
    result = {}; invalid = []
    for key, item in value.items():
        if context == 'handwriting_probes' and key in ('source','frozen','outer_and_helper_outbound_blocked'):
            if type(item) is str and item in STATUSES: result[key] = item
            else: invalid.append(key)
            continue
        if context in ('det', 'rec', 'cls') and key not in {'sha256','size_bytes','version','character_sha256','decoder_sha256','character_count','output_classes'}:
            continue  # model path/source URL/license free text is not public metadata
        if key in ('source','source_manifest') and not isinstance(item, dict):
            continue  # upstream URLs are not the structured source-identity containers
        if key == 'models' and isinstance(item, dict) and context != 'checks':
            result[key] = safe_fields(item, depth + 1, 'models')
        elif key in BOOLS:
            if type(item) is bool: result[key] = item
            else: invalid.append(key)
        elif key in HASHES:
            if item is None: result[key] = None
            elif isinstance(item, str) and re.fullmatch('[0-9a-f]{64}', item): result[key] = item
            else: invalid.append(key)
        elif key in COMMITS:
            if isinstance(item, str) and re.fullmatch('[0-9a-f]{40}', item): result[key] = item
            else: invalid.append(key)
        elif key == 'version':
            versions = {'pp-ocrv6-small','pp-ocrv6-medium','ppocr-mobile-v2.0'} if context in ('det','rec','cls') else {VERSION}
            if type(item) is str and item in versions: result[key] = item
            else: invalid.append(key)
        elif key in ENUMS and (key != 'status' or type(item) is str):
            if type(item) is str and item in ENUMS[key]: result[key] = item
            else: invalid.append(key)
        elif key in NUMBERS:
            if item is None and key in ('training_rank', 'peak_sampled_direct_child_rss_kib', 'exit_code'): result[key] = None
            elif key in ('elapsed_seconds','wall_seconds') and safe_number(item) and item >= 0: result[key] = item
            elif key not in ('elapsed_seconds','wall_seconds') and type(item) is int and safe_number(item) and (item >= 0 or key == 'exit_code'): result[key] = item
            else: invalid.append(key)
        elif key in CONTAINERS:
            if isinstance(item, dict): result[key] = safe_fields(item, depth + 1, key)
            elif type(item) is str and item in STATUSES: result[key] = item
            else: invalid.append(key)
    # These flags disclose only presence/count, never error/trace/paths/labels.
    result['nonpublic_error_present'] = any(bool(value.get(k)) for k in
        ('error', 'traceback', 'stderr', 'error_type', 'cleanup_error_types', 'group_termination_error_type'))
    for key in ('cases', 'negative_protocol', 'failures', 'wheels', 'installed', 'candidates'):
        item = value.get(key)
        if isinstance(item, (list, dict)):
            result[key + '_count'] = len(item)
            if key in ('cases', 'negative_protocol') and isinstance(item, list):
                result[key + '_passed_count'] = sum(type(x) is dict and x.get('passed') is True for x in item)
                result[key + '_failed_count'] = sum(type(x) is dict and x.get('passed') is False for x in item)
    if invalid: result['invalid_allowlisted_fields'] = sorted(invalid)  # fixed known keys only
    return result


def checked_path(root, relative):
    path = Path(relative)
    if path.is_absolute() or '..' in path.parts: raise ValueError('path')
    current = root
    if current.is_symlink() or (hasattr(current, 'is_junction') and current.is_junction()): raise ValueError('link')
    for part in path.parts:
        current = current / part
        if current.is_symlink() or (hasattr(current, 'is_junction') and current.is_junction()): raise ValueError('link')
    if not current.resolve().is_relative_to(root.resolve()): raise ValueError('escape')
    return current


def fingerprint(root, relative, maximum, read=False):
    """Fixed relative source identifiers only; content is never returned publicly."""
    result = {'source': relative, 'state': 'MISSING'}
    try:
        path = checked_path(root, relative)
        if not path.exists(): return result, None
        before = path.stat()
        if not stat.S_ISREG(before.st_mode): raise ValueError('notregular')
        result['size_bytes'] = before.st_size
        if before.st_size > maximum:
            result['state'] = 'SIZE_LIMIT'; return result, None
        flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
        fd = os.open(path, flags)
        with os.fdopen(fd, 'rb') as stream:
            opened = os.fstat(stream.fileno())
            if (before.st_ino, before.st_dev, before.st_size) != (opened.st_ino, opened.st_dev, opened.st_size): raise ValueError('changed')
            digest = hashlib.sha256(); chunks = [] if read else None; count = 0
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk: break
                count += len(chunk)
                if count > maximum: raise ValueError('grew')
                digest.update(chunk)
                if read: chunks.append(chunk)
            after = os.fstat(stream.fileno())
            if count != before.st_size or after.st_mtime_ns != before.st_mtime_ns: raise ValueError('changed')
        result.update(state='PRESENT', sha256=digest.hexdigest())
        return result, b''.join(chunks) if read else None
    except (OSError, ValueError):
        result['state'] = 'UNSAFE_OR_UNREADABLE'; return result, None


def junit_counts(raw):
    # Reject DTD/entity and UTF-16 input before parsing bounded UTF-8 XML.
    if b'\x00' in raw or b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper(): raise ValueError('xml')
    tree = ET.fromstring(raw)
    suites = list(tree.iter('testsuite'))
    if not suites: raise ValueError('suite')
    counts = dict(tests=0, failures=0, errors=0, skipped=0)
    for suite in suites:
        if any(node is not suite for node in suite.iter('testsuite')): raise ValueError('nested_suite')
        cases = list(suite.iter('testcase'))
        observed = dict(tests=len(cases), failures=0, errors=0, skipped=0)
        for case in cases:
            states = [tag for tag in ('failure','error','skipped') if case.find(tag) is not None]
            if len(states) > 1: raise ValueError('conflicting_case')
            for tag, key in (('failure','failures'),('error','errors'),('skipped','skipped')):
                observed[key] += int(case.find(tag) is not None)
        for key in counts:
            value = suite.get(key, '0')
            if not re.fullmatch(r'\d{1,9}', value) or int(value) != observed[key]: raise ValueError('count')
            counts[key] += int(value)
    counts['testcase_count'] = sum(1 for _ in tree.iter('testcase'))
    counts['outcome'] = 'FAIL' if counts['failures'] or counts['errors'] else 'PASS' if counts['tests'] else 'EMPTY'
    return counts


def junit_failure_fingerprints(raw):
    """Opaque case identifiers only; no names, parameters, paths or failure text."""
    junit_counts(raw)  # retain all bounded XML/schema/count checks
    result = []
    for case in ET.fromstring(raw).iter('testcase'):
        for state in ('failure', 'error'):
            if case.find(state) is not None:
                identity = (case.get('classname', '') + '\0' + case.get('name', '')).encode('utf-8')
                result.append({'state': state, 'case_sha256': hashlib.sha256(identity).hexdigest()})
    return result


def observed_result(value):
    records = []
    def visit(item):
        if isinstance(item, dict):
            records.append(item)
            for child in item.values(): visit(child)
        elif isinstance(item, list):
            for child in item: visit(child)
    visit(value)
    if any(r.get('invalid_allowlisted_fields') for r in records): return 'INVALID_FIELDS'
    failed = any(r.get('passed') is False or any(r.get(k) is True for k in ('timed_out','output_limit_exceeded','nonpublic_error_present')) or (type(r.get('exit_code')) is int and r['exit_code'] != 0) or any(r.get(k, 0) > 0 for k in
        ('failures_count', 'cases_failed_count', 'negative_protocol_failed_count')) or
        (type(r.get('checks')) is dict and any(r['checks'].get(k) is False for k in ('qt','models','ocr','database')))
        for r in records)
    if failed:
        return 'INCONSISTENT_AS_RECORDED' if value.get('passed') is True else 'FAIL_AS_RECORDED'
    if value.get('passed') is True: return 'PASS_AS_RECORDED_NOT_OVERALL_APPROVAL'
    return 'NOT_ASSERTED'


def sources():
    result = [('test-results.xml', 'xml', False),
        ('source-selftest.json', 'json', False), ('source-app-smoke.json', 'json', False),
        ('source-handwriting.json', 'json', False), ('source-handwriting-image.json', 'json', False),
        ('artifacts/pytest-bounds.json', 'json', False),
        ('artifacts/pytest-collection.stdout.log', 'hash', False),
        ('artifacts/pytest-collection.stderr.log', 'hash', False),
        ('artifacts/pytest-execution.stdout.log', 'hash', False),
        ('artifacts/pytest-execution.stderr.log', 'hash', False),
        ('artifacts/dependency-inventory.json', 'json', False),
        ('artifacts/handwriting-native-probe.json', 'json', False),
        ('artifacts/verified-bundle.json', 'json', True)]
    for directory in ('candidate-validation', 'firewall-validation'):
        for name in ('frozen-selftest.json', 'frozen-app-smoke.json', 'frozen-handwriting.json', 'frozen-handwriting-image.json'):
            result.append((f'artifacts/{directory}/{name}', 'json', True))
    for name in ('candidate-gate.json', 'handwriting-firewall-gate.json', 'frozen-runtime-stress.json'):
        result.append((f'artifacts/firewall-validation/{name}', 'json', True))
    result.extend([
        (f'artifacts/release-v{VERSION}/BUILD_MANIFEST.json', 'json', True),
        (f'artifacts/release-v{VERSION}/SOURCE_MANIFEST.json', 'json', True),
        (f'artifacts/release-v{VERSION}/InkPick-v{VERSION}.exe', 'binary', True),
        (f'artifacts/InkPick-v{VERSION}.zip', 'binary', True),
        ('artifacts/dist/InkPick.exe', 'binary', True),
    ])
    return result


def source_step(relative):
    fixed = {'test-results.xml':'tests', 'source-selftest.json':'source_selftest',
        'source-app-smoke.json':'source_app', 'source-handwriting.json':'source_handwriting',
        'source-handwriting-image.json':'source_image',
        'artifacts/dependency-inventory.json':'installed_inventory',
        'artifacts/handwriting-native-probe.json':'helper_probe',
        'artifacts/verified-bundle.json':'bundle_verify'}
    if relative in fixed: return fixed[relative]
    if relative.startswith('artifacts/pytest-'): return 'tests'
    if relative.startswith('artifacts/firewall-validation/'): return 'firewall'
    return 'build'


def export(root, commit, platform, step_data, job_status):
    if not re.fullmatch('[0-9a-f]{40}', commit): raise ValueError('commit')
    if platform not in ('Linux', 'Windows') or job_status not in OUTCOMES: raise ValueError('context')
    if not isinstance(step_data, dict): raise ValueError('steps')
    destination = checked_path(root, OUTPUT)
    if destination.exists(): raise ValueError('output_exists')
    destination.mkdir(parents=True)  # new-only, never mix stale/raw contents
    report = dict(schema=1, kind='PUBLIC_ALLOWLIST_REVIEW_METADATA_ONLY', source_commit=commit,
        runner_platform=platform, preceding_job_status=job_status, release_approved=False,
        raw_evidence_uploaded=False, executable_or_zip_uploaded=False,
        original_gates_modified=False, steps={}, evidence=[])
    for name in STEP_IDS:
        entry = step_data.get(name)
        expected = not ((platform == 'Linux' and name in WINDOWS_ONLY) or (platform == 'Windows' and name in LINUX_ONLY))
        safe = {'expected_on_platform': expected, 'outcome': 'MISSING', 'conclusion': 'MISSING'}
        if isinstance(entry, dict):
            for key in ('outcome', 'conclusion'):
                if entry.get(key) in OUTCOMES: safe[key] = entry[key]
                elif key in entry: safe[key] = 'INVALID'
        report['steps'][name] = safe
    for relative, kind, windows in sources():
        maximum = MAX_BINARY if kind == 'binary' else MAX_REPORT
        record, raw = fingerprint(root, relative, maximum, read=kind in ('xml', 'json'))
        record['expected_on_platform'] = not windows or platform == 'Windows'
        record['source_step'] = source_step(relative)
        record['step_outcome'] = report['steps'][record['source_step']]['outcome']
        if raw is not None:
            try:
                if kind == 'xml':
                    record['aggregate'] = junit_counts(raw)
                    record['failed_case_fingerprints'] = junit_failure_fingerprints(raw)
                else:
                    record['allowlisted'] = safe_fields(strict_json(raw))
                    record['observed_result'] = observed_result(record['allowlisted'])
                    if record['observed_result'].startswith('PASS_') and record['step_outcome'] != 'success':
                        record['observed_result'] = 'INCONSISTENT_STEP_OUTCOME'
            except (ValueError, TypeError, ET.ParseError, RecursionError, UnicodeError):
                record['state'] = 'INVALID_REPORT'; record['error_code'] = 'REPORT_PARSE_OR_SCHEMA'
        report['evidence'].append(record)
    raw = (json.dumps(report, sort_keys=True, ensure_ascii=True, indent=2, allow_nan=False) + '\n').encode()
    (destination/'summary.json').write_bytes(raw)
    (destination/'summary.sha256').write_text(hashlib.sha256(raw).hexdigest() + '  summary.json\n', encoding='ascii')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-commit', required=True)
    parser.add_argument('--platform', required=True, choices=('Linux', 'Windows'))
    args = parser.parse_args(argv)
    try:
        raw = os.environ.get('VALIDATION_STEP_RESULTS', '{}')
        if len(raw.encode()) > 64 * 1024: raise ValueError('steps_size')
        steps = strict_json(raw)
        actual = subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,timeout=5).decode('ascii').strip()
        if actual != args.expected_commit: raise ValueError('commit_mismatch')
        export(ROOT,args.expected_commit,args.platform,steps,os.environ.get('VALIDATION_JOB_STATUS','failure'))
        return 0
    except Exception:  # all failures use a fixed code, never exception text
        # No str(exc), arbitrary path, stderr, traceback or environment disclosure.
        print('PUBLIC_METADATA_EXPORT_FAILED', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
