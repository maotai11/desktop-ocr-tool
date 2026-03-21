# -*- coding: utf-8 -*-
"""
主控台視窗
"""
import logging
import os
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QListWidget, QListWidgetItem, QTableWidget, QTableWidgetItem,
    QSplitter, QLabel, QLineEdit, QPushButton, QTextEdit,
    QStatusBar, QTabWidget, QMessageBox, QScrollArea,
    QMenu, QFileDialog
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap

from .theme import (
    _BG, _BG_SIDE, _BG_RAISE, _BG_HOVER, _BORDER,
    _TEXT_PRI, _TEXT_SEC, _ACCENT, _ACCENT_H, _ACCENT_T, _ERROR,
    _ACCENT_15, _ERROR_15, _ERROR_25, _ERROR_30,
)

logger = logging.getLogger(__name__)

_MAIN_QSS = f"""
    QMainWindow, QWidget {{
        background: {_BG};
        color: {_TEXT_PRI};
    }}
    QMenuBar {{
        background: {_BG_SIDE};
        color: {_TEXT_PRI};
        border-bottom: 1px solid {_BORDER};
    }}
    QMenuBar::item:selected {{
        background: {_BG_HOVER};
    }}
    QMenu {{
        background: {_BG_RAISE};
        color: {_TEXT_PRI};
        border: 1px solid {_BORDER};
    }}
    QMenu::item:selected {{
        background: {_ACCENT_15};
        color: {_ACCENT};
    }}
    QMenu::separator {{
        background: {_BORDER};
        height: 1px;
        margin: 2px 6px;
    }}
    QSplitter::handle {{
        background: {_BORDER};
        width: 1px;
    }}
    QStatusBar {{
        background: {_BG_SIDE};
        color: {_TEXT_SEC};
        font-size: 11px;
        border-top: 1px solid {_BORDER};
    }}
    QScrollBar:vertical {{
        background: {_BG};
        width: 6px;
        margin: 0;
    }}
    QScrollBar::handle:vertical {{
        background: {_BORDER};
        border-radius: 3px;
        min-height: 24px;
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar:horizontal {{
        background: {_BG};
        height: 6px;
        margin: 0;
    }}
    QScrollBar::handle:horizontal {{
        background: {_BORDER};
        border-radius: 3px;
        min-width: 24px;
    }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
    QTabWidget::pane {{
        border: 1px solid {_BORDER};
        background: {_BG};
    }}
    QTabBar::tab {{
        background: {_BG_RAISE};
        color: {_TEXT_SEC};
        padding: 5px 14px;
        border: none;
        margin-right: 2px;
    }}
    QTabBar::tab:selected {{
        background: {_BG};
        color: {_ACCENT};
        border-bottom: 2px solid {_ACCENT};
    }}
    QTabBar::tab:hover:!selected {{
        background: {_BG_HOVER};
        color: {_TEXT_PRI};
    }}
    QTextEdit {{
        background: {_BG_RAISE};
        color: {_TEXT_PRI};
        border: 1px solid {_BORDER};
        border-radius: 4px;
        font-size: 12px;
        selection-background-color: {_ACCENT_15};
    }}
"""


class MainWindow(QMainWindow):
    def __init__(self, item_repo, tag_repo, file_mgr, db_worker,
                 ocr_worker, cfg, data_dir, parent=None):
        super().__init__(parent)
        self._item_repo = item_repo
        self._tag_repo = tag_repo
        self._file_mgr = file_mgr
        self._db_worker = db_worker
        self._ocr_worker = ocr_worker
        self._editor_windows: dict = {}   # item_id → EditorWindow
        self._cfg = cfg
        self._data_dir = data_dir
        self._selected_item = None
        self._current_items = []

        self.setWindowTitle("桌面OCR擷取工具 - 主控台")
        self.resize(1200, 800)
        self._setup_ui()
        self._setup_menu()
        self._load_items()
        self.setStyleSheet(_MAIN_QSS)

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left sidebar
        sidebar = QWidget()
        sidebar.setFixedWidth(200)
        sidebar.setStyleSheet(
            f"background: {_BG_SIDE}; border-right: 1px solid {_BORDER};"
        )
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(6, 12, 6, 8)

        lbl = QLabel("分類")
        lbl.setStyleSheet(
            f"font-weight: 600; font-size: 11px; padding: 4px 6px;"
            f" color: {_TEXT_SEC}; letter-spacing: 1px; text-transform: uppercase;"
        )
        side_layout.addWidget(lbl)

        self._side_list = QListWidget()
        self._side_list.setStyleSheet(f"""
            QListWidget {{
                border: none; background: transparent;
                font-size: 13px; color: {_TEXT_PRI};
            }}
            QListWidget::item {{
                padding: 7px 10px; border-radius: 5px; margin: 1px 0;
            }}
            QListWidget::item:selected {{
                background: {_ACCENT_15};
                color: {_ACCENT};
            }}
            QListWidget::item:hover:!selected {{
                background: {_BG_HOVER};
            }}
        """)
        for label, key in [
            ("全部", "all"), ("文字", "text"), ("圖片", "image"),
            ("混合", "mixed"), ("釘選", "pinned"),
            ("待確認 OCR", "needs_review"), ("已歸檔", "archived"),
        ]:
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, key)
            self._side_list.addItem(item)
        self._side_list.setCurrentRow(0)
        self._side_list.currentItemChanged.connect(self._on_category_changed)
        side_layout.addWidget(self._side_list)
        splitter.addWidget(sidebar)

        # Center
        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(10, 10, 10, 0)

        search_row = QHBoxLayout()
        search_row.setSpacing(6)
        self._search = QLineEdit()
        self._search.setPlaceholderText("搜尋文字內容...")
        self._search.setStyleSheet(f"""
            QLineEdit {{
                background: {_BG_RAISE};
                border: 1px solid {_BORDER};
                border-radius: 5px;
                padding: 6px 12px;
                color: {_TEXT_PRI};
                font-size: 13px;
            }}
            QLineEdit:focus {{ border: 1px solid {_ACCENT}; }}
        """)
        self._search.returnPressed.connect(self._do_search)
        btn_search = QPushButton("搜尋")
        btn_search.clicked.connect(self._do_search)
        btn_search.setStyleSheet(f"""
            QPushButton {{
                background: {_ACCENT}; color: {_ACCENT_T};
                border-radius: 5px; padding: 6px 14px;
                border: none; font-weight: 600;
            }}
            QPushButton:hover {{ background: {_ACCENT_H}; }}
        """)
        search_row.addWidget(self._search)
        search_row.addWidget(btn_search)
        center_layout.addLayout(search_row)

        self._table = QTableWidget()
        self._table.setColumnCount(6)
        self._table.setHorizontalHeaderLabels(
            ["ID", "類型", "來源", "文字預覽", "OCR狀態", "建立時間"]
        )
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setColumnWidth(0, 50)
        self._table.setColumnWidth(1, 60)
        self._table.setColumnWidth(2, 90)
        self._table.setColumnWidth(3, 350)
        self._table.setColumnWidth(4, 80)
        self._table.setStyleSheet(f"""
            QTableWidget {{
                border: none; font-size: 12px;
                background: {_BG};
                color: {_TEXT_PRI};
                gridline-color: {_BORDER};
                alternate-background-color: {_BG_RAISE};
            }}
            QTableWidget::item:selected {{
                background: {_ACCENT_15};
                color: {_ACCENT};
            }}
            QHeaderView::section {{
                background: {_BG_SIDE};
                color: {_TEXT_SEC};
                padding: 6px;
                border: none;
                border-bottom: 1px solid {_BORDER};
                border-right: 1px solid {_BORDER};
                font-size: 11px;
                font-weight: 600;
                letter-spacing: 0.5px;
            }}
        """)
        self._table.currentCellChanged.connect(
            lambda curr_row, _cc, _pr, _pc: self._on_row_changed(curr_row)
        )
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_table_context_menu)
        center_layout.addWidget(self._table)
        splitter.addWidget(center)

        # Right detail
        detail = QWidget()
        detail.setMinimumWidth(280)
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(4, 10, 10, 10)

        self._detail_tabs = QTabWidget()

        self._preview_scroll = QScrollArea()
        self._preview_scroll.setWidgetResizable(True)
        self._preview_scroll.setStyleSheet(
            f"QScrollArea {{ background: {_BG_RAISE}; border: none; }}"
        )
        self._preview_label = QLabel()
        self._preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_label.setStyleSheet(f"background: {_BG_RAISE}; color: {_TEXT_SEC};")
        self._preview_scroll.setWidget(self._preview_label)
        self._detail_tabs.addTab(self._preview_scroll, "預覽")

        self._text_view = QTextEdit()
        self._text_view.setReadOnly(True)
        self._text_view.setStyleSheet(f"font-size: 13px; color: {_TEXT_PRI};")
        self._detail_tabs.addTab(self._text_view, "文字")

        self._info_view = QTextEdit()
        self._info_view.setReadOnly(True)
        self._info_view.setStyleSheet(f"font-size: 12px; color: {_TEXT_PRI};")
        self._detail_tabs.addTab(self._info_view, "資訊")

        detail_layout.addWidget(self._detail_tabs)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        btn_copy = QPushButton("複製")
        btn_copy.clicked.connect(self._copy_selected)
        btn_copy.setStyleSheet(f"""
            QPushButton {{
                background: {_ACCENT}; color: {_ACCENT_T};
                border-radius: 4px; padding: 5px 12px;
                border: none; font-weight: 600;
            }}
            QPushButton:hover {{ background: {_ACCENT_H}; }}
        """)
        btn_delete = QPushButton("刪除")
        btn_delete.clicked.connect(self._delete_selected)
        btn_delete.setStyleSheet(f"""
            QPushButton {{
                background: {_ERROR_15};
                color: {_ERROR};
                border-radius: 4px; padding: 5px 12px;
                border: 1px solid {_ERROR_30};
            }}
            QPushButton:hover {{
                background: {_ERROR_25};
                border: 1px solid {_ERROR};
            }}
        """)
        btn_row.addWidget(btn_copy)
        btn_row.addStretch()
        btn_row.addWidget(btn_delete)
        detail_layout.addLayout(btn_row)
        splitter.addWidget(detail)

        splitter.setSizes([200, 700, 300])
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 7)
        splitter.setStretchFactor(2, 3)
        main_layout.addWidget(splitter)

        self._status = QStatusBar()
        self.setStatusBar(self._status)
        self._status_lbl = QLabel("就緒")
        self._status.addWidget(self._status_lbl)

    def _setup_menu(self):
        mb = self.menuBar()
        file_menu = mb.addMenu("檔案")
        file_menu.addAction("匯出（CSV）...").triggered.connect(self._export_csv)
        file_menu.addSeparator()
        file_menu.addAction("關閉").triggered.connect(self.close)

        edit_menu = mb.addMenu("編輯")
        edit_menu.addAction("複製文字").triggered.connect(self._copy_selected)
        edit_menu.addAction("刪除（移至垃圾桶）").triggered.connect(self._delete_selected)
        edit_menu.addSeparator()
        edit_menu.addAction("清理已刪除...").triggered.connect(self._clear_deleted)

    def _load_items(self, category: str = 'all', search: str = ''):
        self._table.setRowCount(0)
        try:
            if search:
                items = self._item_repo.search_fulltext(search, limit=200)
            elif category == 'pinned':
                items = self._item_repo.list_recent(limit=200, pinned_only=True)
            elif category == 'needs_review':
                items = self._item_repo.list_recent(limit=200, needs_review_only=True)
            elif category == 'archived':
                items = self._item_repo.list_recent(limit=200, show_archived=True)
            elif category in ('text', 'image', 'mixed'):
                items = self._item_repo.list_recent(limit=200, item_type=category)
            else:
                items = self._item_repo.list_recent(limit=200)

            self._current_items = items
            self._table.setRowCount(len(items))

            type_names = {'text': '文字', 'image': '圖片', 'mixed': '混合'}
            source_names = {
                'region_ocr': 'OCR框選', 'region_image': '框選截圖',
                'fullscreen': '全螢幕', 'clipboard_text': '剪貼簿',
                'clipboard_image': '剪貼板圖', 'import': '匯入',
            }
            status_names = {
                'none': '-', 'pending': '排隊', 'processing': '處理中',
                'done': '完成', 'needs_review': '待確認',
                'confirmed': '已確認', 'failed': '失敗',
            }

            for row, item in enumerate(items):
                preview = (item.get_effective_text() or '')[:80].replace('\n', ' ')
                for col, val in enumerate([
                    str(item.id),
                    type_names.get(item.item_type, item.item_type),
                    source_names.get(item.source_mode, item.source_mode),
                    preview,
                    status_names.get(item.ocr_status, item.ocr_status),
                    item.created_at or '',
                ]):
                    self._table.setItem(row, col, QTableWidgetItem(val))

            stats = self._item_repo.get_statistics()
            self._status_lbl.setText(
                f"共 {stats.total} 筆 | OCR完成 {stats.ocr_done} | 待確認 {stats.ocr_needs_review}"
            )
        except Exception as e:
            logger.error(f"載入清單失敗: {e}", exc_info=True)

    def _on_category_changed(self, current, _):
        if current:
            self._load_items(category=current.data(Qt.ItemDataRole.UserRole))

    def _do_search(self):
        self._load_items(search=self._search.text().strip())

    def _on_row_changed(self, row: int):
        if 0 <= row < len(self._current_items):
            self._selected_item = self._current_items[row]
            self._update_detail()

    def _update_detail(self):
        item = self._selected_item
        if not item:
            return
        self._text_view.setPlainText(item.get_effective_text() or '')

        if item.thumbnail_path or item.raw_image_path:
            path = item.thumbnail_path or item.raw_image_path
            abs_p = self._file_mgr.get_abs_path(path)
            if os.path.exists(abs_p):
                px = QPixmap(abs_p).scaled(
                    260, 200,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                )
                self._preview_label.setPixmap(px)
            else:
                self._preview_label.setText("（圖片不存在）")
        else:
            self._preview_label.setText("（無圖片）")

        type_names = {'text': '文字', 'image': '圖片', 'mixed': '混合'}
        self._info_view.setPlainText(
            f"ID: {item.id}\n"
            f"類型: {type_names.get(item.item_type, item.item_type)}\n"
            f"來源: {item.source_mode}\n"
            f"OCR 狀態: {item.ocr_status}\n"
            f"OCR 置信度: {item.ocr_confidence:.3f}\n"
            f"建立時間: {item.created_at}\n"
            f"更新時間: {item.updated_at}\n"
            f"釘選: {'是' if item.is_pinned else '否'}\n"
            f"圖片路徑: {item.raw_image_path or '(無)'}"
        )

    def _copy_selected(self):
        if not self._selected_item:
            return
        text = self._selected_item.get_effective_text()
        if text:
            from ..clipboard.writer import write_text_to_clipboard
            write_text_to_clipboard(text)
            self._status_lbl.setText("已複製到剪貼簿")
            QTimer.singleShot(2000, lambda: self._status_lbl.setText("就緒"))

    def _delete_selected(self):
        if not self._selected_item:
            return
        ret = QMessageBox.question(
            self, "確認刪除",
            f"確定要刪除此筆記錄嗎？\nID: {self._selected_item.id}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if ret == QMessageBox.StandardButton.Yes:
            self._item_repo.soft_delete(self._selected_item.id)
            self._selected_item = None
            self._load_items()

    def _clear_deleted(self):
        total = self._item_repo.count(show_deleted=True)
        active = self._item_repo.count()
        soft_del = total - active
        ret = QMessageBox.question(
            self, "清理已刪除",
            f"將永久刪除 {soft_del} 筆已軟刪除的紀錄（含圖片）。\n確定繼續？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if ret == QMessageBox.StandardButton.Yes:
            n, items = self._item_repo.hard_delete_all_soft_deleted()
            for item in items:
                self._file_mgr.delete_item_files(item)
            self._load_items()
            QMessageBox.information(self, "完成", f"已清理 {n} 筆紀錄。")

    def _export_csv(self):
        if not self._current_items:
            QMessageBox.information(self, "匯出", "沒有可匯出的資料。")
            return
        from ..data.exporter import Exporter
        exp = Exporter(self._file_mgr.get_export_dir(), self._data_dir)
        path = exp.export_csv(self._current_items)
        QMessageBox.information(self, "匯出完成", f"已匯出至：\n{path}")

    def refresh(self):
        item = self._side_list.currentItem()
        cat = item.data(Qt.ItemDataRole.UserRole) if item else 'all'
        self._load_items(category=cat)

    # ------------------------------------------------------------------
    # 右鍵選單
    # ------------------------------------------------------------------
    def _on_table_context_menu(self, pos):
        row = self._table.rowAt(pos.y())
        if row < 0 or row >= len(self._current_items):
            return
        item = self._current_items[row]

        menu = QMenu(self)

        act_copy_text = menu.addAction("複製文字")
        act_copy_text.setEnabled(bool(item.get_effective_text()))
        act_copy_text.triggered.connect(lambda: self._copy_item_text(item))

        act_copy_img = menu.addAction("複製圖片")
        act_copy_img.setEnabled(
            item.item_type in ('image', 'mixed') and bool(item.raw_image_path)
        )
        act_copy_img.triggered.connect(lambda: self._copy_item_image(item))

        act_save_img = menu.addAction("另存圖片...")
        act_save_img.setEnabled(
            item.item_type in ('image', 'mixed') and bool(item.raw_image_path)
        )
        act_save_img.triggered.connect(lambda: self._save_item_image(item))

        menu.addSeparator()

        menu.addAction("編輯").triggered.connect(lambda: self._open_editor(item))

        menu.addSeparator()

        pin_text = "取消釘選" if item.is_pinned else "釘選"
        menu.addAction(pin_text).triggered.connect(
            lambda: (self._item_repo.set_pinned(item.id, not item.is_pinned),
                     self._load_items())
        )

        arch_text = "取消歸檔" if item.is_archived else "歸檔"
        menu.addAction(arch_text).triggered.connect(
            lambda: (self._item_repo.set_archived(item.id, not item.is_archived),
                     self._load_items())
        )

        menu.addSeparator()

        menu.addAction("刪除（移至垃圾桶）").triggered.connect(
            lambda: (self._item_repo.soft_delete(item.id),
                     self._load_items())
        )

        menu.exec(self._table.viewport().mapToGlobal(pos))

    # ------------------------------------------------------------------
    # 動作實作
    # ------------------------------------------------------------------
    def _copy_item_text(self, item):
        text = item.get_effective_text()
        if text:
            from ..clipboard.writer import write_text_to_clipboard
            write_text_to_clipboard(text)
            self._status_lbl.setText("已複製文字到剪貼簿")
            QTimer.singleShot(2000, lambda: self._status_lbl.setText("就緒"))

    def _copy_item_image(self, item):
        if not item.raw_image_path:
            return
        abs_p = self._file_mgr.get_abs_path(item.raw_image_path)
        from ..clipboard.writer import write_image_to_clipboard
        write_image_to_clipboard(abs_p)
        self._status_lbl.setText("已複製圖片到剪貼簿")
        QTimer.singleShot(2000, lambda: self._status_lbl.setText("就緒"))

    def _save_item_image(self, item):
        if not item.raw_image_path:
            return
        src = self._file_mgr.get_abs_path(item.raw_image_path)
        ext = os.path.splitext(src)[1] or '.png'
        dest, _ = QFileDialog.getSaveFileName(
            self, "另存圖片", f"capture_{item.id}{ext}",
            "圖片 (*.png *.jpg *.bmp);;所有檔案 (*)"
        )
        if dest:
            import shutil
            shutil.copy2(src, dest)
            self._status_lbl.setText(f"已匯出：{dest}")

    def _open_editor(self, item):
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
            parent=self,
        )
        editor.item_updated.connect(lambda _: self._load_items())
        editor.destroyed.connect(
            lambda: self._editor_windows.pop(item.id, None)
        )
        self._editor_windows[item.id] = editor
        editor.show()
