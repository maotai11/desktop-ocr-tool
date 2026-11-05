"""Synthetic metadata for release-boundary tests, never native/AV evidence."""
from datetime import datetime, timedelta, timezone

from scripts.audit_native_payload import (
    ROOT,
    file_digest,
    inventory_digest,
    scan_scope_digest,
)
from scripts.prepare_windows_runtime import load_runtime_lock
from src.core.version import APP_VERSION


def inventory_and_report(executable_sha256, source_commit='a' * 40, *, source_run_id=None,
                         source_run_attempt=None, executable_size=1024):
    runtime = load_runtime_lock()
    runtime_dll = runtime['files']['python312.dll']
    files = [
        {'path': '@executable', 'scan_path': 'candidate/InkPick.exe', 'sha256': executable_sha256,
         'size_bytes': executable_size, 'machine': 'AMD64', 'is_dll': False,
         'file_version': APP_VERSION, 'product_version': APP_VERSION, 'component_version': '',
         'provenance': {'kind': 'pyinstaller_bootloader_code', 'package': 'pyinstaller', 'version': '6.22.3',
             'source_path': 'PyInstaller/bootloader/Windows-64bit-intel/runw.exe',
             'source_sha256': 'b' * 64, 'match': 'executable_sections'}, 'imports': [],
         'signature_status': 'NotSigned'},
        {'path': 'python312.dll', 'scan_path': 'payload/python312.dll', 'sha256': runtime_dll['sha256'],
         'size_bytes': runtime_dll['size_bytes'], 'machine': 'AMD64', 'is_dll': True,
         'file_version': runtime['python_version'], 'product_version': runtime['python_version'], 'component_version': '',
         'provenance': {'kind': 'cpython_runtime', 'package': 'CPython', 'version': runtime['python_version'],
             'source_path': 'python312.dll', 'source_sha256': runtime_dll['sha256'], 'match': 'sha256'},
         'imports': [{'name': 'kernel32.dll', 'kind': 'regular', 'resolution': 'system32',
                      'target': 'kernel32.dll', 'symbols_count': 1, 'missing_symbols_count': 0}],
         'signature_status': 'Valid'},
    ]
    inventory = {'schema': 1, 'probe': 'native_payload_inventory', 'version': APP_VERSION,
        'platform': 'win32', 'source_commit': source_commit, 'source_run_id': source_run_id,
        'source_run_attempt': source_run_attempt, 'executable_sha256': executable_sha256,
        'archive_entries_count': 1, 'native_files_count': 2, 'files': files}
    start = datetime.now(timezone.utc).replace(microsecond=0)
    report = {key: inventory[key] for key in ('version', 'platform', 'source_commit', 'source_run_id',
        'source_run_attempt', 'executable_sha256', 'native_files_count', 'archive_entries_count')}
    report.update(schema=1, probe='native_payload_audit', passed=True,
        native_security_policy_passed=True,
        native_security_policy_sha256=file_digest(ROOT / 'scripts/native_security_policy.py'),
        files_inventory_sha256=inventory_digest(inventory), unmapped_count=0,
        unresolved_dependencies_count=0, invalid_signatures_count=0, signature_files_checked=2,
        antivirus_scan_completed=True, antivirus_detections_count=0, antivirus_scanned_files_count=2,
        scan_scope_files_sha256=scan_scope_digest(files), scanner='Microsoft Defender Antivirus',
        scanner_version='1.1.26090.1', antivirus_signature_version='1.455.1.0',
        signature_updated_at=(start - timedelta(hours=1)).isoformat(), scan_started_at=start.isoformat(),
        scan_finished_at=(start + timedelta(seconds=1)).isoformat(), scan_duration_seconds=1,
        am_running_mode='Passive Mode', real_time_protection_enabled=False,
        sample_submission_consent=2, maps_reporting=0)
    return inventory, report
