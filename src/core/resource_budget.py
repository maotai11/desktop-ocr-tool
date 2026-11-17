"""Shared decimal-GB budgets for owned files and the application process tree.

RSS, private allocation and on-disk bytes are different measurements. No model
file size or estimate is substituted for a live process measurement here.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
import math
import os
from pathlib import Path
import stat
import sys
import threading
import weakref

RESOURCE_LIMIT_BYTES = 1_000_000_000
MAX_CAPTURE_PIXELS = 16_000_000
MAX_OCR_PIXELS = 4_000_000
MAX_OCR_SIDE = 4096
DEFAULT_MEMORY_RESERVE_BYTES = 128_000_000


class ResourceBudgetExceeded(OSError):
    def __init__(self, kind='disk'):
        self.kind = kind
        message = ('可用記憶體不足，請縮小範圍或關閉其他辨識工作後重試。' if kind == 'memory'
                   else '程式檔案與資料已接近 1 GB，請先開啟檔案位置整理，再重試。')
        super().__init__(message)


class ResourceInspectionError(ResourceBudgetExceeded):
    def __init__(self, *, native_error=None):
        super().__init__('inspection')
        self.native_error = native_error
        self.args = ('無法完整確認資源用量，已停止新增資料；請檢查檔案位置及權限。',)


class ResourceMetadataChanged(ResourceInspectionError):
    """A live node changed during measurement; no partial total is usable."""


@dataclass(frozen=True)
class DiskUsage:
    bytes: int
    files: int
    directories: int


@dataclass(frozen=True)
class ProcessUsage:
    rss_bytes: int
    private_bytes: int | None
    pids: tuple[int, ...]


def raster_preflight(width: int, height: int) -> tuple[int, int]:
    """Validate before capture; return the largest bounded OCR working size."""
    if (type(width) is not int or type(height) is not int or width < 1 or height < 1
            or width * height > MAX_CAPTURE_PIXELS):
        raise ResourceBudgetExceeded('memory')
    scale = min(1., MAX_OCR_SIDE / max(width, height),
                math.sqrt(MAX_OCR_PIXELS / (width * height)))
    return max(1, int(width * scale)), max(1, int(height * scale))


def _confirmed_missing(path):
    try:
        path.lstat()
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return False


def _measure_storage_node(path, info):
    """Requery a growing regular file once; never combine two file identities."""
    for attempt in range(2):
        if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
            raise ResourceInspectionError()
        allocated = (_windows_allocated_bytes(path) if sys.platform == 'win32'
                     else max(info.st_size, getattr(info, 'st_blocks', 0) * 512))
        children = list(path.iterdir()) if stat.S_ISDIR(info.st_mode) else None
        current = path.lstat()
        if (current.st_dev, current.st_ino, stat.S_IFMT(current.st_mode)) != (
                info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode)):
            raise ResourceMetadataChanged()
        if children is None and (
                current.st_size, current.st_mtime_ns, getattr(current, 'st_blocks', None)) != (
                info.st_size, info.st_mtime_ns, getattr(info, 'st_blocks', None)):
            if attempt == 0:
                info = current
                continue
            raise ResourceMetadataChanged()
        return max(allocated, current.st_size, getattr(current, 'st_blocks', 0) * 512), children
    raise ResourceInspectionError()


def managed_disk_usage(roots) -> DiskUsage:
    """Count owned bytes once, including hard links; never follow inner links.

    A linked configured root is resolved deliberately. An unexpected link below
    it makes inspection fail closed instead of silently excluding its target.
    Sparse/compressed files use the larger of logical and allocated bytes.
    """
    pending, seen_paths, seen_nodes = [], set(), set()
    for supplied in roots:
        if supplied is None or not str(supplied):
            continue
        try:
            path = Path(supplied).resolve()
        except (OSError, RuntimeError):
            raise ResourceInspectionError() from None
        if path not in seen_paths:
            seen_paths.add(path)
            pending.append(path)
    total = files = directories = 0
    while pending:
        path = pending.pop()
        try:
            info = path.lstat()
        except FileNotFoundError:
            if _confirmed_missing(path):
                continue
            raise ResourceInspectionError() from None
        except OSError:
            raise ResourceInspectionError() from None
        identity = (info.st_dev, info.st_ino) if info.st_ino else str(path)
        if identity in seen_nodes:
            continue
        if stat.S_ISLNK(info.st_mode) or (hasattr(path, 'is_junction') and path.is_junction()):
            raise ResourceInspectionError()
        try:
            allocated, children = _measure_storage_node(path, info)
        except ResourceInspectionError as exc:
            # A bootloader can remove an unpacked file after lstat. Only a
            # native not-found result AND a fresh absence check may retire it.
            # Recreated/live/inaccessible entries remain a failed inspection.
            if exc.native_error in (2, 3) and _confirmed_missing(path):
                continue
            raise
        except FileNotFoundError:
            if _confirmed_missing(path):
                continue
            raise ResourceInspectionError() from None
        except OSError:
            raise ResourceInspectionError() from None
        seen_nodes.add(identity)
        total += allocated
        if children is not None:
            directories += 1
            pending.extend(children)
        else:
            files += 1
    return DiskUsage(total, files, directories)


_storage_lock = threading.RLock()
_reserved_bytes = 0
_registered_roots: set[Path] = set()
_owned_roots = weakref.WeakKeyDictionary()


def register_managed_root(path, *, owner=None):
    with _storage_lock:
        resolved = Path(path).resolve()
        if owner is None:
            _registered_roots.add(resolved)
        else:
            _owned_roots.setdefault(owner, set()).add(resolved)


def managed_storage_roots(file_manager=None, cfg=None, *, extra_roots=()):
    """Return explicit app-owned roots, never the whole system TEMP or checkout."""
    if cfg is not None:
        root = Path(cfg._root)
        data = Path(cfg.get_data_directory())
    else:
        from .config import get_project_root
        root = Path(get_project_root())
        data = Path(getattr(file_manager, '_data_dir', root / 'data'))
    roots = [data, root / 'config', root / 'logs', root / 'cache', root / 'tmp', *extra_roots]
    if file_manager is not None:
        roots.append(Path(file_manager._data_dir))
    if getattr(sys, 'frozen', False):
        roots.append(Path(sys.executable))
        roots.extend(active_extraction_roots())
    else:
        roots.append(Path(__file__).resolve().parents[2] / 'models')
    with _storage_lock:
        roots.extend(_registered_roots)
        for paths in tuple(_owned_roots.values()):
            roots.extend(paths)
    return tuple(roots)


@contextmanager
def disk_quota_guard(file_manager=None, cfg=None, *, reserve_bytes=0, extra_roots=()):
    """Serialize owned writers and reserve their maximum transient growth.

    The reservation includes temporary files/journals, not only final size.
    This never deletes existing files and never allows an equality-at-limit.
    """
    global _reserved_bytes
    if type(reserve_bytes) is not int or reserve_bytes < 0:
        raise ValueError('Invalid resource reservation')
    with _storage_lock:
        usage = managed_disk_usage(managed_storage_roots(file_manager, cfg, extra_roots=extra_roots))
        if usage.bytes + _reserved_bytes + reserve_bytes >= RESOURCE_LIMIT_BYTES:
            raise ResourceBudgetExceeded('disk')
        _reserved_bytes += reserve_bytes
        try:
            yield usage
        finally:
            _reserved_bytes -= reserve_bytes


def _linux_processes():
    result = {}
    for path in Path('/proc').iterdir():
        if not path.name.isdecimal():
            continue
        try:
            values = (path / 'stat').read_text().rsplit(')', 1)[1].split()
            result[int(path.name)] = int(values[1])
        except (FileNotFoundError, ProcessLookupError):
            continue
        except (OSError, ValueError, IndexError):
            # An unrelated process may hide its metadata; the owned root and
            # every identified descendant are verified separately below.
            continue
    return result


def _windows_processes():
    import ctypes
    from ctypes import wintypes
    class Entry(ctypes.Structure):
        _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD),
                    ('th32ProcessID', wintypes.DWORD), ('th32DefaultHeapID', ctypes.c_size_t),
                    ('th32ModuleID', wintypes.DWORD), ('cntThreads', wintypes.DWORD),
                    ('th32ParentProcessID', wintypes.DWORD), ('pcPriClassBase', wintypes.LONG),
                    ('dwFlags', wintypes.DWORD), ('szExeFile', wintypes.WCHAR * 260)]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise ResourceInspectionError()
    try:
        entry = Entry(); entry.dwSize = ctypes.sizeof(entry)
        if not kernel.Process32FirstW(snapshot, ctypes.byref(entry)):
            raise ResourceInspectionError()
        result = {}
        while True:
            result[entry.th32ProcessID] = entry.th32ParentProcessID
            if not kernel.Process32NextW(snapshot, ctypes.byref(entry)):
                if ctypes.get_last_error() != 18:  # ERROR_NO_MORE_FILES
                    raise ResourceInspectionError()
                return result
    finally:
        kernel.CloseHandle(snapshot)


def _windows_memory(pid):
    import ctypes
    from ctypes import wintypes
    class Counters(ctypes.Structure):
        _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in ('PeakWorkingSetSize', 'WorkingSetSize',
            'QuotaPeakPagedPoolUsage', 'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage',
            'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage', 'PrivateUsage')]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    psapi = ctypes.WinDLL('psapi', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    handle = kernel.OpenProcess(0x0400 | 0x0010, False, pid)
    if not handle:
        raise ResourceInspectionError()
    try:
        counters = Counters(); counters.cb = ctypes.sizeof(counters)
        if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            raise ResourceInspectionError()
        return counters.WorkingSetSize, counters.PrivateUsage
    finally:
        kernel.CloseHandle(handle)


def measure_process_tree(root_pid=None) -> ProcessUsage:
    root_pid = current_process_root() if root_pid is None else root_pid
    if type(root_pid) is not int or root_pid <= 0:
        raise ValueError('Invalid owned process root')
    processes = _windows_processes() if sys.platform == 'win32' else _linux_processes()
    if root_pid not in processes:
        raise ResourceInspectionError()
    owned = {root_pid}
    while True:
        expanded = owned | {pid for pid, parent in processes.items() if parent in owned}
        if expanded == owned:
            break
        owned = expanded
    rss = private = 0
    live = []
    for pid in sorted(owned):
        if sys.platform == 'win32':
            try:
                resident, allocated = _windows_memory(pid)
            except ResourceInspectionError:
                if pid != root_pid and pid not in _windows_processes():
                    continue
                raise
            private += allocated
        else:
            try:
                lines = Path(f'/proc/{pid}/status').read_text().splitlines()
            except FileNotFoundError:
                if pid != root_pid:
                    continue
                raise ResourceInspectionError() from None
            except OSError:
                raise ResourceInspectionError() from None
            values = {line.split(':', 1)[0]: line.split(':', 1)[1].strip() for line in lines if ':' in line}
            if 'VmRSS' not in values:
                if values.get('State', '').startswith('Z'):
                    continue
                raise ResourceInspectionError()
            resident = int(values['VmRSS'].split()[0]) * 1024
        rss += resident
        live.append(pid)
    return ProcessUsage(rss, private if sys.platform == 'win32' else None, tuple(live))


def memory_preflight(additional_bytes=DEFAULT_MEMORY_RESERVE_BYTES, *, root_pid=None):
    if type(additional_bytes) is not int or additional_bytes < 0:
        raise ValueError('Invalid memory reservation')
    usage = measure_process_tree(root_pid)
    if max(usage.rss_bytes, usage.private_bytes or 0) + additional_bytes >= RESOURCE_LIMIT_BYTES:
        raise ResourceBudgetExceeded('memory')
    return usage


def current_process_root():
    """Use the PID visible in /proc, or the owned onefile bootloader on Windows."""
    if sys.platform != 'win32':
        try:
            for line in Path('/proc/self/status').read_text().splitlines():
                if line.startswith('Tgid:'):
                    return int(line.split(':', 1)[1])
        except (OSError, ValueError):
            raise ResourceInspectionError() from None
        raise ResourceInspectionError()
    pid = os.getpid()
    if getattr(sys, 'frozen', False):
        processes = _windows_processes()
        executable = Path(sys.executable).resolve()
        for _ in range(32):
            parent = processes.get(pid)
            if not parent or _windows_executable(parent) != executable:
                break
            pid = parent
        else:
            raise ResourceInspectionError()
    return pid


def _windows_executable(pid):
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                  wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        raise ResourceInspectionError()
    try:
        buffer = ctypes.create_unicode_buffer(32768)
        length = wintypes.DWORD(len(buffer))
        if not kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(length)):
            raise ResourceInspectionError()
        return Path(buffer.value).resolve()
    finally:
        kernel.CloseHandle(handle)


class BoundedWriter:
    """A streaming encoder cannot exceed the bytes admitted before file creation."""
    def __init__(self, stream, limit):
        self.stream, self.limit = stream, limit
    def write(self, data):
        if self.stream.tell() + len(data) > self.limit:
            raise ResourceBudgetExceeded('disk')
        return self.stream.write(data)
    def seek(self, offset, whence=0):
        position = self.stream.seek(offset, whence)
        if position < 0 or position > self.limit:
            raise ResourceBudgetExceeded('disk')
        return position
    def tell(self):
        return self.stream.tell()
    def flush(self):
        self.stream.flush()
    # No fileno: encoders must not bypass the bounded Python write method.


_runtime_job = None


class RuntimeMemoryJob:
    """Aggregate Windows private-commit ceiling; working-set evidence is separate."""
    def __init__(self):
        import ctypes
        from ctypes import wintypes
        import uuid
        class Basic(ctypes.Structure):
            _fields_ = [('user', ctypes.c_int64), ('job_user', ctypes.c_int64),
                        ('flags', wintypes.DWORD), ('minimum_ws', ctypes.c_size_t),
                        ('maximum_ws', ctypes.c_size_t), ('active', wintypes.DWORD),
                        ('affinity', ctypes.c_size_t), ('priority', wintypes.DWORD),
                        ('scheduling', wintypes.DWORD)]
        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in ('read_ops', 'write_ops', 'other_ops',
                                                           'read_bytes', 'write_bytes', 'other_bytes')]
        class Extended(ctypes.Structure):
            _fields_ = [('basic', Basic), ('io', IO), ('process_limit', ctypes.c_size_t),
                        ('job_limit', ctypes.c_size_t), ('peak_process', ctypes.c_size_t),
                        ('peak_job', ctypes.c_size_t)]
        class Accounting(ctypes.Structure):
            _fields_ = [(name, ctypes.c_int64) for name in ('user', 'kernel', 'period_user', 'period_kernel')]
            _fields_ += [(name, wintypes.DWORD) for name in ('faults', 'total', 'active', 'terminated')]
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.OpenJobObjectW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
        kernel.OpenJobObjectW.restype = wintypes.HANDLE
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                                     wintypes.DWORD, ctypes.c_void_p]
        kernel.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        supplied = os.environ.get('DESKTOP_OCR_RESOURCE_JOB')
        handle = kernel.OpenJobObjectW(0x4, False, supplied) if supplied else None
        self.kernel, self.Extended, self.Accounting = kernel, Extended, Accounting
        self.handle = None
        if supplied:
            if not handle:
                raise ResourceInspectionError()
            self.handle = handle
            member = wintypes.BOOL()
            if (not kernel.IsProcessInJob(kernel.GetCurrentProcess(), handle, ctypes.byref(member))
                    or not member.value):
                kernel.CloseHandle(handle); self.handle = None
                raise ResourceInspectionError()
            limits = self._query_limits()
            if not (limits.basic.flags & 0x200) or not 0 < limits.job_limit < RESOURCE_LIMIT_BYTES:
                kernel.CloseHandle(handle); self.handle = None
                raise ResourceInspectionError()
            return
        name = 'Local\\DesktopOcrResource-' + uuid.uuid4().hex
        handle = kernel.CreateJobObjectW(None, name)
        if not handle or ctypes.get_last_error() == 183:
            if handle:
                kernel.CloseHandle(handle)
            raise ResourceInspectionError()
        try:
            limits = Extended()
            limits.basic.flags = 0x200  # JOB_OBJECT_LIMIT_JOB_MEMORY
            limits.job_limit = RESOURCE_LIMIT_BYTES - 1
            if not kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise ResourceInspectionError()
            root = current_process_root()
            if root != os.getpid():
                parent = kernel.OpenProcess(0x0100 | 0x0001, False, root)
                if not parent:
                    raise ResourceInspectionError()
                try:
                    if not kernel.AssignProcessToJobObject(handle, parent):
                        raise ResourceInspectionError()
                finally:
                    kernel.CloseHandle(parent)
            if not kernel.AssignProcessToJobObject(handle, kernel.GetCurrentProcess()):
                raise ResourceInspectionError()
            self.handle = handle
            os.environ['DESKTOP_OCR_RESOURCE_JOB'] = name
        except BaseException:
            kernel.CloseHandle(handle)
            raise

    def _query_limits(self):
        import ctypes
        limits = self.Extended()
        if not self.kernel.QueryInformationJobObject(self.handle, 9, ctypes.byref(limits),
                                                      ctypes.sizeof(limits), None):
            raise ResourceInspectionError()
        return limits

    def accounting(self):
        import ctypes
        limits, accounting = self._query_limits(), self.Accounting()
        if not self.kernel.QueryInformationJobObject(self.handle, 1, ctypes.byref(accounting),
                                                      ctypes.sizeof(accounting), None):
            raise ResourceInspectionError()
        return {'installed': True, 'limit_bytes': int(limits.job_limit),
                'peak_job_private_bytes': int(limits.peak_job),
                'active_processes': int(accounting.active)}


def install_runtime_budget():
    """Explicit standalone entry only; importing this module never caps a host."""
    global _runtime_job
    if sys.platform == 'win32' and _runtime_job is None:
        _runtime_job = RuntimeMemoryJob()
    return runtime_memory_accounting()


def runtime_memory_accounting():
    if _runtime_job is not None:
        return _runtime_job.accounting()
    return {'installed': False, 'limit_bytes': RESOURCE_LIMIT_BYTES - 1,
            'peak_job_private_bytes': 0, 'active_processes': 0}


@contextmanager
def bounded_output(path, maximum_bytes, *, binary=False, encoding='utf-8', newline=None):
    """Atomic owned output, with a bounded temporary stream and shared quota."""
    import tempfile
    destination = Path(path)
    if type(maximum_bytes) is not int or maximum_bytes < 0:
        raise ValueError('Invalid output reservation')
    temporary = None
    with disk_quota_guard(reserve_bytes=disk_write_reservation(destination, maximum_bytes),
                          extra_roots=(destination,)):
        if not destination.parent.is_dir():
            raise FileNotFoundError('請先選擇已存在的輸出資料夾')
        descriptor, temporary = tempfile.mkstemp(prefix='.ocr-output-', suffix='.tmp', dir=destination.parent)
        try:
            with os.fdopen(descriptor, 'wb') as raw:
                writer = BoundedWriter(raw, maximum_bytes)
                if binary:
                    yield writer
                else:
                    class TextWriter:
                        def write(self, text):
                            if newline is not None and newline != '':
                                text = text.replace('\n', newline)
                            data = text.encode(encoding)
                            writer.write(data)
                            return len(text)
                        def flush(self):
                            writer.flush()
                    # utf-8-sig must emit its BOM once, not once per CSV row.
                    if encoding.lower().replace('_', '-') == 'utf-8-sig':
                        writer.write(b'\xef\xbb\xbf')
                        encoding = 'utf-8'
                    yield TextWriter()
                raw.flush()
                os.fsync(raw.fileno())
            os.replace(temporary, destination)
            temporary = None
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)


def copy_file_with_budget(source, destination):
    import shutil
    maximum = Path(source).stat().st_size
    with bounded_output(destination, maximum, binary=True) as output, open(source, 'rb') as incoming:
        shutil.copyfileobj(incoming, output, length=64 * 1024)
    return str(destination)


def write_bytes_with_budget(destination, data):
    with bounded_output(destination, len(data), binary=True) as output:
        output.write(data)


def active_extraction_roots():
    """Find owned onefile extraction roots from loaded modules, never all TEMP."""
    if not getattr(sys, 'frozen', False):
        return ()
    roots = {Path(sys._MEIPASS).resolve()}
    if sys.platform != 'win32':
        return tuple(roots)
    import ctypes
    from ctypes import wintypes
    class Module(ctypes.Structure):
        _fields_ = [(name, wintypes.DWORD) for name in ('size', 'module_id', 'pid', 'global_count', 'count')]
        _fields_ += [('base', ctypes.c_void_p), ('base_size', wintypes.DWORD),
                    ('handle', wintypes.HMODULE), ('name', wintypes.WCHAR * 256),
                    ('path', wintypes.WCHAR * 260)]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Module32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Module)]
    kernel.Module32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Module)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    processes = _windows_processes()
    owned = {current_process_root()}
    while True:
        expanded = owned | {pid for pid, parent in processes.items() if parent in owned}
        if expanded == owned:
            break
        owned = expanded
    for pid in owned:
        handle = kernel.CreateToolhelp32Snapshot(0x8 | 0x10, pid)
        if handle == ctypes.c_void_p(-1).value:
            if pid not in _windows_processes():
                continue
            raise ResourceInspectionError()
        try:
            item = Module(); item.size = ctypes.sizeof(item)
            if not kernel.Module32FirstW(handle, ctypes.byref(item)):
                if pid not in _windows_processes():
                    continue
                raise ResourceInspectionError()
            while True:
                module_path = Path(item.path)
                for parent in module_path.parents:
                    if parent.name.startswith('_MEI'):
                        roots.add(parent.resolve())
                        break
                if not kernel.Module32NextW(handle, ctypes.byref(item)):
                    if ctypes.get_last_error() != 18:
                        raise ResourceInspectionError()
                    break
        finally:
            kernel.CloseHandle(handle)
    return tuple(roots)


def storage_block_size(path):
    """Allocation unit of the actual destination volume (not assumed 4 KiB)."""
    candidate = Path(path).absolute()
    while not candidate.exists():
        if candidate.parent == candidate:
            raise ResourceInspectionError()
        candidate = candidate.parent
    if sys.platform != 'win32':
        try:
            result = os.statvfs(candidate)
            block = result.f_frsize or result.f_bsize
        except OSError:
            raise ResourceInspectionError() from None
    else:
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetVolumePathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        kernel.GetDiskFreeSpaceW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD),
                                           ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
                                           ctypes.POINTER(wintypes.DWORD)]
        volume = ctypes.create_unicode_buffer(32768)
        sectors, size, free, total = (wintypes.DWORD() for _ in range(4))
        if (not kernel.GetVolumePathNameW(str(candidate), volume, len(volume))
                or not kernel.GetDiskFreeSpaceW(volume.value, ctypes.byref(sectors), ctypes.byref(size),
                                                ctypes.byref(free), ctypes.byref(total))):
            raise ResourceInspectionError()
        block = int(sectors.value) * int(size.value)
    if type(block) is not int or block < 512 or block > 32 * 1024 * 1024:
        raise ResourceInspectionError()
    return block


def disk_write_reservation(path, payload_bytes=0, *, new_files=1, new_directories=0):
    """Payload rounded to its volume, plus file/directory metadata growth space."""
    if any(type(value) is not int or value < 0 for value in (payload_bytes, new_files, new_directories)):
        raise ValueError('Invalid disk write size')
    block = storage_block_size(path)
    return ((payload_bytes + block - 1) // block + 2 * new_files + 2 * new_directories) * block


def create_managed_directory(path, *, owner=None, cfg=None):
    """Create at most one missing component, admitted before creating it."""
    target = Path(path)
    if target.is_dir():
        return
    if not target.parent.is_dir():
        raise FileNotFoundError('請先選擇已存在的上層資料夾')
    register_managed_root(target, owner=owner)
    with disk_quota_guard(cfg=cfg, reserve_bytes=disk_write_reservation(target, new_files=0, new_directories=1),
                          extra_roots=(target,)):
        target.mkdir(exist_ok=True)


@lru_cache(maxsize=1)
def _windows_file_api():
    """Cache API bindings only; every allocation measurement still opens its file."""
    import ctypes
    from ctypes import wintypes
    class Standard(ctypes.Structure):
        _fields_ = [('allocation', ctypes.c_int64), ('end', ctypes.c_int64), ('links', wintypes.DWORD),
                    ('pending', ctypes.c_ubyte), ('directory', ctypes.c_ubyte)]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.GetFileInformationByHandleEx.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    return kernel, Standard


def _windows_allocated_bytes(path):
    import ctypes
    kernel, Standard = _windows_file_api()
    handle = kernel.CreateFileW(str(path), 0x80, 7, None, 3, 0x02000000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ResourceInspectionError(native_error=ctypes.get_last_error())
    try:
        value = Standard()
        if not kernel.GetFileInformationByHandleEx(handle, 1, ctypes.byref(value), ctypes.sizeof(value)):
            raise ResourceInspectionError(native_error=ctypes.get_last_error())
        return max(int(value.allocation), int(value.end))
    finally:
        kernel.CloseHandle(handle)
