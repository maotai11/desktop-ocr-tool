"""Inventory the actual frozen PE payload without loading any of its DLLs.

Windows runs this after building, in the same installed-wheel environment.
Authenticode and Defender are supplied by validate_native_payload.ps1; neither
an absent scanner nor an unsigned file is silently described as a clean scan.
This is finite provenance/import/AV evidence, not an unknown-CVE certification.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.metadata
import json
import os
import re
import shutil
import struct
import sys
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath, PureWindowsPath

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from src.core.version import APP_VERSION

MAX_ENTRIES = 10_000
MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
MAX_NATIVE_FILES = 2048
SHA = re.compile(r'[0-9a-f]{64}')
COMMIT = re.compile(r'[0-9a-f]{40}')
NATIVE_SUFFIXES = ('.dll', '.pyd', '.exe')
PROVENANCE_KINDS = {'wheel_record', 'cpython_runtime', 'native_helper_lock',
                    'pyinstaller_bootloader_code', 'windows_system_file'}
SCANNER_KEYS = ('antivirus_scan_completed', 'antivirus_detections_count',
    'antivirus_scanned_files_count', 'scan_scope_files_sha256', 'scanner',
    'scanner_version', 'antivirus_signature_version', 'signature_updated_at',
    'scan_started_at', 'scan_finished_at', 'scan_duration_seconds',
    'am_running_mode', 'real_time_protection_enabled', 'sample_submission_consent', 'maps_reporting')


def require(value, message):
    if not value:
        raise ValueError(message)


def canonical_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')


def inventory_digest(inventory):
    return hashlib.sha256(canonical_bytes(inventory)).hexdigest()


def file_digest(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def safe_path(value, *, outer=False):
    if outer and value == '@executable':
        return value
    require(type(value) is str and 0 < len(value) <= 512, 'Invalid relative payload path')
    require('\\' not in value and not PureWindowsPath(value).drive
            and not value.startswith('/') and PurePosixPath(value).as_posix() == value,
            'Non-relative payload path')
    parts = value.split('/')
    reserved = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)),
                *(f'LPT{i}' for i in range(1, 10))}
    require(all(part not in ('', '.', '..') and not part.endswith((' ', '.'))
                and part.split('.')[0].upper() not in reserved
                and not any(ord(char) < 32 or char in ':<>"|?*' for char in part)
                for part in parts), 'Unsafe payload path component')
    return value


def _short_text(value, maximum=160):
    return type(value) is str and len(value) <= maximum and not any(
        ord(char) < 32 or char in '\\/' for char in value)


def _utc(value):
    require(type(value) is str, 'Invalid timestamp type')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    require(parsed.utcoffset() == timedelta(0), 'Timestamp must be UTC')
    return parsed


def check_inventory(inventory, *, executable_sha256, source_commit):
    """Strict public-safe inventory boundary, independent of extraction/tools."""
    keys = {'schema', 'probe', 'version', 'platform', 'source_commit', 'source_run_id',
            'source_run_attempt', 'executable_sha256', 'archive_entries_count', 'native_files_count', 'files'}
    require(type(inventory) is dict and set(inventory) == keys, 'Invalid native inventory fields')
    require(type(inventory['schema']) is int and inventory['schema'] == 1
            and inventory['probe'] == 'native_payload_inventory' and inventory['version'] == APP_VERSION
            and inventory['platform'] == 'win32', 'Invalid native inventory identity')
    require(type(executable_sha256) is str and SHA.fullmatch(executable_sha256)
            and inventory['executable_sha256'] == executable_sha256
            and type(source_commit) is str and COMMIT.fullmatch(source_commit)
            and inventory['source_commit'] == source_commit, 'Native inventory source or EXE mismatch')
    for key in ('source_run_id', 'source_run_attempt'):
        require(inventory[key] is None or (type(inventory[key]) is str
                and re.fullmatch(r'[1-9][0-9]{0,19}', inventory[key])), 'Invalid workflow identity')
    count, entries, files = inventory['native_files_count'], inventory['archive_entries_count'], inventory['files']
    require(type(count) is int and 2 <= count <= MAX_NATIVE_FILES and type(entries) is int
            and 1 <= entries <= MAX_ENTRIES and count <= entries + 1
            and type(files) is list and len(files) == count, 'Incomplete native inventory coverage')
    paths, scan_paths = set(), set()
    for item in files:
        require(type(item) is dict and set(item) == {'path', 'scan_path', 'sha256', 'size_bytes',
            'machine', 'is_dll', 'file_version', 'product_version', 'component_version', 'provenance', 'imports', 'signature_status'},
            'Invalid native file fields')
        path = safe_path(item['path'], outer=True)
        scan_path = safe_path(item['scan_path'])
        require(path.casefold() not in paths and scan_path.casefold() not in scan_paths,
                'Duplicate native inventory path')
        paths.add(path.casefold())
        scan_paths.add(scan_path.casefold())
        require((path == '@executable' and scan_path.startswith('candidate/')
                 and len(PurePosixPath(scan_path).parts) == 2)
                or (path != '@executable' and scan_path == 'payload/' + path), 'Native scan path mismatch')
        require(type(item['sha256']) is str and SHA.fullmatch(item['sha256'])
                and type(item['size_bytes']) is int and 64 <= item['size_bytes'] <= MAX_FILE_BYTES
                and item['machine'] == 'AMD64' and type(item['is_dll']) is bool,
                'Invalid PE identity')
        require(all(_short_text(item[key]) for key in ('file_version', 'product_version', 'component_version')),
                'Invalid PE version metadata')
        require(item['signature_status'] in ('Valid', 'NotSigned'), 'Native signature was not verified')
        source = item['provenance']
        require(type(source) is dict and set(source) == {'kind', 'package', 'version', 'source_path',
                'source_sha256', 'match'} and source['kind'] in PROVENANCE_KINDS,
                'Unmapped native provenance')
        safe_path(source['source_path'])
        require(_short_text(source['package']) and bool(source['package']) and _short_text(source['version'])
                and bool(source['version']) and type(source['source_sha256']) is str
                and SHA.fullmatch(source['source_sha256']), 'Invalid native provenance identity')
        if path == '@executable':
            require(source['kind'] == 'pyinstaller_bootloader_code'
                    and source['match'] == 'executable_sections', 'Unverified outer bootloader')
        else:
            require(source['kind'] != 'pyinstaller_bootloader_code' and source['match'] == 'sha256'
                    and source['source_sha256'] == item['sha256'], 'Native source hash mismatch')
        if source['kind'] == 'windows_system_file':
            require(item['signature_status'] == 'Valid', 'Bundled Windows source must have a valid signature')
        require(type(item['imports']) is list and len(item['imports']) <= 1024, 'Invalid import list')
        seen_imports = set()
        for dependency in item['imports']:
            require(type(dependency) is dict and set(dependency) == {'name', 'kind', 'resolution', 'target',
                'symbols_count', 'missing_symbols_count'}, 'Invalid dependency fields')
            name = safe_path(dependency['name'])
            require('/' not in name and name.lower().endswith('.dll')
                    and dependency['kind'] in ('regular', 'delay')
                    and dependency['resolution'] in ('bundled', 'system32', 'api_set'), 'Unresolved native dependency')
            identity = (name.casefold(), dependency['kind'])
            require(identity not in seen_imports, 'Duplicate import descriptor')
            seen_imports.add(identity)
            target = safe_path(dependency['target'])
            require(type(dependency['symbols_count']) is int and 0 <= dependency['symbols_count'] <= 65536
                    and type(dependency['missing_symbols_count']) is int
                    and dependency['missing_symbols_count'] == 0, 'Native imported symbol is unresolved')
            if dependency['resolution'] != 'bundled':
                require('/' not in target and target.lower().endswith('.dll'), 'Unsafe system dependency target')
    require('@executable' in paths and next(item for item in files if item['path'] == '@executable')['sha256']
            == executable_sha256, 'Outer executable is absent from inventory')
    for item in files:
        for dependency in item['imports']:
            if dependency['resolution'] == 'bundled':
                require(dependency['target'].casefold() in paths and dependency['target'] != '@executable',
                        'Bundled import points outside inventory')
    from scripts.native_security_policy import check_inventory_policy
    check_inventory_policy(files)


def scan_scope_digest(files):
    return inventory_digest(sorted([{'path': item['scan_path'], 'sha256': item['sha256']}
                                    for item in files], key=lambda item: item['path'].casefold()))


def check_archive_coverage(executable, inventory):
    """Rebind every PE member/hash to actual CArchive bytes, also on Linux."""
    from PyInstaller.archive.readers import CArchiveReader
    require(file_digest(executable) == inventory['executable_sha256'], 'Native coverage EXE hash mismatch')
    reader = CArchiveReader(str(executable))
    require(len(reader.toc) == inventory['archive_entries_count'] and len(reader.toc) <= MAX_ENTRIES,
            'Native archive entry coverage mismatch')
    expected = {'@executable': (file_digest(executable), Path(executable).stat().st_size)}
    seen, total = set(), 0
    for packed, entry in reader.toc.items():
        path = safe_path(packed.replace('\\', '/'))
        require(path.casefold() not in seen, 'Duplicate frozen archive member')
        seen.add(path.casefold())
        size = entry[2]
        require(type(size) is int and 0 <= size <= MAX_FILE_BYTES, 'Oversized frozen archive member')
        total += size
        require(total <= MAX_TOTAL_BYTES, 'Oversized decompressed frozen archive')
        data = reader.extract(packed)
        require(type(data) is bytes and len(data) == size, 'Incomplete frozen archive member')
        if data.startswith(b'MZ') or path.lower().endswith(NATIVE_SUFFIXES):
            require(data.startswith(b'MZ'), 'Native extension contains no PE payload')
            expected[path] = (hashlib.sha256(data).hexdigest(), size)
    actual = {item['path']: (item['sha256'], item['size_bytes']) for item in inventory['files']}
    require(len(actual) == len(inventory['files']) and actual == expected,
            'Native inventory does not cover actual complete PE payload')


def check_audit_report(report, inventory, *, executable_sha256, source_commit):
    check_inventory(inventory, executable_sha256=executable_sha256, source_commit=source_commit)
    keys = {'schema', 'probe', 'passed', 'version', 'platform', 'source_commit', 'source_run_id',
        'source_run_attempt', 'executable_sha256', 'native_files_count', 'archive_entries_count',
        'files_inventory_sha256', 'unmapped_count', 'unresolved_dependencies_count',
        'invalid_signatures_count', 'signature_files_checked', 'native_security_policy_passed',
        'native_security_policy_sha256', *SCANNER_KEYS}
    require(type(report) is dict and set(report) == keys, 'Invalid native audit summary fields')
    require(type(report['schema']) is int and report['schema'] == 1
            and report['probe'] == 'native_payload_audit' and report['passed'] is True,
            'Native audit did not pass')
    for key in ('version', 'platform', 'source_commit', 'source_run_id', 'source_run_attempt',
                'executable_sha256', 'native_files_count', 'archive_entries_count'):
        require(type(report[key]) is type(inventory[key]) and report[key] == inventory[key],
                'Native audit/inventory identity mismatch')
    require(report['files_inventory_sha256'] == inventory_digest(inventory)
            and report['scan_scope_files_sha256'] == scan_scope_digest(inventory['files']),
            'Native inventory or scan scope hash mismatch')
    require(report['native_security_policy_passed'] is True and report['native_security_policy_sha256']
            == file_digest(ROOT / 'scripts/native_security_policy.py'), 'Native advisory policy was not verified')
    for key in ('unmapped_count', 'unresolved_dependencies_count', 'invalid_signatures_count',
                'antivirus_detections_count'):
        require(type(report[key]) is int and report[key] == 0, 'Native audit contains a failure')
    for key in ('signature_files_checked', 'antivirus_scanned_files_count'):
        require(type(report[key]) is int and report[key] == inventory['native_files_count'],
                'Native security coverage is incomplete')
    require(report['antivirus_scan_completed'] is True and report['scanner'] == 'Microsoft Defender Antivirus'
            and type(report['real_time_protection_enabled']) is bool
            and type(report['sample_submission_consent']) is int and report['sample_submission_consent'] == 2
            and type(report['maps_reporting']) is int and report['maps_reporting'] == 0,
            'Required local-only Defender scan is absent')
    require(all(_short_text(report[key]) and bool(report[key]) for key in
                ('scanner_version', 'antivirus_signature_version', 'am_running_mode')), 'Incomplete scanner identity')
    start, end, signatures = (_utc(report[key]) for key in
                             ('scan_started_at', 'scan_finished_at', 'signature_updated_at'))
    require(start <= end and timedelta(0) <= start - signatures <= timedelta(hours=72)
            and end - start <= timedelta(minutes=20), 'Stale signatures or invalid scan interval')
    duration = report['scan_duration_seconds']
    require(type(duration) in (int, float) and 0 <= duration <= 1200
            and abs(duration - (end - start).total_seconds()) <= 5, 'Invalid native scan duration')


def _pe_metadata(data):
    import pefile
    require(len(data) <= MAX_FILE_BYTES, 'Native PE file is too large')
    pe = pefile.PE(data=data, fast_load=True)
    try:
        require(pe.FILE_HEADER.Machine == 0x8664 and pe.OPTIONAL_HEADER.Magic == 0x20B,
                'Native payload is not AMD64 PE32+')
        pe.parse_data_directories(directories=[1, 13, 0, 2])
        versions = {}
        for block in getattr(pe, 'FileInfo', []):
            for entry in block:
                for table in getattr(entry, 'StringTable', []):
                    for key, value in table.entries.items():
                        key = key.decode('utf-8', 'replace') if isinstance(key, bytes) else key
                        value = value.decode('utf-8', 'replace') if isinstance(value, bytes) else value
                        if key in ('FileVersion', 'ProductVersion'):
                            versions[key] = ''.join(char for char in value if ord(char) >= 32 and char not in '\\/')[:160]
        imports = []
        for attribute, kind in (('DIRECTORY_ENTRY_IMPORT', 'regular'), ('DIRECTORY_ENTRY_DELAY_IMPORT', 'delay')):
            for entry in getattr(pe, attribute, []):
                name = entry.dll.decode('ascii').lower()
                safe_path(name)
                require('/' not in name and name.endswith('.dll'), 'Import descriptor is not a DLL basename')
                symbols = [symbol.name.decode('ascii') if symbol.name is not None else int(symbol.ordinal)
                           for symbol in entry.imports]
                imports.append({'name': name, 'kind': kind, '_symbols': symbols})
        exports = getattr(pe, 'DIRECTORY_ENTRY_EXPORT', None)
        exported = set()
        if exports is not None:
            for symbol in exports.symbols:
                exported.add(int(symbol.ordinal))
                if symbol.name:
                    exported.add(symbol.name.decode('ascii'))
        return {'machine': 'AMD64', 'is_dll': bool(pe.FILE_HEADER.Characteristics & 0x2000),
                'file_version': versions.get('FileVersion', ''), 'product_version': versions.get('ProductVersion', ''),
                'imports': imports, '_exports': exported,
                '_code': [(section.Name.hex(), section.VirtualAddress, hashlib.sha256(section.get_data()).hexdigest())
                          for section in pe.sections if section.Characteristics & 0x20000000]}
    finally:
        pe.close()


def _record_sources(distributions=None):
    """Only RECORD-verified native bytes count as wheel provenance."""
    index, dependencies = {}, {}
    for distribution in distributions or importlib.metadata.distributions():
        package, version = distribution.metadata.get('Name'), distribution.version
        if not package:
            continue
        canonical = re.sub(r'[-_.]+', '-', package).lower()
        dependencies[canonical] = {re.sub(r'[-_.]+', '-', requirement.split()[0].split('(')[0].split(';')[0]
            .split('=')[0].split('<')[0].split('>')[0].split('!')[0].split('[')[0]).lower()
            for requirement in distribution.requires or []}
        for entry in distribution.files or []:
            relative = str(entry).replace('\\', '/')
            if not relative.lower().endswith(NATIVE_SUFFIXES) or entry.hash is None or entry.hash.mode != 'sha256':
                continue
            try:
                safe_path(relative)
            except ValueError:
                continue  # wheel's Scripts/../ entry is not a payload provenance path
            source = Path(distribution.locate_file(entry))
            expected = base64.urlsafe_b64decode(entry.hash.value + '===').hex()
            if not source.is_file() or file_digest(source) != expected:
                continue
            index.setdefault(expected, []).append({'kind': 'wheel_record', 'package': package,
                'version': version, 'source_path': relative, 'source_sha256': expected, 'match': 'sha256'})
    return index, dependencies


def _runtime_sources(index):
    for base in {Path(sys.base_prefix).resolve(), Path(sys.prefix).resolve()}:
        for directory in (base, base / 'DLLs'):
            if not directory.is_dir():
                continue
            for source in directory.iterdir():
                if source.is_file() and source.suffix.lower() in NATIVE_SUFFIXES:
                    digest = file_digest(source)
                    index.setdefault(digest, []).append({'kind': 'cpython_runtime', 'package': 'CPython',
                        'version': '.'.join(map(str, sys.version_info[:3])), 'source_path': source.relative_to(base).as_posix(),
                        'source_sha256': digest, 'match': 'sha256'})


def parse_api_set(data):
    """Read Windows API-set namespace v6 from System32/apisetschema.dll .apiset."""
    require(len(data) >= 28, 'Incomplete API-set namespace')
    version, size, _flags, count, offset, _hash_offset, _factor = struct.unpack_from('<7I', data)
    require(version == 6 and 28 <= size <= len(data) and count <= 65536, 'Unsupported API-set namespace')
    def string(start, length):
        require(length % 2 == 0 and 0 <= start <= start + length <= size, 'Invalid API-set string')
        return data[start:start + length].decode('utf-16-le').lower()
    require(offset + count * 24 <= size, 'Invalid API-set entries')
    result = {}
    for index in range(count):
        _, name_offset, name_size, _, values_offset, value_count = struct.unpack_from('<6I', data, offset + index * 24)
        require(value_count <= 1024 and values_offset + value_count * 20 <= size, 'Invalid API-set values')
        name = string(name_offset, name_size) + '.dll'
        safe_path(name)
        hosts = []
        for item in range(value_count):
            _, _alias_offset, _alias_size, target_offset, target_size = struct.unpack_from('<5I', data, values_offset + item * 20)
            if target_size:
                target = string(target_offset, target_size)
                safe_path(target)
                require('/' not in target and target.endswith('.dll'), 'Unsafe API-set host')
                hosts.append(target)
        result[name] = tuple(dict.fromkeys(hosts))
    return result


def _windows_loader():
    import winreg

    import pefile
    system = Path(os.environ['SystemRoot']) / 'System32'
    require(system.is_dir(), 'Windows System32 is unavailable')
    pe = pefile.PE(str(system / 'apisetschema.dll'), fast_load=True)
    try:
        sections = [section.get_data() for section in pe.sections if section.Name.rstrip(b'\0') == b'.apiset']
        require(len(sections) == 1, 'Windows API-set schema is unavailable')
        api_sets = parse_api_set(sections[0])
    finally:
        pe.close()
    known = set()
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SYSTEM\CurrentControlSet\Control\Session Manager\KnownDLLs') as key:
        for index in range(winreg.QueryInfoKey(key)[1]):
            _name, value, kind = winreg.EnumValue(key, index)
            if kind == winreg.REG_SZ and value.lower().endswith('.dll'):
                known.add(value.lower())
    return system, api_sets, known


def resolve_import(importer, dependency, files, *, system_metadata, api_sets, known_dlls, package_dependencies):
    """Resolve explicit directories/owner scopes; never use a global basename glob."""
    name, symbols = dependency['name'], dependency['_symbols']
    paths = {item['path'].casefold(): item for item in files if item['path'] != '@executable'}
    def outcome(kind, target, metadata):
        return {'name': name, 'kind': dependency['kind'], 'resolution': kind, 'target': target,
                'symbols_count': len(symbols), 'missing_symbols_count': sum(symbol not in metadata['_exports'] for symbol in symbols)}
    if name in api_sets:
        for host in api_sets[name]:
            metadata = system_metadata(host)
            if metadata is not None and all(symbol in metadata['_exports'] for symbol in symbols):
                return outcome('api_set', host, metadata)
        return outcome('unresolved', name, {'_exports': set()})
    if name in known_dlls:
        metadata = system_metadata(name)
        if metadata is not None:
            return outcome('system32', name, metadata)
    parent = PurePosixPath(importer['path']).parent
    candidates = [str(parent / name) if str(parent) != '.' else name, name]
    source = importer['provenance'] or {}
    package = re.sub(r'[-_.]+', '-', source.get('package', '')).lower()
    owners = {package} | package_dependencies.get(package, set())
    scoped = [item['path'] for item in files if item['path'] != '@executable'
              and PurePosixPath(item['path']).name.casefold() == name
              and item.get('provenance') is not None
              and re.sub(r'[-_.]+', '-', item['provenance']['package']).lower() in owners]
    # Package-owned native directories (e.g. numpy.libs, PySide6, shiboken6)
    # establish static availability; real feature-load gates remain separate.
    candidates.extend(sorted(scoped, key=str.casefold))
    for candidate in dict.fromkeys(candidates):
        if candidate.casefold() in paths:
            target = paths[candidate.casefold()]
            return outcome('bundled', target['path'], target)
    metadata = system_metadata(name)
    if metadata is not None:
        return outcome('system32', name, metadata)
    return outcome('unresolved', name, {'_exports': set()})


def _bootloader_provenance(executable_metadata, record_index):
    distribution = importlib.metadata.distribution('pyinstaller')
    for entry in distribution.files or []:
        if str(entry).replace('\\', '/').endswith('/Windows-64bit-intel/runw.exe'):
            source = Path(distribution.locate_file(entry))
            digest = file_digest(source)
            choices = record_index.get(digest, [])
            require(any(choice['source_path'] == str(entry).replace('\\', '/') for choice in choices),
                    'PyInstaller bootloader RECORD does not match installed bytes')
            metadata = _pe_metadata(source.read_bytes())
            require(bool(metadata['_code']) and metadata['_code'] == executable_metadata['_code'],
                    'Outer executable code differs from the verified PyInstaller bootloader')
            return {'kind': 'pyinstaller_bootloader_code', 'package': 'pyinstaller', 'version': distribution.version,
                    'source_path': str(entry).replace('\\', '/'), 'source_sha256': digest, 'match': 'executable_sections'}
    raise ValueError('Installed Windows GUI bootloader is unavailable')


def audit_static(executable, extract_dir, source_commit):
    require(sys.platform == 'win32', 'Actual native payload audit requires Windows build environment')
    require(type(source_commit) is str and COMMIT.fullmatch(source_commit), 'Invalid source commit')
    from PyInstaller.archive.readers import CArchiveReader

    from scripts.native_security_policy import check_native_component
    executable, extract_dir = Path(executable).resolve(), Path(extract_dir).resolve()
    require(executable.is_file() and not extract_dir.exists(), 'EXE absent or extraction directory already exists')
    require(executable.stat().st_size <= MAX_FILE_BYTES, 'Outer executable is too large')
    safe_path(executable.name)
    reader = CArchiveReader(str(executable))
    require(1 <= len(reader.toc) <= MAX_ENTRIES, 'Invalid frozen archive entry count')
    index, dependencies = _record_sources()
    _runtime_sources(index)
    helper_lock = json.loads((ROOT / 'native/handwriting/runtime/helper.lock.json').read_text(encoding='utf-8'))
    from scripts.build_handwriting_helper import source_identity
    require(helper_lock['source'] == source_identity(ROOT) and helper_lock['platform'] == 'win32',
            'Native helper source lock does not match repository')
    digest = helper_lock['sha256']
    index.setdefault(digest, []).append({'kind': 'native_helper_lock', 'package': 'inkpick-native-handwriting',
        'version': 'protocol-' + str(helper_lock['protocol']), 'source_path': 'native/handwriting/runtime/' + helper_lock['name'],
        'source_sha256': digest, 'match': 'sha256'})
    system, api_sets, known = _windows_loader()
    system_cache = {}
    def system_metadata(name):
        if name not in system_cache:
            path = system / name
            system_cache[name] = _pe_metadata(path.read_bytes()) if path.is_file() else None
        return system_cache[name]
    extract_dir.mkdir(parents=True)
    total, seen, files = 0, set(), []
    for packed, entry in reader.toc.items():
        path = safe_path(packed.replace('\\', '/'))
        require(path.casefold() not in seen, 'Duplicate frozen archive path')
        seen.add(path.casefold())
        size = entry[2]
        require(type(size) is int and 0 <= size <= MAX_FILE_BYTES, 'Oversized frozen archive member')
        total += size
        require(total <= MAX_TOTAL_BYTES, 'Oversized decompressed frozen archive')
        data = reader.extract(packed)
        require(type(data) is bytes and len(data) == size, 'Incomplete frozen archive extraction')
        native = data.startswith(b'MZ') or path.lower().endswith(NATIVE_SUFFIXES)
        if not native:
            continue
        metadata = _pe_metadata(data)
        digest = hashlib.sha256(data).hexdigest()
        choices = index.get(digest, [])
        source = next((choice for choice in choices if choice['source_path'].casefold() == path.casefold()),
                      min(choices, key=lambda choice: (choice['kind'], choice['package'], choice['source_path']))
                      if choices else None)
        if source is None and (system / PurePosixPath(path).name).is_file():
            installed = system / PurePosixPath(path).name
            if file_digest(installed) == digest:
                source = {'kind': 'windows_system_file', 'package': 'Microsoft-Windows',
                    'version': metadata['file_version'] or 'installed-os',
                    'source_path': 'System32/' + installed.name, 'source_sha256': digest, 'match': 'sha256'}
        scan_path = 'payload/' + path
        target = extract_dir / scan_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        component = check_native_component(path, data, distribution_name=(source or {}).get('package'),
            distribution_version=(source or {}).get('version'), pe_version=metadata['file_version'])
        files.append({'path': path, 'scan_path': scan_path, 'sha256': digest, 'size_bytes': len(data),
                      'component_version': component,
                      **metadata, 'provenance': source, 'signature_status': 'Pending'})
        require(len(files) < MAX_NATIVE_FILES, 'Too many native payload files')
    outer_metadata = _pe_metadata(executable.read_bytes())
    outer_path = 'candidate/' + executable.name
    (extract_dir / 'candidate').mkdir()
    shutil.copyfile(executable, extract_dir / outer_path)
    files.append({'path': '@executable', 'scan_path': outer_path, 'sha256': file_digest(executable),
        'size_bytes': executable.stat().st_size, **outer_metadata,
        'component_version': '',
        'provenance': _bootloader_provenance(outer_metadata, index), 'signature_status': 'Pending'})
    for item in files:
        item['imports'] = [resolve_import(item, dependency, files, system_metadata=system_metadata,
             api_sets=api_sets, known_dlls=known, package_dependencies=dependencies) for dependency in item['imports']]
    for item in files:
        item.pop('_exports')
        item.pop('_code')
    files.sort(key=lambda item: item['path'].casefold())
    inventory = {'schema': 1, 'probe': 'native_payload_inventory', 'version': APP_VERSION, 'platform': 'win32',
        'source_commit': source_commit, 'source_run_id': os.environ.get('GITHUB_RUN_ID'),
        'source_run_attempt': os.environ.get('GITHUB_RUN_ATTEMPT'), 'executable_sha256': file_digest(executable),
        'archive_entries_count': len(reader.toc), 'native_files_count': len(files), 'files': files}
    unmapped = sum(item['provenance'] is None for item in files)
    unresolved = sum(dependency['resolution'] == 'unresolved' or dependency['missing_symbols_count'] != 0
                     for item in files for dependency in item['imports'])
    static = {'schema': 1, 'probe': 'native_payload_static', 'passed': unmapped == unresolved == 0,
              'executable_sha256': inventory['executable_sha256'], 'unmapped_count': unmapped,
              'unresolved_dependencies_count': unresolved, 'native_files_count': len(files),
              'scan_relative_paths': [item['scan_path'] for item in files],
              'scan_scope_files_sha256': scan_scope_digest(files)}
    return static, inventory


def finalize(static, inventory, windows, executable):
    require(static.get('passed') is True and windows.get('platform') == 'win32', 'Static/native Windows audit failed')
    require(file_digest(executable) == inventory['executable_sha256'] == windows.get('executable_sha256')
            == static.get('executable_sha256'), 'EXE changed during native audit')
    signatures = windows.get('signatures')
    require(type(signatures) is list and len(signatures) == len(inventory['files']), 'Signature coverage is incomplete')
    by_path = {}
    for signature in signatures:
        require(type(signature) is dict and set(signature) == {'path', 'sha256', 'status'}, 'Invalid signature record')
        safe_path(signature['path'], outer=True)
        require(signature['path'] not in by_path, 'Duplicate native signature result')
        by_path[signature['path']] = signature
    require(set(by_path) == {item['path'] for item in inventory['files']}, 'Signature files differ from inventory')
    for item in inventory['files']:
        signature = by_path[item['path']]
        require(signature['sha256'] == item['sha256'], 'Native file changed during signature scan')
        item['signature_status'] = signature['status']
    report = {key: inventory[key] for key in ('version', 'platform', 'source_commit', 'source_run_id',
        'source_run_attempt', 'executable_sha256', 'native_files_count', 'archive_entries_count')}
    report.update(schema=1, probe='native_payload_audit', passed=True,
        native_security_policy_passed=True,
        native_security_policy_sha256=file_digest(ROOT / 'scripts/native_security_policy.py'),
        files_inventory_sha256=inventory_digest(inventory), unmapped_count=static['unmapped_count'],
        unresolved_dependencies_count=static['unresolved_dependencies_count'],
        invalid_signatures_count=sum(item['signature_status'] not in ('Valid', 'NotSigned') for item in inventory['files']),
        signature_files_checked=len(signatures), **{key: windows.get(key) for key in SCANNER_KEYS})
    check_audit_report(report, inventory, executable_sha256=file_digest(executable), source_commit=inventory['source_commit'])
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--executable', required=True)
    parser.add_argument('--inventory', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--extract-dir')
    parser.add_argument('--source-commit')
    parser.add_argument('--finalize', metavar='STATIC_JSON')
    parser.add_argument('--windows-report')
    args = parser.parse_args(argv)
    output, inventory_path = Path(args.output), Path(args.inventory)
    output.parent.mkdir(parents=True, exist_ok=True)
    inventory = None
    try:
        if args.finalize:
            inventory = json.loads(inventory_path.read_text(encoding='utf-8-sig'))
            report = finalize(json.loads(Path(args.finalize).read_text(encoding='utf-8-sig')), inventory,
                              json.loads(Path(args.windows_report).read_text(encoding='utf-8-sig')), args.executable)
        else:
            require(args.extract_dir and args.source_commit, 'Extraction directory/source commit required')
            report, inventory = audit_static(args.executable, args.extract_dir, args.source_commit)
        inventory_path.parent.mkdir(parents=True, exist_ok=True)
        inventory_path.write_text(json.dumps(inventory, sort_keys=True, indent=2) + '\n', encoding='utf-8')
        output.write_text(json.dumps(report, sort_keys=True, indent=2) + '\n', encoding='utf-8')
        return 0 if report['passed'] else 1
    except Exception:  # noqa: BLE001 - never publish native paths, scanner output, or runner directories
        if inventory is not None:
            inventory_path.parent.mkdir(parents=True, exist_ok=True)
            inventory_path.write_text(json.dumps(inventory, sort_keys=True, indent=2) + '\n', encoding='utf-8')
        output.write_text(json.dumps({'schema': 1, 'probe': 'native_payload_audit', 'passed': False,
                                      'error_code': 'NATIVE_PAYLOAD_AUDIT_FAILED'}) + '\n', encoding='utf-8')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
