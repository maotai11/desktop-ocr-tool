# -*- coding: utf-8 -*-
"""
浮動窗 - 主要 UI 入口點（常駐右下角）
"""
import logging
import os
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLineEdit, QScrollArea, QLabel, QMessageBox,
    QFrame, QMenu, QApplication
)
from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QCursor

from .theme import (
    _BG, _BG_RAISE, _BG_HOVER, _BORDER, _BORDER_L,
    _TEXT_PRI, _TEXT_SEC, _ACCENT, _ACCENT_H, _ACCENT_T,
    _TEAL, _TEAL_H, _SUCCESS, _ERROR,
    _ACCENT_10, _ACCENT_18, _ACCENT_35, _ERROR_18, _ERROR_35,
)

logger = logging.getLogger(__name__)


class FloatingWidget(QWidget):
    def __init__(self, item_repo, file_mgr, db_worker, ocr_worker,
                 cfg, data_dir, parent=None):
        super().__init__(parent)
        self._item_repo = item_repo
        self._file_mgr = file_mgr
        self._db_worker = db_worker
        self._ocr_worker = ocr_worker
        self._cfg = cfg
        self._data_dir = data_dir
        self._console = None
        self._clip_paused = False
        self._ocr_ready = False
        self._items = []
        self._drag_pos = None
        self._capture_ocr_callback = None
        self._capture_image_callback = None
        self._clip_watcher = None
        self._selected_item_id = None   # track selection for efficient re-styling

        self._setup_ui()
        self._position_widget()
        self.refresh_list()

    def _setup_ui(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumSize(320, 480)
        self.resize(320, 480)

        container = QWidget(self)
        container.setObjectName("floatContainer")
        container.setStyleSheet(f"""
            QWidget#floatContainer {{
                background: {_BG};
                border-radius: 12px;
                border: 1px solid {_BORDER};
            }}
            QWidget#floatContainer QScrollBar:vertical {{
                background: {_BG};
                width: 4px;
                margin: 0;
                border-radius: 2px;
            }}
            QWidget#floatContainer QScrollBar::handle:vertical {{
                background: {_BORDER_L};
                border-radius: 2px;
                min-height: 24px;
            }}
            QWidget#floatContainer QScrollBar::add-line:vertical,
            QWidget#floatContainer QScrollBar::sub-line:vertical {{
                height: 0;
            }}
            QWidget#floatContainer QScrollBar::add-page:vertical,
            QWidget#floatContainer QScrollBar::sub-page:vertical {{
                background: none;
            }}
        """)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(container)

        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(10, 10, 10, 10)
        vbox.setSpacing(6)

        # --- Top bar ---
        top_bar = QHBoxLayout()
        top_bar.setSpacing(5)

        self._btn_ocr = QPushButton("OCR 框選")
        self._btn_ocr.setEnabled(False)
        self._btn_ocr.setToolTip("框選螢幕區域並執行 OCR (Ctrl+Shift+O)")
        self._btn_ocr.clicked.connect(self.trigger_capture_ocr)
        self._btn_ocr.setStyleSheet(f"""
            QPushButton {{
                background: {_ACCENT}; color: {_ACCENT_T};
                border-radius: 5px; padding: 4px 10px;
                font-size: 12px; font-weight: 600; border: none;
            }}
            QPushButton:hover {{ background: {_ACCENT_H}; }}
            QPushButton:disabled {{ background: {_BG_RAISE}; color: {_TEXT_SEC}; }}
        """)

        self._btn_screenshot = QPushButton("截圖")
        self._btn_screenshot.setToolTip("框選截圖 (Ctrl+Shift+S)")
        self._btn_screenshot.clicked.connect(self.trigger_capture_image)
        self._btn_screenshot.setStyleSheet(f"""
            QPushButton {{
                background: {_TEAL}; color: {_ACCENT_T};
                border-radius: 5px; padding: 4px 10px;
                font-size: 12px; font-weight: 600; border: none;
            }}
            QPushButton:hover {{ background: {_TEAL_H}; }}
        """)

        self._ocr_status_lbl = QLabel("OCR 載入中（約 5-10 秒）")
        self._ocr_status_lbl.setStyleSheet(
            f"font-size: 10px; color: {_TEXT_SEC}; background: transparent;"
        )

        self._btn_settings = QPushButton("⚙")
        self._btn_settings.setFixedSize(28, 28)
        self._btn_settings.setToolTip("設定")
        self._btn_settings.clicked.connect(self.open_settings)
        self._btn_settings.setStyleSheet(f"""
            QPushButton {{
                background: {_BG_RAISE}; border-radius: 5px; border: none;
                padding: 0; font-size: 14px; color: {_TEXT_SEC};
            }}
            QPushButton:hover {{ background: {_BG_HOVER}; color: {_TEXT_PRI}; }}
        """)

        top_bar.addWidget(self._btn_ocr)
        top_bar.addWidget(self._btn_screenshot)
        top_bar.addStretch()
        top_bar.addWidget(self._ocr_status_lbl)
        top_bar.addWidget(self._btn_settings)
        vbox.addLayout(top_bar)

        # --- Search ---
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("搜尋...")
        self._search_edit.textChanged.connect(self._on_search)
        self._search_edit.setStyleSheet(f"""
            QLineEdit {{
                background: {_BG_RAISE};
                border: 1px solid {_BORDER};
                border-radius: 5px;
                padding: 4px 10px;
                font-size: 12px;
                color: {_TEXT_PRI};
            }}
            QLineEdit:focus {{
                border: 1px solid {_ACCENT};
            }}
            QLineEdit::placeholder {{
                color: {_TEXT_SEC};
            }}
        """)
        vbox.addWidget(self._search_edit)

        # --- Filter tabs ---
        filter_bar = QHBoxLayout()
        filter_bar.setSpacing(3)
        self._filter_buttons = {}
        for label, key in [("全部", "all"), ("文字", "text"),
                            ("圖片", "image"), ("釘選", "pinned")]:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setFixedHeight(22)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: {_BG_RAISE}; border-radius: 4px;
                    padding: 2px 8px; font-size: 11px; border: none;
                    color: {_TEXT_SEC};
                }}
                QPushButton:checked {{
                    background: {_ACCENT_18};
                    color: {_ACCENT};
                    border: 1px solid {_ACCENT_35};
                }}
                QPushButton:hover:!checked {{
                    background: {_BG_HOVER}; color: {_TEXT_PRI};
                }}
            """)
            btn.clicked.connect(lambda _checked, k=key: self._set_filter(k))
            self._filter_buttons[key] = btn
            filter_bar.addWidget(btn)
        filter_bar.addStretch()
        self._filter_buttons['all'].setChecked(True)
        self._current_filter = 'all'
        vbox.addLayout(filter_bar)

        # --- Separator ---
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFixedHeight(1)
        line.setStyleSheet(f"background: {_BORDER}; border: none;")
        vbox.addWidget(line)

        # --- List ---
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }"
        )

        self._list_widget = QWidget()
        self._list_widget.setStyleSheet("background: transparent;")
        self._list_layout = QVBoxLayout(self._list_widget)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(2)
        self._list_layout.addStretch()
        self._scroll.setWidget(self._list_widget)
        vbox.addWidget(self._scroll, 1)

        # --- Bottom bar ---
        bottom_bar = QHBoxLayout()
        bottom_bar.setSpacing(5)

        self._btn_console = QPushButton("主控台")
        self._btn_console.clicked.connect(self.open_console)
        self._btn_console.setStyleSheet(f"""
            QPushButton {{
                background: {_BG_RAISE}; border-radius: 5px;
                padding: 4px 10px; font-size: 12px; border: none;
                color: {_TEXT_SEC};
            }}
            QPushButton:hover {{ background: {_BG_HOVER}; color: {_TEXT_PRI}; }}
        """)

        self._btn_pause = QPushButton("暫停監聽")
        self._btn_pause.setCheckable(True)
        self._btn_pause.clicked.connect(self.toggle_clipboard_pause)
        self._btn_pause.setStyleSheet(f"""
            QPushButton {{
                background: {_BG_RAISE}; border-radius: 5px;
                padding: 4px 10px; font-size: 12px; border: none;
                color: {_TEXT_SEC};
            }}
            QPushButton:checked {{
                background: {_ERROR_18};
                color: {_ERROR};
                border: 1px solid {_ERROR_35};
            }}
            QPushButton:hover:!checked {{ background: {_BG_HOVER}; color: {_TEXT_PRI}; }}
        """)

        bottom_bar.addWidget(self._btn_console)
        bottom_bar.addStretch()
        bottom_bar.addWidget(self._btn_pause)
        vbox.addLayout(bottom_bar)

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
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton and self._drag_pos:
            self.move(event.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        if self._cfg.get('ui', 'widget_position', default='remember') == 'remember':
            pos = self.pos()
            self._cfg.set('ui', 'widget_saved_x', pos.x())
            self._cfg.set('ui', 'widget_saved_y', pos.y())

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

    def _on_search(self, text: str):
        self.refresh_list(search_text=text)

    # --- List population ---
    def refresh_list(self, search_text: str = None):
        self._selected_item_id = None   # clear selection on refresh
        # Clear items (keep stretch at end)
        while self._list_layout.count() > 1:
            item = self._list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if search_text is None:
            search_text = self._search_edit.text()

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

                bg = _BG_RAISE if idx % 2 == 0 else _BG
                card.setStyleSheet(
                    f"background: {bg}; border-radius: 4px;"
                    f" border: 1px solid transparent;"
                )

                click_action = self._cfg.get('ui', 'widget_click_action', default='select')
                if click_action == 'copy':
                    card.clicked.connect(self._on_item_copy)
                    card.double_clicked.connect(self._on_item_select)
                else:
                    card.clicked.connect(self._on_item_select)
                    card.double_clicked.connect(self._on_item_copy)

                card.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                card.customContextMenuRequested.connect(
                    lambda pos, i=item, c=card: self._show_item_menu(pos, i, c)
                )
                self._list_layout.insertWidget(idx, card)
        except Exception as e:
            logger.error(f"refresh_list 失敗: {e}", exc_info=True)

    def _on_item_select(self, item_id: int):
        prev_id = self._selected_item_id
        self._selected_item_id = item_id

        # Only re-style the two affected cards (skip QSS re-parse for unchanged cards)
        card_idx = 0
        for i in range(self._list_layout.count()):
            w = self._list_layout.itemAt(i).widget()
            if w is None:
                continue
            wid = w.property('item_id')
            if wid == item_id:
                w.setStyleSheet(
                    f"background: {_ACCENT_10}; border-radius: 4px;"
                    f" border: 1px solid {_ACCENT_35};"
                )
            elif wid == prev_id:
                bg = _BG_RAISE if card_idx % 2 == 0 else _BG
                w.setStyleSheet(
                    f"background: {bg}; border-radius: 4px;"
                    f" border: 1px solid transparent;"
                )
            card_idx += 1

    def _on_item_copy(self, item_id: int):
        item = self._item_repo.get_by_id(item_id)
        if not item:
            return
        text = item.get_effective_text()
        if text:
            from ..clipboard.writer import write_text_to_clipboard
            write_text_to_clipboard(text)
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
                     self.refresh_list())
        )

        act_ocr = menu.addAction("重新執行 OCR")
        act_ocr.setEnabled(self._ocr_ready and bool(item.raw_image_path))
        act_ocr.triggered.connect(lambda: self._rerun_ocr(item))

        menu.addSeparator()
        menu.addAction("刪除").triggered.connect(lambda: self._soft_delete(item.id))
        menu.exec(card.mapToGlobal(pos))

    def _soft_delete(self, item_id: int):
        self._item_repo.soft_delete(item_id)
        self.refresh_list()

    def _copy_item_image(self, item):
        if not item.raw_image_path:
            return
        abs_p = self._file_mgr.get_abs_path(item.raw_image_path)
        from ..clipboard.writer import write_image_to_clipboard
        write_image_to_clipboard(abs_p)

    def _save_item_image(self, item):
        if not item.raw_image_path:
            return
        import os
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
        editor.item_updated.connect(lambda _: self.refresh_list())
        editor.destroyed.connect(
            lambda: self._editor_windows.pop(item.id, None)
        )
        self._editor_windows[item.id] = editor
        editor.show()

    def _rerun_ocr(self, item):
        if item.raw_image_path:
            abs_path = self._file_mgr.get_abs_path(item.raw_image_path)
            self._item_repo.update_ocr_status(item.id, 'pending')
            self._ocr_worker._mode = 'ocr'
            self._ocr_worker.queue_ocr(item.id, abs_path, 'screen')
            self.refresh_list()

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
                tag_repo=None,
                file_mgr=self._file_mgr,
                db_worker=self._db_worker,
                ocr_worker=self._ocr_worker,
                cfg=self._cfg,
                data_dir=self._data_dir,
            )
            self._console.destroyed.connect(
                lambda: setattr(self, '_console', None)
            )
        self._console.show()
        self._console.raise_()
        self._console.activateWindow()

    def open_settings(self):
        from .settings_dialog import SettingsDialog
        dlg = SettingsDialog(self._cfg, self)
        dlg.exec()

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
    def toggle_clipboard_pause(self):
        self._clip_paused = not self._clip_paused
        self._btn_pause.setChecked(self._clip_paused)
        if self._clip_watcher:
            self._clip_watcher.pause(self._clip_paused)

    def paste_last_item(self):
        items = self._item_repo.list_recent(limit=1)
        if items:
            text = items[0].get_effective_text()
            if text:
                from ..clipboard.paste_simulator import simulate_paste
                simulate_paste(text)

    # --- Status label (public slot used by app.py) ---
    def set_ocr_status(self, text: str):
        self._ocr_status_lbl.setText(text)

    # --- OCR engine status ---
    def on_ocr_engine_progress(self, pct: int, msg: str):
        self._ocr_status_lbl.setText(f"OCR {pct}%")

    def on_ocr_engine_ready(self):
        self._ocr_ready = True
        self._btn_ocr.setEnabled(True)
        self._ocr_status_lbl.setText("OCR 就緒")
        self._ocr_status_lbl.setStyleSheet(
            f"font-size: 10px; color: {_SUCCESS}; background: transparent;"
        )
        logger.info("OCR 引擎就緒，按鈕已啟用")

    def on_ocr_engine_failed(self, error: str):
        self._ocr_ready = False
        self._btn_ocr.setEnabled(False)
        self._ocr_status_lbl.setText("OCR 失敗")
        self._ocr_status_lbl.setStyleSheet(
            f"font-size: 10px; color: {_ERROR}; background: transparent;"
        )
        QMessageBox.warning(
            self, "OCR 引擎載入失敗",
            f"OCR 引擎載入失敗，截圖功能仍可用。\n\n錯誤：{error}"
        )
