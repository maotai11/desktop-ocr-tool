from PySide6.QtWidgets import QListWidgetItem, QTableWidgetItem, QHBoxLayout
from PySide6.QtGui import QColor
# -*- coding: utf-8 -*-
import logging
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QTabWidget,
    QWidget, QLabel, QCheckBox, QSpinBox, QDoubleSpinBox,
    QLineEdit, QPushButton, QFormLayout,
    QDialogButtonBox, QComboBox, QGroupBox
)

from .theme import (
    _BG, _BG_RAISE, _BG_HOVER, _BORDER,
    _TEXT_PRI, _TEXT_SEC, _ACCENT, _ACCENT_H, _ACCENT_T,
    _ACCENT_15, _ACCENT_18, _ACCENT_35,
)

logger = logging.getLogger(__name__)

_DIALOG_QSS = f"""
    QDialog {{
        background: {_BG};
    }}
    QWidget {{
        background: {_BG};
        color: {_TEXT_PRI};
        font-size: 13px;
    }}
    QTabWidget::pane {{
        border: 1px solid {_BORDER};
        border-radius: 5px;
        background: {_BG};
    }}
    QTabBar::tab {{
        background: {_BG_RAISE};
        color: {_TEXT_SEC};
        padding: 6px 16px;
        border: none;
        margin-right: 2px;
    }}
    QTabBar::tab:selected {{
        background: {_ACCENT_15};
        color: {_ACCENT};
        border-bottom: 2px solid {_ACCENT};
    }}
    QTabBar::tab:hover:!selected {{
        background: {_BG_HOVER};
        color: {_TEXT_PRI};
    }}
    QLabel {{
        color: {_TEXT_PRI};
        background: transparent;
    }}
    QCheckBox {{
        color: {_TEXT_PRI};
        background: transparent;
        spacing: 6px;
    }}
    QCheckBox::indicator {{
        width: 15px;
        height: 15px;
        border-radius: 3px;
        border: 1px solid {_BORDER};
        background: {_BG_RAISE};
    }}
    QCheckBox::indicator:checked {{
        background: {_ACCENT};
        border: 1px solid {_ACCENT};
        image: none;
    }}
    QCheckBox::indicator:hover {{
        border: 1px solid {_ACCENT};
    }}
    QSpinBox {{
        background: {_BG_RAISE};
        color: {_TEXT_PRI};
        border: 1px solid {_BORDER};
        border-radius: 4px;
        padding: 3px 6px;
    }}
    QSpinBox:focus {{
        border: 1px solid {_ACCENT};
    }}
    QSpinBox::up-button, QSpinBox::down-button {{
        background: {_BG_HOVER};
        border: none;
        width: 16px;
    }}
    QLineEdit {{
        background: {_BG_RAISE};
        color: {_TEXT_PRI};
        border: 1px solid {_BORDER};
        border-radius: 4px;
        padding: 3px 8px;
    }}
    QLineEdit:focus {{
        border: 1px solid {_ACCENT};
    }}
    QComboBox {{
        background: {_BG_RAISE};
        color: {_TEXT_PRI};
        border: 1px solid {_BORDER};
        border-radius: 4px;
        padding: 3px 8px;
        min-width: 100px;
    }}
    QComboBox:focus {{
        border: 1px solid {_ACCENT};
    }}
    QComboBox::drop-down {{
        border: none;
        width: 20px;
    }}
    QComboBox QAbstractItemView {{
        background: {_BG_RAISE};
        color: {_TEXT_PRI};
        border: 1px solid {_BORDER};
        selection-background-color: {_ACCENT_18};
        selection-color: {_ACCENT};
    }}
    QDialogButtonBox QPushButton {{
        background: {_BG_RAISE};
        color: {_TEXT_PRI};
        border: 1px solid {_BORDER};
        border-radius: 4px;
        padding: 5px 16px;
        min-width: 64px;
    }}
    QDialogButtonBox QPushButton:hover {{
        background: {_BG_HOVER};
        border: 1px solid {_ACCENT};
        color: {_ACCENT};
    }}
    QDialogButtonBox QPushButton[text="OK"],
    QDialogButtonBox QPushButton[text="確定"] {{
        background: {_ACCENT};
        color: {_ACCENT_T};
        border: none;
        font-weight: 600;
    }}
    QDialogButtonBox QPushButton[text="OK"]:hover,
    QDialogButtonBox QPushButton[text="確定"]:hover {{
        background: {_ACCENT_H};
        color: {_ACCENT_T};
    }}
"""


class SettingsDialog(QDialog):
    def __init__(self, cfg, parent=None, ocr_engine=None, tag_repo=None):
        super().__init__(parent)
        self._cfg = cfg
        self._ocr_engine = ocr_engine
        self._tag_repo = tag_repo
        self.setWindowTitle("設定")
        self.setMinimumSize(520, 420)
        self._setup_ui()
        self.setStyleSheet(_DIALOG_QSS)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        tabs = QTabWidget()

        # ---- 一般 ----
        gen = QWidget()
        gf = QFormLayout(gen)
        gf.setSpacing(10)
        self._cb_autostart = QCheckBox("開機自動啟動")
        self._cb_autostart.setChecked(
            self._cfg.get('general', 'start_with_windows', default=False)
        )
        gf.addRow("系統啟動：", self._cb_autostart)

        self._cb_start_min = QCheckBox("啟動時最小化到系統匣")
        self._cb_start_min.setChecked(
            self._cfg.get('general', 'start_minimized', default=True)
        )
        gf.addRow("", self._cb_start_min)
        tabs.addTab(gen, "一般")

        # ---- 擷取 ----
        cap = QWidget()
        cf = QFormLayout(cap)
        cf.setSpacing(10)
        self._cb_auto_ocr = QCheckBox("截圖後自動執行 OCR")
        self._cb_auto_ocr.setChecked(
            self._cfg.get('capture', 'auto_ocr_on_capture', default=True)
        )
        cf.addRow("OCR：", self._cb_auto_ocr)
        tabs.addTab(cap, "擷取")

        # ---- OCR ----
        ocr_tab = QWidget()
        of = QFormLayout(ocr_tab)
        of.setSpacing(10)

        self._cb_second_pass = QCheckBox("空結果或低信心時自動重試")
        self._cb_second_pass.setChecked(
            self._cfg.get('ocr', 'enable_second_pass', default=True)
        )
        of.addRow("自動重試：", self._cb_second_pass)
        self._sp_max_passes = QSpinBox()
        self._sp_max_passes.setRange(1, 3)
        self._sp_max_passes.setValue(self._cfg.get('ocr', 'max_ocr_passes', default=2))
        self._sp_max_passes.setSuffix(" 次（含首次）")
        self._sp_max_passes.setEnabled(self._cb_second_pass.isChecked())
        self._cb_second_pass.toggled.connect(self._sp_max_passes.setEnabled)
        of.addRow("每區辨識上限：", self._sp_max_passes)
        retry_note = QLabel('預設最多 2 次；第 3 次僅用於仍低信心、空白或候選衝突。\n'
                            '重試採不同影像處理；分數不是正確率，仍須人工確認。')
        retry_note.setWordWrap(True)
        of.addRow('', retry_note)

        self._cb_handwriting = QCheckBox("手寫友善模式（積極前處理）")
        self._cb_handwriting.setChecked(
            self._cfg.get('ocr', 'enable_handwriting_mode', default=False)
        )
        of.addRow("手寫模式：", self._cb_handwriting)

        self._sp_short_side = QSpinBox()
        self._sp_short_side.setRange(64, 1920)
        self._sp_short_side.setSingleStep(64)
        self._sp_short_side.setSuffix(" px")
        self._sp_short_side.setValue(
            self._cfg.get('ocr', 'max_image_short_side', default=960)
        )
        of.addRow("小圖放大門檻：", self._sp_short_side)

        _ocr_note = QLabel("設定儲存後，重新啟動程式生效。")
        _ocr_note.setStyleSheet(f"color: {_TEXT_SEC}; font-size: 11px;")
        of.addRow("", _ocr_note)

        self._model_profile = QComboBox()
        self._model_profile.addItem('PP-OCRv6 Small（較輕量）', 'v6-small')
        self._model_profile.addItem('PP-OCRv6 Medium 辨字（較大模型）', 'v6-medium')
        selected = self._model_profile.findData(self._cfg.get('ocr', 'model_profile', default='v6-small'))
        if selected < 0:
            self._model_profile.addItem('未知設定，請選擇內含模型', None)
            selected = self._model_profile.count() - 1
        self._model_profile.setCurrentIndex(selected)
        of.addRow('辨識模型：', self._model_profile)
        engine_note = QLabel('兩種模式都用 v6 Small 找字，辨字模型可選 Small／Medium。\n'
                             '模型已內含，不需下載；儲存後重啟生效。Medium 較耗記憶體，不保證每字更準。\n'
                             '使用 RapidOCR／ONNX Runtime；失敗不退回舊模型。')
        engine_note.setWordWrap(True)
        of.addRow('辨識引擎：', engine_note)
        tabs.addTab(ocr_tab, "OCR")

        # ---- 剪貼簿 ----
        clip = QWidget()
        clf = QFormLayout(clip)
        clf.setSpacing(10)
        self._cb_monitor = QCheckBox("啟用剪貼簿監聽")
        self._cb_monitor.setChecked(
            self._cfg.get('clipboard', 'monitor_clipboard', default=False)
        )
        clf.addRow("監聽：", self._cb_monitor)

        self._cb_auto_text = QCheckBox("自動收錄複製的文字")
        self._cb_auto_text.setChecked(
            self._cfg.get('clipboard', 'auto_save_text', default=True)
        )
        clf.addRow("文字：", self._cb_auto_text)

        from ..clipboard.text_cleanup import COPY_CLEANUP_OPTIONS
        self._copy_cleanup_checks = {}
        for key, label, _ in COPY_CLEANUP_OPTIONS:
            check = QCheckBox(label)
            check.setChecked(self._cfg.get('clipboard', key, default=False) is True)
            self._copy_cleanup_checks[key] = check
            clf.addRow('複製清理：', check)
        cleanup_note = QLabel(
            '全部預設不勾；只改複製輸出，原始辨識、候選與人工校正不變。\n'
            '一般文字的逗號／撇號也會移除；負號、小數點、其他符號與空格保留。\n'
            '移除換行會把 123 與下一行 456 拼成 123456，請確認是同一個數字。\n'
            '批量複製仍保留不同記錄的分隔。儲存後，複製清理立即生效。')
        cleanup_note.setWordWrap(True)
        clf.addRow('', cleanup_note)

        self._cb_auto_image = QCheckBox("自動收錄剪貼簿圖片")
        self._cb_auto_image.setChecked(
            self._cfg.get('clipboard', 'auto_save_image', default=False)
        )
        self._cb_auto_image.setEnabled(False)
        self._cb_auto_image.setToolTip('剪貼簿圖片收錄尚未完成，此版本停用')
        clf.addRow("圖片（未支援）：", self._cb_auto_image)
        privacy = QLabel('啟用後，複製的密碼、Token 或個資也可能以明文存入歷史。\n'
                         '目前沒有自動到期刪除；請使用主控台刪除並清空回收桶。')
        privacy.setWordWrap(True)
        clf.addRow('', privacy)

        dedup_lbl = QLabel("連續相同內容（60 秒內）自動去重")
        dedup_lbl.setStyleSheet(f"color: {_TEXT_SEC}; font-size: 11px;")
        clf.addRow("去重：", dedup_lbl)
        tabs.addTab(clip, "剪貼簿")

        # ---- 快捷鍵 ----
        hk_tab = QWidget()
        hkf = QFormLayout(hk_tab)
        hkf.setSpacing(10)
        hk_defs = self._cfg.get('hotkeys', default={})
        self._hk_edits = {}
        for key, lbl_text in [
            ('capture_region_ocr', 'OCR 框選'),
            ('capture_region_image', '截圖框選'),
            ('capture_fullscreen', '全螢幕截圖'),
            ('toggle_widget', '顯示/隱藏浮動窗'),
            ('open_console', '開啟主控台'),
            ('paste_last', '貼上最後一筆'),
        ]:
            edit = QLineEdit(hk_defs.get(key, ''))
            edit.setReadOnly(True)
            self._hk_edits[key] = edit
            hkf.addRow(f"{lbl_text}：", edit)
        tabs.addTab(hk_tab, "快捷鍵")

        # ---- 介面 ----
        ui_tab = QWidget()
        uf = QFormLayout(ui_tab)
        uf.setSpacing(10)
        self._cb_theme = QComboBox()
        self._cb_theme.addItems(["system", "light", "dark"])
        theme = self._cfg.get('ui', 'theme', default='system')
        idx = self._cb_theme.findText(theme)
        if idx >= 0:
            self._cb_theme.setCurrentIndex(idx)
        uf.addRow("佈景主題：", self._cb_theme)

        self._sp_font_size = QSpinBox()
        self._sp_font_size.setRange(9, 24)
        self._sp_font_size.setValue(self._cfg.get('ui', 'font_size', default=13))
        uf.addRow("字型大小：", self._sp_font_size)
        self._cb_theme.setEnabled(False)
        self._sp_font_size.setEnabled(False)
        uf.addRow(QLabel('佈景與字級尚未接入完整介面，本版停用調整。'))
        tabs.addTab(ui_tab, "介面")

        # ---- 標籤管理 ----
        if self._tag_repo is not None:
            tag_tab = self._create_tag_management_tab()
            tabs.addTab(tag_tab, "標籤管理")

        layout.addWidget(tabs)

        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        btn_box.accepted.connect(self._save)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    def _save(self):

        self._cfg.set('general', 'start_with_windows', self._cb_autostart.isChecked())
        self._cfg.set('general', 'start_minimized', self._cb_start_min.isChecked())
        self._cfg.set('capture', 'auto_ocr_on_capture', self._cb_auto_ocr.isChecked())
        self._cfg.set('ocr', 'enable_second_pass', self._cb_second_pass.isChecked())
        self._cfg.set('ocr', 'max_ocr_passes', self._sp_max_passes.value())
        self._cfg.set('ocr', 'enable_handwriting_mode', self._cb_handwriting.isChecked())
        self._cfg.set('ocr', 'max_image_short_side', self._sp_short_side.value())
        if self._model_profile.currentData() is not None:
            self._cfg.set('ocr', 'model_profile', self._model_profile.currentData())
        self._cfg.set('clipboard', 'monitor_clipboard', self._cb_monitor.isChecked())
        self._cfg.set('clipboard', 'auto_save_text', self._cb_auto_text.isChecked())
        for key, check in self._copy_cleanup_checks.items():
            self._cfg.set('clipboard', key, check.isChecked())

        from ..core.autostart import set_autostart
        set_autostart(self._cb_autostart.isChecked())

        logger.info('設定已儲存；重新啟動後生效')
        # 最後才關閉 dialog
        self.accept()

    def _create_tag_management_tab(self) -> QWidget:
        """建立標籤管理分頁。"""
        from PySide6.QtWidgets import (
            QListWidget, QListWidgetItem, QTableWidget,
            QTableWidgetItem, QHeaderView, QColorDialog,
        )
        from PySide6.QtGui import QColor

        tag_tab = QWidget()
        main_layout = QVBoxLayout(tag_tab)

        # 上方：標籤列表
        top_layout = QVBoxLayout()
        top_label = QLabel("現有標籤：")
        top_layout.addWidget(top_label)

        self._tag_list = QListWidget()
        self._tag_list.setMaximumHeight(200)
        top_layout.addWidget(self._tag_list)

        # 標籤操作按鈕
        tag_btn_layout = QHBoxLayout()
        self._btn_add_tag = QPushButton("新增標籤")
        self._btn_edit_tag = QPushButton("編輯")
        self._btn_delete_tag = QPushButton("刪除")
        tag_btn_layout.addWidget(self._btn_add_tag)
        tag_btn_layout.addWidget(self._btn_edit_tag)
        tag_btn_layout.addWidget(self._btn_delete_tag)
        top_layout.addLayout(tag_btn_layout)

        main_layout.addLayout(top_layout)

        # 下方：標籤關聯列表（顯示哪些項目有標籤）
        bottom_layout = QVBoxLayout()
        bottom_label = QLabel("標籤使用統計：")
        bottom_layout.addWidget(bottom_label)

        self._tag_stats_table = QTableWidget()
        self._tag_stats_table.setColumnCount(4)
        self._tag_stats_table.setHorizontalHeaderLabels(["標籤名稱", "顏色", "使用次數", "操作"])
        self._tag_stats_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._tag_stats_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self._tag_stats_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self._tag_stats_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self._tag_stats_table.setColumnWidth(1, 80)
        self._tag_stats_table.setColumnWidth(2, 80)
        self._tag_stats_table.setColumnWidth(3, 80)
        self._tag_stats_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._tag_stats_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        bottom_layout.addWidget(self._tag_stats_table)

        main_layout.addLayout(bottom_layout)

        # 連線信號
        self._btn_add_tag.clicked.connect(self._add_tag_dialog)
        self._btn_edit_tag.clicked.connect(self._edit_tag_dialog)
        self._btn_delete_tag.clicked.connect(self._delete_tag_confirm)

        # 載入標籤
        self._load_tags()

        return tag_tab

    def _load_tags(self):
        """載入所有標籤到列表和統計表。"""
        if self._tag_repo is None:
            return

        # 清空
        self._tag_list.clear()
        self._tag_stats_table.setRowCount(0)

        # 載入標籤
        all_tags = self._tag_repo.list_all()

        for tag in all_tags:
            # 加入列表
            item = QListWidgetItem(f"● {tag.name}")
            item.setData(Qt.ItemDataRole.UserRole, tag)
            item.setForeground(QColor(tag.color))
            self._tag_list.addItem(item)

            # 加入統計表
            row = self._tag_stats_table.rowCount()
            self._tag_stats_table.insertRow(row)

            name_item = QTableWidgetItem(tag.name)
            name_item.setData(Qt.ItemDataRole.UserRole, tag.id)

            color_item = QTableWidgetItem(tag.color)
            color_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            # 使用次數（暫時顯示 "-"，需要 repository 支援查詢）
            usage_item = QTableWidgetItem(str(self._tag_repo.usage_count(tag.id)))
            usage_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            # 操作按鈕
            from PySide6.QtWidgets import QPushButton
            action_btn = QPushButton("套用")
            action_btn.clicked.connect(lambda checked, tid=tag.id: self._apply_tag_to_selected(tid))

            self._tag_stats_table.setItem(row, 0, name_item)
            self._tag_stats_table.setItem(row, 1, color_item)
            self._tag_stats_table.setItem(row, 2, usage_item)
            self._tag_stats_table.setCellWidget(row, 3, action_btn)

    def _add_tag_dialog(self):
        """顯示新增標籤對話框。"""
        from PySide6.QtWidgets import (
            QDialog, QFormLayout, QColorDialog,
            QDialogButtonBox,
        )
        from PySide6.QtGui import QColor

        dlg = QDialog(self)
        dlg.setWindowTitle("新增標籤")
        dlg.setModal(True)
        dlg.setMinimumWidth(350)

        layout = QFormLayout(dlg)

        self._new_tag_name = QLineEdit()
        self._new_tag_name.setPlaceholderText("例如：財務、個資、待辦...")
        layout.addRow("標籤名稱：", self._new_tag_name)

        self._new_tag_color = QPushButton("#4A90D9")
        self._new_tag_color.clicked.connect(self._pick_color)
        self._new_tag_color.setStyleSheet("background: #4A90D9; color: white;")
        layout.addRow("顏色：", self._new_tag_color)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        layout.addRow(btns)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        name = self._new_tag_name.text().strip()
        if not name:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "警告", "請輸入標籤名稱。")
            return

        color = self._new_tag_color.text()
        tag = self._tag_repo.create(name=name, color=color)

        if tag:
            self._load_tags()
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, "成功", f"已新增標籤 '{name}'。")
        else:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "警告", f"標籤 '{name}' 已存在。")

    def _edit_tag_dialog(self):
        """顯示編輯標籤對話框。"""
        from PySide6.QtWidgets import (
            QDialog, QFormLayout, QColorDialog,
            QDialogButtonBox,
        )
        from PySide6.QtGui import QColor

        current_item = self._tag_list.currentItem()
        if not current_item:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, "提示", "請先選擇要編輯的標籤。")
            return

        tag = current_item.data(Qt.ItemDataRole.UserRole)

        dlg = QDialog(self)
        dlg.setWindowTitle("編輯標籤")
        dlg.setModal(True)
        dlg.setMinimumWidth(350)

        layout = QFormLayout(dlg)

        self._edit_tag_name = QLineEdit()
        self._edit_tag_name.setText(tag.name)
        layout.addRow("標籤名稱：", self._edit_tag_name)

        self._edit_tag_color = QPushButton(tag.color)
        self._edit_tag_color.clicked.connect(self._pick_color)
        self._edit_tag_color.setStyleSheet(f"background: {tag.color}; color: white;")
        layout.addRow("顏色：", self._edit_tag_color)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        layout.addRow(btns)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        new_name = self._edit_tag_name.text().strip()
        if not new_name:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "警告", "請輸入標籤名稱。")
            return

        from PySide6.QtWidgets import QMessageBox
        try:
            self._tag_repo.update(tag.id, new_name, self._edit_tag_color.text())
            self._load_tags()
        except Exception as exc:
            QMessageBox.warning(self, '無法更新標籤', str(exc))

    def _delete_tag_confirm(self):
        """確認刪除標籤。"""
        from PySide6.QtWidgets import QMessageBox

        current_item = self._tag_list.currentItem()
        if not current_item:
            QMessageBox.information(self, "提示", "請先選擇要刪除的標籤。")
            return

        tag = current_item.data(Qt.ItemDataRole.UserRole)

        reply = QMessageBox.question(
            self, "確認刪除",
            f"確定要刪除標籤 '{tag.name}' 嗎？\n此操作會同時移除所有項目與此標籤的關聯。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            self._tag_repo.delete(tag.id)
            self._load_tags()
            QMessageBox.information(self, "成功", f"已刪除標籤 '{tag.name}'。")

    def _pick_color(self):
        """顏色選擇器。"""
        from PySide6.QtWidgets import QColorDialog
        from PySide6.QtGui import QColor

        btn = self.sender()
        current_color = QColor(btn.text())
        color = QColorDialog.getColor(current_color, self, "選擇顏色")

        if color.isValid():
            hex_color = color.name()
            btn.setText(hex_color)
            btn.setStyleSheet(f"background: {hex_color}; color: white;")

    def _apply_tag_to_selected(self, tag_id: int):
        from PySide6.QtWidgets import QMessageBox
        owner = self.parent()
        selected = set(getattr(owner, '_selected_items', set()))
        single = getattr(owner, '_selected_item_id', None)
        if not selected and single is not None:
            selected.add(single)
        if not selected:
            QMessageBox.information(self, '尚未選取', '請先在浮動窗選取項目。')
            return
        for item_id in selected:
            self._tag_repo.add_to_item(item_id, tag_id)
        self._load_tags()
