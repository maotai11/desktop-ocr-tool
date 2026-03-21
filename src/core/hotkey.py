# -*- coding: utf-8 -*-
import ctypes
import ctypes.wintypes
import logging
from PySide6.QtCore import QThread, Signal

logger = logging.getLogger(__name__)

MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_ALT = 0x0001
MOD_WIN = 0x0008
WM_HOTKEY = 0x0312

VK_MAP = {
    'A': 0x41, 'B': 0x42, 'C': 0x43, 'D': 0x44, 'E': 0x45, 'F': 0x46,
    'G': 0x47, 'H': 0x48, 'I': 0x49, 'J': 0x4A, 'K': 0x4B, 'L': 0x4C,
    'M': 0x4D, 'N': 0x4E, 'O': 0x4F, 'P': 0x50, 'Q': 0x51, 'R': 0x52,
    'S': 0x53, 'T': 0x54, 'U': 0x55, 'V': 0x56, 'W': 0x57, 'X': 0x58,
    'Y': 0x59, 'Z': 0x5A,
    'F1': 0x70, 'F2': 0x71, 'F3': 0x72, 'F4': 0x73, 'F5': 0x74,
    'F6': 0x75, 'F7': 0x76, 'F8': 0x77, 'F9': 0x78, 'F10': 0x79,
    'F11': 0x7A, 'F12': 0x7B,
    'SPACE': 0x20, 'RETURN': 0x0D, 'ESCAPE': 0x1B, 'TAB': 0x09,
    'DELETE': 0x2E, 'INSERT': 0x2D, 'HOME': 0x24, 'END': 0x23,
    '0': 0x30, '1': 0x31, '2': 0x32, '3': 0x33, '4': 0x34,
    '5': 0x35, '6': 0x36, '7': 0x37, '8': 0x38, '9': 0x39,
}


def parse_hotkey(hotkey_str: str) -> tuple:
    parts = [p.strip() for p in hotkey_str.split('+')]
    mods = 0
    vk = 0
    for p in parts:
        pu = p.upper()
        if pu in ('CTRL', 'CONTROL'):
            mods |= MOD_CONTROL
        elif pu == 'SHIFT':
            mods |= MOD_SHIFT
        elif pu == 'ALT':
            mods |= MOD_ALT
        elif pu == 'WIN':
            mods |= MOD_WIN
        else:
            vk = VK_MAP.get(pu, 0)
    return mods, vk


class HotkeyListener(QThread):
    hotkey_pressed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pending: list = []   # [(name, mods, vk), ...] — queued before thread starts
        self._hotkeys: dict = {}   # hid → name, only populated inside run()
        self._next_id = 1
        self._running = False

    def register(self, name: str, hotkey_str: str) -> bool:
        """Queue a hotkey for registration. Actual RegisterHotKey runs inside run()
        so that WM_HOTKEY messages arrive in the correct thread's message queue."""
        mods, vk = parse_hotkey(hotkey_str)
        if vk == 0:
            logger.warning(f"無效熱鍵格式: {hotkey_str}")
            return False
        self._pending.append((name, mods, vk, hotkey_str))
        return True

    def _register_pending(self):
        for name, mods, vk, hotkey_str in self._pending:
            hid = self._next_id
            self._next_id += 1
            ok = ctypes.windll.user32.RegisterHotKey(None, hid, mods, vk)
            if ok:
                self._hotkeys[hid] = name
                logger.info(f"已註冊熱鍵 [{name}] = {hotkey_str} (id={hid})")
            else:
                err = ctypes.windll.kernel32.GetLastError()
                logger.warning(f"熱鍵 [{name}]={hotkey_str} 註冊失敗 (err={err})")
        self._pending.clear()

    def unregister_all(self):
        for hid in list(self._hotkeys.keys()):
            ctypes.windll.user32.UnregisterHotKey(None, hid)
        self._hotkeys.clear()

    def run(self):
        self._running = True
        # Must register from THIS thread so WM_HOTKEY goes to this thread's queue
        self._register_pending()
        msg = ctypes.wintypes.MSG()
        while self._running:
            if ctypes.windll.user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                if msg.message == WM_HOTKEY:
                    hid = msg.wParam
                    if hid in self._hotkeys:
                        self.hotkey_pressed.emit(self._hotkeys[hid])
            self.msleep(10)
        self.unregister_all()

    def stop(self):
        self._running = False
        self.quit()
        self.wait(2000)
