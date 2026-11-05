# -*- coding: utf-8 -*-
"""
浮動窗 - 主要 UI 入口點（常駐右下角）
"""
import logging
import os
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLineEdit, QTextEdit, QScrollArea, QLabel, QMessageBox,
    QFrame, QMenu, QApplication, QProgressBar, QComboBox, QGraphicsOpacityEffect, QStyle
)
from PySide6.QtCore import Qt, QEvent, QUrl, QPropertyAnimation, QEasingCurve, QSize, Signal
from PySide6.QtGui import QDesktopServices, QIcon, QPixmap, QPainter, QPen, QColor, QPainterPath

from .theme import (
    _BG, _BG_RAISE, _BG_HOVER, _BORDER, _BORDER_L,
    _TEXT_PRI, _TEXT_SEC, _ACCENT, _ACCENT_H, _ACCENT_T,
    _SUCCESS, _ERROR,
    _ACCENT_10, _ACCENT_18, _ACCENT_35, _ERROR_18, _ERROR_35,
)

logger = logging.getLogger(__name__)


def _line_icon(kind: str, color: str) -> QIcon:
    """Small native vector marks; no icon font, SVG plugin or network required."""
    pixmap = QPixmap(40, 40)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(2, 2)
    pen = QPen(QColor(color), 1.5)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    if kind == 'capture':
        for x, y, dx, dy in ((3, 3, 4, 4), (17, 3, -4, 4), (3, 17, 4, -4), (17, 17, -4, -4)):
            painter.drawLine(x, y, x + dx, y)
            painter.drawLine(x, y, x, y + dy)
        painter.drawLine(7, 8, 13, 8)
        painter.drawLine(7, 12, 11, 12)
    elif kind == 'image':
        painter.drawRoundedRect(3, 4, 14, 12, 2, 2)
        painter.drawEllipse(6, 7, 2, 2)
        painter.drawLine(5, 14, 10, 10)
        painter.drawLine(10, 10, 15, 14)
    elif kind == 'pen':
        path = QPainterPath()
        path.moveTo(4, 13)
        path.lineTo(12, 5)
        path.lineTo(15, 8)
        path.lineTo(7, 16)
        path.lineTo(3, 17)
        path.closeSubpath()
        painter.drawPath(path)
        painter.drawLine(11, 6, 14, 9)
    elif kind == 'settings':
        for y, knob in ((5, 7), (10, 13), (15, 8)):
            painter.drawLine(3, y, knob - 2, y)
            painter.drawLine(knob + 2, y, 17, y)
            painter.drawEllipse(knob - 2, y - 2, 4, 4)
    elif kind == 'history':
        painter.drawArc(4, 4, 12, 12, -45 * 16, 300 * 16)
        painter.drawLine(3, 4, 3, 8)
        painter.drawLine(3, 8, 7, 8)
        painter.drawLine(10, 6, 10, 10)
        painter.drawLine(10, 10, 13, 11)
    elif kind == 'folder':
        path = QPainterPath()
        path.moveTo(3, 6)
        path.lineTo(3, 15)
        path.quadTo(3, 16, 4, 16)
        path.lineTo(16, 16)
        path.quadTo(17, 16, 17, 15)
        path.lineTo(17, 7)
        path.lineTo(10, 7)
        path.lineTo(8, 4)
        path.lineTo(4, 4)
        path.quadTo(3, 4, 3, 6)
        painter.drawPath(path)
    elif kind == 'play':
        path = QPainterPath()
        path.moveTo(7, 4)
        path.lineTo(15, 10)
        path.lineTo(7, 16)
        path.closeSubpath()
        painter.drawPath(path)
    else:
        painter.drawLine(7, 5, 7, 15)
        painter.drawLine(13, 5, 13, 15)
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)


class FloatingWidget(QWidget):
    comparison_status = Signal(str)
    shared_search_status = Signal(str)
    settings_saved = Signal()
    clipboard_pause_changed = Signal(bool)

    def __init__(self, item_repo, file_mgr, db_worker, ocr_worker,
                 cfg, data_dir, parent=None, tag_repo=None):
        super().__init__(parent)
        self._item_repo = item_repo
        self._tag_repo = tag_repo
        self._file_mgr = file_mgr
        self._db_worker = db_worker
        self._ocr_worker = ocr_worker
        self._cfg = cfg
        self.setWindowOpacity(max(0.3, min(1., float(cfg.get('ui', 'widget_opacity', default=0.95)))))
        self._data_dir = data_dir
        self._console = None
        self._refreshing_history = False
        self._handwriting = None
        self._documents = None
        self._document_controller = None
        self._documents_closing = False
        self._comparison = None
        self._comparison_controller = None
        self._comparison_overlay = None
        self._comparison_closing = False
        self._shared_search = None
        self._shared_search_controller = None
        self._shared_search_selector = None
        self._shared_search_closing = False
        self._shared_search_selection = 0
        self._shared_search_selection_signature = None
        self._shared_search_applying_selection = False
        self._clip_paused = False
        self._ocr_ready = False
        self._items = []
        self._drag_pos = None
        self._capture_ocr_callback = None
        self._capture_image_callback = None
        self._clip_watcher = None
        self._selected_item_id = None   # track selection for efficient re-styling
        self._drag_region_height = 44
        self._drag_host = None

        self._setup_ui()
        self._position_widget()
        self.refresh_list()
        # Worker mutations may originate from either window or maintenance.
        # Connect once here so an already-open console and the launcher agree.
        for name in ('items_soft_deleted', 'item_pinned', 'item_archived', 'purge_done', 'item_deleted'):
            signal = getattr(self._db_worker, name, None)
            if signal is not None:
                signal.connect(self.refresh_history_views)

    def refresh_history_views(self, *_):
        """Invalidate both views without changing their filters or recursing."""
        if self._refreshing_history:
            return
        self._refreshing_history = True
        try:
            self.refresh_list()
            if self._console is not None:
                self._console.refresh()
        finally:
            self._refreshing_history = False

    def _setup_ui(self):
        from ..core.version import APP_DISPLAY_NAME
        from ..core.branding import application_icon
        self.setWindowTitle(APP_DISPLAY_NAME)
        self.setWindowIcon(application_icon())
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumSize(320, 480)
        self.resize(348, 560)

        container = QWidget(self)
        self._drag_host = container
        container.installEventFilter(self)
        container.setObjectName("floatContainer")
        container.setStyleSheet(f"""
            QWidget#floatContainer {{
                background: {_BG}; border-radius: 16px; border: 1px solid {_BORDER_L};
            }}
            QWidget#floatContainer QLabel {{ background: transparent; border: none; }}
            QWidget#floatContainer QPushButton {{
                background: {_BG_RAISE}; color: {_TEXT_PRI}; border: 1px solid transparent;
                border-radius: 8px; padding: 5px 10px; font-size: 12px;
            }}
            QWidget#floatContainer QPushButton:hover {{ background: {_BG_HOVER}; }}
            QWidget#floatContainer QPushButton:focus {{ border: 1px solid {_ACCENT}; }}
            QWidget#floatContainer QPushButton:pressed {{ background: {_ACCENT_18}; }}
            QWidget#floatContainer QScrollBar:vertical {{
                background: transparent; width: 4px; margin: 0; border-radius: 2px;
            }}
            QWidget#floatContainer QScrollBar::handle:vertical {{
                background: {_BORDER_L}; border-radius: 2px; min-height: 24px;
            }}
            QWidget#floatContainer QScrollBar::add-line:vertical,
            QWidget#floatContainer QScrollBar::sub-line:vertical {{ height: 0; }}
            QWidget#floatContainer QScrollBar::add-page:vertical,
            QWidget#floatContainer QScrollBar::sub-page:vertical {{ background: none; }}
            QWidget#floatContainer QMenu {{
                background: {_BG_RAISE}; color: {_TEXT_PRI}; border: 1px solid {_BORDER_L};
            }}
            QWidget#floatContainer QMenu::item:selected {{ background: {_BG_HOVER}; }}
        """)
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(container)
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(16, 12, 16, 14)
        vbox.setSpacing(10)

        # The only draggable region is the quiet title row.
        top_bar = QHBoxLayout()
        top_bar.setSpacing(8)
        brand = QLabel()
        brand.setPixmap(application_icon().pixmap(28, 28))
        brand.setFixedSize(28, 28)
        title = QLabel("拾字筆")
        title.setStyleSheet(f"color: {_TEXT_PRI}; font-size: 15px; font-weight: 600;")
        for label in (brand, title):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        top_bar.addWidget(brand)
        top_bar.addWidget(title)
        top_bar.addStretch()
        self._ocr_status_lbl = QLabel("準備中")
        self._ocr_status_lbl.installEventFilter(self)
        self._ocr_status_lbl.setMaximumWidth(92)
        self._ocr_status_lbl.setStyleSheet(f"font-size: 11px; color: {_TEXT_SEC};")
        top_bar.addWidget(self._ocr_status_lbl)
        self._btn_settings = QPushButton()
        self._btn_settings.setIcon(_line_icon('settings', _TEXT_SEC))
        self._btn_settings.setFixedSize(28, 28)
        self._btn_settings.setAccessibleName("設定")
        self._btn_settings.setToolTip("設定")
        self._btn_settings.setStyleSheet("padding: 4px; background: transparent;")
        self._btn_settings.clicked.connect(self.open_settings)
        top_bar.addWidget(self._btn_settings)
        vbox.addLayout(top_bar)

        self._btn_ocr = QPushButton("擷取文字")
        self._btn_ocr.setEnabled(False)
        self._btn_ocr.setIcon(_line_icon('capture', _ACCENT_T))
        self._btn_ocr.setIconSize(QSize(20, 20))
        self._btn_ocr.setMinimumHeight(46)
        self._btn_ocr.setToolTip("框選螢幕並辨識文字 · Ctrl+Shift+O")
        self._btn_ocr.clicked.connect(self.trigger_capture_ocr)
        self._btn_ocr.setStyleSheet(f"""
            QPushButton {{
                background: {_ACCENT}; color: {_ACCENT_T}; border-radius: 10px;
                font-size: 14px; font-weight: 600; border: 1px solid transparent;
            }}
            QPushButton:hover {{ background: {_ACCENT_H}; }}
            QPushButton:pressed {{ background: {_ACCENT_H}; border-color: {_TEXT_PRI}; }}
            QPushButton:disabled {{ background: {_BG_RAISE}; color: {_TEXT_SEC}; }}
            QPushButton:focus {{ border-color: {_TEXT_PRI}; }}
        """)
        vbox.addWidget(self._btn_ocr)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self._btn_screenshot = QPushButton("截圖")
        self._btn_screenshot.setIcon(_line_icon('image', _TEXT_SEC))
        self._btn_screenshot.setMinimumHeight(34)
        self._btn_screenshot.setToolTip("框選截圖 · Ctrl+Shift+S")
        self._btn_screenshot.clicked.connect(self.trigger_capture_image)
        self._btn_handwriting = QPushButton("手寫")
        self._btn_handwriting.setIcon(_line_icon('pen', _TEXT_SEC))
        self._btn_handwriting.setMinimumHeight(34)
        self._btn_handwriting.setToolTip("寫一個字，點選候選即可複製")
        self._btn_handwriting.clicked.connect(self.open_handwriting)
        actions.addWidget(self._btn_screenshot)
        actions.addWidget(self._btn_handwriting)
        vbox.addLayout(actions)

        self._btn_documents = QPushButton("文件套版")
        self._btn_documents.setIcon(_line_icon('folder', _TEXT_SEC))
        self._btn_documents.setMinimumHeight(32)
        self._btn_documents.setToolTip("匯入 PDF／圖片，框選欄位後套用文字模板")
        self._btn_documents.clicked.connect(self.open_document_template)
        vbox.addWidget(self._btn_documents)
        self._btn_comparison = QPushButton("A／B 核對")
        self._btn_comparison.setMinimumHeight(32)
        self._btn_comparison.setToolTip("固定兩個位置持續核對，用顏色提醒不同")
        self._btn_comparison.clicked.connect(self.open_comparison)
        comparison_actions = QHBoxLayout()
        comparison_actions.setSpacing(8)
        comparison_actions.addWidget(self._btn_comparison)
        self._btn_shared_search = QPushButton("整列搜尋")
        self._btn_shared_search.setMinimumHeight(32)
        self._btn_shared_search.setToolTip("多個 A 欄位，在同一個 B 範圍找位置；支援換列與反藍追蹤")
        self._btn_shared_search.clicked.connect(self.open_shared_search)
        comparison_actions.addWidget(self._btn_shared_search)
        vbox.addLayout(comparison_actions)

        self._ocr_progress_bar = QProgressBar()
        self._ocr_progress_bar.setFixedHeight(3)
        self._ocr_progress_bar.setRange(0, 100)
        self._ocr_progress_bar.setValue(0)
        self._ocr_progress_bar.setTextVisible(False)
        self._ocr_progress_bar.setStyleSheet(f"""
            QProgressBar {{ background: {_BG_RAISE}; border: none; border-radius: 1px; }}
            QProgressBar::chunk {{ background: {_ACCENT}; border-radius: 1px; }}
        """)
        self._ocr_progress_bar.hide()
        vbox.addWidget(self._ocr_progress_bar)

        section = QHBoxLayout()
        recent_label = QLabel("最近紀錄")
        recent_label.setStyleSheet(f"font-size: 11px; color: {_TEXT_SEC};")
        section.addWidget(recent_label)
        section.addStretch()
        self._btn_batch_select = QPushButton("多選")
        self._btn_batch_select.setCheckable(True)
        self._btn_batch_select.setFixedHeight(26)
        self._btn_batch_select.setToolTip("選取多筆紀錄")
        self._btn_batch_select.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {_TEXT_SEC}; padding: 2px 6px; }}
            QPushButton:checked {{ background: {_ACCENT_18}; color: {_ACCENT}; }}
        """)
        self._btn_batch_select.clicked.connect(self._toggle_batch_mode)
        section.addWidget(self._btn_batch_select)
        self._btn_batch_action = QPushButton("操作 (0)")
        self._btn_batch_action.setFixedHeight(26)
        self._btn_batch_action.hide()
        self._btn_batch_action.setStyleSheet(f"color: {_ACCENT}; padding: 2px 6px;")
        self._btn_batch_action.clicked.connect(self._show_batch_menu)
        section.addWidget(self._btn_batch_action)
        vbox.addLayout(section)

        # Segmented filters stay in the small window; search lives in history.
        filter_shell = QWidget()
        filter_shell.setObjectName("filterShell")
        filter_shell.setStyleSheet(f"QWidget#filterShell {{ background: {_BG_RAISE}; border-radius: 9px; }}")
        filter_bar = QHBoxLayout(filter_shell)
        filter_bar.setContentsMargins(3, 3, 3, 3)
        filter_bar.setSpacing(2)
        self._filter_buttons = {}
        for label, key in [("全部", "all"), ("文字", "text"), ("圖片", "image"), ("釘選", "pinned")]:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setFixedHeight(28)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; border-radius: 6px; padding: 3px 8px;
                    font-size: 11px; color: {_TEXT_SEC}; border: 1px solid transparent;
                }}
                QPushButton:checked {{ background: {_BG_HOVER}; color: {_TEXT_PRI}; border-color: {_BORDER_L}; }}
                QPushButton:hover:!checked {{ color: {_TEXT_PRI}; background: {_BG_HOVER}; }}
                QPushButton:focus {{ border-color: {_ACCENT}; }}
            """)
            btn.clicked.connect(lambda _checked, k=key: self._set_filter(k))
            self._filter_buttons[key] = btn
            filter_bar.addWidget(btn)
        self._batch_mode = False
        self._selected_items = set()
        self._filter_buttons['all'].setChecked(True)
        self._current_filter = 'all'
        vbox.addWidget(filter_shell)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        self._list_widget = QWidget()
        self._list_widget.setStyleSheet("background: transparent;")
        self._list_layout = QVBoxLayout(self._list_widget)
        self._list_layout.setContentsMargins(0, 0, 3, 0)
        self._list_layout.setSpacing(6)
        self._list_layout.addStretch()
        self._scroll.setWidget(self._list_widget)
        vbox.addWidget(self._scroll, 1)
        # One retained effect/animation avoids accumulating objects on refresh.
        self._list_opacity = QGraphicsOpacityEffect(self._list_widget)
        self._list_opacity.setOpacity(1.0)
        self._list_widget.setGraphicsEffect(self._list_opacity)
        self._list_reveal = QPropertyAnimation(self._list_opacity, b"opacity", self)
        self._list_reveal.setDuration(160)
        self._list_reveal.setStartValue(0.45)
        self._list_reveal.setEndValue(1.0)
        self._list_reveal.setEasingCurve(QEasingCurve.Type.OutCubic)

        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background: {_BORDER}; border: none;")
        vbox.addWidget(line)
        bottom_bar = QHBoxLayout()
        bottom_bar.setSpacing(6)
        self._btn_console = QPushButton("歷史紀錄")
        self._btn_console.setIcon(_line_icon('history', _TEXT_SEC))
        self._btn_console.setToolTip("查看、搜尋及匯出紀錄")
        self._btn_console.clicked.connect(self.open_console)
        self._btn_data_dir = QPushButton("檔案位置")
        self._btn_data_dir.setIcon(_line_icon('folder', _TEXT_SEC))
        self._btn_data_dir.setToolTip("開啟截圖、附件、匯出、設定、Log 與程式暫存位置")
        self._storage_menu = QMenu(self._btn_data_dir)
        self._storage_menu.setStyleSheet(f"""
            QMenu {{
                background: {_BG_RAISE}; color: {_TEXT_PRI}; border: 1px solid {_BORDER_L};
                padding: 4px; font-size: 12px;
            }}
            QMenu::item {{ padding: 7px 24px 7px 10px; background: transparent; }}
            QMenu::item:selected {{ background: {_BG_HOVER}; }}
            QMenu::item:disabled {{ color: {_TEXT_SEC}; }}
            QMenu::separator {{ height: 1px; background: {_BORDER_L}; margin: 4px 6px; }}
        """)
        self._storage_menu.setToolTipsVisible(True)
        self._storage_attachments = QMenu("截圖與附件", self._storage_menu)
        self._storage_records = QMenu("紀錄與設定", self._storage_menu)
        self._storage_attachments.setToolTipsVisible(True)
        self._storage_records.setToolTipsVisible(True)
        self._btn_data_dir.clicked.connect(self.show_storage_locations)
        self._btn_pause = QPushButton()
        self._btn_pause.setCheckable(True)
        self._btn_pause.setFixedSize(30, 30)
        self._btn_pause.setIcon(_line_icon('pause', _TEXT_SEC))
        self._btn_pause.setAccessibleName("暫停剪貼簿紀錄")
        self._btn_pause.setToolTip("暫停剪貼簿紀錄")
        self._btn_pause.clicked.connect(self.toggle_clipboard_pause)
        self._btn_pause.setStyleSheet(f"""
            QPushButton {{ padding: 5px; background: transparent; }}
            QPushButton:checked {{ background: {_ERROR_18}; border-color: {_ERROR_35}; }}
        """)
        for button in (self._btn_console, self._btn_data_dir):
            button.setMinimumHeight(30)
            button.setStyleSheet(f"background: transparent; color: {_TEXT_SEC}; padding: 4px 3px; font-size: 11px;")
        bottom_bar.addWidget(self._btn_console)
        bottom_bar.addWidget(self._btn_data_dir)
        bottom_bar.addStretch()
        bottom_bar.addWidget(self._btn_pause)
        vbox.addLayout(bottom_bar)

    def _populate_storage_menu(self):
        from .storage_locations import get_storage_locations
        self._storage_menu.clear()
        self._storage_attachments.clear()
        self._storage_records.clear()
        self._storage_menu.addMenu(self._storage_attachments)
        attachments = self._storage_attachments
        records = None
        for location in get_storage_locations(self._data_dir, self._cfg):
            if location.key in ('captures', 'annotations', 'thumbnails'):
                host = attachments
            elif location.key in ('history', 'config'):
                if records is None:
                    self._storage_menu.addMenu(self._storage_records)
                    records = self._storage_records
                host = records
            else:
                host = self._storage_menu
            if location.key == 'runtime_temp':
                self._storage_menu.addSeparator()
            action = host.addAction(location.label)
            action.setData(location.key)
            action.setToolTip(location.tooltip)
            action.setEnabled(location.available)
            action.triggered.connect(
                lambda _checked=False, loc=location: self._open_storage_location(loc.path, loc.label)
            )

    def show_storage_locations(self):
        self._populate_storage_menu()
        self._storage_menu.popup(self._btn_data_dir.mapToGlobal(self._btn_data_dir.rect().bottomLeft()))

    def _open_storage_location(self, path, label):
        if path is None:
            return
        absolute = os.path.abspath(path)
        if not os.path.isdir(absolute):
            QMessageBox.information(self, "找不到檔案位置", f"{label}目前不存在：\n{absolute}")
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(absolute)):
            QMessageBox.information(self, "無法開啟資料夾", f"請在檔案總管開啟：\n{absolute}")

    def open_data_directory(self):
        """Compatibility entry point for opening the active history/data root."""
        self._open_storage_location(self._data_dir, "歷史資料")

    def open_handwriting(self):
        if self._handwriting is None:
            from .handwriting_window import HandwritingWindow
            self._handwriting = HandwritingWindow(self)
        self._handwriting.show()
        self._handwriting.raise_()
        self._handwriting.activateWindow()

    def shutdown_handwriting(self):
        if self._handwriting is not None:
            self._handwriting.shutdown()

    def handwriting_is_stopped(self):
        return self._handwriting is None or self._handwriting.is_stopped

    def open_document_template(self):
        if self._documents_closing:
            return
        if self._documents is None:
            from ..workers.document_worker import DocumentController
            from .document_template_window import DocumentTemplateWindow

            window = DocumentTemplateWindow(self._cfg, parent=self)
            controller = DocumentController(self._ocr_worker, parent=self)
            window.export_directory = self._file_mgr.get_export_dir()
            window.import_requested.connect(controller.import_paths)
            window.page_requested.connect(controller.request_page)
            window.recognize_requested.connect(controller.start_recognition)
            window.cancel_requested.connect(controller.cancel)
            window.clear_requested.connect(controller.clear_document)
            controller.document_loaded.connect(window.set_document_pages)
            controller.page_ready.connect(window.set_page_preview)
            controller.results_detailed.connect(window.set_recognition_results)
            controller.progress.connect(window.set_progress)
            controller.busy_changed.connect(window.set_busy)
            controller.failed.connect(window.show_error)
            self._documents, self._document_controller = window, controller
        self._documents.show()
        self._documents.raise_()
        self._documents.activateWindow()

    def shutdown_documents(self):
        self._documents_closing = True
        if self._documents is not None:
            self._documents.shutdown()
            self._documents.hide()
        if self._document_controller is not None:
            self._document_controller.shutdown()

    def documents_is_stopped(self):
        return self._document_controller is None or self._document_controller.is_stopped

    def open_comparison(self):
        if self._comparison_closing:
            return
        self._cancel_shared_search_selection()
        self.pause_shared_search()
        if self._shared_search is not None:
            self._shared_search.hide()
        if self._comparison is None:
            from ..comparison.controller import ComparisonController
            from .comparison_window import ComparisonWindow
            from .capture_overlay import CaptureOverlay
            controller = ComparisonController(self._ocr_worker, parent=self)
            # Independent top-level: hiding the floating launcher must not hide
            # the comparison status while the user works in another program.
            window = ComparisonWindow(controller)
            overlay = CaptureOverlay()
            self._comparison, self._comparison_controller = window, controller
            self._comparison_overlay = overlay
            window.select_requested.connect(self._select_comparison_region)
            window.screen_invalidated.connect(
                lambda: overlay._cancel() if window._selection_active else None)
            window.closed.connect(lambda: overlay._cancel() if window._selection_active else None)
            controller.group_changed.connect(
                lambda: overlay._cancel() if window._selection_active else None)
            overlay.cancelled.connect(lambda: window.set_selection_active(False)
                                      if not self._comparison_closing else None)
            controller.updated.connect(self._comparison_results_changed)
            controller.groups_changed.connect(self._comparison_results_changed)
            controller.state_changed.connect(self._comparison_state_changed)
            controller.failed.connect(lambda message: self.comparison_status.emit('uncertain'))
            controller.before_capture = self._comparison_can_capture
        self.hide()
        self._comparison.open()

    def _comparison_state_changed(self, state):
        if state in ('idle', 'paused'):
            self.comparison_status.emit('paused')
        elif state == 'running':
            self._comparison_results_changed()

    def _comparison_results_changed(self, *_):
        controller = self._comparison_controller
        if controller is None:
            return
        if not controller.running:
            self.comparison_status.emit('paused')
            return
        results = [group['result'] for group in controller.groups]
        verdicts = [result.get('verdict') if result else None for result in results]
        status = ('mismatch' if 'mismatch' in verdicts else
                  'running' if not any(verdicts) else
                  'uncertain' if any(verdict not in ('match', 'mismatch') for verdict in verdicts) else 'match')
        self.comparison_status.emit(status)

    def _select_comparison_region(self, name):
        if self._comparison_closing:
            return
        self.hide()
        group_id = self._comparison_controller.active_group_id
        self._comparison.set_selection_active(True)
        self._comparison_overlay.selection_prompt = f'框選 {name} 區域 · Esc 取消'

        def selected(x, y, width, height, monitor):
            if (self._comparison_closing or self._comparison._closed_by_user
                    or not self._comparison._selection_active):
                return
            if self._comparison_controller.active_group_id != group_id:
                self._comparison.set_selection_active(False)
                return
            self._comparison.set_region(name, (x, y, width, height, monitor))
            self._comparison.set_selection_active(False)

        self._comparison_overlay.start_capture(selected)

    def _comparison_can_capture(self):
        if self._comparison_closing or not self._comparison.can_capture():
            return False
        # Other InkPick windows can obscure source pixels as well. Check them
        # before every cycle; never silently compare our own tool over a target.
        from PySide6.QtCore import QRect
        screen = QApplication.primaryScreen()
        if screen is None:
            return False
        origin, dpr = screen.geometry().topLeft(), screen.devicePixelRatio()
        regions = [QRect(*rect[:4]) for rect in self._comparison_controller.target_regions]
        for window in QApplication.topLevelWidgets():
            if window is self._comparison or not window.isVisible() or window.isMinimized():
                continue
            frame = window.frameGeometry()
            bounds = QRect(round((frame.x() - origin.x()) * dpr),
                           round((frame.y() - origin.y()) * dpr),
                           round(frame.width() * dpr), round(frame.height() * dpr))
            if any(bounds.intersects(region) for region in regions):
                return False
        return True

    def pause_comparison(self):
        if self._comparison_controller is not None:
            self._comparison_controller.pause()

    def shutdown_comparison(self):
        self._comparison_closing = True
        if self._comparison_overlay is not None:
            self._comparison_overlay._cancel()
        if self._comparison_controller is not None:
            self._comparison_controller.shutdown()
        if self._comparison is not None:
            self._comparison.shutdown()

    def comparison_is_stopped(self):
        return self._comparison_controller is None or self._comparison_controller.is_stopped

    def open_shared_search(self):
        """One session-only search tool, sharing the existing serialized OCR worker."""
        if self._shared_search_closing:
            return
        if self._comparison_overlay is not None and self._comparison._selection_active:
            self._comparison_overlay._cancel()
        self.pause_comparison()
        if self._comparison is not None:
            self._comparison.hide()
        if self._shared_search is None:
            from ..comparison.shared_controller import SharedSearchController
            from .shared_search_window import SharedSearchWindow
            from .capture_overlay import CaptureOverlay
            controller = SharedSearchController(self._ocr_worker, parent=self)
            window = SharedSearchWindow(controller)
            selector = CaptureOverlay()
            self._shared_search = window
            self._shared_search_controller = controller
            self._shared_search_selector = selector
            window.select_requested.connect(self._select_shared_search_region)
            window.screen_invalidated.connect(self._cancel_shared_search_selection)
            window.closed.connect(self._cancel_shared_search_selection)
            selector.cancelled.connect(self._shared_search_selection_cancelled)
            controller.fields_changed.connect(self._shared_search_fields_changed)
            self.settings_saved.connect(controller.invalidate_cache)
            controller.updated.connect(self._shared_search_results_changed)
            controller.state_changed.connect(self._shared_search_results_changed)
            controller.failed.connect(lambda _: self.shared_search_status.emit('uncertain'))
            controller.before_capture = self._shared_search_can_capture
            controller.after_capture = window.after_capture
        self.hide()
        self._shared_search.open()

    def _shared_search_region_signature(self):
        controller = self._shared_search_controller
        if controller is None:
            return None
        return (tuple((field['id'], field.get('region')) for field in controller.fields),
                controller.b_region, controller.tracking_region, controller.row_pitch)

    def _shared_search_fields_changed(self):
        window = self._shared_search
        if (window is not None and window._selection_active
                and not self._shared_search_applying_selection
                and self._shared_search_selection_signature != self._shared_search_region_signature()):
            self._cancel_shared_search_selection()

    def _cancel_shared_search_selection(self):
        self._shared_search_selection += 1
        self._shared_search_selection_signature = None
        if self._shared_search_selector is not None:
            self._shared_search_selector._cancel()

    def _shared_search_selection_cancelled(self):
        self._shared_search_selection += 1
        self._shared_search_selection_signature = None
        if self._shared_search is not None and not self._shared_search_closing:
            self._shared_search.set_selection_active(False)

    def _select_shared_search_region(self, name):
        if self._shared_search_closing or self._shared_search is None:
            return
        controller, window = self._shared_search_controller, self._shared_search
        if name not in ('B', 'TRACK') and name not in {field['id'] for field in controller.fields}:
            return
        self._cancel_shared_search_selection()
        token = self._shared_search_selection
        self.hide()
        window.set_selection_active(True)
        self._shared_search_selection_signature = self._shared_search_region_signature()
        label = ('B 搜尋範圍' if name == 'B' else '表格追蹤範圍' if name == 'TRACK' else
                 next(field['name'] for field in controller.fields if field['id'] == name))
        self._shared_search_selector.selection_prompt = f'框選 {label} · Esc 取消'

        def selected(x, y, width, height, monitor):
            if (self._shared_search_closing or token != self._shared_search_selection
                    or getattr(window, '_closed_by_user', False) or not window._selection_active
                    or self._shared_search_selection_signature != self._shared_search_region_signature()):
                return
            if name not in ('B', 'TRACK') and name not in {field['id'] for field in controller.fields}:
                return
            self._shared_search_applying_selection = True
            try:
                window.set_region(name, (x, y, width, height, monitor))
            finally:
                self._shared_search_applying_selection = False
                self._shared_search_selection_signature = None
                window.set_selection_active(False)

        self._shared_search_selector.start_capture(selected)

    def _shared_search_can_capture(self):
        window, controller = self._shared_search, self._shared_search_controller
        if self._shared_search_closing or window is None or not window.can_capture():
            return False
        from PySide6.QtCore import QRect
        screen = QApplication.primaryScreen()
        if screen is None:
            return False
        origin, dpr = screen.geometry().topLeft(), screen.devicePixelRatio()
        regions = [QRect(*region[:4]) for region in controller.target_regions]
        for other in QApplication.topLevelWidgets():
            if (other is window or other is window.highlight_overlay
                    or not other.isVisible() or other.isMinimized()):
                continue
            frame = other.frameGeometry()
            bounds = QRect(round((frame.x() - origin.x()) * dpr),
                           round((frame.y() - origin.y()) * dpr),
                           round(frame.width() * dpr), round(frame.height() * dpr))
            if any(bounds.intersects(region) for region in regions):
                return False
        return window.before_capture()

    def _shared_search_results_changed(self, *_):
        controller = self._shared_search_controller
        if controller is None or not controller.running:
            self.shared_search_status.emit('paused')
            return
        states = [(field.get('result') or {}).get('status') for field in controller.fields]
        self.shared_search_status.emit('missing' if 'missing' in states else
                                       'uncertain' if any(state not in ('matched',) for state in states)
                                       else 'matched')

    def pause_shared_search(self):
        if self._shared_search is not None:
            self._shared_search.pause()
        elif self._shared_search_controller is not None:
            self._shared_search_controller.pause()

    def shutdown_shared_search(self):
        self._shared_search_closing = True
        self._cancel_shared_search_selection()
        if self._shared_search_controller is not None:
            self._shared_search_controller.shutdown()
        if self._shared_search is not None:
            self._shared_search.shutdown()

    def shared_search_is_stopped(self):
        return self._shared_search_controller is None or self._shared_search_controller.is_stopped

    def _position_widget(self):
        pos_mode = self._cfg.get('ui', 'widget_position', default='remember')
        if pos_mode == 'remember':
            saved_x = self._cfg.get('ui', 'widget_saved_x', default=None)
            saved_y = self._cfg.get('ui', 'widget_saved_y', default=None)
            if saved_x is not None and saved_y is not None:
                sx, sy = int(saved_x), int(saved_y)
                # Validate position is visible on at least one screen (multi-monitor safety)
                cx, cy = sx + self.width() // 2, sy + self.height() // 2
                for screen in QApplication.screens():
                    if screen.availableGeometry().contains(cx, cy):
                        self.move(sx, sy)
                        return
        screen = QApplication.primaryScreen().availableGeometry()
        x = screen.right() - self.width() - 20
        y = screen.bottom() - self.height() - 40
        self.move(x, y)

    # --- Drag to move ---
    def _is_interactive_widget(self, widget) -> bool:
        return isinstance(
            widget,
            (QPushButton, QLineEdit, QTextEdit, QComboBox, QScrollArea, QProgressBar),
        )

    def _persist_widget_position(self):
        if self._cfg.get('ui', 'widget_position', default='remember') == 'remember':
            pos = self.pos()
            self._cfg.set('ui', 'widget_saved_x', pos.x())
            self._cfg.set('ui', 'widget_saved_y', pos.y())

    def eventFilter(self, watched, event):
        drag_targets = tuple(
            target
            for target in (self._drag_host, getattr(self, "_ocr_status_lbl", None))
            if target is not None
        )
        if watched in drag_targets:
            if event.type() == QEvent.Type.MouseButtonPress:
                local_pos = event.position().toPoint()
                child = watched.childAt(local_pos)
                if (
                    event.button() == Qt.MouseButton.LeftButton
                    and local_pos.y() <= self._drag_region_height
                    and not self._is_interactive_widget(child)
                ):
                    self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                    event.accept()
                    return True
            elif event.type() == QEvent.Type.MouseMove:
                if self._drag_pos and event.buttons() & Qt.MouseButton.LeftButton:
                    self.move(event.globalPosition().toPoint() - self._drag_pos)
                    event.accept()
                    return True
            elif event.type() == QEvent.Type.MouseButtonRelease:
                if self._drag_pos:
                    self._drag_pos = None
                    self._persist_widget_position()
                    event.accept()
                    return True
        return super().eventFilter(watched, event)

    # --- Single-instance bring-to-front ---
    def nativeEvent(self, eventType, message):
        if eventType == b'windows_generic_MSG':
            import ctypes
            from ctypes import wintypes
            class _MSG(ctypes.Structure):
                _fields_ = [
                    ('hwnd', wintypes.HWND),
                    ('message', wintypes.UINT),
                    ('wParam', wintypes.WPARAM),
                    ('lParam', wintypes.LPARAM),
                    ('time', wintypes.DWORD),
                    ('pt', wintypes.POINT),
                ]
            msg = _MSG.from_address(int(message))
            if msg.message == 0x0401:  # WM_USER + 1
                self.show()
                self.raise_()
                self.activateWindow()
                return True, 0
        return super().nativeEvent(eventType, message)

    # --- Filter ---
    def _set_filter(self, key: str):
        self._current_filter = key
        for k, btn in self._filter_buttons.items():
            btn.setChecked(k == key)
        self.refresh_list()
        self._list_reveal.stop()
        animate = self.style().styleHint(QStyle.StyleHint.SH_Widget_Animate, None, self)
        duration = self.style().styleHint(QStyle.StyleHint.SH_Widget_Animation_Duration, None, self)
        if self.isVisible() and animate and duration > 0:
            self._list_reveal.setDuration(min(160, duration))
            self._list_reveal.start()
        else:
            self._list_opacity.setOpacity(1.0)

    # --- Batch operations ---
    def _toggle_batch_mode(self):
        self._batch_mode = self._btn_batch_select.isChecked()
        self._selected_items.clear()
        self._btn_batch_select.setText("完成" if self._batch_mode else "多選")
        self._btn_batch_action.setVisible(self._batch_mode)
        self._update_batch_button_text()
        self.refresh_list()

    def _update_batch_button_text(self):
        count = len(self._selected_items)
        self._btn_batch_action.setText(f"操作 ({count})" if count > 0 else "操作 (0)")
        self._btn_batch_action.setEnabled(count > 0)

    def _show_batch_menu(self):
        if not self._selected_items:
            return
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        menu.addAction(f"已選取 {len(self._selected_items)} 個項目").setEnabled(False)
        menu.addSeparator()
        menu.addAction("📋 批量複製").triggered.connect(self._batch_copy)
        menu.addAction("🗑️ 批量刪除").triggered.connect(self._batch_delete)
        menu.addSeparator()
        menu.addAction("全選").triggered.connect(self._select_all)
        menu.addAction("取消全選").triggered.connect(self._deselect_all)
        menu.exec(self._btn_batch_action.mapToGlobal(self._btn_batch_action.rect().bottomLeft()))

    def _batch_copy(self):
        texts = []
        for item_id in self._selected_items:
            item = self._item_repo.get_by_id(item_id)
            if item:
                text = item.get_effective_text()
                if text:
                    texts.append(text)
        if texts:
            from ..clipboard.writer import copy_text_batch
            copy_text_batch(texts, self._cfg)
        self._selected_items.clear()
        self._update_batch_button_text()
        self.refresh_list()

    def _batch_delete(self):
        from PySide6.QtWidgets import QMessageBox
        reply = QMessageBox.question(
            self, "確認批量刪除",
            f"確定要刪除 {len(self._selected_items)} 個項目嗎？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            for item_id in list(self._selected_items):
                self._item_repo.soft_delete(item_id)
            self._selected_items.clear()
            self._update_batch_button_text()
            self.refresh_history_views()

    def _select_all(self):
        for item in self._items:
            self._selected_items.add(item.id)
        self._update_batch_button_text()
        self.refresh_list()

    def _deselect_all(self):
        self._selected_items.clear()
        self._update_batch_button_text()
        self.refresh_list()

    def _handle_card_click(self, item_id: int):
        if self._batch_mode:
            self._toggle_item_selection(item_id)
            return

        click_action = self._cfg.get('ui', 'widget_click_action', default='select')
        if click_action == 'copy':
            self._on_item_copy(item_id)
        else:
            self._on_item_select(item_id)

    def _handle_card_double_click(self, item_id: int):
        if self._batch_mode:
            return

        click_action = self._cfg.get('ui', 'widget_click_action', default='select')
        if click_action == 'copy':
            self._on_item_select(item_id)
        else:
            self._on_item_copy(item_id)

    def _toggle_item_selection(self, item_id: int):
        if item_id in self._selected_items:
            self._selected_items.remove(item_id)
        else:
            self._selected_items.add(item_id)
        self._update_batch_button_text()
        self._restyle_cards()

    def _card_style(self, idx: int, item_id: int) -> str:
        if self._batch_mode and item_id in self._selected_items:
            return (
                f"background: {_ACCENT_18}; border-radius: 8px;"
                f" border: 1px solid {_ACCENT_35};"
            )
        if not self._batch_mode and self._selected_item_id == item_id:
            return (
                f"background: {_ACCENT_10}; border-radius: 8px;"
                f" border: 1px solid {_ACCENT_35};"
            )
        bg = _BG_RAISE if idx % 2 == 0 else _BG
        return (
            f"background: {bg}; border-radius: 8px;"
            f" border: 1px solid transparent;"
        )

    def _restyle_cards(self):
        card_idx = 0
        for i in range(self._list_layout.count()):
            widget = self._list_layout.itemAt(i).widget()
            item_id = None if widget is None else widget.property('item_id')
            if widget is None or item_id is None:
                continue
            widget.setStyleSheet(self._card_style(card_idx, item_id))
            card_idx += 1

    # --- List population ---
    def refresh_list(self, search_text: str = None):
        if self._batch_mode:
            self._selected_item_id = None
        # Clear items (keep stretch at end)
        while self._list_layout.count() > 1:
            item = self._list_layout.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()

        max_items = self._cfg.get('ui', 'widget_max_items', default=20)

        try:
            if search_text and len(search_text) >= 2:
                items = self._item_repo.search_fulltext(search_text, limit=max_items)
            elif self._current_filter == 'pinned':
                items = self._item_repo.list_recent(limit=max_items, pinned_only=True)
            elif self._current_filter == 'text':
                items = self._item_repo.list_recent(limit=max_items, item_type='text')
            elif self._current_filter == 'image':
                items = self._item_repo.list_recent(limit=max_items, item_type='image')
            else:
                items = self._item_repo.list_recent(limit=max_items)

            self._items = items
            present_ids = {item.id for item in items}
            self._selected_items.intersection_update(present_ids)
            if self._selected_item_id not in present_ids:
                self._selected_item_id = None
            self._update_batch_button_text()

            if not items:
                lbl = QLabel("尚無紀錄")
                lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                lbl.setStyleSheet(
                    f"color: {_TEXT_SEC}; font-size: 12px; padding: 24px;"
                    " background: transparent;"
                )
                self._list_layout.insertWidget(0, lbl)
                return

            from .components.item_card import ItemCard
            for idx, item in enumerate(items):
                card = ItemCard(item, self._data_dir, parent=self._list_widget)
                card.setProperty('item_id', item.id)
                card.setStyleSheet(self._card_style(idx, item.id))

                card.clicked.connect(self._handle_card_click)
                card.double_clicked.connect(self._handle_card_double_click)

                card.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                card.customContextMenuRequested.connect(
                    lambda pos, i=item, c=card: self._show_item_menu(pos, i, c)
                )
                self._list_layout.insertWidget(idx, card)
        except Exception as e:
            logger.error(f"refresh_list 失敗: {e}", exc_info=True)

    def _on_item_select(self, item_id: int):
        self._selected_item_id = item_id
        self._restyle_cards()

    def _on_item_copy(self, item_id: int):
        item = self._item_repo.get_by_id(item_id)
        if not item:
            return
        text = item.get_effective_text()
        if text:
            from ..clipboard.writer import copy_text
            if copy_text(text, self._cfg):
                logger.info(f"已複製 item #{item_id}")

    def _show_item_menu(self, pos, item, card):
        menu = QMenu(self)

        # 複製文字
        act_copy_text = menu.addAction("複製文字")
        act_copy_text.setEnabled(bool(item.get_effective_text()))
        act_copy_text.triggered.connect(lambda: self._on_item_copy(item.id))

        # 複製圖片（image / mixed）
        act_copy_img = menu.addAction("複製圖片")
        act_copy_img.setEnabled(
            item.item_type in ('image', 'mixed') and bool(item.raw_image_path)
        )
        act_copy_img.triggered.connect(lambda: self._copy_item_image(item))

        # 另存圖片
        act_save_img = menu.addAction("另存圖片...")
        act_save_img.setEnabled(
            item.item_type in ('image', 'mixed') and bool(item.raw_image_path)
        )
        act_save_img.triggered.connect(lambda: self._save_item_image(item))

        menu.addSeparator()

        # 編輯（開啟 EditorWindow）
        menu.addAction("編輯").triggered.connect(lambda: self._open_editor(item))

        menu.addSeparator()

        pin_text = "取消釘選" if item.is_pinned else "釘選"
        menu.addAction(pin_text).triggered.connect(
            lambda: (self._item_repo.set_pinned(item.id, not item.is_pinned),
                     self.refresh_history_views())
        )

        act_ocr = menu.addAction("重新執行 OCR")
        act_ocr.setEnabled(self._ocr_ready and bool(item.raw_image_path))
        act_ocr.triggered.connect(lambda: self._rerun_ocr(item))

        menu.addSeparator()
        menu.addAction("刪除").triggered.connect(lambda: self._soft_delete(item.id))
        menu.exec(card.mapToGlobal(pos))

    def _soft_delete(self, item_id: int):
        self._item_repo.soft_delete(item_id)
        self.refresh_history_views()

    def _copy_item_image(self, item):
        if not item.raw_image_path:
            return
        abs_p = self._file_mgr.get_abs_path(item.raw_image_path)
        from ..clipboard.writer import write_image_to_clipboard
        write_image_to_clipboard(abs_p)

    def _save_item_image(self, item):
        if not item.raw_image_path:
            return
        from PySide6.QtWidgets import QFileDialog
        src = self._file_mgr.get_abs_path(item.raw_image_path)
        ext = os.path.splitext(src)[1] or '.png'
        dest, _ = QFileDialog.getSaveFileName(
            self, "另存圖片", f"capture_{item.id}{ext}",
            "圖片 (*.png *.jpg *.bmp);;所有檔案 (*)"
        )
        if dest:
            import shutil
            shutil.copy2(src, dest)

    def _open_editor(self, item):
        if not hasattr(self, '_editor_windows'):
            self._editor_windows = {}
        if item.id in self._editor_windows:
            w = self._editor_windows[item.id]
            w.raise_()
            w.activateWindow()
            return
        from .editor_window import EditorWindow
        editor = EditorWindow(
            item=item,
            item_repo=self._item_repo,
            file_mgr=self._file_mgr,
            ocr_worker=self._ocr_worker,
        )
        editor.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        editor.item_updated.connect(self.refresh_history_views)
        editor.destroyed.connect(
            lambda: self._editor_windows.pop(item.id, None)
        )
        self._editor_windows[item.id] = editor
        editor.show()

    def _rerun_ocr(self, item):
        if item.raw_image_path:
            abs_path = self._file_mgr.get_abs_path(item.raw_image_path)
            self._ocr_worker.queue_ocr(item.id, abs_path, 'screen')
            self.refresh_history_views()

    # --- Visibility ---
    def toggle_visibility(self):
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.raise_()

    # --- Windows ---
    def open_console(self):
        if self._console is None:
            from .main_window import MainWindow
            self._console = MainWindow(
                item_repo=self._item_repo,
                tag_repo=self._tag_repo,
                file_mgr=self._file_mgr,
                db_worker=self._db_worker,
                ocr_worker=self._ocr_worker,
                cfg=self._cfg,
                data_dir=self._data_dir,
            )
            self._console.use_shared_history_refresh()
            self._console.history_changed.connect(self.refresh_history_views)
            self._console.destroyed.connect(
                lambda: setattr(self, '_console', None)
            )
        self._console.show()
        self._console.raise_()
        self._console.activateWindow()

    def open_settings(self):
        from .settings_dialog import SettingsDialog
        from PySide6.QtWidgets import QDialog
        ocr_engine = getattr(self, '_ocr_engine', None)
        tag_repo = getattr(self, '_tag_repo', None)
        dlg = SettingsDialog(self._cfg, self, ocr_engine=ocr_engine, tag_repo=tag_repo)
        dlg.history_changed.connect(self.refresh_history_views)
        try:
            if dlg.exec() == QDialog.DialogCode.Accepted:
                self.settings_saved.emit()
                self.refresh_history_views()
        finally:
            dlg.deleteLater()

    # --- Capture callbacks ---
    def set_capture_callbacks(self, ocr_cb, image_cb):
        self._capture_ocr_callback = ocr_cb
        self._capture_image_callback = image_cb

    def trigger_capture_ocr(self):
        if self._capture_ocr_callback:
            self._capture_ocr_callback()

    def trigger_capture_image(self):
        if self._capture_image_callback:
            self._capture_image_callback()

    # --- Clipboard ---
    def focus_search(self):
        self.open_console()
        self._console._search.setFocus()
        self._console._search.selectAll()

    def toggle_clipboard_pause(self):
        self._clip_paused = not self._clip_paused
        self._btn_pause.setChecked(self._clip_paused)
        if self._clip_watcher:
            self._clip_watcher.pause(self._clip_paused)
        label = "繼續剪貼簿紀錄" if self._clip_paused else "暫停剪貼簿紀錄"
        self._btn_pause.setAccessibleName(label)
        self._btn_pause.setToolTip(label)
        self._btn_pause.setIcon(_line_icon('play' if self._clip_paused else 'pause', _TEXT_SEC))
        self.clipboard_pause_changed.emit(self._clip_paused)

    def paste_last_item(self):
        items = self._item_repo.list_recent(limit=1)
        if items:
            text = items[0].get_effective_text()
            from ..clipboard.text_cleanup import prepare_copy_text
            text = prepare_copy_text(text or '', self._cfg)
            if text and text.strip():
                from ..clipboard.paste_simulator import simulate_paste
                simulate_paste(text)

    # --- Status label (public slot used by app.py) ---
    def set_ocr_status(self, text: str):
        # Keep detailed diagnostics available without crowding the title bar.
        if '未加入佇列' in text:
            label = "無法辨識"
        elif '失敗' in text:
            label = "請重試"
        elif '未找到文字' in text:
            label = "未找到文字"
        elif '待確認' in text:
            label = "請核對文字"
        elif '完成' in text and '關閉' not in text:
            label = "辨識完成"
        elif '關閉' in text:
            label = "正在關閉"
        elif '辨識' in text or 'OCR' in text:
            label = "辨識中"
        else:
            label = self._ocr_status_lbl.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, 90)
        self._ocr_status_lbl.setText(label)
        self._ocr_status_lbl.setToolTip(text)

    def set_ocr_progress(self, pct: int):
        """更新 OCR 進度條。"""
        if 0 < pct < 100:
            self._ocr_progress_bar.show()
            self._ocr_progress_bar.setValue(pct)
        else:
            self._ocr_progress_bar.hide()

    # --- OCR engine status ---
    def on_ocr_engine_progress(self, pct: int, msg: str):
        self._ocr_status_lbl.setText(f"準備中 {pct}%")
        self._ocr_status_lbl.setToolTip(msg)
        # 顯示進度條
        self._ocr_progress_bar.show()
        self._ocr_progress_bar.setValue(pct)

    def on_ocr_engine_ready(self):
        self._ocr_ready = True
        self._btn_ocr.setEnabled(True)
        self._ocr_status_lbl.setText("可擷取文字")
        self._ocr_status_lbl.setToolTip("離線辨識已就緒")
        self._ocr_status_lbl.setStyleSheet(
            f"font-size: 11px; color: {_SUCCESS}; background: transparent;"
        )
        # 隱藏進度條
        self._ocr_progress_bar.hide()
        logger.info("OCR 引擎就緒，按鈕已啟用")

    def on_ocr_engine_failed(self, error: str):
        self._ocr_ready = False
        self._btn_ocr.setEnabled(False)
        self._ocr_status_lbl.setText("暫時無法辨識")
        self._ocr_status_lbl.setStyleSheet(
            f"font-size: 11px; color: {_ERROR}; background: transparent;"
        )
        # 隱藏進度條
        self._ocr_progress_bar.hide()
        QMessageBox.warning(
            self, "暫時無法辨識文字",
            f"請重新開啟拾字筆後再試。你仍可使用截圖與歷史紀錄。\n\n詳細資訊：{error}"
        )
