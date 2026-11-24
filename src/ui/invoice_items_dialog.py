"""Preview the available invoice items and copy the chosen fields on demand."""
from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from ..clipboard.writer import copy_text
from .copy_preferences import (
    INVOICE_ITEM_COLUMNS, invoice_copy_field, invoice_copy_field_combo, save_invoice_copy_field,
)
from .design_system import apply_button
from .theme import apply_combo_box_theme


HEADERS = tuple(INVOICE_ITEM_COLUMNS.values())


class InvoiceItemsDialog(QDialog):
    retry_requested = Signal()

    def __init__(self, parent, *, cfg=None):
        super().__init__(parent)
        self._cfg = cfg
        self._rows = ()
        self.setWindowTitle('發票 QR 品項')
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
        self.resize(390, 240)
        layout = QVBoxLayout(self)
        self.summary = QLabel('正在讀取品項…')
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.summary)
        self.table = QTableWidget(0, len(HEADERS))
        self.table.setHorizontalHeaderLabels(list(HEADERS))
        self.table.setAccessibleName('發票品項明細')
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2):
            self.table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table)
        self.copy_notice = QLabel()
        self.copy_notice.setWordWrap(True)
        self.copy_notice.hide()
        layout.addWidget(self.copy_notice)
        actions = QHBoxLayout()
        again = apply_button(QPushButton('重新框選'), compact=True)
        again.clicked.connect(self.retry_requested)
        actions.addWidget(again)
        actions.addStretch()
        self.copy_field = invoice_copy_field_combo(cfg)
        apply_combo_box_theme(self.copy_field)
        actions.addWidget(self.copy_field)
        self.copy_button = apply_button(QPushButton(), 'primary', compact=True)
        self.copy_button.setEnabled(False)
        self.copy_button.clicked.connect(self._copy)
        actions.addWidget(self.copy_button)
        layout.addLayout(actions)
        self._refresh_copy_action()
        self.copy_field.currentIndexChanged.connect(self._field_changed)
        settings_saved = getattr(parent, 'settings_saved', None)
        if settings_saved is not None:
            settings_saved.connect(self._load_field)

    def _load_field(self):
        with QSignalBlocker(self.copy_field):
            self.copy_field.setCurrentIndex(self.copy_field.findData(invoice_copy_field(self._cfg)))
        self._refresh_copy_action()

    def _refresh_copy_action(self):
        label = INVOICE_ITEM_COLUMNS.get(self.copy_field.currentData(), '明細')
        self.copy_button.setText('複製' + label)
        self.copy_notice.hide()

    def _field_changed(self):
        self._refresh_copy_action()
        if self._cfg is None or not self.isVisible():
            return
        try:
            save_invoice_copy_field(self._cfg, self.copy_field.currentData())
        except (OSError, ValueError):
            self._load_field()
            self.copy_notice.setText('欄位未能儲存，請檢查設定檔與資料夾權限。')
            self.copy_notice.show()

    def set_result(self, result, error):
        self._rows = result.rows if result else ()
        self.summary.setText(error or result.summary)
        self.table.setRowCount(len(self._rows))
        for row, values in enumerate(self._rows):
            for column, text in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(text))
        self.copy_button.setEnabled(bool(self._rows))
        self._refresh_copy_action()

    def _copy(self):
        if not self._rows or not self.isVisible():
            return
        field = self.copy_field.currentData()
        if field == 'all':
            lines = ['\t'.join(row) for row in (HEADERS, *self._rows)]
        else:
            column = tuple(INVOICE_ITEM_COLUMNS).index(field)
            lines = [row[column] for row in self._rows]

        # Treat one receipt as one text result so field and row separators use
        # the same whitespace / replacement rules as an ordinary OCR copy.
        if copy_text('\n'.join(lines), self._cfg):
            self.copy_notice.hide()
            self.copy_button.setText('已複製' + INVOICE_ITEM_COLUMNS.get(field, '明細'))
        else:
            self._refresh_copy_action()
            self.copy_notice.setText('套用複製清理後沒有可複製的內容。')
            self.copy_notice.show()
