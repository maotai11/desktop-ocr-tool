# -*- coding: utf-8 -*-
"""
OCR 文字編輯器 — spec §7.4
- 上方黃色提示列（needs_review 狀態）
- Tab 1: edited_text（OCR 辨識結果校正）
- Tab 2: note_richtext（備注，HTML 格式）
- 儲存後自動 confirmed；可觸發重跑 OCR
"""
import logging
import os
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTextEdit, QTabWidget, QWidget, QFrame, QFileDialog, QMessageBox
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QTextOption

logger = logging.getLogger(__name__)


class EditorWindow(QDialog):
    item_updated = Signal(int)   # 儲存/確認後通知外部刷新

    def __init__(self, item, item_repo, file_mgr, ocr_worker=None, parent=None):
        super().__init__(parent)
        self._item = item
        self._repo = item_repo
        self._file_mgr = file_mgr
        self._ocr_worker = ocr_worker

        type_names = {'text': '文字', 'image': '圖片', 'mixed': '混合'}
        self.setWindowTitle(f"編輯 #{item.id} — {type_names.get(item.item_type, item.item_type)}")
        self.resize(640, 520)
        self.setWindowFlags(
            Qt.WindowType.Dialog |
            Qt.WindowType.WindowCloseButtonHint |
            Qt.WindowType.WindowMaximizeButtonHint
        )

        self._setup_ui()
        self._load_data()

    def _setup_ui(self):
        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(10, 10, 10, 10)
        vbox.setSpacing(6)

        # --- needs_review 提示列 ---
        self._review_bar = QFrame()
        self._review_bar.setStyleSheet(
            "background: #FFF3CD; border: 1px solid #FFEEBA; border-radius: 4px;"
        )
        rb_layout = QHBoxLayout(self._review_bar)
        rb_layout.setContentsMargins(8, 4, 8, 4)
        rb_lbl = QLabel("⚠ OCR 信心值偏低，請確認辨識結果是否正確")
        rb_lbl.setStyleSheet("color: #856404; font-size: 12px;")
        rb_layout.addWidget(rb_lbl, 1)

        btn_confirm = QPushButton("確認無誤")
        btn_confirm.setStyleSheet(
            "background: #28a745; color: white; border-radius: 4px; "
            "padding: 3px 10px; border: none; font-size: 12px;"
        )
        btn_confirm.clicked.connect(self._confirm_ocr)

        btn_rerun = QPushButton("重跑 OCR")
        btn_rerun.setEnabled(
            self._ocr_worker is not None and bool(self._item.raw_image_path)
        )
        btn_rerun.setStyleSheet(
            "background: #17a2b8; color: white; border-radius: 4px; "
            "padding: 3px 10px; border: none; font-size: 12px;"
        )
        btn_rerun.clicked.connect(self._rerun_ocr)

        rb_layout.addWidget(btn_confirm)
        rb_layout.addWidget(btn_rerun)
        vbox.addWidget(self._review_bar)
        self._review_bar.setVisible(self._item.ocr_status == 'needs_review')

        # --- Tabs ---
        self._tabs = QTabWidget()

        # Tab 1: OCR 文字
        tab_text = QWidget()
        tl = QVBoxLayout(tab_text)
        tl.setContentsMargins(4, 6, 4, 4)

        ocr_lbl = QLabel("OCR 辨識結果（可直接編輯）：")
        ocr_lbl.setStyleSheet("font-size: 11px; color: #555;")
        tl.addWidget(ocr_lbl)

        self._text_edit = QTextEdit()
        self._text_edit.setAcceptRichText(False)
        self._text_edit.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self._text_edit.setStyleSheet("font-size: 13px; font-family: 'Courier New', monospace;")
        tl.addWidget(self._text_edit)
        self._tabs.addTab(tab_text, "OCR 文字")

        # Tab 2: 備注
        tab_note = QWidget()
        nl = QVBoxLayout(tab_note)
        nl.setContentsMargins(4, 6, 4, 4)

        note_lbl = QLabel("備注（支援 HTML 富文字）：")
        note_lbl.setStyleSheet("font-size: 11px; color: #555;")
        nl.addWidget(note_lbl)

        self._note_edit = QTextEdit()
        self._note_edit.setAcceptRichText(True)
        self._note_edit.setStyleSheet("font-size: 13px;")
        nl.addWidget(self._note_edit)
        self._tabs.addTab(tab_note, "備注")

        vbox.addWidget(self._tabs, 1)

        # --- 底部按鈕列 ---
        btn_row = QHBoxLayout()

        self._btn_save_image = QPushButton("另存圖片...")
        self._btn_save_image.setVisible(
            self._item.item_type in ('image', 'mixed') and
            bool(self._item.raw_image_path)
        )
        self._btn_save_image.setStyleSheet(
            "background: #6c757d; color: white; border-radius: 4px; "
            "padding: 5px 12px; border: none;"
        )
        self._btn_save_image.clicked.connect(self._save_image_as)
        btn_row.addWidget(self._btn_save_image)

        btn_row.addStretch()

        btn_cancel = QPushButton("取消")
        btn_cancel.clicked.connect(self.reject)
        btn_cancel.setStyleSheet(
            "background: #f0f0f0; border-radius: 4px; "
            "padding: 5px 12px; border: 1px solid #ccc;"
        )
        btn_row.addWidget(btn_cancel)

        btn_save = QPushButton("儲存")
        btn_save.setDefault(True)
        btn_save.clicked.connect(self._save)
        btn_save.setStyleSheet(
            "background: #4A90D9; color: white; border-radius: 4px; "
            "padding: 5px 14px; border: none;"
        )
        btn_row.addWidget(btn_save)

        vbox.addLayout(btn_row)

    def _load_data(self):
        # OCR 文字：優先顯示 edited_text，沒有則顯示 text_content
        text = self._item.edited_text or self._item.text_content or ''
        self._text_edit.setPlainText(text)

        # 備注
        if self._item.note_richtext:
            self._note_edit.setHtml(self._item.note_richtext)
        else:
            self._note_edit.clear()

    def _save(self):
        edited = self._text_edit.toPlainText()
        richtext = self._note_edit.toHtml()
        plaintext = self._note_edit.toPlainText()

        try:
            self._repo.update_edited_text(self._item.id, edited)
            self._repo.update_note(self._item.id, richtext, plaintext)

            # 修改文字後若為 needs_review → 自動 confirmed
            if self._item.ocr_status == 'needs_review':
                self._repo.update_ocr_status(self._item.id, 'confirmed')
                self._review_bar.setVisible(False)

            self.item_updated.emit(self._item.id)
            logger.info(f"已儲存編輯 item #{self._item.id}")
            self.accept()
        except Exception as e:
            logger.error(f"儲存失敗: {e}", exc_info=True)
            QMessageBox.warning(self, "儲存失敗", str(e))

    def _confirm_ocr(self):
        try:
            self._repo.confirm_review(self._item.id)
            self._review_bar.setVisible(False)
            self.item_updated.emit(self._item.id)
            logger.info(f"已確認 OCR item #{self._item.id}")
        except Exception as e:
            logger.error(f"確認失敗: {e}", exc_info=True)

    def _rerun_ocr(self):
        if not self._ocr_worker or not self._item.raw_image_path:
            return
        abs_path = self._file_mgr.get_abs_path(self._item.raw_image_path)
        self._repo.update_ocr_status(self._item.id, 'pending')
        self._ocr_worker.queue_ocr(self._item.id, abs_path, 'screen')
        self.item_updated.emit(self._item.id)
        self.accept()

    def _save_image_as(self):
        if not self._item.raw_image_path:
            return
        src = self._file_mgr.get_abs_path(self._item.raw_image_path)
        ext = os.path.splitext(src)[1] or '.png'
        dest, _ = QFileDialog.getSaveFileName(
            self, "另存圖片", f"capture_{self._item.id}{ext}",
            "圖片檔案 (*.png *.jpg *.bmp);;所有檔案 (*)"
        )
        if dest:
            import shutil
            shutil.copy2(src, dest)
            logger.info(f"圖片已匯出: {dest}")
