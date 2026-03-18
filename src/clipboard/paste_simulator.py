# -*- coding: utf-8 -*-
import ctypes
import ctypes.wintypes
import time
import logging
from .writer import write_text_to_clipboard

logger = logging.getLogger(__name__)

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
VK_CONTROL = 0x11
VK_V = 0x56


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ('wVk', ctypes.wintypes.WORD),
        ('wScan', ctypes.wintypes.WORD),
        ('dwFlags', ctypes.wintypes.DWORD),
        ('time', ctypes.wintypes.DWORD),
        ('dwExtraInfo', ctypes.POINTER(ctypes.c_ulong)),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [('ki', KEYBDINPUT), ('padding', ctypes.c_byte * 28)]


class INPUT(ctypes.Structure):
    _fields_ = [('type', ctypes.wintypes.DWORD), ('union', INPUT_UNION)]


def _send_key(vk: int, flags: int = 0):
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.union.ki.wVk = vk
    inp.union.ki.dwFlags = flags
    ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))


def simulate_paste(text: str):
    write_text_to_clipboard(text)
    time.sleep(0.05)
    _send_key(VK_CONTROL)
    time.sleep(0.02)
    _send_key(VK_V)
    time.sleep(0.02)
    _send_key(VK_V, KEYEVENTF_KEYUP)
    time.sleep(0.02)
    _send_key(VK_CONTROL, KEYEVENTF_KEYUP)
    logger.debug("已模擬 Ctrl+V 貼上")
