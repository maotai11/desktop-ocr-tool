"""Do not let package-only audit results conceal known native version gaps."""
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.native_security_policy import (
    check_inventory_policy,
    check_native_component,
)


@pytest.mark.parametrize('path', ['libcrypto-3.dll', 'libssl-3.dll',
    'PySide6/plugins/tls/qopensslbackend.dll', 'PySide6/plugins/tls/qschannelbackend.dll'])
def test_legacy_or_unused_tls_is_rejected_even_with_new_version_marker(path):
    with pytest.raises(ValueError):
        check_native_component(path, b'OpenSSL 3.5.9', distribution_name='CPython',
                               distribution_version='3.12.14', pe_version='3.5.9.0')


@pytest.mark.parametrize(('wheel_version', 'binary_version'), [('6.11.2', '6.11.2.0'),
    ('6.11.3', '6.11.2.0'), ('6.12.0', '6.12.0.0'), ('6.11.3', '')])
def test_qt_requires_both_patched_wheel_and_matching_actual_dll_version(wheel_version, binary_version):
    with pytest.raises(ValueError):
        check_native_component('PySide6/Qt6Core.dll', b'PE', distribution_name='PySide6_Essentials',
                               distribution_version=wheel_version, pe_version=binary_version)


def test_patched_qt_and_cpython_openssl_have_explicit_component_evidence():
    version = check_native_component('PySide6/Qt6Network.dll', b'PE',
        distribution_name='PySide6_Essentials', distribution_version='6.11.3', pe_version='6.11.3.0')
    assert version == '6.11.3'
    assert check_native_component('libcrypto-3-x64.dll', b'OpenSSL 3.5.9 29 Sep 2026',
        distribution_name='CPython', distribution_version='3.12.14', pe_version='3.5.9.0') == '3.5.9'


@pytest.mark.parametrize('version', ['3.5.8', '3.0.22', '3.3.7', '9.0.0'])
def test_old_or_unreviewed_openssl_series_is_not_silently_approved(version):
    with pytest.raises(ValueError):
        check_native_component('libcrypto-3-x64.dll', ('OpenSSL ' + version).encode())


def test_public_inventory_missing_or_changed_policy_evidence_is_rejected():
    row = {'path': 'PySide6/Qt6Core.dll', 'file_version': '6.11.3.0', 'component_version': '6.11.3',
           'provenance': {'package': 'PySide6_Essentials', 'version': '6.11.3'}}
    check_inventory_policy([row])
    row['component_version'] = '6.11.2'
    with pytest.raises(ValueError):
        check_inventory_policy([row])
    del row['component_version']
    with pytest.raises(ValueError):
        check_inventory_policy([row])


def test_qtnetwork_hook_does_not_promote_unrelated_path_openssl(monkeypatch):
    entries = [('C:/Qt/Qt6Network.dll', 'PySide6'),
               ('C:/Qt/plugins/tls/qopensslbackend.dll', 'PySide6/plugins/tls'),
               ('C:/Git/bin/libcrypto-3.dll', '.'),
               ('C:/Python/DLLs/libcrypto-3-x64.dll', '.')]
    fake = SimpleNamespace(add_qt6_dependencies=lambda _: (['PySide6.QtCore'], entries, []))
    monkeypatch.setitem(sys.modules, 'PyInstaller.utils.hooks.qt', fake)
    hook = Path(__file__).resolve().parents[1] / 'scripts/pyinstaller_hooks/hook-PySide6.QtNetwork.py'
    result = runpy.run_path(str(hook))
    assert result['hiddenimports'] == ['PySide6.QtCore']
    assert result['binaries'] == [entries[0], entries[3]]
