"""Resolve OS side-by-side DLL metadata using the actual candidate manifest.

This module never loads a component DLL or executes its code. Windows selects
the assembly through an activation context; no directory search or version
guessing is used. Only a genuinely absent redirection permits caller fallback.
"""
from __future__ import annotations

from contextlib import contextmanager
import ctypes as C
import os
from pathlib import Path
import re
import struct
import threading


class WindowsSxsError(ValueError):
    """Fixed, public-safe metadata failure; never contains private file paths."""


_U32, _U16, _PTR = C.c_uint32, C.c_uint16, C.c_void_p
_MAX_DETAIL_BYTES = 1024 * 1024
_MAX_PE_BYTES = 512 * 1024 * 1024
_RESERVED = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)),
             *(f'LPT{i}' for i in range(1, 10))}
_ASSEMBLY_NAME = re.compile(
    r'amd64_[a-z0-9_.-]+_[a-f0-9]{16}_[0-9]+(?:\.[0-9]+){3}_[a-z0-9-]+_[a-f0-9]{16}',
    re.IGNORECASE,
)


def _require(value, message):
    if not value:
        raise WindowsSxsError(message)


def _library_name(name):
    _require(type(name) is str and bool(re.fullmatch(r'[A-Za-z0-9_.+-]{1,128}', name))
             and name.lower().endswith(('.dll', '.drv'))
             and name.split('.')[0].upper() not in _RESERVED,
             'Invalid SxS library name')
    return name.lower()


def _canonical(path, message):
    try:
        return Path(path).resolve(strict=True)
    except (OSError, RuntimeError, ValueError, TypeError):
        raise WindowsSxsError(message) from None


def _check_pe(path, *, dll):
    """Bounded header reads, including for the large frozen executable."""
    try:
        size = path.stat().st_size
        _require(path.is_file() and 64 <= size <= _MAX_PE_BYTES, 'Invalid SxS PE size')
        with path.open('rb') as stream:
            dos = stream.read(64)
            _require(len(dos) == 64 and dos[:2] == b'MZ', 'Invalid SxS DOS header')
            offset = struct.unpack_from('<I', dos, 60)[0]
            _require(64 <= offset <= min(size - 26, 4 * 1024 * 1024), 'Invalid SxS PE offset')
            stream.seek(offset)
            header = stream.read(26)
        _require(len(header) == 26 and header[:4] == b'PE\0\0', 'Invalid SxS PE header')
        machine, sections, _, _, _, optional_size, characteristics = struct.unpack_from('<HHIIIHH', header, 4)
        _require(machine == 0x8664 and struct.unpack_from('<H', header, 24)[0] == 0x20b,
                 'SxS PE must be AMD64')
        _require(0 < sections <= 96 and 112 <= optional_size <= 4096
                 and offset + 24 + optional_size + sections * 40 <= size,
                 'Invalid SxS PE layout')
        _require(bool(characteristics & 0x2000) is dll, 'Unexpected SxS PE image kind')
    except OSError:
        raise WindowsSxsError('Cannot read SxS PE metadata') from None


class _ACTCTX(C.Structure):
    _fields_ = [('cbSize', _U32), ('dwFlags', _U32), ('lpSource', C.c_wchar_p),
                ('wProcessorArchitecture', _U16), ('wLangId', _U16),
                ('lpAssemblyDirectory', C.c_wchar_p), ('lpResourceName', _PTR),
                ('lpApplicationName', C.c_wchar_p), ('hModule', _PTR)]


class _ASSEMBLY_METADATA(C.Structure):
    _fields_ = [('lpInformation', _PTR), ('lpSectionBase', _PTR), ('ulSectionLength', _U32),
                ('lpSectionGlobalDataBase', _PTR), ('ulSectionGlobalDataLength', _U32)]


class _SECTION_DATA(C.Structure):
    _fields_ = [('cbSize', _U32), ('ulDataFormatVersion', _U32), ('lpData', _PTR),
                ('ulLength', _U32), ('lpSectionGlobalData', _PTR),
                ('ulSectionGlobalDataLength', _U32), ('lpSectionBase', _PTR),
                ('ulSectionTotalLength', _U32), ('hActCtx', _PTR),
                ('ulAssemblyRosterIndex', _U32), ('ulFlags', _U32),
                ('AssemblyMetadata', _ASSEMBLY_METADATA)]


class _ASSEMBLY_DETAIL(C.Structure):
    _fields_ = [('ulFlags', _U32), ('ulEncodedAssemblyIdentityLength', _U32),
                ('ulManifestPathType', _U32), ('ulManifestPathLength', _U32),
                ('liManifestLastWriteTime', C.c_int64), ('ulPolicyPathType', _U32),
                ('ulPolicyPathLength', _U32), ('liPolicyLastWriteTime', C.c_int64),
                ('ulMetadataSatelliteRosterIndex', _U32), ('ulManifestVersionMajor', _U32),
                ('ulManifestVersionMinor', _U32), ('ulPolicyVersionMajor', _U32),
                ('ulPolicyVersionMinor', _U32), ('ulAssemblyDirectoryNameLength', _U32),
                ('lpAssemblyEncodedAssemblyIdentity', _PTR), ('lpAssemblyManifestPath', _PTR),
                ('lpAssemblyPolicyPath', _PTR), ('lpAssemblyDirectoryName', _PTR),
                ('ulFileCount', _U32)]


def _buffer_text(buffer, pointer, length):
    base, size = C.addressof(buffer), C.sizeof(buffer)
    _require(pointer is not None and type(length) is int and 0 < length <= 4096
             and length % 2 == 0 and pointer % 2 == 0
             and base <= pointer and pointer + length <= base + size,
             'Invalid SxS assembly string bounds')
    try:
        text = C.string_at(pointer, length).decode('utf-16-le').rstrip('\0')
    except UnicodeDecodeError:
        raise WindowsSxsError('Invalid SxS assembly string encoding') from None
    _require('\0' not in text, 'Invalid SxS assembly string')
    return text


def _require_platform():
    _require(os.name == 'nt' and struct.calcsize('P') == 8 and C.sizeof(C.c_wchar) == 2,
             'SxS metadata requires 64-bit Windows')
    _require(C.sizeof(_ACTCTX) == 56 and C.sizeof(_ASSEMBLY_DETAIL) == 104
             and C.sizeof(_SECTION_DATA) == 112, 'Unexpected SxS API structure layout')


class _WindowsApi:
    def __init__(self):
        # Kernel32 is the OS API provider, not an inspected component DLL.
        self.api = C.WinDLL('kernel32', use_last_error=True)
        definitions = {
            'CreateActCtxW': ([C.POINTER(_ACTCTX)], _PTR),
            'ActivateActCtx': ([_PTR, C.POINTER(C.c_size_t)], C.c_int),
            'DeactivateActCtx': ([_U32, C.c_size_t], C.c_int),
            'ReleaseActCtx': ([_PTR], None),
            'FindActCtxSectionStringW': ([_U32, _PTR, _U32, C.c_wchar_p, C.POINTER(_SECTION_DATA)], C.c_int),
            'QueryActCtxW': ([_U32, _PTR, _PTR, _U32, _PTR, C.c_size_t, C.POINTER(C.c_size_t)], C.c_int),
            'GetWindowsDirectoryW': ([C.c_wchar_p, _U32], _U32),
        }
        for name, (arguments, result) in definitions.items():
            function = getattr(self.api, name)
            function.argtypes, function.restype = arguments, result

    def windows_root(self):
        buffer = C.create_unicode_buffer(32768)
        count = self.api.GetWindowsDirectoryW(buffer, len(buffer))
        _require(0 < count < len(buffer), 'Cannot query Windows directory')
        return _canonical(buffer.value, 'Invalid Windows directory')

    def create(self, executable):
        context = _ACTCTX()
        context.cbSize = C.sizeof(context)
        context.dwFlags = 0x8  # ACTCTX_FLAG_RESOURCE_NAME_VALID
        context.lpSource = str(executable)
        context.lpResourceName = 1  # Actual embedded CREATEPROCESS manifest, never a fixture.
        handle = self.api.CreateActCtxW(C.byref(context))
        _require(handle not in (None, C.c_void_p(-1).value), 'Cannot create candidate activation context')
        return handle

    def activate(self, handle):
        cookie = C.c_size_t()
        _require(bool(self.api.ActivateActCtx(handle, C.byref(cookie))), 'Cannot activate candidate context')
        return cookie.value

    def deactivate(self, cookie):
        _require(bool(self.api.DeactivateActCtx(0, cookie)), 'Cannot deactivate candidate context')

    def release(self, handle):
        self.api.ReleaseActCtx(handle)

    def find(self, handle, name):
        section = _SECTION_DATA()
        section.cbSize = C.sizeof(section)
        # The lookup can see another context on the activation stack. Bind the
        # returned roster to this candidate, then release its added reference.
        if not self.api.FindActCtxSectionStringW(1, None, 2, name, C.byref(section)):
            # Other failures (including an absent/malformed entire section) fail closed.
            if C.get_last_error() == 14007:  # ERROR_SXS_KEY_NOT_FOUND
                return None
            raise WindowsSxsError('Cannot query candidate DLL redirection')
        returned = section.hActCtx
        _require(returned not in (None, C.c_void_p(-1).value), 'Missing SxS context identity')
        try:
            _require(returned == handle, 'SxS redirection belongs to another context')
            _require(section.ulAssemblyRosterIndex > 0, 'Invalid SxS assembly roster')
            return section.ulAssemblyRosterIndex
        finally:
            self.api.ReleaseActCtx(returned)

    def assembly_directory(self, handle, index):
        roster, required = _U32(index), C.c_size_t()
        result = self.api.QueryActCtxW(0, handle, C.byref(roster), 3, None, 0, C.byref(required))
        _require(not result and C.get_last_error() == 122, 'Cannot size SxS assembly metadata')
        _require(C.sizeof(_ASSEMBLY_DETAIL) <= required.value <= _MAX_DETAIL_BYTES,
                 'Invalid SxS assembly metadata size')
        buffer = C.create_string_buffer(required.value)
        _require(bool(self.api.QueryActCtxW(0, handle, C.byref(roster), 3, buffer, len(buffer),
                                          C.byref(required))), 'Cannot query SxS assembly metadata')
        _require(required.value <= len(buffer), 'SxS assembly metadata exceeds buffer')
        detail = _ASSEMBLY_DETAIL.from_buffer(buffer)
        return _buffer_text(buffer, detail.lpAssemblyDirectoryName, detail.ulAssemblyDirectoryNameLength)


class _CandidateContext:
    def __init__(self, api, handle, windows_root):
        self._api, self._handle = api, handle
        self.windows_root = windows_root
        self._thread = threading.get_ident()
        self._active = True

    def resolve_dll(self, name):
        """Return the exact OS-selected private path, or None for no redirection."""
        _require(self._active and threading.get_ident() == self._thread,
                 'Candidate context is inactive or belongs to another thread')
        name = _library_name(name)
        index = self._api.find(self._handle, name)
        if index is None:
            return None
        directory = self._api.assembly_directory(self._handle, index)
        _require(type(directory) is str and len(directory) <= 512 and bool(_ASSEMBLY_NAME.fullmatch(directory)),
                 'Unsafe SxS assembly directory')
        sxs = _canonical(self.windows_root / 'WinSxS', 'Missing Windows SxS directory')
        _require(sxs.parent == self.windows_root and sxs.name.lower() == 'winsxs',
                 'SxS directory escapes Windows root')
        assembly = _canonical(sxs / directory, 'Missing selected SxS assembly')
        _require(assembly.parent == sxs and assembly.name.lower() == directory.lower(),
                 'Selected SxS assembly redirects outside its identity')
        target = _canonical(assembly / name, 'Missing selected SxS library')
        _require(target.parent == assembly and target.name.lower() == name,
                 'Selected SxS library escapes its assembly')
        _check_pe(target, dll=True)
        return target


@contextmanager
def candidate_activation_context(executable):
    """Activate only the real EXE's embedded manifest ID 1 for this thread.

Missing manifests, unexpected metadata, and API errors raise WindowsSxsError.
The returned paths are private; callers must sanitize any published evidence.
"""
    _require_platform()
    executable = _canonical(executable, 'Invalid candidate executable')
    _check_pe(executable, dll=False)
    api = _WindowsApi()
    windows_root = api.windows_root()
    handle = api.create(executable)
    cookie, context = None, None
    try:
        cookie = api.activate(handle)
        context = _CandidateContext(api, handle, windows_root)
        yield context
    finally:
        if context is not None:
            context._active = False
        try:
            if cookie is not None:
                api.deactivate(cookie)
        finally:
            api.release(handle)
