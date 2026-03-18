# -*- coding: utf-8 -*-
import logging
from PySide6.QtWidgets import QSystemTrayIcon, QMenu, QApplication
from PySide6.QtGui import QIcon, QPixmap, QColor, QPainter, QBrush, QPen
from PySide6.QtCore import Qt

logger = logging.getLogger(__name__)


def _create_tray_icon(size: int = 32) -> QIcon:
    px = QPixmap(size, size)
    px.fill(Qt.GlobalColor.transparent)
    painter = QPainter(px)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QBrush(QColor("#4A90D9")))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(2, 2, size - 4, size - 4)
    painter.setPen(QPen(QColor("white"), 2))
    m = size // 5
    painter.drawLine(m, size // 2, size - m, size // 2)
    painter.drawLine(size // 2, m, size // 2, size - m)
    painter.end()
    return QIcon(px)


class TrayManager:
    def __init__(self, widget):
        self._widget = widget
        self._tray = QSystemTrayIcon()
        self._icon = _create_tray_icon()
        self._tray.setIcon(self._icon)
        self._tray.setToolTip("桌面OCR擷取工具")
        self._build_menu()
        self._tray.activated.connect(self._on_activated)

    def _build_menu(self):
        menu = QMenu()

        act_show = menu.addAction("顯示/隱藏 浮動窗")
        act_show.triggered.connect(self._widget.toggle_visibility)

        act_console = menu.addAction("開啟主控台")
        act_console.triggered.connect(self._widget.open_console)

        menu.addSeparator()

        act_ocr = menu.addAction("OCR 框選  (Ctrl+Shift+O)")
        act_ocr.triggered.connect(self._widget.trigger_capture_ocr)

        act_ss = menu.addAction("截圖框選  (Ctrl+Shift+S)")
        act_ss.triggered.connect(self._widget.trigger_capture_image)

        menu.addSeparator()

        self._act_pause = menu.addAction("暫停剪貼簿監聽")
        self._act_pause.setCheckable(True)
        self._act_pause.triggered.connect(self._widget.toggle_clipboard_pause)

        act_settings = menu.addAction("設定")
        act_settings.triggered.connect(self._widget.open_settings)

        menu.addSeparator()

        act_quit = menu.addAction("退出")
        act_quit.triggered.connect(QApplication.quit)

        self._tray.setContextMenu(menu)

    def _on_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._widget.toggle_visibility()
        elif reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._widget.open_console()

    def show(self):
        self._tray.show()

    def set_tooltip(self, text: str):
        self._tray.setToolTip(text)

    def show_notification(self, title: str, message: str):
        self._tray.showMessage(title, message, self._icon, 3000)
