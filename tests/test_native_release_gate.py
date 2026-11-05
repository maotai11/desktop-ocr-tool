"""Native reports cover this archive and expose only typed relative inventory."""
import json
from pathlib import Path

import pytest
import yaml

from native_payload_fixtures import inventory_and_report
from scripts import prepare_public_validation as public
from scripts import publish_prerelease as publisher


def test_native_workflow_is_mandatory_after_build_before_frozen_and_public_export():
    root = Path(__file__).resolve().parents[1]
    workflow = yaml.load((root / '.github/workflows/validate.yml').read_text(encoding='utf-8'),
                         Loader=yaml.BaseLoader)
    steps = workflow['jobs']['validate']['steps']
    ids = [step.get('id') for step in steps]
    native = steps[ids.index('native_audit')]
    assert ids.index('build') < ids.index('native_audit') < ids.index('firewall') < ids.index('public_release')
    assert native['if'] == "runner.os == 'Windows'" and not native.get('continue-on-error')
    assert int(native['timeout-minutes']) <= 25
    assert 'validate_native_payload.ps1 -Executable $exe' in native['run']
    assert '-SourceCommit $env:GITHUB_SHA' in native['run']
    assert '$LASTEXITCODE -ne 0' in native['run']
    assert {'artifacts/native-payload-audit.json', 'artifacts/native-payload-inventory.json'} <= {
        path for path, _, windows_only in public.sources() if windows_only}


def test_native_public_inventory_keeps_canonical_hash_and_rejects_extra_paths():
    from scripts.audit_native_payload import inventory_digest
    inventory, report = inventory_and_report('a' * 64, source_run_id='1234', source_run_attempt='1')
    data = publisher.public_validation_report('artifacts/native-payload-inventory.json', json.dumps(inventory).encode())
    assert json.loads(data) == inventory
    import hashlib
    assert hashlib.sha256(data).hexdigest() == inventory_digest(inventory) == report['files_inventory_sha256']
    inventory['private_path'] = 'C:/Users/PRIVATE_DOCS'
    with pytest.raises(publisher.PublicationError, match='inventory'):
        publisher.public_validation_report('artifacts/native-payload-inventory.json', json.dumps(inventory).encode())


@pytest.mark.parametrize('key,value', [('antivirus_detections_count', 1), ('unmapped_count', 1),
    ('unresolved_dependencies_count', 1), ('invalid_signatures_count', 1), ('antivirus_scan_completed', False)])
def test_failed_native_observations_remain_failed_in_public_summary(key, value):
    _, report = inventory_and_report('a' * 64, source_run_id='1234', source_run_attempt='1')
    report[key] = value
    assert public.observed_result(public.safe_fields(report)) == 'INCONSISTENT_AS_RECORDED'


def test_native_public_summary_retains_scanner_identity_without_raw_diagnostics():
    _, report = inventory_and_report('a' * 64, source_run_id='1234', source_run_attempt='1')
    report.update(error='C:/Users/PRIVATE_DOCS/file.png', stderr='PRIVATE_RESULT')
    safe = public.safe_fields(report)
    for key in ('source_run_id', 'source_run_attempt', 'scanner_version', 'antivirus_signature_version',
                'signature_updated_at', 'scan_started_at', 'scan_finished_at', 'scan_duration_seconds'):
        assert safe[key] == report[key]
    assert safe['nonpublic_error_present'] is True
    assert not safe.get('invalid_allowlisted_fields')
    assert 'PRIVATE' not in json.dumps(safe)


@pytest.mark.parametrize('entry', ['libcrypto-3.dll', 'nested/libssl-3.dll',
                                   'PySide6/plugins/tls/qcertonlybackend.dll'])
def test_candidate_inspection_rejects_unused_tls_and_foreign_openssl(monkeypatch, entry):
    from PyInstaller.archive import readers
    from model_profile_fixtures import fixture_profiles
    from scripts.verify_candidate_bundle import inspect_frozen_payload
    profiles, assets = fixture_profiles()
    data = {**assets, entry: b'FOREIGN_NATIVE_DLL'}
    class Reader:
        def __init__(self, path):
            self.toc = dict.fromkeys(data)
        def extract(self, name):
            return data[name]
    monkeypatch.setattr(readers, 'CArchiveReader', Reader)
    with pytest.raises(ValueError, match='TLS plugin|legacy OpenSSL'):
        inspect_frozen_payload('synthetic.exe', profiles)
