# -*- coding: utf-8 -*-
import logging
from PySide6.QtWidgets import QSystemTrayIcon, QMenu, QApplication
from PySide6.QtGui import QIcon, QPixmap, QColor, QPainter, QBrush, QPen
from PySide6.QtCore import Qt, QSignalBlocker

logger = logging.getLogger(__name__)


def _create_tray_icon(size: int = 32) -> QIcon:
    from ..core.branding import application_icon
    return application_icon()


class TrayManager:
    def __init__(self, widget):
        self._widget = widget
        self._tray = QSystemTrayIcon()
        self._icon = _create_tray_icon()
        self._tray.setIcon(self._icon)
        from ..core.version import APP_DISPLAY_NAME
        self._tray.setToolTip(APP_DISPLAY_NAME)
        self._build_menu()
        self._tray.activated.connect(self._on_activated)
        if hasattr(widget, 'comparison_status'):
            widget.comparison_status.connect(self.set_comparison_status)
        if hasattr(widget, 'shared_search_status'):
            widget.shared_search_status.connect(self.set_shared_search_status)
        if hasattr(widget, 'clipboard_pause_changed'):
            widget.clipboard_pause_changed.connect(self.set_clipboard_paused)
        self.set_clipboard_paused(bool(getattr(widget, '_clip_paused', False)))

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

        act_handwriting = menu.addAction('手寫')
        act_handwriting.triggered.connect(self._widget.open_handwriting)

        act_documents = menu.addAction('文件套版（PDF／圖片）')
        act_documents.triggered.connect(self._widget.open_document_template)
        act_comparison = menu.addAction('A／B 核對')
        act_comparison.triggered.connect(self._widget.open_comparison)
        act_comparison_pause = menu.addAction('暫停 A／B 核對')
        act_comparison_pause.triggered.connect(self._widget.pause_comparison)
        if hasattr(self._widget, 'open_shared_search'):
            act_search = menu.addAction('整列搜尋（多 A／共用 B）')
            act_search.triggered.connect(self._widget.open_shared_search)
            act_search_pause = menu.addAction('暫停整列搜尋')
            act_search_pause.triggered.connect(self._widget.pause_shared_search)

        menu.addSeparator()

        self._act_pause = menu.addAction("暫停剪貼簿監聽")
        self._act_pause.setCheckable(True)
        self._act_pause.triggered.connect(self._widget.toggle_clipboard_pause)

        act_settings = menu.addAction("設定")
        act_settings.triggered.connect(self._widget.open_settings)

        menu.addSeparator()

        act_quit = menu.addAction("退出")
        self._quit_action = act_quit

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

    def set_clipboard_paused(self, paused):
        with QSignalBlocker(self._act_pause):
            self._act_pause.setChecked(bool(paused))

    def set_comparison_status(self, status):
        from ..core.version import APP_DISPLAY_NAME
        colors = {'match': '#4ade80', 'mismatch': '#fb7185', 'uncertain': '#fbbf24'}
        labels = {'match': '文字一致', 'mismatch': '文字不同', 'uncertain': '待確認', 'paused': '已暫停'}
        self._tray.setToolTip(f'{APP_DISPLAY_NAME} · A／B 核對：{labels.get(status, "核對中")}')
        self._set_status_badge(colors.get(status))

    def set_shared_search_status(self, status):
        from ..core.version import APP_DISPLAY_NAME
        labels = {'matched': '已找到', 'missing': '尚有未找到',
                  'uncertain': '待確認', 'paused': '已暫停'}
        self._tray.setToolTip(f'{APP_DISPLAY_NAME} · 整列搜尋：{labels.get(status, "搜尋中")}')
        self._set_status_badge({'matched': '#4ade80', 'missing': '#fb7185',
                               'uncertain': '#fbbf24'}.get(status))

    def _set_status_badge(self, color):
        if color is None:
            self._tray.setIcon(self._icon)
            return
        pixmap = self._icon.pixmap(32, 32)
        painter = QPainter(pixmap)
        painter.setPen(QPen(QColor('#17202b'), 2))
        painter.setBrush(QColor(color))
        painter.drawEllipse(19, 19, 12, 12)
        painter.end()
        self._tray.setIcon(QIcon(pixmap))

    def show_notification(self, title: str, message: str):
        self._tray.showMessage(title, message, self._icon, 3000)

    def set_quit_callback(self, callback):
        self._quit_action.triggered.connect(callback)

    def is_available(self):
        return QSystemTrayIcon.isSystemTrayAvailable()
