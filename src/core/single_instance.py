# -*- coding: utf-8 -*-
import ctypes
import logging
from .constants import APP_MUTEX_NAME

logger = logging.getLogger(__name__)
_mutex_handle = None


def acquire_instance_lock() -> bool:
    global _mutex_handle
    kernel32 = ctypes.windll.kernel32
    _mutex_handle = kernel32.CreateMutexW(None, True, APP_MUTEX_NAME)
    last_err = kernel32.GetLastError()
    if last_err == 183:  # ERROR_ALREADY_EXISTS
        logger.info("已有另一個實例在執行")
        if _mutex_handle:
            kernel32.CloseHandle(_mutex_handle)
            _mutex_handle = None
        return False
    logger.info("已取得單實例鎖")
    return True


def release_instance_lock():
    global _mutex_handle
    if _mutex_handle:
        ctypes.windll.kernel32.ReleaseMutex(_mutex_handle)
        ctypes.windll.kernel32.CloseHandle(_mutex_handle)
        _mutex_handle = None
        logger.info("已釋放單實例鎖")


def bring_existing_to_front():
    try:
        WM_USER = 0x0400
        WM_SHOW_INSTANCE = WM_USER + 1
        HWND_BROADCAST = 0xFFFF
        ctypes.windll.user32.PostMessageW(HWND_BROADCAST, WM_SHOW_INSTANCE, 0, 0)
    except Exception as e:
        logger.warning(f"喚醒已存在實例失敗: {e}")
