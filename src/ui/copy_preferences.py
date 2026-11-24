"""Shared copy-only drafts for full settings and the floating quick popup."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QVBoxLayout, QHBoxLayout,
    QGroupBox, QCheckBox, QComboBox, QLineEdit, QToolButton, QToolTip, QMessageBox)
from ..clipboard.text_cleanup import (COPY_CLEANUP_OPTIONS, COPY_CLEANUP_MODE_KEYS,
    COPY_REPLACEMENT_KEYS, validate_copy_replacement)
from .theme import (_BG, _BG_RAISE, _TEXT_PRI, _TEXT_SEC, _BORDER,
                    _ACCENT, apply_combo_box_theme)


INVOICE_ITEM_COLUMNS = {'name': '品名', 'quantity': '數量', 'unit_price': '單價'}


def invoice_copy_field(cfg):
    value = cfg.get('clipboard', 'invoice_item_copy_field', default='all') if cfg is not None else 'all'
    return value if isinstance(value, str) and value in INVOICE_ITEM_COLUMNS else 'all'


def invoice_copy_field_combo(cfg):
    combo = QComboBox()
    combo.addItem('全部欄位', 'all')
    for field, label in INVOICE_ITEM_COLUMNS.items():
        combo.addItem('只取' + label, field)
    combo.setCurrentIndex(combo.findData(invoice_copy_field(cfg)))
    combo.setAccessibleName('發票品項複製欄位')
    combo.setToolTip('全部欄位含表頭。單一欄位每項一行，複製時套用剪貼簿的複製清理。')
    return combo


def read_copy_mode(cfg):
    return 'select' if cfg.get('clipboard', 'ocr_copy_mode', default='all') == 'select' else 'all'


def _write_copy_values(cfg, updates):
    update = getattr(cfg, 'update_section', None)
    if callable(update):
        update('clipboard', updates)
    else:
        for key, value in updates.items():
            cfg.set('clipboard', key, value)


def save_invoice_copy_field(cfg, field):
    if field not in ('all', *INVOICE_ITEM_COLUMNS):
        raise ValueError('未知的發票品項欄位')
    if field != invoice_copy_field(cfg):
        _write_copy_values(cfg, {'invoice_item_copy_field': field})


def set_copy_mode(cfg, mode):
    if mode not in ('all', 'select'):
        return False
    if cfg.get('clipboard', 'ocr_copy_mode', default='all') != mode:
        _write_copy_values(cfg, {'ocr_copy_mode': mode})
    return True


def cleanup_summary(cfg):
    enabled = [key for key, _, _ in COPY_CLEANUP_OPTIONS
               if cfg.get('clipboard', key, default=False) is True]
    for key in enabled:
        mode = cfg.get('clipboard', COPY_CLEANUP_MODE_KEYS[key], default='remove')
        if mode not in ('remove', 'replace') or (mode == 'replace' and not validate_copy_replacement(
                cfg.get('clipboard', COPY_REPLACEMENT_KEYS[key], default=''))):
            return '需檢查'
    return f'{len(enabled)} 項啟用' if enabled else '原樣輸出'


class CopyCleanupEditor(QGroupBox):
    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self._cfg = cfg
        self.setStyleSheet(f'QCheckBox {{ color:{_TEXT_PRI}; spacing:6px; }} '
            f'QCheckBox::indicator {{ width:15px; height:15px; border-radius:3px; border:1px solid {_TEXT_SEC}; background:{_BG_RAISE}; }} '
            f'QCheckBox::indicator:checked {{ background:{_ACCENT}; border-color:{_ACCENT}; }} '
            f'QCheckBox::indicator:hover {{ border-color:{_ACCENT}; }}')
        from ..clipboard.text_cleanup import COPY_CLEANUP_OPTIONS, MAX_REPLACEMENT_LENGTH
        self._copy_cleanup_checks = {}
        self._copy_cleanup_modes = {}
        self._copy_cleanup_replacements = {}
        self._original_cleanup_replacements = {}
        self._cleanup_replacement_edited = set()
        cleanup = self
        self.setTitle('複製清理')
        cleanup.setToolTip('套用於一般 OCR、發票 QR 品項、歷史與編輯器的文字複製，以及「貼上最後一筆」；文件套版及手寫候選保留原樣。原始文字不變。個別規則優先於「所有標點與空白」；替代文字不會再次被清理。')
        cleanup_rows = QVBoxLayout(cleanup)
        cleanup_rows.setSpacing(10)
        titles = {'copy_remove_commas': '逗號', 'copy_remove_apostrophes': '撇號',
                  'copy_remove_newlines': '換行', 'copy_remove_punctuation': '所有標點與空白'}
        notes = {'copy_remove_commas': '處理半形／全形逗號（,，）。',
                 'copy_remove_apostrophes': "處理撇號（'‘’＇），不改動其他引號。",
                 'copy_remove_newlines': '處理換行；Windows CRLF 算一次。其他空白保留。',
                 'copy_remove_punctuation': '處理 Unicode 標點及所有空白，包含小數點、負號、半形／全形空格、Tab 與換行；數值格式也會改變。'}
        for key, _, _ in COPY_CLEANUP_OPTIONS:
            suffix = key.removeprefix('copy_remove_')
            row = QHBoxLayout()
            check = QCheckBox(titles[key])
            check.setChecked(self._cfg.get('clipboard', key, default=False) is True)
            check.setFixedWidth(130)
            mode = QComboBox()
            mode.addItem('移除', 'remove')
            mode.addItem('替換', 'replace')
            chosen = self._cfg.get('clipboard', 'copy_cleanup_mode_' + suffix, default='remove')
            if chosen not in ('remove', 'replace'):
                mode.addItem('請選擇處理方式', None)
                mode.setCurrentIndex(2)
            else:
                mode.setCurrentIndex(1 if chosen == 'replace' else 0)
            mode.setFixedWidth(90)
            edit = QLineEdit()
            value = self._cfg.get('clipboard', 'copy_replace_' + suffix, default='')
            self._original_cleanup_replacements[key] = value
            value = value if isinstance(value, str) else ''
            edit.setMaxLength(max(MAX_REPLACEMENT_LENGTH * 2, len(value.encode('utf-16-le', errors='surrogatepass')) // 2))
            edit.setText(value)
            edit.setMinimumWidth(0)
            edit.textChanged.connect(lambda text, rule=key: self._cleanup_replacement_edited.add(rule))
            edit.setPlaceholderText('自訂替代文字')
            edit.setAccessibleName(titles[key] + '替代文字')
            mode.setAccessibleName(titles[key] + '處理方式')
            note = notes[key] + ' 原始文字不變。' + '\n移除會刪除指定字元；替換可輸入自己的字串，留空也可。最多 64 字，儲存後立即生效。'
            for control in (check, mode, edit):
                control.setToolTip(note)
            info = self._info_button(note)
            def refresh(*_, checkbox=check, action=mode, replacement=edit):
                action.setEnabled(checkbox.isChecked())
                replacement.setVisible(action.currentData() == 'replace')
                replacement.setEnabled(checkbox.isChecked())
            check.toggled.connect(refresh)
            mode.currentIndexChanged.connect(refresh)
            refresh()
            row.addWidget(check)
            row.addWidget(mode)
            row.addWidget(edit, 1)
            row.addStretch()
            row.addWidget(info)
            cleanup_rows.addLayout(row)
            self._copy_cleanup_checks[key] = check
            self._copy_cleanup_modes[key] = mode
            self._copy_cleanup_replacements[key] = edit

        for mode in self._copy_cleanup_modes.values():
            apply_combo_box_theme(mode)

    @property
    def checks(self): return self._copy_cleanup_checks
    @property
    def modes(self): return self._copy_cleanup_modes
    @property
    def replacements(self): return self._copy_cleanup_replacements

    def _info_button(self, note):
        button = QToolButton()
        button.setText('?')
        button.setToolTip(note)
        button.setAccessibleName('說明')
        button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        button.setFixedSize(24, 24)
        def show_note():
            if not getattr(self.window(), '_closed', False) and button.isVisible():
                QToolTip.showText(button.mapToGlobal(button.rect().bottomLeft()), note, button)
        button.clicked.connect(show_note)
        return button

    def validation_issue(self):
        for key in self.checks:
            active, mode = self.checks[key].isChecked(), self.modes[key].currentData()
            if active and mode not in ('remove', 'replace'):
                return self.modes[key], '請為每個規則選擇移除或替換。'
            replacement = (self.replacements[key].text() if key in self._cleanup_replacement_edited
                           else self._original_cleanup_replacements[key])
            if ((active and mode == 'replace') or key in self._cleanup_replacement_edited) and not validate_copy_replacement(replacement):
                return self.replacements[key], '替代文字最多 64 字，不可包含換行或控制字元。請調整後再儲存。'
        return None

    def payload(self):
        if self.validation_issue() is not None:
            raise ValueError('Invalid copy cleanup draft')
        updates = {}
        for key, check in self.checks.items():
            updates[key] = check.isChecked()
            if self.modes[key].currentData() is not None:
                updates[COPY_CLEANUP_MODE_KEYS[key]] = self.modes[key].currentData()
            if key in self._cleanup_replacement_edited:
                updates[COPY_REPLACEMENT_KEYS[key]] = self.replacements[key].text()
        return updates

    def save(self):
        _write_copy_values(self._cfg, self.payload())


class CopyCleanupDialog(QDialog):
    preferences_saved = Signal()

    def __init__(self, cfg, parent=None, compact=True):
        super().__init__(parent)
        self._closed = False
        self.setWindowTitle('複製清理')
        self.setWindowFlag(Qt.WindowType.Tool, True)
        self.resize(540 if compact else 620, 270)
        self.setStyleSheet(f'QDialog,QWidget {{ background:{_BG}; color:{_TEXT_PRI}; }} '
            f'QGroupBox {{ border:1px solid {_BORDER}; border-radius:7px; margin-top:10px; padding:12px 8px 8px; }} '
            f'QGroupBox::title {{ subcontrol-origin:margin; left:8px; }} '
            f'QLineEdit,QComboBox {{ background:{_BG_RAISE}; color:{_TEXT_PRI}; border:1px solid {_BORDER}; padding:4px; }} '
            f'QPushButton {{ background:{_BG_RAISE}; color:{_TEXT_PRI}; padding:6px 12px; }} '
            f'QToolButton {{ color:{_TEXT_SEC}; border:1px solid {_BORDER}; border-radius:10px; }}')
        layout = QVBoxLayout(self)
        if compact:
            layout.setContentsMargins(8, 8, 8, 8)
        self._editor = CopyCleanupEditor(cfg, self)
        layout.addWidget(self._editor)
        for name in ('_copy_cleanup_checks', '_copy_cleanup_modes', '_copy_cleanup_replacements'):
            setattr(self, name, getattr(self._editor, name))
        self._buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Cancel)
        self._apply_button = self._buttons.button(QDialogButtonBox.StandardButton.Apply)
        self._apply_button.setText('套用')
        self._buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        self._apply_button.clicked.connect(self._save)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

    def _live(self):
        if self._closed:
            return False
        try:
            owner = self.parentWidget()
            return (self.isVisible() and not self.isMinimized()
                    and not getattr(owner, '_ui_closing', False)
                    and (owner is None or owner.isVisible()))
        except RuntimeError:
            self._closed = True
            return False

    def _save(self):
        if not self._live(): return
        issue = self._editor.validation_issue()
        if issue:
            control, message = issue
            QMessageBox.warning(self, '請確認複製清理', message)
            if self._live(): control.setFocus()
            return
        try:
            self._editor.save()
        except (OSError, ValueError) as error:
            if self._live():
                message = ('複製設定格式有誤，請先修正設定檔後再試。' if isinstance(error, ValueError)
                           else '設定未儲存，請確認資料夾可寫入後再試。')
                QMessageBox.warning(self, '無法儲存複製設定', message)
            return
        if not self._live(): return
        self.preferences_saved.emit()
        if self._live(): self.accept()

    def hideEvent(self, event):
        super().hideEvent(event)
        if not self._closed:
            self.reject()

    def done(self, result):
        if self._closed: return
        self._closed = True
        QToolTip.hideText()
        for child in reversed(self.findChildren(QDialog)):
            if child.isVisible():
                child.setEnabled(False)
                child.reject()
        super().done(result)
