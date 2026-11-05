"""Reviewed native component floors; this is not a universal CVE database.

Reviewed 2026-11-05 against Qt's official advisory list and the OpenSSL release
advisories. File provenance, Windows imports and antivirus coverage are checked
separately by audit_native_payload. Static libraries inside other components
are not silently represented as having completed a component-level CVE audit.
"""
from __future__ import annotations

import re
from pathlib import PurePosixPath

QT_VERSION = '6.11.3'
OPENSSL_FLOORS = {(3, 0): (3, 0, 23), (3, 4): (3, 4, 8),
                  (3, 5): (3, 5, 9), (3, 6): (3, 6, 5), (4, 0): (4, 0, 3)}
ADVISORY_SOURCES = (
    'https://wiki.qt.io/List_of_known_vulnerabilities_in_Qt_products',
    'https://www.qt.io/blog/security-advisory-cve-2026-107157',
    'https://www.qt.io/blog/security-advisory-cve-2026-95620',
    'https://www.openssl-library.org/news/vulnerabilities-3.5/',
)


def _triplet(value):
    if not isinstance(value, str):
        return None
    match = re.match(r'^(\d+)[.,](\d+)[.,](\d+)(?:[.,]0)?(?:\D|$)', value)
    return tuple(map(int, match.groups())) if match else None


def _identity(path, package, package_version, pe_version, component_version):
    if not isinstance(path, str) or '\\' in path:
        raise ValueError('Native policy requires normalized relative paths')
    parts = PurePosixPath(path).parts
    if not parts or '..' in parts or path.startswith('/'):
        raise ValueError('Native policy path is invalid')
    folded = path.casefold()
    name = parts[-1].casefold()
    if ('/plugins/tls/' in '/' + folded or folded.endswith('/plugins/tls')
            or name in ('libcrypto-3.dll', 'libssl-3.dll')):
        raise ValueError('Unused Qt TLS or legacy OpenSSL entered the payload')
    owner = re.sub(r'[-_.]+', '-', package or '').casefold()
    qt_runtime = name.startswith('qt6') and name.endswith('.dll')
    qt_binding = owner in ('pyside6-essentials', 'shiboken6')
    if qt_runtime or qt_binding:
        if not qt_binding or package_version != QT_VERSION or component_version != QT_VERSION:
            raise ValueError('Qt provenance does not meet the reviewed security baseline')
        if qt_runtime and _triplet(pe_version) != (6, 11, 3):
            raise ValueError('Qt DLL version differs from the reviewed wheel')
        return
    if name.startswith(('libcrypto-', 'libssl-')) and name.endswith('.dll'):
        version = _triplet(component_version)
        if version is None or version[:2] not in OPENSSL_FLOORS or version < OPENSSL_FLOORS[version[:2]]:
            raise ValueError('OpenSSL version has no acceptable reviewed security floor')
        if pe_version and _triplet(pe_version) != version:
            raise ValueError('OpenSSL resource and component versions differ')
    elif component_version:
        raise ValueError('Unexpected native security component identity')


def check_native_component(path, data, *, distribution_name=None, distribution_version=None, pe_version=None):
    """Validate actual bytes and return a bounded component version for inventory."""
    if not isinstance(data, bytes):
        raise ValueError('Native policy requires actual binary bytes')  # noqa: TRY004 - reject audit evidence uniformly
    name = PurePosixPath(path.replace('\\', '/')).name.casefold() if isinstance(path, str) else ''
    owner = re.sub(r'[-_.]+', '-', distribution_name or '').casefold()
    if owner in ('pyside6-essentials', 'shiboken6') or name.startswith('qt6'):
        component = distribution_version or ''
    elif name.startswith(('libcrypto-', 'libssl-')) and name.endswith('.dll'):
        markers = {match.decode('ascii') for match in re.findall(rb'\bOpenSSL (\d+\.\d+\.\d+)\b', data)}
        if len(markers) > 1:
            raise ValueError('Multiple OpenSSL identities in one native file')
        component = next(iter(markers), None)
        if component is None:
            version = _triplet(pe_version)
            if version is None:
                raise ValueError('OpenSSL binary identity is missing')
            component = '.'.join(map(str, version))
    else:
        component = ''
    _identity(path, distribution_name, distribution_version, pe_version, component)
    return component


def check_inventory_policy(files):
    """Revalidate the identities previously measured from the actual artifact."""
    if not isinstance(files, list) or not files:
        raise ValueError('Native security inventory is empty')
    for item in files:
        source = item.get('provenance') if isinstance(item, dict) else None
        if not isinstance(source, dict) or not isinstance(item.get('component_version'), str):
            raise ValueError('Native security component evidence is missing')  # noqa: TRY004 - reject audit evidence uniformly
        _identity(item['path'], source.get('package'), source.get('version'),
                  item.get('file_version'), item['component_version'])
