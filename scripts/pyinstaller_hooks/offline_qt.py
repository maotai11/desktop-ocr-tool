"""Build-only Qt collectors with the offline application's shared TLS policy.

Each Qt module collector can discover another Qt library while walking its
native dependencies. Apply the exclusion at every used Qt hook, rather than
assuming all TLS artifacts pass through the QtNetwork hook alone.
"""
from __future__ import annotations

from pathlib import PurePath


def _tls_artifact(entry):
    if (not isinstance(entry, (tuple, list)) or len(entry) != 2
            or any(not isinstance(value, (str, PurePath)) or not str(value)
                   or '\0' in str(value) for value in entry)):
        raise ValueError('Qt collector returned an invalid source/destination pair')
    source, destination = (str(value).replace('\\', '/').casefold() for value in entry[:2])
    for value in (source, destination):
        name = value.rsplit('/', 1)[-1]
        if (name in ('libcrypto-3.dll', 'libssl-3.dll')
                or '/plugins/tls/' in '/' + value or value.endswith('/plugins/tls')
                or value == 'tls' or value.startswith('tls/')):
            return True
    return False


def collect_offline_qt(hook_file):
    # Resolve the pinned PyInstaller API in the build interpreter. Never search
    # PATH or invoke collect_qtnetwork_files(), which can promote foreign SSL.
    from PyInstaller.utils.hooks.qt import add_qt6_dependencies

    hiddenimports, binaries, datas = add_qt6_dependencies(hook_file)
    if not isinstance(binaries, (tuple, list)) or not isinstance(datas, (tuple, list)):
        raise TypeError('Qt collector returned invalid binary/data lists')
    return (hiddenimports,
            [entry for entry in binaries if not _tls_artifact(entry)],
            [entry for entry in datas if not _tls_artifact(entry)])
