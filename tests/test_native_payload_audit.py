"""Negative boundaries for complete native provenance and local scanner evidence."""
import base64
import copy
import hashlib
import json
import struct
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from native_payload_fixtures import inventory_and_report

from scripts import audit_native_payload as audit


@pytest.fixture
def evidence():
    return inventory_and_report('d' * 64)


def check(evidence):
    inventory, report = evidence
    audit.check_audit_report(report, inventory, executable_sha256='d' * 64, source_commit='a' * 40)


def test_synthetic_complete_evidence_has_consistent_hashes(evidence):
    check(evidence)
    inventory, report = evidence
    assert report['files_inventory_sha256'] == audit.inventory_digest(inventory)
    assert report['antivirus_scanned_files_count'] == len(inventory['files'])


@pytest.mark.parametrize('field,value', [
    ('passed', False), ('antivirus_scan_completed', False), ('antivirus_detections_count', 1),
    ('unmapped_count', 1), ('unresolved_dependencies_count', 1), ('invalid_signatures_count', 1),
    ('antivirus_scanned_files_count', 1), ('signature_files_checked', 1), ('antivirus_detections_count', False),
    ('files_inventory_sha256', 'e' * 64), ('scan_scope_files_sha256', 'e' * 64),
    ('scanner', 'pretend scanner'), ('scanner_version', ''), ('maps_reporting', 2),
    ('sample_submission_consent', 1), ('sample_submission_consent', True),
    ('scan_duration_seconds', float('nan')), ('real_time_protection_enabled', 'false'),
    ('source_run_id', '999'), ('version', '0.0.0'), ('native_files_count', True),
    ('native_security_policy_passed', False), ('native_security_policy_sha256', 'e' * 64),
])
def test_incomplete_or_forged_scanner_summary_never_passes(evidence, field, value):
    evidence[1][field] = value
    with pytest.raises(ValueError):
        check(evidence)


@pytest.mark.parametrize('field', ['signature_updated_at', 'scan_started_at', 'scan_finished_at'])
def test_timestamp_types_are_strict(evidence, field):
    evidence[1][field] = 0
    with pytest.raises(ValueError):
        check(evidence)


def test_old_antivirus_signatures_are_not_a_pass(evidence):
    evidence[1]['signature_updated_at'] = (datetime.now(timezone.utc) - timedelta(days=4)).isoformat()
    with pytest.raises(ValueError, match='Stale'):
        check(evidence)


@pytest.mark.parametrize('path', ['../private', '/private', 'C:/private', 'C:\\private', 'payload/../x.dll',
                                   'payload/x.dll:stream', 'payload/NUL.dll', 'payload/x.dll.', 'payload//x'])
def test_public_inventory_rejects_absolute_and_ambiguous_paths(evidence, path):
    evidence[0]['files'][1]['provenance']['source_path'] = path
    with pytest.raises(ValueError):
        audit.check_inventory(evidence[0], executable_sha256='d' * 64, source_commit='a' * 40)


@pytest.mark.parametrize('mutation', ['signature', 'unknown_source', 'source_hash', 'missing_symbol',
                                       'missing_target', 'duplicate', 'count', 'extra_field'])
def test_native_file_coverage_and_provenance_are_not_optional(evidence, mutation):
    inventory, _ = evidence
    item = inventory['files'][1]
    if mutation == 'signature': item['signature_status'] = 'HashMismatch'
    if mutation == 'unknown_source': item['provenance']['kind'] = 'unmapped'
    if mutation == 'source_hash': item['provenance']['source_sha256'] = 'e' * 64
    if mutation == 'missing_symbol': item['imports'][0]['missing_symbols_count'] = 1
    if mutation == 'missing_target': item['imports'][0].update(resolution='bundled', target='absent.dll')
    if mutation == 'duplicate': inventory['files'][0] = copy.deepcopy(item)
    if mutation == 'count': inventory['native_files_count'] = 1
    if mutation == 'extra_field': item['runner_private_path'] = '/private/user'
    with pytest.raises(ValueError):
        audit.check_inventory(inventory, executable_sha256='d' * 64, source_commit='a' * 40)


def tiny_pe(machine=0x8664):
    """A never-executed minimal PE, sufficient for real pefile parsing."""
    data = bytearray(1024)
    data[:2] = b'MZ'
    struct.pack_into('<I', data, 0x3c, 0x80)
    data[0x80:0x84] = b'PE\0\0'
    struct.pack_into('<HHIIIHH', data, 0x84, machine, 1, 0, 0, 0, 240, 0x2022)
    optional = 0x98
    struct.pack_into('<H', data, optional, 0x20B)
    struct.pack_into('<I', data, optional + 16, 0x1000)
    struct.pack_into('<Q', data, optional + 24, 0x140000000)
    struct.pack_into('<II', data, optional + 32, 0x1000, 0x200)
    struct.pack_into('<II', data, optional + 56, 0x2000, 0x200)
    struct.pack_into('<H', data, optional + 68, 2)
    struct.pack_into('<I', data, optional + 108, 16)
    section = optional + 240
    data[section:section + 8] = b'.text\0\0\0'
    struct.pack_into('<IIII', data, section + 8, 1, 0x1000, 512, 512)
    struct.pack_into('<I', data, section + 36, 0x60000020)
    data[512] = 0xc3
    return bytes(data)


def test_actual_pe_parser_records_architecture_and_does_not_load_dll():
    pytest.importorskip('pefile')
    metadata = audit._pe_metadata(tiny_pe())
    assert metadata['machine'] == 'AMD64' and metadata['is_dll']
    assert metadata['imports'] == [] and len(metadata['_code']) == 1
    with pytest.raises(ValueError, match='AMD64'):
        audit._pe_metadata(tiny_pe(0x14c))


def test_dependency_does_not_accept_an_unrelated_same_basename():
    source = {'package': 'owner'}
    importer = {'path': 'owner/module.pyd', 'provenance': source}
    wrong = {'path': 'unrelated/wanted.dll', 'provenance': {'package': 'unrelated'}, '_exports': {'symbol'}}
    dependency = {'name': 'wanted.dll', 'kind': 'regular', '_symbols': ['symbol']}
    result = audit.resolve_import(importer, dependency, [wrong], system_metadata=lambda _: None,
                                  api_sets={}, known_dlls=set(), package_dependencies={})
    assert result['resolution'] == 'unresolved'
    result = audit.resolve_import(importer, dependency, [wrong], system_metadata=lambda _: None,
                                  api_sets={}, known_dlls=set(), package_dependencies={'owner': {'unrelated'}})
    assert result['resolution'] == 'bundled' and result['target'] == wrong['path']


def test_imported_symbols_are_checked_not_only_dll_basename():
    dependency = {'name': 'kernel32.dll', 'kind': 'delay', '_symbols': ['NotAnExport']}
    result = audit.resolve_import({'path': '@executable', 'provenance': {}}, dependency, [],
        system_metadata=lambda _: {'_exports': {'RealExport'}}, api_sets={}, known_dlls={'kernel32.dll'},
        package_dependencies={})
    assert result['missing_symbols_count'] == 1


def test_api_set_v6_uses_measured_host_mapping_and_rejects_bad_bounds():
    name = 'api-ms-win-core-test-l1-1-0'.encode('utf-16-le')
    host = 'kernelbase.dll'.encode('utf-16-le')
    data = bytearray(72 + len(name) + len(host))
    struct.pack_into('<7I', data, 0, 6, len(data), 0, 1, 28, 0, 0)
    struct.pack_into('<6I', data, 28, 0, 72, len(name), len(name), 52, 1)
    struct.pack_into('<5I', data, 52, 0, 0, 0, 72 + len(name), len(host))
    data[72:72 + len(name)] = name
    data[72 + len(name):] = host
    assert audit.parse_api_set(data) == {'api-ms-win-core-test-l1-1-0.dll': ('kernelbase.dll',)}
    struct.pack_into('<I', data, 28 + 4, 999999)
    with pytest.raises(ValueError, match='API-set'):
        audit.parse_api_set(data)


def test_record_hash_must_match_installed_bytes(tmp_path):
    path = tmp_path / 'module.pyd'
    path.write_bytes(b'actual source')
    digest = hashlib.sha256(path.read_bytes()).digest()
    entry = SimpleNamespace(hash=SimpleNamespace(mode='sha256', value=base64.urlsafe_b64encode(digest).decode().rstrip('=')))
    class Entry:
        hash = entry.hash
        def __str__(self): return 'package/module.pyd'
    dist = SimpleNamespace(metadata={'Name': 'package'}, version='1.0', requires=[], files=[Entry()],
                           locate_file=lambda _: path)
    index, _ = audit._record_sources([dist])
    assert digest.hex() in index
    path.write_bytes(b'changed installed source')
    assert not audit._record_sources([dist])[0]


def test_archive_coverage_catches_unlisted_pe_with_non_native_extension(tmp_path, monkeypatch):
    readers = pytest.importorskip('PyInstaller.archive.readers')
    executable = tmp_path / 'candidate.exe'
    executable.write_bytes(b'MZouter')
    files = {'module.dll': b'MZpayload', 'unexpected.dat': b'MZhidden', 'text.txt': b'document'}
    class Reader:
        def __init__(self, _path):
            self.toc = {name: (0, 0, len(value), 0, 'b') for name, value in files.items()}
        def extract(self, name): return files[name]
    monkeypatch.setattr(readers, 'CArchiveReader', Reader)
    inventory = {'executable_sha256': audit.file_digest(executable), 'archive_entries_count': 3,
                 'files': [{'path': '@executable', 'sha256': audit.file_digest(executable), 'size_bytes': 7},
                           {'path': 'module.dll', 'sha256': hashlib.sha256(files['module.dll']).hexdigest(),
                            'size_bytes': len(files['module.dll'])}]}
    with pytest.raises(ValueError, match='complete PE'):
        audit.check_archive_coverage(executable, inventory)
    inventory['files'].append({'path': 'unexpected.dat', 'sha256': hashlib.sha256(files['unexpected.dat']).hexdigest(),
                               'size_bytes': len(files['unexpected.dat'])})
    audit.check_archive_coverage(executable, inventory)


def test_non_windows_audit_cannot_claim_a_native_scan(tmp_path, monkeypatch):
    monkeypatch.setattr(audit.sys, 'platform', 'linux')
    output = tmp_path / 'audit.json'
    code = audit.main(['--executable', str(tmp_path / 'none.exe'), '--extract-dir', str(tmp_path / 'extract'),
                      '--inventory', str(tmp_path / 'inventory.json'), '--source-commit', 'a' * 40,
                      '--output', str(output)])
    assert code == 1
    report = json.loads(output.read_text())
    assert report['passed'] is False and '/private' not in json.dumps(report)
