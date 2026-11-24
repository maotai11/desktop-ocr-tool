"""A one-shot receipt item preview. Only the explicit copy action writes data."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from ..clipboard.writer import write_text_to_clipboard
from .design_system import apply_button


HEADERS = ('品名', '數量', '單價')


class InvoiceItemsDialog(QDialog):
    retry_requested = Signal()

    def __init__(self, parent):
        super().__init__(parent)
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
        actions = QHBoxLayout()
        again = apply_button(QPushButton('重新框選'), compact=True)
        again.clicked.connect(self.retry_requested)
        actions.addWidget(again)
        actions.addStretch()
        self.copy_button = apply_button(QPushButton('複製明細'), 'primary', compact=True)
        self.copy_button.setEnabled(False)
        self.copy_button.clicked.connect(self._copy)
        actions.addWidget(self.copy_button)
        layout.addLayout(actions)
        self._rows = ()

    def set_result(self, result, error):
        self._rows = result.rows if result else ()
        self.summary.setText(error or result.summary)
        self.table.setRowCount(len(self._rows))
        for row, values in enumerate(self._rows):
            for column, text in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(text))
        self.copy_button.setEnabled(bool(self._rows))

    def _copy(self):
        if self._rows:
            # Retain the row/column structure even if OCR cleanup is enabled.
            lines = ['\t'.join(row) for row in (HEADERS, *self._rows)]
            write_text_to_clipboard('\n'.join(lines))
            self.copy_button.setText('已複製明細')
