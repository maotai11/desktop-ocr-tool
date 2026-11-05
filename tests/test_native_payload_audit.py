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


def pe_with_exports(names_count, *, ordinal_only=True, alias=False):
    """Real, never-executed PE tables exceeding pefile's default 8192 exports."""
    functions_count = names_count + (2 if ordinal_only else 0)
    base = 7
    functions_offset = 0x340
    names_offset = functions_offset + functions_count * 4
    ordinals_offset = names_offset + names_count * 4
    strings_offset = ordinals_offset + names_count * 2
    strings = [f'Export{index:06d}'.encode('ascii') + b'\0' for index in range(names_count)]
    size = ((strings_offset + sum(map(len, strings)) + 511) // 512) * 512
    data = bytearray(tiny_pe())
    data.extend(bytes(max(0, size - len(data))))
    raw_size = len(data) - 512
    struct.pack_into('<II', data, 0x98 + 56, ((raw_size + 8191) // 4096) * 4096, 512)
    struct.pack_into('<II', data, 0x98 + 240 + 8, raw_size, 0x1000)
    struct.pack_into('<I', data, 0x98 + 240 + 16, raw_size)
    def rva(offset):
        return 0x1000 + offset - 512
    struct.pack_into('<II', data, 0x98 + 112, 0x1100, len(data) - 0x300)
    struct.pack_into('<IIHHIIIIIII', data, 0x300, 0, 0, 0, 0, 0, base, functions_count,
                     names_count, rva(functions_offset), rva(names_offset), rva(ordinals_offset))
    position = strings_offset
    for index, name in enumerate(strings):
        struct.pack_into('<I', data, functions_offset + index * 4, 0x1000)
        struct.pack_into('<I', data, names_offset + index * 4, rva(position))
        struct.pack_into('<H', data, ordinals_offset + index * 2, 0 if alias else index)
        data[position:position + len(name)] = name
        position += len(name)
    if ordinal_only:
        # An ordinal-only function followed by an unused EAT slot are both valid.
        struct.pack_into('<I', data, functions_offset + names_count * 4, 0x1000)
    return bytes(data)


def test_large_real_export_tables_are_complete_including_ordinal_only_and_holes():
    pefile = pytest.importorskip('pefile')
    data = pe_with_exports(9000)
    default = pefile.PE(data=data, fast_load=True)
    try:
        default.parse_data_directories(directories=[0])
        assert sum(symbol.name is not None for symbol in default.DIRECTORY_ENTRY_EXPORT.symbols) == 8192
    finally:
        default.close()
    metadata = audit._pe_metadata(data)
    assert {name for name in metadata['_exports'] if isinstance(name, str)} == {
        f'Export{index:06d}' for index in range(9000)}
    assert {ordinal for ordinal in metadata['_exports'] if isinstance(ordinal, int)} == set(range(7, 9008))


def test_named_export_aliases_and_ordinal_only_functions_remain_complete():
    pytest.importorskip('pefile')
    metadata = audit._pe_metadata(pe_with_exports(3, alias=True))
    assert metadata['_exports'] == {'Export000000', 'Export000001', 'Export000002', 7, 8, 9, 10}


@pytest.mark.parametrize('alias', [False, True])
def test_forwarder_metadata_preserves_export_name_and_ordinal_privately(alias):
    pytest.importorskip('pefile')
    data = bytearray(pe_with_exports(3, alias=alias))
    forwarder = b'KERNEL32.Sleep\0'
    # A forwarder is an EAT RVA into the export directory, rather than code.
    data[0x328:0x328 + len(forwarder)] = forwarder
    struct.pack_into('<I', data, 0x340, 0x1128)
    metadata = audit._pe_metadata(bytes(data))
    expected = {7: 'KERNEL32.Sleep', 'Export000000': 'KERNEL32.Sleep'}
    if alias:
        expected.update(Export000001='KERNEL32.Sleep', Export000002='KERNEL32.Sleep')
    assert metadata['_forwarders'] == expected
    assert 7 in metadata['_exports'] and 'Export000000' in metadata['_exports']
    audit._strip_private_pe_metadata(metadata)
    assert not any(key.startswith('_') for key in metadata)
    assert 'KERNEL32.Sleep' not in json.dumps(metadata)


@pytest.mark.parametrize('forwarder', [b'KERNEL32.\xff', b'../escape.Entry', b'KERNEL32.Entry\n', b''])
def test_malformed_or_path_like_forwarder_metadata_is_rejected(forwarder):
    pytest.importorskip('pefile')
    data = bytearray(pe_with_exports(1))
    data[0x328:0x328 + len(forwarder) + 1] = forwarder + b'\0'
    struct.pack_into('<I', data, 0x340, 0x1128)
    with pytest.raises((ValueError, UnicodeDecodeError)):
        audit._pe_metadata(bytes(data))


@pytest.mark.parametrize('defect', ['functions_count', 'names_count', 'function_span', 'name_span',
                                   'ordinal_span', 'ordinal_index', 'directory_span', 'function_address'])
def test_raw_export_counts_and_spans_are_checked_before_parsing(defect):
    pefile = pytest.importorskip('pefile')
    data = bytearray(pe_with_exports(3))
    image_size = struct.unpack_from('<I', data, 0x98 + 56)[0]
    if defect == 'functions_count': struct.pack_into('<I', data, 0x300 + 20, audit.MAX_EXPORT_SYMBOLS + 1)
    if defect == 'names_count': struct.pack_into('<I', data, 0x300 + 24, audit.MAX_EXPORT_SYMBOLS + 1)
    if defect == 'function_span': struct.pack_into('<I', data, 0x300 + 28, image_size - 4)
    if defect == 'name_span': struct.pack_into('<I', data, 0x300 + 32, image_size - 4)
    if defect == 'ordinal_span': struct.pack_into('<I', data, 0x300 + 36, image_size - 2)
    if defect == 'ordinal_index': struct.pack_into('<H', data, 0x340 + 5 * 4 + 3 * 4, 65535)
    if defect == 'directory_span': struct.pack_into('<I', data, 0x98 + 112 + 4, image_size)
    if defect == 'function_address': struct.pack_into('<I', data, 0x340, image_size)
    with pytest.raises((ValueError, pefile.PEFormatError), match='export|RVA'):
        audit._pe_metadata(bytes(data))


def test_silent_export_parser_truncation_is_a_failure(monkeypatch):
    pefile = pytest.importorskip('pefile')
    original = pefile.PE.parse_data_directories
    def truncate(pe, *args, **kwargs):
        original(pe, *args, **kwargs)
        pe.DIRECTORY_ENTRY_EXPORT.symbols.pop()
    monkeypatch.setattr(pefile.PE, 'parse_data_directories', truncate)
    with pytest.raises(ValueError, match='Incomplete native export parsing'):
        audit._pe_metadata(pe_with_exports(3))


def test_dependency_does_not_accept_an_unrelated_same_basename():
    source = {'package': 'owner'}
    importer = {'path': 'owner/module.pyd', 'provenance': source}
    wrong = {'path': 'unrelated/wanted.dll', 'provenance': {'package': 'unrelated'}, '_exports': {'symbol'},
             'machine': 'AMD64', 'is_dll': True}
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
        system_metadata=lambda _: {'_exports': {'RealExport'}, 'machine': 'AMD64', 'is_dll': True},
        api_sets={}, known_dlls={'kernel32.dll'},
        package_dependencies={})
    assert result['missing_symbols_count'] == 1


SXS_TARGET = 'WinSxS/amd64_microsoft.windows.common-controls_fixture/comctl32.dll'


def test_actual_activation_context_target_has_priority_over_a_same_named_bundle():
    metadata = {'machine': 'AMD64', 'is_dll': True, '_exports': {380},
                '_windows_sxs_target': SXS_TARGET, '_windows_sxs_sha256': 'e' * 64}
    bundled = {'path': 'comctl32.dll', 'machine': 'AMD64', 'is_dll': True,
               'provenance': None, '_exports': set()}
    result = audit.resolve_import({'path': '@executable', 'provenance': {}},
        {'name': 'comctl32.dll', 'kind': 'regular', '_symbols': [380]}, [bundled],
        system_metadata=lambda _: pytest.fail('SxS must be resolved before System32/bundle'),
        sxs_metadata=lambda name: metadata, api_sets={}, known_dlls=set(), package_dependencies={})
    assert result == {'name': 'comctl32.dll', 'kind': 'regular', 'resolution': 'windows_sxs',
                      'target': SXS_TARGET, 'target_sha256': 'e' * 64,
                      'symbols_count': 1, 'missing_symbols_count': 0}
    metadata['_exports'] = set()
    assert audit.resolve_import({'path': '@executable', 'provenance': {}},
        {'name': 'comctl32.dll', 'kind': 'regular', '_symbols': [380]}, [], system_metadata=lambda _: None,
        sxs_metadata=lambda name: metadata, api_sets={}, known_dlls=set(), package_dependencies={}
    )['missing_symbols_count'] == 1


def test_public_sxs_evidence_requires_canonical_os_relative_identity_and_hash(evidence):
    inventory, _ = evidence
    dependency = inventory['files'][1]['imports'][0]
    dependency.update(name='comctl32.dll', resolution='windows_sxs', target=SXS_TARGET, target_sha256='e' * 64)
    audit.check_inventory(inventory, executable_sha256='d' * 64, source_commit='a' * 40)


@pytest.mark.parametrize('defect', ['missing_hash', 'bad_hash', 'system32_path', 'private_path',
                                   'wrong_architecture', 'other_dll', 'extra_private_field', 'system32_extra_hash'])
def test_sxs_public_metadata_cannot_hide_missing_proof_or_private_paths(evidence, defect):
    inventory, _ = evidence
    dependency = inventory['files'][1]['imports'][0]
    dependency.update(name='comctl32.dll', resolution='windows_sxs', target=SXS_TARGET, target_sha256='e' * 64)
    if defect == 'missing_hash': dependency.pop('target_sha256')
    if defect == 'bad_hash': dependency['target_sha256'] = False
    if defect == 'system32_path': dependency['target'] = 'System32/comctl32.dll'
    if defect == 'private_path': dependency['target'] = 'C:/private/WinSxS/amd64_component/comctl32.dll'
    if defect == 'wrong_architecture': dependency['target'] = 'WinSxS/x86_component/comctl32.dll'
    if defect == 'other_dll': dependency.update(name='unrelated.dll', target='WinSxS/amd64_component/unrelated.dll')
    if defect == 'extra_private_field': dependency['private_path'] = '/private/user'
    if defect == 'system32_extra_hash': dependency.update(resolution='system32', target='comctl32.dll')
    with pytest.raises(ValueError):
        audit.check_inventory(inventory, executable_sha256='d' * 64, source_commit='a' * 40)


def test_system_metadata_uses_exact_context_path_and_hash_then_caches_it(tmp_path):
    pytest.importorskip('pefile')
    windows = tmp_path / 'Windows'
    target = windows / SXS_TARGET
    target.parent.mkdir(parents=True)
    data = pe_with_exports(1)
    target.write_bytes(data)
    system = windows / 'System32'
    system.mkdir()
    (system / 'comctl32.dll').write_bytes(tiny_pe())
    calls = []
    context = SimpleNamespace(windows_root=windows, resolve_dll=lambda name: calls.append(name) or target)
    system_metadata, redirected_metadata = audit._system_metadata_loader(system, context)
    metadata = redirected_metadata('comctl32.dll')
    assert system_metadata('comctl32.dll') is metadata
    assert metadata['_windows_sxs_target'] == SXS_TARGET
    assert metadata['_windows_sxs_sha256'] == hashlib.sha256(data).hexdigest()
    assert metadata['_exports'] == {7, 8, 'Export000000'} and calls == ['comctl32.dll']


@pytest.mark.parametrize('defect', ['outside_windows', 'system32_target', 'not_dll'])
def test_invalid_context_target_fails_instead_of_falling_back_to_system32(tmp_path, defect):
    pytest.importorskip('pefile')
    windows = tmp_path / 'Windows'
    system = windows / 'System32'
    system.mkdir(parents=True)
    target = windows / SXS_TARGET
    if defect == 'outside_windows': target = tmp_path / SXS_TARGET
    if defect == 'system32_target': target = system / 'comctl32.dll'
    target.parent.mkdir(parents=True, exist_ok=True)
    data = bytearray(tiny_pe())
    if defect == 'not_dll': struct.pack_into('<H', data, 0x84 + 18, 0x22)
    target.write_bytes(data)
    context = SimpleNamespace(windows_root=windows, resolve_dll=lambda name: target)
    system_metadata, _ = audit._system_metadata_loader(system, context)
    with pytest.raises(ValueError):
        system_metadata('comctl32.dll')


def test_no_sxs_redirection_preserves_the_system32_resolution(tmp_path):
    pytest.importorskip('pefile')
    system = tmp_path / 'System32'
    system.mkdir()
    (system / 'ordinary.dll').write_bytes(tiny_pe())
    context = SimpleNamespace(windows_root=tmp_path, resolve_dll=lambda name: None)
    system_metadata, sxs_metadata = audit._system_metadata_loader(system, context)
    assert sxs_metadata('ordinary.dll') is None
    assert system_metadata('ordinary.dll')['machine'] == 'AMD64'
    assert system_metadata('missing.dll') is None


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


def api_set_fixture(*hosts, name='api-ms-win-test-l1-1-0', hashed_prefix=None):
    hashed_length = len((hashed_prefix or name).encode('utf-16-le'))
    name = name.encode('utf-16-le')
    encoded = [host.encode('utf-16-le') for host in hosts]
    strings = 52 + 20 * len(hosts)
    data = bytearray(strings + len(name) + sum(map(len, encoded)))
    struct.pack_into('<7I', data, 0, 6, len(data), 0, 1, 28, 0, 0)
    struct.pack_into('<6I', data, 28, 0, strings, len(name), hashed_length, 52, len(hosts))
    data[strings:strings + len(name)] = name
    position = strings + len(name)
    for index, raw in enumerate(encoded):
        struct.pack_into('<5I', data, 52 + 20 * index, 0, 0, 0, position, len(raw))
        data[position:position + len(raw)] = raw
        position += len(raw)
    return bytes(data)


def conditional_api_set_fixture(default_hosts, conditional_values, *, measured_revision=False, name=None):
    name = name or ('api-ms-win-test-l1-1-2' if measured_revision else 'api-ms-win-test-l1-1-0')
    data = bytearray(api_set_fixture(*default_hosts, *(host for alias, host in conditional_values),
                                    name=name, hashed_prefix=name.rsplit('-', 1)[0] if measured_revision else None))
    for index, (alias, _host) in enumerate(conditional_values, len(default_hosts)):
        encoded = alias.encode('utf-16-le')
        offset = len(data)
        data.extend(encoded)
        struct.pack_into('<II', data, 52 + index * 20 + 4, offset, len(encoded))
    struct.pack_into('<I', data, 4, len(data))
    return bytes(data)


@pytest.mark.parametrize('measured_revision', [False, True])
def test_conditional_host_for_another_importer_cannot_create_a_false_proof(measured_revision):
    namespace = audit.parse_api_set(conditional_api_set_fixture(
        ['default.dll'], [('special.dll', 'alternate.dll')], measured_revision=measured_revision))
    canonical = 'api-ms-win-test-l1-1-2.dll' if measured_revision else 'api-ms-win-test-l1-1-0.dll'
    assert namespace[canonical] == ('default.dll', 'alternate.dll')  # Preserve the canonical metadata.
    calls = []
    def system_metadata(name):
        calls.append(name)
        return {'machine': 'AMD64', 'is_dll': True, '_exports': {'Missing'} if name == 'alternate.dll' else set()}
    result = audit.resolve_import({'path': 'regular.pyd', 'provenance': {}},
        {'name': 'api-ms-win-test-l1-1-0.dll', 'kind': 'regular', '_symbols': ['Missing']}, [],
        system_metadata=system_metadata, api_sets=namespace, known_dlls=set(), package_dependencies={})
    assert result['resolution'] == 'unresolved' and result['missing_symbols_count'] == 1 and calls == ['default.dll']


@pytest.mark.parametrize('importer,expected', [
    ('kernel32.dll', ('kernelbase.dll',)), ('KERNEL32.DLL', ('kernelbase.dll',)),
    ('regular.pyd', ('kernel32.dll',)), ('kernel32', ('kernel32.dll',)),
    ('kernel32.dll.extra', ('kernel32.dll',)), (None, ()), ('../kernel32.dll', ()),
])
def test_measured_windows_conditional_contract_uses_exact_importer_basename(importer, expected):
    # Actual Windows run 41993201779 observed canonical processthreads revision8,
    # default kernel32.dll, and only alias kernel32.dll -> kernelbase.dll.
    canonical = 'api-ms-win-core-processthreads-l1-1-8'
    namespace = audit.parse_api_set(conditional_api_set_fixture(
        ['kernel32.dll'], [('kernel32.dll', 'kernelbase.dll')], measured_revision=True, name=canonical))
    assert namespace.hosts_for_name(canonical + '.dll', importer) == expected
    assert namespace.hosts_for_name('api-ms-win-core-processthreads-l1-1-0.dll', importer) == expected
    assert namespace.all_hosts_for_name('api-ms-win-core-processthreads-l1-1-0.dll') == ('kernel32.dll', 'kernelbase.dll')


def test_true_logical_parent_is_derived_but_explicit_unknown_overrides_it():
    namespace = audit.parse_api_set(conditional_api_set_fixture(['default.dll'], [('kernel32.dll', 'alternate.dll')]))
    dependency = {'name': 'api-ms-win-test-l1-1-0.dll', 'kind': 'regular', '_symbols': ['Entry']}
    def resolve(path, **kwargs):
        return audit.resolve_import({'path': path, 'provenance': {}}, dependency, [],
            system_metadata=lambda _: {'machine': 'AMD64', 'is_dll': True, '_exports': {'Entry'}},
            api_sets=namespace, known_dlls=set(), package_dependencies={}, **kwargs)
    assert resolve('package/KERNEL32.DLL')['target'] == 'alternate.dll'
    assert resolve('package/regular.pyd')['target'] == 'default.dll'
    assert resolve('package/kernel32.dll', importer_name=None)['resolution'] == 'unresolved'
    assert resolve('C:/private/kernel32.dll')['resolution'] == 'unresolved'
    assert resolve('@executable')['resolution'] == 'unresolved'
    assert resolve('@executable', importer_name='InkPick.exe')['target'] == 'default.dll'


def test_selected_importer_alias_must_have_a_single_unambiguous_host():
    namespace = audit.parse_api_set(conditional_api_set_fixture(
        ['default.dll'], [('special.dll', 'first.dll'), ('special.dll', 'second.dll')]))
    assert namespace.hosts_for_name('api-ms-win-test-l1-1-0.dll', 'special.dll') == ()
    assert namespace.hosts_for_name('api-ms-win-test-l1-1-0.dll', 'regular.pyd') == ('default.dll',)
    assert namespace.hosts_for_name('api-ms-win-test-l1-1-0.dll') == ()
    assert namespace.all_hosts_for_name('api-ms-win-test-l1-1-0.dll') == ()
    no_default = audit.parse_api_set(conditional_api_set_fixture([], [('special.dll', 'selected.dll')]))
    assert no_default.hosts_for_name('api-ms-win-test-l1-1-0.dll', 'special.dll') == ('selected.dll',)
    assert no_default.hosts_for_name('api-ms-win-test-l1-1-0.dll', 'regular.pyd') == ()
    assert no_default.all_hosts_for_name('api-ms-win-test-l1-1-0.dll') == ()
    conflicting_default = audit.parse_api_set(conditional_api_set_fixture(['first.dll', 'second.dll'], []))
    assert conflicting_default.hosts_for_name('api-ms-win-test-l1-1-0.dll', 'regular.pyd') == ()
    assert conflicting_default.all_hosts_for_name('api-ms-win-test-l1-1-0.dll') == ()


def test_same_prefix_different_context_maps_cannot_be_guessed_for_another_revision():
    namespace = audit._ApiSetNamespace()
    prefix = 'api-ms-win-test-l1-1'
    namespace.add_record(prefix + '-1.dll', prefix, ('default.dll', 'first.dll'),
                         selection_hosts={None: ['default.dll'], 'kernel32.dll': ['first.dll']})
    namespace.add_record(prefix + '-2.dll', prefix, ('default.dll', 'second.dll'),
                         selection_hosts={None: ['default.dll'], 'kernel32.dll': ['second.dll']})
    assert namespace.hosts_for_name(prefix + '-1.dll', 'kernel32.dll') == ('first.dll',)
    assert namespace.hosts_for_name(prefix + '-0.dll', 'kernel32.dll') == ()
    assert namespace.hosts_for_name(prefix + '-0.dll', 'regular.pyd') == ()
    assert namespace.all_hosts_for_name(prefix + '-0.dll') == ()


def test_all_host_identity_choices_preserve_unusable_and_empty_values():
    kernel = audit.parse_api_set(conditional_api_set_fixture(['default.dll'], [('special.dll', 'win32k.sys')]))
    assert kernel.all_hosts_for_name('api-ms-win-test-l1-1-0.dll') == ('default.dll', 'win32k.sys')
    empty = audit.parse_api_set(conditional_api_set_fixture(['default.dll'], [('special.dll', '')]))
    assert empty.all_hosts_for_name('api-ms-win-test-l1-1-0.dll') == ('', 'default.dll')
    assert empty.all_hosts_for_name('api-ms-win-missing-l1-1-0.dll') is None


def test_conditional_host_ambiguity_also_blocks_the_build_filter_os_proof(tmp_path):
    from scripts.windows_binary_policy import _SystemProof
    system = tmp_path / 'System32'
    system.mkdir()
    namespace = audit.parse_api_set(conditional_api_set_fixture(['default.dll'], [('special.dll', 'alternate.dll')]))
    proof = _SystemProof(loader=lambda: (system, namespace, set()))
    proof.metadata = lambda _: pytest.fail('Context-required contracts cannot select a host')
    assert proof.satisfies('api-ms-win-test-l1-1-0.dll', ['Missing']) is False


@pytest.mark.parametrize('defaults,conditional,expected', [
    (['default.dll'], [('special.dll', 'default.dll')], ('default.dll',)),
    (['default.dll', 'default.dll'], [('special.dll', 'default.dll')], ('default.dll',)),
    ([], [('special.dll', 'alternate.dll')], ()),
    (['first.dll', 'second.dll'], [], ()),
])
def test_only_equivalent_conditional_hosts_and_a_unique_default_are_provable(defaults, conditional, expected):
    namespace = audit.parse_api_set(conditional_api_set_fixture(defaults, conditional))
    assert namespace.hosts_for_name('api-ms-win-test-l1-1-0.dll') == expected


def test_conditional_importer_alias_is_validated_even_when_its_host_is_not_provable():
    data = conditional_api_set_fixture(['default.dll'], [('../private.dll', 'alternate.dll')])
    with pytest.raises(ValueError, match='path|alias'):
        audit.parse_api_set(data)


def test_observed_windows_kernel_and_winspool_hosts_parse_without_global_failure():
    # Windows Server 2025 diagnostic run 41983531330 observed .sys records and
    # winspool.drv in the real v6 schema. The synthetic contract keeps no host paths.
    result = audit.parse_api_set(api_set_fixture('win32kfull.sys', 'winspool.drv', 'kernelbase.dll'))
    assert result['api-ms-win-test-l1-1-0.dll'] == ('win32kfull.sys', 'winspool.drv', 'kernelbase.dll')


@pytest.mark.parametrize('canonical,requested,host,symbol', [
    ('api-ms-win-shcore-scaling-l1-1-2', 'api-ms-win-shcore-scaling-l1-1-1', 'shcore.dll', 'GetDpiForMonitor'),
    ('api-ms-win-core-winrt-string-l1-1-1', 'api-ms-win-core-winrt-string-l1-1-0', 'combase.dll',
     'WindowsCreateStringReference'),
])
def test_observed_api_set_revisions_use_measured_hash_prefix(canonical, requested, host, symbol):
    # Actual Windows Server 2025 run 41988813188 measured these HashedLength
    # prefixes (60/66 bytes); exact requested revision records were absent.
    namespace = audit.parse_api_set(api_set_fixture(host, name=canonical, hashed_prefix=canonical.rsplit('-', 1)[0]))
    assert list(namespace) == [canonical + '.dll']
    calls = []
    def system_metadata(name):
        calls.append(name)
        return {'machine': 'AMD64', 'is_dll': True, '_exports': {symbol}}
    result = audit.resolve_import({'path': '@executable', 'provenance': {}},
        {'name': requested + '.dll', 'kind': 'delay', '_symbols': [symbol]}, [],
        system_metadata=system_metadata, api_sets=namespace, known_dlls=set(), package_dependencies={})
    assert result['resolution'] == 'api_set' and result['target'] == host and result['missing_symbols_count'] == 0
    assert calls == [host]


@pytest.mark.parametrize('defect', ['plain_dict', 'full_name_hash', 'wrong_prefix', 'non_numeric_revision',
                                   'kernel_only', 'missing_symbol', 'not_dll'])
def test_revision_alias_never_guesses_unmeasured_or_invalid_hosts(defect):
    canonical = 'api-ms-win-test-l1-1-2'
    prefix = canonical.rsplit('-', 1)[0]
    namespace = audit.parse_api_set(api_set_fixture('kernelbase.dll', name=canonical, hashed_prefix=prefix))
    requested = 'api-ms-win-test-l1-1-0.dll'
    metadata = {'machine': 'AMD64', 'is_dll': True, '_exports': {'Entry'}}
    if defect == 'plain_dict': namespace = dict(namespace)
    if defect == 'full_name_hash': namespace = audit.parse_api_set(api_set_fixture('kernelbase.dll', name=canonical))
    if defect == 'wrong_prefix': requested = 'api-ms-win-other-l1-1-0.dll'
    if defect == 'non_numeric_revision': requested = 'api-ms-win-test-l1-1-guess.dll'
    if defect == 'kernel_only': namespace = audit.parse_api_set(api_set_fixture('win32k.sys', name=canonical, hashed_prefix=prefix))
    if defect == 'missing_symbol': metadata['_exports'] = set()
    if defect == 'not_dll': metadata['is_dll'] = False
    calls = []
    def system_metadata(name):
        calls.append(name)
        return metadata if name == 'kernelbase.dll' else None
    result = audit.resolve_import({'path': '@executable', 'provenance': {}},
        {'name': requested, 'kind': 'regular', '_symbols': ['Entry']}, [],
        system_metadata=system_metadata, api_sets=namespace, known_dlls=set(), package_dependencies={})
    assert result['resolution'] == 'unresolved' and result['missing_symbols_count'] == 1
    if defect == 'kernel_only': assert calls == []


def test_conflicting_hash_prefix_is_unresolved_but_canonical_records_stay_exact():
    namespace = audit._ApiSetNamespace()
    prefix = 'api-ms-win-test-l1-1'
    namespace.add_record(prefix + '-1.dll', prefix, ('first.dll',))
    namespace.add_record(prefix + '-2.dll', prefix, ('second.dll',))
    assert namespace.hosts_for_name(prefix + '-1.dll') == ('first.dll',)
    assert namespace.hosts_for_name(prefix + '-2.dll') == ('second.dll',)
    calls = []
    result = audit.resolve_import({'path': '@executable', 'provenance': {}},
        {'name': prefix + '-0.dll', 'kind': 'regular', '_symbols': ['Entry']}, [],
        system_metadata=lambda name: calls.append(name), api_sets=namespace,
        known_dlls=set(), package_dependencies={})
    assert result['resolution'] == 'unresolved' and calls == []
    namespace.add_record(prefix + '-3.dll', prefix, ('first.dll',))
    assert namespace.hosts_for_name(prefix + '-0.dll') == ()


def test_identical_measured_host_tuples_can_share_a_prefix():
    namespace = audit._ApiSetNamespace()
    prefix = 'api-ms-win-test-l1-1'
    namespace.add_record(prefix + '-1.dll', prefix, ('kernelbase.dll',))
    namespace.add_record(prefix + '-2.dll', prefix, ('kernelbase.dll',))
    assert namespace.hosts_for_name(prefix + '-0.dll') == ('kernelbase.dll',)


@pytest.mark.parametrize('hashed_size', [0, 1, 65536])
def test_invalid_measured_hash_prefix_bounds_are_rejected(hashed_size):
    data = bytearray(api_set_fixture('kernelbase.dll'))
    struct.pack_into('<I', data, 28 + 12, hashed_size)
    with pytest.raises(ValueError, match='hashed length'):
        audit.parse_api_set(data)


def test_kernel_only_api_set_never_resolves_a_user_mode_import():
    calls = []
    namespace = audit.parse_api_set(api_set_fixture('win32kfull.sys'))
    result = audit.resolve_import({'path': '@executable', 'provenance': {}},
        {'name': 'api-ms-win-test-l1-1-0.dll', 'kind': 'regular', '_symbols': ['KernelEntry']}, [],
        system_metadata=lambda name: calls.append(name), api_sets=namespace,
        known_dlls=set(), package_dependencies={})
    assert result['resolution'] == 'unresolved' and result['missing_symbols_count'] == 1
    assert calls == []


@pytest.mark.parametrize('via_api_set', [False, True])
@pytest.mark.parametrize('kind', ['regular', 'delay'])
def test_winspool_drv_resolves_only_as_an_actual_user_dll(via_api_set, kind):
    namespace = audit.parse_api_set(api_set_fixture('winspool.drv')) if via_api_set else {}
    name = 'api-ms-win-test-l1-1-0.dll' if via_api_set else 'winspool.drv'
    calls = []
    def system_metadata(target):
        calls.append(target)
        return {'machine': 'AMD64', 'is_dll': True, '_exports': {'OpenPrinterW'}}
    result = audit.resolve_import({'path': '@executable', 'provenance': {}},
        {'name': name, 'kind': kind, '_symbols': ['OpenPrinterW']}, [], system_metadata=system_metadata,
        api_sets=namespace, known_dlls=set(), package_dependencies={})
    assert result == {'name': name, 'kind': kind, 'resolution': 'api_set' if via_api_set else 'system32',
                      'target': 'winspool.drv', 'symbols_count': 1, 'missing_symbols_count': 0}
    assert calls == ['winspool.drv']


@pytest.mark.parametrize('defect', ['driver_image', 'wrong_architecture', 'missing_export'])
def test_special_library_host_does_not_bypass_pe_or_symbol_validation(defect):
    metadata = {'machine': 'AMD64', 'is_dll': True, '_exports': {'OpenPrinterW'}}
    if defect == 'driver_image': metadata['is_dll'] = False
    if defect == 'wrong_architecture': metadata['machine'] = 'I386'
    if defect == 'missing_export': metadata['_exports'] = set()
    result = audit.resolve_import({'path': '@executable', 'provenance': {}},
        {'name': 'api-ms-win-test-l1-1-0.dll', 'kind': 'delay', '_symbols': ['OpenPrinterW']}, [],
        system_metadata=lambda _: metadata, api_sets=audit.parse_api_set(api_set_fixture('winspool.drv')),
        known_dlls=set(), package_dependencies={})
    assert result['resolution'] == 'unresolved'


@pytest.mark.parametrize('host', ['../escape.dll', '/outside.dll', 'C:\\private.dll', 'bad.dll:stream',
                                 'unknown.exe', 'unknown.bin'])
def test_api_set_host_path_and_unobserved_extensions_remain_rejected(host):
    with pytest.raises(ValueError):
        audit.parse_api_set(api_set_fixture(host))


def tiny_pe_with_import(name):
    data = bytearray(tiny_pe())
    struct.pack_into('<II', data, 0x98 + 112 + 8, 0x1100, 40)
    struct.pack_into('<5I', data, 0x300, 0x1180, 0, 0, 0x1140, 0x1190)
    raw = name.encode('ascii') + b'\0'
    data[0x340:0x340 + len(raw)] = raw
    struct.pack_into('<Q', data, 0x380, 0x11A0)
    struct.pack_into('<Q', data, 0x390, 0x11A0)
    data[0x3A0:0x3A0 + 15] = b'\0\0OpenPrinterW\0'
    return bytes(data)


def test_real_pe_parser_accepts_drv_import_without_accepting_kernel_imports():
    pytest.importorskip('pefile')
    metadata = audit._pe_metadata(tiny_pe_with_import('WINSPOOL.DRV'))
    assert metadata['imports'] == [{'name': 'winspool.drv', 'kind': 'regular', '_symbols': ['OpenPrinterW']}]
    for name in ('win32kfull.sys', 'ntoskrnl.exe'):
        with pytest.raises(ValueError, match='user-mode'):
            audit._pe_metadata(tiny_pe_with_import(name))


def test_public_dependency_shape_accepts_drv_but_rejects_kernel_and_executable(evidence):
    inventory, _ = evidence
    dependency = inventory['files'][1]['imports'][0]
    dependency.update(name='winspool.drv', target='winspool.drv')
    audit.check_inventory(inventory, executable_sha256='d' * 64, source_commit='a' * 40)
    for name in ('win32kfull.sys', 'ntoskrnl.exe'):
        dependency.update(name=name, target=name)
        with pytest.raises(ValueError):
            audit.check_inventory(inventory, executable_sha256='d' * 64, source_commit='a' * 40)


def test_bundled_executable_image_cannot_be_claimed_as_a_dll(evidence):
    inventory, _ = evidence
    inventory['files'][1]['is_dll'] = False
    inventory['files'][0]['imports'] = [{'name': 'python312.dll', 'kind': 'regular', 'resolution': 'bundled',
        'target': 'python312.dll', 'symbols_count': 1, 'missing_symbols_count': 0}]
    with pytest.raises(ValueError, match='not a DLL'):
        audit.check_inventory(inventory, executable_sha256='d' * 64, source_commit='a' * 40)


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


@pytest.fixture
def locked_runtime(tmp_path, monkeypatch):
    from scripts import prepare_windows_runtime
    payloads = {'python312.dll': b'MZlocked-python' + bytes(80),
                'DLLs/_ssl.pyd': b'MZlocked-ssl' + bytes(80),
                'Lib/venv/scripts/nt/python.exe': b'MZlocked-venv' + bytes(80),
                'Lib/site-packages/pip/_vendor/distlib/t64.exe': b'MZold-pip' + bytes(80)}
    lock = {'python_version': '3.12.16', 'files': {
        path: {'sha256': hashlib.sha256(data).hexdigest(), 'size_bytes': len(data)}
        for path, data in payloads.items()}}
    for relative, data in payloads.items():
        source = tmp_path / relative
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(data)
    # This double isolates the lock consumer. The bootstrap's own tests verify
    # provider/archive identity and the actual committed map independently.
    committed = copy.deepcopy(lock)
    monkeypatch.setattr(prepare_windows_runtime, 'load_runtime_lock', lambda *args: copy.deepcopy(committed))
    return tmp_path, lock, payloads


def test_runtime_sources_require_committed_path_hash_and_version(locked_runtime):
    directory, lock, _ = locked_runtime
    index = {}
    audit._runtime_sources(index, runtime_root=directory, runtime_version='3.12.16')
    sources = [item for values in index.values() for item in values]
    assert {item['source_path'] for item in sources} == {
        'python312.dll', 'DLLs/_ssl.pyd', 'Lib/venv/scripts/nt/python.exe'}
    assert all(item['version'] == lock['python_version'] and item['kind'] == 'cpython_runtime'
               and item['source_sha256'] == lock['files'][item['source_path']]['sha256'] for item in sources)


@pytest.mark.parametrize('defect', ['modified', 'replacement', 'missing', 'version', 'unknown', 'disguised'])
def test_runtime_tampering_or_unknown_pe_never_receives_provenance(locked_runtime, defect):
    directory, _, payloads = locked_runtime
    version = '3.12.16'
    if defect == 'modified': (directory / 'python312.dll').write_bytes(b'MZunlocked-python' + bytes(80))
    if defect == 'replacement': (directory / 'python312.dll').write_bytes(payloads['DLLs/_ssl.pyd'])
    if defect == 'missing': (directory / 'DLLs/_ssl.pyd').unlink()
    if defect == 'version': version = '3.12.10'
    if defect == 'unknown': (directory / 'DLLs/unknown.dll').write_bytes(b'MZunknown' + bytes(80))
    if defect == 'disguised': (directory / 'DLLs/hidden.dat').write_bytes(b'MZhidden' + bytes(80))
    index = {}
    with pytest.raises(ValueError):
        audit._runtime_sources(index, runtime_root=directory, runtime_version=version)
    assert index == {}


def test_runtime_report_cannot_override_committed_pe_hashes(locked_runtime):
    directory, lock, _ = locked_runtime
    changed = b'MZunlocked-python' + bytes(80)
    (directory / 'python312.dll').write_bytes(changed)
    lock['files']['python312.dll']['sha256'] = hashlib.sha256(changed).hexdigest()
    lock['files']['python312.dll']['size_bytes'] = len(changed)
    (directory / 'mutable-runtime-report.json').write_text(json.dumps(lock), encoding='utf-8')
    index = {}
    with pytest.raises(ValueError):
        audit._runtime_sources(index, runtime_root=directory, runtime_version='3.12.16')
    assert index == {}


def test_pip_replacement_is_wheel_owned_and_not_indexed_as_cpython(locked_runtime):
    directory, _, _ = locked_runtime
    package = directory / 'Lib/site-packages'
    (package / 'pip/_vendor/distlib/t64.exe').write_bytes(b'MZnew-pip' + bytes(80))
    (package / 'new-package').mkdir()
    (package / 'new-package/native.pyd').write_bytes(b'MZwheel-owned' + bytes(80))
    index = {}
    audit._runtime_sources(index, runtime_root=directory, runtime_version='3.12.16')
    assert all('/site-packages/' not in '/' + item['source_path']
               for values in index.values() for item in values)


def test_redirected_runtime_file_is_rejected_even_for_known_bytes(locked_runtime):
    directory, _, payloads = locked_runtime
    source = directory / 'DLLs/_ssl.pyd'
    source.unlink()
    target = directory / 'elsewhere.dat'
    target.write_bytes(payloads['DLLs/_ssl.pyd'])
    try:
        source.symlink_to(target)
    except OSError:
        pytest.skip('Creating a test symlink requires host privileges')
    with pytest.raises(ValueError, match='redirected'):
        audit._runtime_sources({}, runtime_root=directory, runtime_version='3.12.16')


@pytest.mark.parametrize('defect', ['version', 'path', 'size', 'digest', 'package', 'site_packages'])
def test_public_runtime_provenance_is_revalidated_against_committed_map(evidence, defect):
    inventory, _ = evidence
    item = inventory['files'][1]
    if defect == 'version': item['provenance']['version'] = '3.12.10'
    if defect == 'path': item['provenance']['source_path'] = 'DLLs/_ssl.pyd'
    if defect == 'size': item['size_bytes'] += 1
    if defect == 'digest': item['sha256'] = item['provenance']['source_sha256'] = 'e' * 64
    if defect == 'package': item['provenance']['package'] = 'unverified-provider'
    if defect == 'site_packages': item['provenance']['source_path'] = 'Lib/site-packages/native.pyd'
    with pytest.raises(ValueError):
        audit.check_inventory(inventory, executable_sha256='d' * 64, source_commit='a' * 40)
