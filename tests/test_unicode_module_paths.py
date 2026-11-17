"""Mapped module identity never depends on Toolhelp's lossy path field."""
import ctypes
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.core import resource_budget as budget


class Function:
    def __init__(self, call):
        self.call = call
    def __call__(self, *args):
        return self.call(*args)


@pytest.fixture
def api(monkeypatch):
    state = SimpleNamespace(closed=[], opens=[], mapped='\\Device\\HarddiskVolume3\\中文😀\\_MEI1\\a.dll',
                            final='\\\\?\\C:\\中文😀\\_MEI1\\a.dll', failure=None)
    class UnicodeString(ctypes.Structure):
        _fields_ = [('length', ctypes.c_ushort), ('maximum_length', ctypes.c_ushort),
                    ('buffer', ctypes.c_void_p)]
    def mapped(process, base, information_class, output, capacity, returned):
        assert information_class == 2
        if state.failure == 'mapped': return -1073741823
        if state.failure == 'mapped_truncated':
            returned._obj.value = capacity + 1
            return 0
        value = state.mapped.encode('utf-16-le') + b'\0\0'
        start = ctypes.addressof(output) + ctypes.sizeof(UnicodeString)
        ctypes.memmove(start, value, len(value))
        name = UnicodeString.from_buffer(output)
        name.length = len(value) - 2
        name.maximum_length = len(value)
        name.buffer = start
        returned._obj.value = ctypes.sizeof(UnicodeString) + len(value)
        if state.failure == 'mapped_odd': name.length -= 1
        if state.failure == 'mapped_empty': name.length = 0
        if state.failure == 'mapped_bad_maximum': name.maximum_length = name.length - 2
        if state.failure == 'mapped_before': name.buffer = ctypes.addressof(output)
        if state.failure == 'mapped_after': name.buffer = ctypes.addressof(output) + capacity
        if state.failure == 'mapped_null': name.buffer = None
        if state.failure == 'mapped_bad_utf16':
            ctypes.memmove(start, b'\x00\xd8', 2); name.length = 2
        return 0
    def final(handle, output, capacity, flags):
        if state.failure == 'final': return 0
        if state.failure == 'final_truncated': return capacity + 1
        output.value = state.final
        return len(state.final.encode('utf-16-le')) // 2
    def create(path, access, share, security, creation, flags, template):
        state.opens.append((path, access, share, creation))
        return ctypes.c_void_p(-1).value if state.failure == 'file' else 22
    kernel = SimpleNamespace(
        OpenProcess=Function(lambda *args: 0 if state.failure == 'process' else 11),
        CreateFileW=Function(create),
        GetFinalPathNameByHandleW=Function(final),
        CloseHandle=Function(lambda handle: state.closed.append(handle) or 1))
    native = SimpleNamespace(NtQueryVirtualMemory=Function(mapped))
    monkeypatch.setattr(ctypes, 'WinDLL', lambda name, **k: native if name == 'ntdll' else kernel, raising=False)
    monkeypatch.setattr(ctypes, 'get_last_error', lambda: 5, raising=False)
    state.native = native
    return state, kernel


def test_mapping_uses_unicode_device_handle_and_actual_utf16_counts(api):
    state, kernel = api
    assert budget._windows_module_path(9, 1234) == state.final
    assert state.opens == [('\\\\?\\GLOBALROOT' + state.mapped, 0, 7, 3)]
    assert state.closed == [22, 11]
    assert state.native.NtQueryVirtualMemory.restype is ctypes.c_int32


def test_failed_section_query_preserves_ntstatus_namespace(api):
    state, _ = api
    state.failure = 'mapped'
    with pytest.raises(budget.ResourceInspectionError) as caught:
        budget._windows_module_path(9, 1234)
    assert caught.value.ntstatus == 0xC0000001
    assert caught.value.native_error is None
    assert state.closed == [11]


def test_section_name_retains_long_unicode_path_without_fixed_max_path_truncation(api):
    state, _ = api
    suffix = ('中文😀' * 80) + r'\_MEI-long\a.dll'
    state.mapped = '\\Device\\HarddiskVolume3\\' + suffix
    state.final = '\\\\?\\C:\\' + suffix
    assert len(state.mapped.encode('utf-16-le')) // 2 > 260
    assert budget._windows_module_path(9, 1234) == state.final
    assert state.closed == [22, 11]


@pytest.mark.parametrize('failure,closed', [('process', []), ('mapped', [11]),
    ('mapped_truncated', [11]), ('mapped_odd', [11]), ('mapped_empty', [11]),
    ('mapped_bad_maximum', [11]), ('mapped_before', [11]), ('mapped_after', [11]),
    ('mapped_null', [11]), ('mapped_bad_utf16', [11]), ('file', [11]), ('final', [22, 11]), ('final_truncated', [22, 11])])
def test_every_api_failure_rejects_and_releases_acquired_handles(api, failure, closed):
    state, _ = api; state.failure = failure
    with pytest.raises(budget.ResourceInspectionError):
        budget._windows_module_path(9, 1234)
    assert state.closed == closed


@pytest.mark.parametrize('field,value', [('mapped', 'C:\\not-device.dll'),
    ('mapped', '\\Device\\x\x00hidden'), ('final', 'C:\\missing-namespace.dll'),
    ('final', '\\\\?\\C:\\x\x00hidden')])
def test_malformed_or_embedded_null_results_never_become_roots(api, field, value):
    state, _ = api; setattr(state, field, value)
    with pytest.raises(budget.ResourceInspectionError):
        budget._windows_module_path(9, 1234)
    assert state.closed[-1] == 11


@pytest.mark.parametrize('gone', [False, True])
def test_only_fresh_confirmed_process_disappearance_can_retire_mapping_error(tmp_path, monkeypatch, gone):
    calls, closed = [], []
    def first(snapshot, pointer):
        pointer._obj.base = 1234
        pointer._obj.path = 'C:\\LOSSY????\\_MEI_WRONG\\a.dll'
        return 1
    kernel = SimpleNamespace(CreateToolhelp32Snapshot=Function(lambda *args: 33),
        Module32FirstW=Function(first), Module32NextW=Function(lambda *args: 0),
        CloseHandle=Function(lambda handle: closed.append(handle) or 1))
    monkeypatch.setattr(ctypes, 'WinDLL', lambda *a, **k: kernel, raising=False)
    monkeypatch.setattr(ctypes, 'get_last_error', lambda: 18, raising=False)
    monkeypatch.setattr(budget, 'sys', SimpleNamespace(frozen=True, platform='win32', _MEIPASS=str(tmp_path)))
    monkeypatch.setattr(budget, 'current_process_root', lambda: 9)
    def processes():
        calls.append(True)
        return {} if gone and len(calls) > 1 else {9: 1}
    monkeypatch.setattr(budget, '_windows_processes', processes)
    def fail(*args): raise budget.ResourceInspectionError(native_error=5)
    monkeypatch.setattr(budget, '_windows_module_path', fail)
    if gone:
        assert set(budget.active_extraction_roots()) == {tmp_path.resolve()}
    else:
        with pytest.raises(budget.ResourceInspectionError): budget.active_extraction_roots()
    assert len(calls) == 2 and closed == [33]


def test_all_owned_child_physical_mei_roots_retained_despite_lossy_toolhelp(tmp_path, monkeypatch):
    own = tmp_path/'_MEI-own'; own.mkdir()
    other = tmp_path/'中文'/'_MEI-child'; other.mkdir(parents=True)
    closed, queried = [], []
    kernel = SimpleNamespace(CreateToolhelp32Snapshot=Function(lambda flags, pid: pid+100),
        Module32FirstW=Function(lambda handle, pointer: setattr(pointer._obj, 'base', handle) or
                              setattr(pointer._obj, 'path', 'C:\\????\\_MEI-LOSSY\\a.dll') or 1),
        Module32NextW=Function(lambda *args: 0), CloseHandle=Function(lambda handle: closed.append(handle) or 1))
    monkeypatch.setattr(ctypes, 'WinDLL', lambda *a, **k: kernel, raising=False)
    monkeypatch.setattr(ctypes, 'get_last_error', lambda: 18, raising=False)
    monkeypatch.setattr(budget, 'sys', SimpleNamespace(frozen=True, platform='win32', _MEIPASS=str(own)))
    monkeypatch.setattr(budget, 'current_process_root', lambda: 9)
    monkeypatch.setattr(budget, '_windows_processes', lambda: {9:1, 10:9, 99:1})
    def physical(pid, base):
        queried.append((pid, base))
        return str((own if pid == 9 else other)/'a.dll')
    monkeypatch.setattr(budget, '_windows_module_path', physical)
    assert set(budget.active_extraction_roots()) == {own.resolve(), other.resolve()}
    assert set(queried) == {(9,109),(10,110)} and set(closed) == {109,110}
