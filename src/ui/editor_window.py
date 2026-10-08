# -*- coding: utf-8 -*-
"""
OCR 文字編輯器 — spec §7.4
- 上方黃色提示列（needs_review 狀態）
- Tab 1: edited_text（OCR 辨識結果校正）
- Tab 2: note_richtext（備注，HTML 格式）
- 儲存後自動 confirmed；可觸發重跑 OCR
"""
import logging
import json
import os
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTextEdit, QTabWidget, QWidget, QFrame, QFileDialog, QMessageBox, QComboBox
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QTextOption, QTextCursor

from .theme import (
    _BG, _BG_RAISE, _BG_HOVER, _BORDER,
    _TEXT_PRI, _TEXT_SEC, _ACCENT, _ACCENT_H, _ACCENT_T,
    _TEAL, _TEAL_H, _SUCCESS,
    _ACCENT_12, _ACCENT_30,
    _TEAL_15, _TEAL_25, _TEAL_30,
    _SUCCESS_15, _SUCCESS_25, _SUCCESS_30,
)

logger = logging.getLogger(__name__)

_EDITOR_QSS = f"""
    QDialog {{
        background: {_BG};
    }}
    QWidget {{
        background: {_BG};
        color: {_TEXT_PRI};
    }}
    QTabWidget::pane {{
        border: 1px solid {_BORDER};
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
        selection-background-color: {_ACCENT_12};
    }}
    QTextEdit:focus {{
        border: 1px solid {_ACCENT};
    }}
    QScrollBar:vertical {{
        background: {_BG_RAISE};
        width: 6px;
        margin: 0;
    }}
    QScrollBar::handle:vertical {{
        background: {_BORDER};
        border-radius: 3px;
        min-height: 20px;
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
"""


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
        self.setStyleSheet(_EDITOR_QSS)
        # The default Save button otherwise receives initial Ctrl+V.
        self._text_edit.setFocus(Qt.FocusReason.OtherFocusReason)

    def _setup_ui(self):
        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(12, 12, 12, 12)
        vbox.setSpacing(8)

        # --- needs_review 提示列 ---
        self._review_bar = QFrame()
        self._review_bar.setStyleSheet(
            f"background: {_ACCENT_12};"
            f" border: 1px solid {_ACCENT_30};"
            " border-radius: 5px;"
        )
        rb_layout = QHBoxLayout(self._review_bar)
        rb_layout.setContentsMargins(10, 5, 10, 5)
        rb_lbl = QLabel("⚠ 待確認：低信心、候選衝突或圖片邊界，請核對原圖")
        rb_lbl.setWordWrap(True)
        rb_lbl.setStyleSheet(
            f"color: {_ACCENT}; font-size: 12px; background: transparent;"
        )
        rb_layout.addWidget(rb_lbl, 1)

        btn_confirm = self._btn_confirm = QPushButton("確認無誤")
        btn_confirm.setStyleSheet(f"""
            QPushButton {{
                background: {_SUCCESS_15};
                color: {_SUCCESS};
                border-radius: 4px; padding: 3px 10px;
                border: 1px solid {_SUCCESS_30};
                font-size: 12px;
            }}
            QPushButton:hover {{
                background: {_SUCCESS_25};
                border: 1px solid {_SUCCESS};
            }}
        """)
        btn_confirm.clicked.connect(self._confirm_ocr)

        btn_rerun = QPushButton("重跑 OCR")
        btn_rerun.setEnabled(
            self._ocr_worker is not None and bool(self._item.raw_image_path)
        )
        btn_rerun.setStyleSheet(f"""
            QPushButton {{
                background: {_TEAL_15};
                color: {_TEAL};
                border-radius: 4px; padding: 3px 10px;
                border: 1px solid {_TEAL_30};
                font-size: 12px;
            }}
            QPushButton:hover {{
                background: {_TEAL_25};
                border: 1px solid {_TEAL};
            }}
            QPushButton:disabled {{
                color: {_TEXT_SEC};
                border: 1px solid {_BORDER};
                background: transparent;
            }}
        """)
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
        tl.setContentsMargins(6, 8, 6, 6)

        ocr_lbl = QLabel("OCR 辨識結果（可直接編輯）：")
        ocr_lbl.setStyleSheet(
            f"font-size: 11px; color: {_TEXT_SEC}; background: transparent;"
        )
        tl.addWidget(ocr_lbl)

        self._text_edit = QTextEdit()
        self._text_edit.setAcceptRichText(False)
        self._text_edit.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self._text_edit.setStyleSheet(
            f"font-size: 13px; font-family: 'Consolas', 'Courier New', monospace;"
            f" color: {_TEXT_PRI};"
        )
        tl.addWidget(self._text_edit)
        self._tabs.addTab(tab_text, "OCR 文字")

        # Tab 2: 備注
        tab_note = QWidget()
        nl = QVBoxLayout(tab_note)
        nl.setContentsMargins(6, 8, 6, 6)

        note_lbl = QLabel("備注（支援 HTML 富文字）：")
        note_lbl.setStyleSheet(
            f"font-size: 11px; color: {_TEXT_SEC}; background: transparent;"
        )
        nl.addWidget(note_lbl)

        self._note_edit = QTextEdit()
        self._note_edit.setAcceptRichText(True)
        self._note_edit.setStyleSheet(f"font-size: 13px; color: {_TEXT_PRI};")
        nl.addWidget(self._note_edit)
        self._tabs.addTab(tab_note, "備注")

        # Raw candidates are review evidence. Reading/selecting a candidate
        # never changes the stored transcript or the current editor draft.
        tab_candidates = QWidget()
        candidates_layout = QVBoxLayout(tab_candidates)
        self._attempt_combo = QComboBox()
        self._candidate_combo = QComboBox()
        self._candidate_info = QLabel()
        self._candidate_info.setWordWrap(True)
        self._candidate_info.setTextFormat(Qt.TextFormat.PlainText)
        self._candidate_preview = QTextEdit()
        self._candidate_preview.setReadOnly(True)
        self._candidate_preview.setAcceptRichText(False)
        self._apply_candidate = QPushButton('套用候選至草稿（尚未儲存）')
        self._apply_candidate.setEnabled(False)
        self._apply_candidate.clicked.connect(self._apply_review_candidate)
        self._attempt_combo.currentIndexChanged.connect(self._load_attempt_candidates)
        self._candidate_combo.currentIndexChanged.connect(self._preview_candidate)
        candidates_layout.addWidget(QLabel('各次原始候選，不代表多數票或正確答案。套用後可復原，按「儲存」才寫入。'))
        candidates_layout.addWidget(self._attempt_combo)
        candidates_layout.addWidget(self._candidate_combo)
        candidates_layout.addWidget(self._candidate_info)
        candidates_layout.addWidget(self._candidate_preview, 1)
        candidates_layout.addWidget(self._apply_candidate)
        self._tabs.addTab(tab_candidates, '辨識候選／歷次紀錄')
        self._text_edit.selectionChanged.connect(self._update_candidate_action)
        self._text_edit.textChanged.connect(self._update_confirm_action)
        vbox.addWidget(self._tabs, 1)

        # --- 底部按鈕列 ---
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)

        self._btn_save_image = QPushButton("另存圖片...")
        self._btn_save_image.setVisible(
            self._item.item_type in ('image', 'mixed') and
            bool(self._item.raw_image_path)
        )
        self._btn_save_image.setStyleSheet(f"""
            QPushButton {{
                background: {_BG_RAISE}; color: {_TEXT_SEC};
                border-radius: 4px; padding: 5px 12px;
                border: 1px solid {_BORDER};
            }}
            QPushButton:hover {{
                background: {_BG_HOVER}; color: {_TEXT_PRI};
                border: 1px solid {_TEXT_SEC};
            }}
        """)
        self._btn_save_image.clicked.connect(self._save_image_as)
        btn_row.addWidget(self._btn_save_image)

        btn_row.addStretch()

        btn_cancel = QPushButton("取消")
        btn_cancel.clicked.connect(self.reject)
        btn_cancel.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {_TEXT_SEC};
                border-radius: 4px; padding: 5px 14px;
                border: 1px solid {_BORDER};
            }}
            QPushButton:hover {{
                background: {_BG_HOVER}; color: {_TEXT_PRI};
                border: 1px solid {_TEXT_SEC};
            }}
        """)
        btn_row.addWidget(btn_cancel)

        btn_save = QPushButton("儲存")
        btn_save.setDefault(True)
        btn_save.clicked.connect(self._save)
        btn_save.setStyleSheet(f"""
            QPushButton {{
                background: {_ACCENT}; color: {_ACCENT_T};
                border-radius: 4px; padding: 5px 16px;
                border: none; font-weight: 600;
            }}
            QPushButton:hover {{ background: {_ACCENT_H}; }}
        """)
        btn_row.addWidget(btn_save)

        vbox.addLayout(btn_row)

    def _load_data(self):
        # OCR 文字：優先顯示 edited_text，沒有則顯示 text_content
        text = self._item.get_effective_text() or ''
        self._text_edit.setPlainText(text)

        # 備注
        if self._item.note_richtext:
            self._note_edit.setHtml(self._item.note_richtext)
        else:
            self._note_edit.clear()
        self._loaded_note_html = self._note_edit.toHtml()
        self._load_review_history()
        self._update_confirm_action()

    def _load_review_history(self):
        self._attempt_combo.blockSignals(True)
        self._attempt_combo.clear()
        try:
            attempts = self._repo.get_ocr_attempts(self._item.id, include_provenance=False)
            for attempt in reversed(attempts):
                when = attempt.get('completed_at') or '處理中'
                self._attempt_combo.addItem(
                    f"{when} · {attempt.get('status', '')} · {attempt.get('disposition', '')}",
                    attempt['job_id'])
        except Exception:
            logger.exception('無法讀取 OCR 候選紀錄')
        finally:
            self._attempt_combo.blockSignals(False)
        self._load_attempt_candidates()

    def _load_attempt_candidates(self, *_):
        self._candidate_combo.blockSignals(True)
        self._candidate_combo.clear()
        job = self._attempt_combo.currentData()
        try:
            attempt = self._repo.get_ocr_attempt(self._item.id, job) if job else None
            if attempt:
                try:
                    provenance = json.loads(attempt.get('provenance_json') or '{}')
                except (TypeError, ValueError):
                    provenance = {}
                if not isinstance(provenance, dict):
                    provenance = {}
                self._candidate_info.setToolTip(attempt.get('model_version') or '')
                # The stored machine transcript is included separately from raw
                # pass output, so legacy/failed attempts stay inspectable too.
                if attempt.get('text_content'):
                    self._candidate_combo.addItem('該次整體結果（已後處理）', {
                        'text': attempt['text_content'], 'partial': False,
                        'info': self._candidate_source(attempt) + '\n整體機器結果，可能已簡轉繁；仍需核對。'})
                hypotheses = provenance.get('hypotheses', {})
                if isinstance(hypotheses, dict):
                    self._add_pass_candidates(attempt, hypotheses)
                    for tile in hypotheses.get('tiles', []):
                        if isinstance(tile, dict) and isinstance(tile.get('hypotheses'), dict):
                            self._add_pass_candidates(
                                attempt, tile['hypotheses'], tile=tile.get('tile_index', 0),
                                partial=not self._covers_full_source(provenance, tile))
        except Exception:
            logger.exception('無法讀取 OCR 候選內容')
        finally:
            self._candidate_combo.blockSignals(False)
        self._preview_candidate()

    @staticmethod
    def _candidate_source(attempt):
        identity = attempt.get('model_version') or 'unknown'
        display_identity = identity if len(identity) <= 110 else identity[:107] + '…'
        return f"來源：{attempt.get('engine', 'unknown')}\n模型：{display_identity}"

    @staticmethod
    def _covers_full_source(provenance, tile):
        prep = provenance.get('preprocessing', {})
        if not isinstance(prep, dict) or prep.get('strategy') != 'bounded_small_region':
            return False
        source_hw, geometry = prep.get('source_hw'), prep.get('tiles', [])
        if (not isinstance(source_hw, list) or len(source_hw) != 2 or
                not isinstance(geometry, list) or len(geometry) != 1 or
                tile.get('tile_index') != 0 or not isinstance(geometry[0], dict)):
            return False
        return geometry[0].get('source_xywh') == [0, 0, source_hw[1], source_hw[0]]

    def _add_pass_candidates(self, attempt, hypotheses, tile=None, partial=True):
        from ..ocr.postprocessor import sort_boxes_and_merge
        labels = {'first_pass': '首次', 'second_pass': '第二次', 'third_pass': '第三次', 'secondary': '第二引擎'}
        reasons = {'initial': '首次辨識', 'empty_text': '未辨識到文字',
                   'weak_region': '局部低信心', 'low_average_confidence': '平均低信心',
                   'conflicting_text': '候選文字衝突', 'secondary_fallback': '第二引擎備援'}
        metadata = hypotheses.get('pass_metadata', {})
        if not isinstance(metadata, dict):
            metadata = {}
        for key, label in labels.items():
            rows = hypotheses.get(key, [])
            meta = metadata.get(key, {})
            if not isinstance(meta, dict):
                meta = {}
            if key not in hypotheses and not meta:
                continue
            rendered = []
            for row in rows if isinstance(rows, list) else []:
                if isinstance(row, dict) and isinstance(row.get('raw_text', row.get('text')), str):
                    rendered.append(dict(row, text=row.get('raw_text', row.get('text'))))
            try:
                text = sort_boxes_and_merge(rendered)
            except (TypeError, ValueError, KeyError, IndexError):
                text = '\n'.join(row['text'] for row in rendered)
            reason = meta.get('reason', [])
            if not isinstance(reason, list):
                reason = []
            info = self._candidate_source(attempt)
            info += f"\n影像處理：{meta.get('variant', '舊版紀錄未提供')}"
            info += '\n觸發原因：' + ('、'.join(reasons.get(r, str(r)) for r in reason) or '舊版紀錄未提供')
            info += f"\n結果：{meta.get('outcome', 'completed')}；模型原始文字，未簡轉繁"
            if meta.get('conflict'):
                info += '；與先前候選衝突，未自動覆蓋'
            if meta.get('error'):
                info += '\n' + str(meta['error'])
            if tile is not None:
                info += ('\n局部切片候選：先在「OCR 文字」選取要替換的範圍，再套用。'
                         if partial else '\n單片涵蓋整張原圖，可套用至整份草稿。')
                label = f'切片 {tile + 1} · {label}'
            if not text:
                info += '\n沒有可套用文字（空白、失敗或跳過）'
            self._candidate_combo.addItem(label, {'text': text, 'partial': tile is not None and partial, 'info': info})

    def _preview_candidate(self, *_):
        candidate = self._candidate_combo.currentData()
        self._candidate_preview.setPlainText(candidate['text'] if candidate else '')
        self._candidate_info.setText(candidate['info'] if candidate else '尚無可用候選紀錄；原有文字與備注保持不變。')
        self._update_candidate_action()

    def _update_candidate_action(self):
        candidate = self._candidate_combo.currentData()
        partial = bool(candidate and candidate.get('partial'))
        self._apply_candidate.setText('替換草稿的選取範圍（尚未儲存）' if partial else '套用候選至整份草稿（尚未儲存）')
        self._apply_candidate.setEnabled(bool(candidate and candidate.get('text') and (
            not partial or self._text_edit.textCursor().hasSelection())))

    def _update_confirm_action(self):
        clean = self._text_edit.toPlainText() == (self._item.get_effective_text() or '')
        self._btn_confirm.setEnabled(clean)
        self._btn_confirm.setToolTip('請先按「儲存」，一併保存草稿並確認' if not clean else '')

    def _apply_review_candidate(self):
        candidate = self._candidate_combo.currentData()
        if not candidate or not candidate.get('text'):
            return
        cursor = self._text_edit.textCursor()
        if candidate.get('partial') and not cursor.hasSelection():
            return
        cursor.beginEditBlock()
        if not candidate.get('partial'):
            cursor.select(QTextCursor.SelectionType.Document)
        cursor.insertText(candidate['text'])
        cursor.endEditBlock()
        self._text_edit.setTextCursor(cursor)
        self._tabs.setCurrentIndex(0)
        self._text_edit.setFocus()

    def _save(self):
        edited = self._text_edit.toPlainText()
        richtext = self._note_edit.toHtml()
        plaintext = self._note_edit.toPlainText()

        try:
            self._repo.save_editor_content(
                self._item.id, edited, richtext, plaintext, self._item.edit_revision,
                expected_ocr_job_id=self._item.ocr_job_id,
                expected_ocr_text=self._item.text_content)

            # 修改文字後若為 needs_review → 自動 confirmed
            if self._item.ocr_status == 'needs_review':
                self._review_bar.setVisible(False)

            self.item_updated.emit(self._item.id)
            logger.info(f"已儲存編輯 item #{self._item.id}")
            self.accept()
        except Exception as e:
            logger.error(f"儲存失敗: {e}", exc_info=True)
            QMessageBox.warning(self, "儲存失敗", str(e))

    def _confirm_ocr(self):
        try:
            if self._text_edit.toPlainText() != (self._item.get_effective_text() or ''):
                return  # Applying a candidate requires the explicit Save action.
            self._repo.confirm_review(self._item.id, self._item.edit_revision,
                                      expected_ocr_job_id=self._item.ocr_job_id,
                                      expected_ocr_text=self._item.text_content)
            self._review_bar.setVisible(False)
            self.item_updated.emit(self._item.id)
            logger.info(f"已確認 OCR item #{self._item.id}")
        except Exception as e:
            logger.error(f"確認失敗: {e}", exc_info=True)
            QMessageBox.warning(self, "確認失敗", str(e))

    def _rerun_ocr(self):
        if not self._ocr_worker or not self._item.raw_image_path:
            return
        try:
            # Preserve unsaved edits and notes before closing the dialog for a
            # rerun. OCR updates the machine text, never this manual draft.
            draft = self._text_edit.toPlainText()
            text_dirty = draft != (self._item.get_effective_text() or '')
            note_html = self._note_edit.toHtml()
            if text_dirty or note_html != self._loaded_note_html:
                # Both note-only and text drafts use the same revision check.
                # An untouched stale editor never rewrites newer saved notes.
                self._repo.save_editor_content(
                    self._item.id, draft, note_html, self._note_edit.toPlainText(),
                    self._item.edit_revision, update_text=text_dirty,
                    confirm_review=text_dirty,
                    expected_ocr_job_id=self._item.ocr_job_id,
                    expected_ocr_text=self._item.text_content)
            self._loaded_note_html = note_html
            self._item = self._repo.get_by_id(self._item.id)
            abs_path = self._file_mgr.get_abs_path(self._item.raw_image_path)
            accepted = self._ocr_worker.queue_ocr(self._item.id, abs_path, 'screen')
            self.item_updated.emit(self._item.id)
            if accepted:
                self.accept()
        except Exception as exc:
            logger.exception('重跑 OCR 未開始')
            QMessageBox.warning(self, '重跑 OCR 未開始', str(exc))

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
