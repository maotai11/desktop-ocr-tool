# -*- coding: utf-8 -*-
import sys
import logging
from .constants import APP_REGISTRY_KEY

logger = logging.getLogger(__name__)

REG_RUN_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"


def set_autostart(enabled: bool):
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_RUN_PATH, 0,
                             winreg.KEY_READ | winreg.KEY_WRITE)
        if enabled:
            exe_path = sys.executable
            winreg.SetValueEx(key, APP_REGISTRY_KEY, 0, winreg.REG_SZ, f'"{exe_path}"')
            logger.info(f"已設定開機自動啟動: {exe_path}")
        else:
            try:
                winreg.DeleteValue(key, APP_REGISTRY_KEY)
                logger.info("已移除開機自動啟動")
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
    except Exception as e:
        logger.error(f"設定開機自動啟動失敗: {e}")


def is_autostart_enabled() -> bool:
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_RUN_PATH, 0,
                             winreg.KEY_READ)
        try:
            val, _ = winreg.QueryValueEx(key, APP_REGISTRY_KEY)
            winreg.CloseKey(key)
            current = f'"{sys.executable}"'
            return val == current or val == sys.executable
        except FileNotFoundError:
            winreg.CloseKey(key)
            return False
    except Exception:
        return False
