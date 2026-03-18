# -*- coding: utf-8 -*-
from PySide6.QtCore import QObject, Signal


class AppSignals(QObject):
    capture_started = Signal()
    capture_finished = Signal(str)
    capture_cancelled = Signal()

    ocr_engine_loading = Signal()
    ocr_engine_ready = Signal()
    ocr_engine_failed = Signal(str)
    ocr_started = Signal(int)
    ocr_finished = Signal(int, str, float)
    ocr_failed = Signal(int, str)

    item_saved = Signal(int)
    item_updated = Signal(int)
    item_deleted = Signal(int)

    clipboard_item_captured = Signal()

    show_widget = Signal()
    hide_widget = Signal()
    toggle_widget = Signal()
    open_console = Signal()
    show_notification = Signal(str, str)


_signals: "AppSignals | None" = None


def get_signals() -> AppSignals:
    global _signals
    if _signals is None:
        _signals = AppSignals()
    return _signals
