"""Hold a process-lifetime single-instance lock without platform-only imports."""
import ctypes
import ctypes.wintypes
import errno
import logging
import os
import stat
import sys
import tempfile
from pathlib import Path

from .constants import APP_MUTEX_NAME

logger = logging.getLogger(__name__)
_mutex_handle = None
_kernel32 = None
_posix_lock = None


def _windows_api():
    # Cache the OS error before any subsequent ctypes call can overwrite it.
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.wintypes.BOOL,
                                    ctypes.wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = ctypes.wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]
    kernel32.CloseHandle.restype = ctypes.wintypes.BOOL
    return kernel32


def _posix_lock_path():
    # Never unlink on release: unlink/recreate allows two holders to lock
    # different inodes. The private directory also rejects symlink traps.
    directory = Path(tempfile.gettempdir()) / f"desktop-ocr-{os.getuid()}"
    directory.mkdir(mode=0o700, exist_ok=True)
    info = directory.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o077):
        raise PermissionError(f"Unsafe instance-lock directory: {directory}")
    return directory / "instance.lock"


def acquire_instance_lock() -> bool:
    global _mutex_handle, _kernel32, _posix_lock
    if _mutex_handle is not None or _posix_lock is not None:
        return True
    if sys.platform == "win32":
        kernel32 = _windows_api()
        ctypes.set_last_error(0)
        # Named-object lifetime prevents duplicates; mutex ownership is not
        # needed, so shutdown is independent of the original acquiring thread.
        handle = kernel32.CreateMutexW(None, False, APP_MUTEX_NAME)
        last_error = ctypes.get_last_error()
        if not handle:
            raise ctypes.WinError(last_error)
        if last_error == 183:  # ERROR_ALREADY_EXISTS
            kernel32.CloseHandle(handle)
            logger.info("已有另一個實例在執行")
            return False
        _mutex_handle, _kernel32 = handle, kernel32
    elif os.name == "posix":
        import fcntl
        fd = os.open(_posix_lock_path(), os.O_CREAT | os.O_RDWR
                     | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) & 0o077):
                raise PermissionError("Unsafe instance-lock file")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN):
                    raise
                return False
            _posix_lock = fd
            fd = None
        finally:
            if fd is not None:
                os.close(fd)
    else:
        raise RuntimeError(f"Unsupported single-instance platform: {sys.platform}")
    logger.info("已取得單實例鎖")
    return True


def release_instance_lock():
    global _mutex_handle, _kernel32, _posix_lock
    if _mutex_handle is not None:
        handle, _mutex_handle = _mutex_handle, None
        try:
            if not _kernel32.CloseHandle(handle):
                logger.warning("無法關閉單實例 handle")
        finally:
            _kernel32 = None
    if _posix_lock is not None:
        fd, _posix_lock = _posix_lock, None
        os.close(fd)


def bring_existing_to_front():
    if sys.platform != "win32":
        logger.info("非 Windows 平台不支援喚醒既有視窗")
        return
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.PostMessageW.argtypes = [ctypes.wintypes.HWND, ctypes.wintypes.UINT,
                                      ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM]
        user32.PostMessageW.restype = ctypes.wintypes.BOOL
        if not user32.PostMessageW(0xFFFF, 0x0401, 0, 0):
            raise ctypes.WinError(ctypes.get_last_error())
    except OSError as exc:
        logger.warning("喚醒已存在實例失敗: %s", exc)
