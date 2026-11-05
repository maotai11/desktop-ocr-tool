"""Bounded synthetic metadata; no inspected DLL is ever loaded or executed."""
import ctypes as C
import os
from pathlib import Path
import shutil
import struct
import threading
from types import SimpleNamespace

import pytest

from scripts import windows_sxs as sxs


ASSEMBLY = 'amd64_microsoft.windows.common-controls_6595b64144ccf1df_6.0.26100.33648_none_ee38cd5ddaee2cd9'


def pe_image(*, dll=False, machine=0x8664):
    data = bytearray(1024)
    data[:2] = b'MZ'
    struct.pack_into('<I', data, 60, 0x80)
    data[0x80:0x84] = b'PE\0\0'
    struct.pack_into('<HHIIIHH', data, 0x84, machine, 1, 0, 0, 0, 240, 0x2022 if dll else 0x22)
    struct.pack_into('<H', data, 0x98, 0x20b)
    return data


class FakeApi:
    def __init__(self, root):
        self.root = root
        self.calls = []
        self.directory = ASSEMBLY
        self.redirected = True
        self.failure = None

    def _call(self, name):
        self.calls.append(name)
        if self.failure == name:
            raise sxs.WindowsSxsError('Synthetic API failure')

    def windows_root(self):
        return self.root

    def create(self, executable):
        self._call('create')
        self.executable = executable
        return 11

    def activate(self, handle):
        assert handle == 11
        self._call('activate')
        return 22

    def deactivate(self, cookie):
        assert cookie == 22
        self._call('deactivate')

    def release(self, handle):
        assert handle == 11
        self._call('release')

    def find(self, handle, name):
        assert handle == 11
        self._call('find')
        self.name = name
        return 3 if self.redirected else None

    def assembly_directory(self, handle, index):
        assert handle == 11 and index == 3
        self._call('query')
        return self.directory


@pytest.fixture
def environment(tmp_path, monkeypatch):
    root = tmp_path / 'Windows'
    assembly = root / 'WinSxS' / ASSEMBLY
    assembly.mkdir(parents=True)
    dll = assembly / 'comctl32.dll'
    dll.write_bytes(pe_image(dll=True))
    executable = tmp_path / 'candidate.exe'
    executable.write_bytes(pe_image())
    api = FakeApi(root)
    monkeypatch.setattr(sxs, '_require_platform', lambda: None)
    monkeypatch.setattr(sxs, '_WindowsApi', lambda: api)
    return SimpleNamespace(root=root, assembly=assembly, dll=dll, exe=executable, api=api)


def test_exact_os_selected_assembly_and_thread_lifetime(environment):
    env = environment
    # A newer-looking decoy must never be enumerated or chosen.
    decoy = env.root / 'WinSxS' / ASSEMBLY.replace('6.0.26100.33648', '99.0.0.0')
    decoy.mkdir()
    (decoy / 'comctl32.dll').write_bytes(pe_image(dll=True, machine=0x14c))
    with sxs.candidate_activation_context(env.exe) as context:
        assert context.windows_root == env.root
        assert context.resolve_dll('COMCTL32.DLL') == env.dll
        assert env.api.name == 'comctl32.dll'
    assert env.api.calls == ['create', 'activate', 'find', 'query', 'deactivate', 'release']
    with pytest.raises(sxs.WindowsSxsError, match='inactive'):
        context.resolve_dll('comctl32.dll')


def test_only_absent_redirection_allows_fallback(environment):
    environment.api.redirected = False
    with sxs.candidate_activation_context(environment.exe) as context:
        assert context.resolve_dll('kernel32.dll') is None
    assert 'query' not in environment.api.calls


@pytest.mark.parametrize('name', ['../x.dll', 'x/y.dll', r'x\y.dll', 'C:x.dll', 'x.dll:stream',
                                  'x.dll ', 'NUL.dll', 'COM1.drv', 'x.exe', 'x.sys', '', None,
                                  'x.dll\n', 'a' * 129 + '.dll'])
def test_unsafe_library_names_are_rejected_before_windows_lookup(environment, name):
    with sxs.candidate_activation_context(environment.exe) as context:
        with pytest.raises(sxs.WindowsSxsError, match='library name'):
            context.resolve_dll(name)
    assert 'find' not in environment.api.calls


@pytest.mark.parametrize('directory', ['../outside', 'C:/outside', 'arm64_' + ASSEMBLY[6:],
                                       ASSEMBLY + '/other', ASSEMBLY + '\0', ASSEMBLY + '..',
                                       'amd64_unknown', None])
def test_non_amd64_or_malformed_assembly_identity_never_falls_back(environment, directory):
    environment.api.directory = directory
    with sxs.candidate_activation_context(environment.exe) as context:
        with pytest.raises(sxs.WindowsSxsError, match='assembly directory'):
            context.resolve_dll('comctl32.dll')


@pytest.mark.parametrize('image', [pe_image(dll=True, machine=0x14c), pe_image(dll=False), b'not a PE'])
def test_redirected_component_must_be_an_actual_amd64_dll(environment, image):
    environment.dll.write_bytes(image)
    with sxs.candidate_activation_context(environment.exe) as context:
        with pytest.raises(sxs.WindowsSxsError):
            context.resolve_dll('comctl32.dll')


def test_redirected_component_missing_is_not_a_system32_fallback(environment):
    environment.dll.unlink()
    with sxs.candidate_activation_context(environment.exe) as context:
        with pytest.raises(sxs.WindowsSxsError, match='Missing selected'):
            context.resolve_dll('comctl32.dll')


def test_drv_is_allowed_only_when_actual_os_selected_component_is_a_dll(environment):
    target = environment.assembly / 'sample.drv'
    target.write_bytes(pe_image(dll=True))
    with sxs.candidate_activation_context(environment.exe) as context:
        assert context.resolve_dll('sample.drv') == target


@pytest.mark.parametrize('scope', ['library', 'assembly', 'winsxs'])
def test_reparse_style_escape_is_rejected(environment, tmp_path, scope):
    env = environment
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'comctl32.dll').write_bytes(pe_image(dll=True))
    try:
        if scope == 'library':
            env.dll.unlink()
            env.dll.symlink_to(outside / 'comctl32.dll')
        elif scope == 'assembly':
            env.dll.unlink()
            env.assembly.rmdir()
            env.assembly.symlink_to(outside, target_is_directory=True)
        else:
            env.dll.unlink()
            env.assembly.rmdir()
            (env.root / 'WinSxS').rmdir()
            (env.root / 'WinSxS').symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip('This test host cannot create synthetic symlinks')
    with sxs.candidate_activation_context(env.exe) as context:
        with pytest.raises(sxs.WindowsSxsError, match='escapes|outside'):
            context.resolve_dll('comctl32.dll')


@pytest.mark.parametrize('failure,expected', [
    ('create', ['create']),
    ('activate', ['create', 'activate', 'release']),
    ('find', ['create', 'activate', 'find', 'deactivate', 'release']),
    ('query', ['create', 'activate', 'find', 'query', 'deactivate', 'release']),
    ('deactivate', ['create', 'activate', 'find', 'query', 'deactivate', 'release']),
])
def test_fail_closed_and_handle_cleanup_at_every_stage(environment, failure, expected):
    environment.api.failure = failure
    with pytest.raises(sxs.WindowsSxsError, match='Synthetic API failure'):
        with sxs.candidate_activation_context(environment.exe) as context:
            context.resolve_dll('comctl32.dll')
    assert environment.api.calls == expected


def test_body_failure_still_deactivates_and_releases(environment):
    with pytest.raises(RuntimeError, match='synthetic caller'):
        with sxs.candidate_activation_context(environment.exe):
            raise RuntimeError('synthetic caller')
    assert environment.api.calls == ['create', 'activate', 'deactivate', 'release']


def test_cross_thread_resolution_is_refused_before_api_lookup(environment):
    errors = []
    with sxs.candidate_activation_context(environment.exe) as context:
        def other_thread():
            try:
                context.resolve_dll('comctl32.dll')
            except sxs.WindowsSxsError as error:
                errors.append(str(error))
        thread = threading.Thread(target=other_thread)
        thread.start()
        thread.join(timeout=3)
        assert not thread.is_alive()
    assert len(errors) == 1 and 'another thread' in errors[0]
    assert 'find' not in environment.api.calls


def test_candidate_is_canonicalized_and_bad_pe_never_reaches_windows(environment):
    env = environment
    child = env.exe.parent / 'child'
    child.mkdir()
    with sxs.candidate_activation_context(child / '..' / env.exe.name):
        assert env.api.executable == env.exe
    env.api.calls.clear()
    env.exe.write_bytes(pe_image(dll=True))
    with pytest.raises(sxs.WindowsSxsError, match='image kind'):
        with sxs.candidate_activation_context(env.exe):
            pass
    assert not env.api.calls


def test_missing_candidate_error_never_contains_private_path(tmp_path):
    with pytest.raises(sxs.WindowsSxsError) as error:
        sxs._canonical(tmp_path / 'private-user-directory' / 'absent.exe', 'Invalid candidate executable')
    assert str(error.value) == 'Invalid candidate executable'


def test_real_api_create_uses_only_embedded_resource_one():
    calls = []
    def create(pointer):
        context = C.cast(pointer, C.POINTER(sxs._ACTCTX)).contents
        calls.append((context.cbSize, context.dwFlags, context.lpSource, context.lpResourceName,
                      context.lpAssemblyDirectory, context.hModule))
        return 11
    api = sxs._WindowsApi.__new__(sxs._WindowsApi)
    api.api = SimpleNamespace(CreateActCtxW=create)
    assert api.create(Path('synthetic-candidate.exe')) == 11
    assert calls == [(C.sizeof(sxs._ACTCTX), 0x8, 'synthetic-candidate.exe', 1, None, None)]


@pytest.mark.parametrize('code', [0, 2, 5, 87, 14000, 14007, 14001])
def test_only_documented_missing_key_can_be_none(monkeypatch, code):
    monkeypatch.setattr(sxs.C, 'get_last_error', lambda: code, raising=False)
    api = sxs._WindowsApi.__new__(sxs._WindowsApi)
    api.api = SimpleNamespace(FindActCtxSectionStringW=lambda *args: False)
    if code == 14007:
        assert api.find(11, 'kernel32.dll') is None
    else:
        with pytest.raises(sxs.WindowsSxsError, match='redirection'):
            api.find(11, 'kernel32.dll')


@pytest.mark.parametrize('returned,roster', [(11, 3), (99, 3), (11, 0), (None, 3)])
def test_lookup_roster_is_bound_to_candidate_and_extra_reference_is_released(returned, roster):
    released = []
    def find(flags, extension, section_id, name, pointer):
        assert (flags, extension, section_id, name) == (1, None, 2, 'comctl32.dll')
        section = C.cast(pointer, C.POINTER(sxs._SECTION_DATA)).contents
        section.hActCtx = returned
        section.ulAssemblyRosterIndex = roster
        return True
    api = sxs._WindowsApi.__new__(sxs._WindowsApi)
    api.api = SimpleNamespace(FindActCtxSectionStringW=find, ReleaseActCtx=released.append)
    if returned == 11 and roster:
        assert api.find(11, 'comctl32.dll') == 3
    else:
        with pytest.raises(sxs.WindowsSxsError, match='context|roster'):
            api.find(11, 'comctl32.dll')
    assert released == ([returned] if returned is not None else [])


def test_real_query_reads_utf16_only_inside_bounded_return_buffer(monkeypatch):
    encoded = ASSEMBLY.encode('utf-16-le')
    total = C.sizeof(sxs._ASSEMBLY_DETAIL) + len(encoded) + 2
    calls = []
    def query(flags, handle, roster, info, buffer, length, required):
        calls.append((flags, handle, C.cast(roster, C.POINTER(sxs._U32))[0], info))
        C.cast(required, C.POINTER(C.c_size_t))[0] = total
        if buffer is None:
            return False
        detail = sxs._ASSEMBLY_DETAIL.from_buffer(buffer)
        pointer = C.addressof(buffer) + C.sizeof(sxs._ASSEMBLY_DETAIL)
        C.memmove(pointer, encoded, len(encoded))
        detail.lpAssemblyDirectoryName = pointer
        detail.ulAssemblyDirectoryNameLength = len(encoded)
        return True
    monkeypatch.setattr(sxs.C, 'get_last_error', lambda: 122, raising=False)
    api = sxs._WindowsApi.__new__(sxs._WindowsApi)
    api.api = SimpleNamespace(QueryActCtxW=query)
    assert api.assembly_directory(11, 3) == ASSEMBLY
    assert calls == [(0, 11, 3, 3), (0, 11, 3, 3)]


@pytest.mark.parametrize('offset,length', [(-2, 4), (0, 0), (0, 3), (30, 4), (1, 2), (0, 4098)])
def test_out_of_bounds_utf16_pointers_are_rejected_without_reading(offset, length):
    buffer = C.create_string_buffer(32)
    with pytest.raises(sxs.WindowsSxsError, match='bounds'):
        sxs._buffer_text(buffer, C.addressof(buffer) + offset, length)


def test_invalid_utf16_and_embedded_null_are_rejected():
    for raw in (b'\x00\xd8', b'a\x00\0\0b\x00'):
        buffer = C.create_string_buffer(raw)
        with pytest.raises(sxs.WindowsSxsError, match='encoding|string'):
            sxs._buffer_text(buffer, C.addressof(buffer), len(raw))


@pytest.mark.skipif(os.name != 'nt', reason='Real activation-context APIs require Windows')
def test_windows_real_embedded_manifest_selects_sxs_and_binds_context(tmp_path):
    """Owned binary fixture with an embedded manifest; no EXE or DLL execution."""
    import PyInstaller
    import pefile
    from PyInstaller.utils.win32.winmanifest import (
        create_application_manifest, write_manifest_to_executable,
    )

    original = Path(PyInstaller.__file__).parent / 'bootloader' / 'Windows-64bit-intel' / 'runw.exe'
    candidate = tmp_path / 'metadata-fixture.exe'
    shutil.copyfile(original, candidate)
    write_manifest_to_executable(str(candidate), create_application_manifest())
    with sxs.candidate_activation_context(candidate) as context:
        selected = context.resolve_dll('comctl32.dll')
        assert selected is not None
        assert selected.relative_to(context.windows_root).parts[0].lower() == 'winsxs'
        assert context.resolve_dll('kernel32.dll') is None
        pe = pefile.PE(str(selected), fast_load=True, max_symbol_exports=65536)
        try:
            pe.parse_data_directories(directories=[0])
            assert 380 in {entry.ordinal for entry in pe.DIRECTORY_ENTRY_EXPORT.symbols if entry.address}
        finally:
            pe.close()
