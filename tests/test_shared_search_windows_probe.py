"""A native pixel claim is unavailable on non-native Qt platforms."""
import sys

import pytest

from src.shared_search_windows_probe import verify_overlay_capture


@pytest.mark.parametrize('platform,qt_platform', [('linux', 'windows'), ('win32', 'offscreen'),
                                                ('win32', 'minimal')])
def test_native_overlay_proof_does_not_claim_unsupported_platform(monkeypatch, platform, qt_platform):
    monkeypatch.setattr(sys, 'platform', platform)

    class App:
        def platformName(self):
            return qt_platform

    assert verify_overlay_capture(App()) is False


@pytest.mark.parametrize('parent', [True, 0, -1, '1; exit', 1.5, None])
def test_external_surface_rejects_non_owned_pid(parent):
    from src.shared_search_windows_probe import _surface_script
    with pytest.raises(ValueError, match='parent'):
        _surface_script(parent)


def test_external_surface_has_fixed_deadline_and_parent_liveness():
    from src.shared_search_windows_probe import _surface_script
    script = _surface_script(123)
    assert 'GetProcessById(123)' in script and 'PARENT_PID' not in script
    assert 'AddSeconds(45)' in script and '$form.Close()' in script
    assert '[System.Windows.Forms.Application]::Run($form)' in script
    assert '$timer.Dispose(); $form.Dispose()' in script


@pytest.mark.parametrize('already_exited,timeout', [(True, False), (False, False), (False, True)])
def test_owned_surface_is_reaped_on_normal_and_forced_shutdown(already_exited, timeout):
    import subprocess
    from src.shared_search_windows_probe import _reap_surface
    calls = []

    class Child:
        exited = already_exited
        waited = False
        def poll(self):
            return 0 if self.exited else None
        def terminate(self):
            calls.append('terminate')
        def kill(self):
            calls.append('kill')
        def wait(self, *, timeout):
            calls.append(('wait', timeout))
            if self.waited is False:
                self.waited = True
                if timeout_first:
                    raise subprocess.TimeoutExpired('owned-surface', timeout)
            self.exited = True
            return 0

    timeout_first = timeout
    _reap_surface(Child())
    expected = [] if already_exited else ['terminate']
    expected += [('wait', 5)]
    if timeout:
        expected += ['kill', ('wait', 5)]
    assert calls == expected


@pytest.mark.parametrize('failure', [None, 'not_owned', 'down', 'event_wait'])
def test_native_resume_click_only_targets_owned_window_and_always_releases(monkeypatch, failure):
    from types import SimpleNamespace
    from src import shared_search_windows_probe as native
    calls = []

    class Function:
        def __init__(self, action):
            self.action = action
        def __call__(self, *args):
            return self.action(*args)

    def rectangle(hwnd, pointer):
        target = pointer._obj
        target.left, target.top, target.right, target.bottom = 10, 20, 110, 60
        return True

    def cursor(pointer):
        pointer._obj.x, pointer._obj.y = 4, 5
        return True

    def mouse(flags, *args):
        calls.append(('mouse', flags))
        if flags == 0x0002 and failure == 'down':
            raise RuntimeError('synthetic native input failure')

    def settle(milliseconds):
        if failure == 'event_wait':
            raise RuntimeError('synthetic event failure')

    user32 = SimpleNamespace(
        GetWindowRect=Function(rectangle), GetCursorPos=Function(cursor),
        SetCursorPos=Function(lambda x, y: calls.append(('cursor', x, y)) or True),
        WindowFromPoint=Function(lambda point: 999 if failure == 'not_owned' else 321),
        mouse_event=Function(mouse))
    button = SimpleNamespace(winId=lambda: 321, isVisible=lambda: True)
    monkeypatch.setattr(native, '_wait', settle)
    if failure is None:
        native._click_owned_resume(user32, button)
    else:
        with pytest.raises((ValueError, RuntimeError)):
            native._click_owned_resume(user32, button)
    if failure == 'not_owned':
        assert calls == []
    else:
        assert calls == [('cursor', 60, 40), ('mouse', 0x0002), ('mouse', 0x0004), ('cursor', 4, 5)]



@pytest.mark.skipif(sys.platform != 'win32', reason='Windows inbox WinForms fixture')
def test_owned_surface_starts_without_module_paths_or_existing_profile(qapp, tmp_path):
    """Exercise the real fixture with the portable validator's empty environment."""
    import ctypes
    import os
    from pathlib import Path
    from unittest.mock import patch
    from src.shared_search_windows_probe import _owned_foreground_surface
    if qapp.platformName().lower() != 'windows':
        pytest.skip('Native Windows Qt platform required')
    root = os.environ['SystemRoot']
    profile = tmp_path / '全新使用者'
    roaming = profile / 'AppData' / 'Roaming'
    local = profile / 'AppData' / 'Local'
    temporary = local / 'Temp'
    for directory in (profile, roaming, local, temporary):
        directory.mkdir(parents=True, exist_ok=True)
    isolated = {
        'SystemRoot': root, 'WINDIR': root,
        'SystemDrive': Path(root).anchor.rstrip('\\'),
        'COMSPEC': str(Path(root) / 'System32' / 'cmd.exe'),
        'PATH': str(Path(root) / 'System32') + ';' + root,
        'PATHEXT': '.COM;.EXE;.BAT;.CMD',
        'USERPROFILE': str(profile), 'HOME': str(profile),
        'APPDATA': str(roaming), 'LOCALAPPDATA': str(local),
        'TEMP': str(temporary), 'TMP': str(temporary),
    }
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    with patch.dict(os.environ, isolated, clear=True):
        with _owned_foreground_surface(user32, (32, 32, 180, 100)) as hwnd:
            assert hwnd and user32.IsWindowVisible(hwnd)
