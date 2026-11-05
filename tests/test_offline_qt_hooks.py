"""All Qt collectors retain dependencies while excluding unused TLS inputs."""
from __future__ import annotations

import runpy
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ('QtCore', 'QtGui', 'QtWidgets', 'QtNetwork', 'QtSvg')


@pytest.mark.parametrize('module', HOOKS)
def test_every_used_qt_hook_filters_transitively_discovered_tls(module, monkeypatch):
    kept_binaries = [
        ('C:/Qt/Qt6Network.dll', 'PySide6'),
        ('C:/Qt/Qt6Core.dll', 'PySide6'),
        ('C:/Qt/Qt6Gui.dll', 'PySide6'),
        ('C:/Qt/plugins/platforms/qwindows.dll', 'PySide6/plugins/platforms'),
        ('C:/Qt/plugins/imageformats/qsvg.dll', 'PySide6/plugins/imageformats'),
        ('C:/Qt/plugins/networkinformation/qnetworklistmanager.dll', 'PySide6/plugins/networkinformation'),
        ('C:/Python/DLLs/libcrypto-3-x64.dll', '.'),
        ('C:/Python/DLLs/libssl-3-x64.dll', '.'),
        ('C:/Qt/MSVCP140.dll', 'PySide6'),
    ]
    removed_binaries = [
        ('C:/Qt/plugins/tls/qopensslbackend.dll', 'PySide6/plugins/tls'),
        ('C:\\Qt\\plugins\\TLS\\qschannelbackend.dll', 'PySide6\\plugins\\TLS'),
        ('C:/Qt/plugins/tls/qcertonlybackend.dll', '.'),
        ('C:/separate/qcertonlybackend.dll', 'PySide6/plugins/tls'),
        ('C:/Git/bin/libcrypto-3.dll', '.'),
        ('C:/Git/bin/libssl-3.dll', '.'),
        ('C:/separate/backend.dll', 'tls'),
        ('tls/qopensslbackend.dll', '.'),
    ]
    kept_data = [('C:/Qt/translations/qtbase_zh_TW.qm', 'PySide6/translations')]
    removed_data = [('C:/Qt/plugins/tls/config.json', 'PySide6/plugins/tls')]
    imports = ['PySide6.QtCore', 'PySide6.QtNetwork', 'shiboken6']
    calls = []
    def dependencies(hook_file):
        calls.append(Path(hook_file).name)
        return imports, kept_binaries + removed_binaries, kept_data + removed_data
    def forbidden_ssl_collector():
        pytest.fail('Offline Qt hooks must not search the build machine for extra SSL DLLs')
    fake = SimpleNamespace(add_qt6_dependencies=dependencies,
                           pyside6_library_info=SimpleNamespace(collect_qtnetwork_files=forbidden_ssl_collector))
    monkeypatch.setitem(sys.modules, 'PyInstaller.utils.hooks.qt', fake)
    result = runpy.run_path(str(ROOT / f'scripts/pyinstaller_hooks/hook-PySide6.{module}.py'))
    assert calls == [f'hook-PySide6.{module}.py']
    assert result['hiddenimports'] is imports
    assert result['binaries'] == kept_binaries
    assert result['datas'] == kept_data


def test_shared_helper_is_importable_from_explicit_build_root(tmp_path):
    """PyInstaller loads hooks as files; helper resolution cannot rely on CWD."""
    import os
    import subprocess

    source = (
        'import runpy,sys; from types import SimpleNamespace; '
        'sys.path.insert(0,sys.argv[1]); '
        "sys.modules['PyInstaller.utils.hooks.qt']=SimpleNamespace(add_qt6_dependencies=lambda _: ([],[],[])); "
        'runpy.run_path(sys.argv[2])'
    )
    env = dict(os.environ)
    env.pop('PYTHONPATH', None)
    for module in HOOKS:
        result = subprocess.run([sys.executable, '-I', '-c', source, str(ROOT),
            str(ROOT / f'scripts/pyinstaller_hooks/hook-PySide6.{module}.py')], cwd=tmp_path,
            env=env, capture_output=True, text=True, timeout=15, check=False)
        assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('entry', [None, 'not-a-pair', ('only-source',), ('source', '.', 'extra'),
                                  (None, '.'), ('source', 42), ('', '.'), ('source\0.dll', '.')])
def test_invalid_collector_entries_fail_instead_of_being_silently_ignored(entry):
    from scripts.pyinstaller_hooks.offline_qt import _tls_artifact
    with pytest.raises(ValueError, match='invalid'):
        _tls_artifact(entry)
