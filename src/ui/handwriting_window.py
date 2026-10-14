"""One-character handwriting with ranked candidates and explicit copy."""
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel

from ..clipboard.writer import write_text_to_clipboard
from ..core.branding import application_icon
from ..core.version import APP_DISPLAY_NAME
from ..handwriting.controller import HandwritingController
from .handwriting_pad import HandwritingPad


class HandwritingWindow(QWidget):
    stopped = Signal()

    def __init__(self, parent=None, *, controller=None):
        super().__init__(parent, Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle(f'{APP_DISPLAY_NAME} - 手寫')
        self.setWindowIcon(application_icon())
        self.resize(440, 460)
        self.controller = controller or HandwritingController(self)
        self._submitted = None
        self._shutting_down = False
        self._buttons = []
        layout = QVBoxLayout(self)
        note = QLabel('寫一個字，點選候選即可複製')
        note.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(note)
        self.pad = HandwritingPad(self)
        layout.addWidget(self.pad, 1)
        tools = QHBoxLayout()
        self.undo_button = QPushButton('撤銷一筆')
        self.clear_button = QPushButton('清除')
        self.recognize_button = QPushButton('辨識')
        tools.addWidget(self.undo_button)
        tools.addWidget(self.clear_button)
        tools.addStretch()
        tools.addWidget(self.recognize_button)
        layout.addLayout(tools)
        self.status = QLabel('候選以常用繁體字為主，部分罕字未收錄')
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.candidate_layout = QHBoxLayout()
        self.candidate_layout.setSpacing(4)
        layout.addLayout(self.candidate_layout)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(350)
        self._debounce.timeout.connect(self.recognize)
        self.undo_button.clicked.connect(self.pad.undo)
        self.clear_button.clicked.connect(self.pad.clear)
        self.recognize_button.clicked.connect(self.recognize)
        self.pad.changed.connect(self._changed)
        self.pad.stroke_finished.connect(self._debounce.start)
        self.pad.limit_reached.connect(lambda: self.status.setText('筆畫已達上限，請清除後重寫'))
        self.controller.candidates.connect(self._candidates)
        self.controller.failed.connect(self.status.setText)
        self.controller.stopped.connect(self._stopped)
        self.controller.busy_changed.connect(self._busy)
        self._changed()

    def _clear_candidates(self):
        for button in self._buttons:
            self.candidate_layout.removeWidget(button)
            button.deleteLater()
        self._buttons.clear()

    def _changed(self):
        self._submitted = None
        self._debounce.stop()
        self._clear_candidates()
        self.status.setText('書寫中…' if self.pad.buffer.active is not None else
                            ('等待辨識…' if self.pad.buffer.strokes else
                             '候選以常用繁體字為主，部分罕字未收錄'))
        self.recognize_button.setEnabled(bool(self.pad.buffer.strokes) and self.pad.buffer.active is None)
        self.undo_button.setEnabled(bool(self.pad.buffer.strokes) or self.pad.buffer.active is not None)
        self.clear_button.setEnabled(bool(self.pad.buffer.strokes) or self.pad.buffer.active is not None)

    def _busy(self, busy):
        if busy:
            self.status.setText('辨識中…')

    def recognize(self):
        self._debounce.stop()
        if self._shutting_down or self.pad.buffer.active is not None or not self.pad.buffer.strokes:
            return
        revision = self.pad.buffer.revision
        generation = self.controller.submit(self.pad.buffer.snapshot())
        if generation is not None:
            self._submitted = generation, revision

    def _candidates(self, generation, values):
        if (not self.isVisible() or self._shutting_down or
                self._submitted != (generation, self.pad.buffer.revision)):
            return
        self._clear_candidates()
        revision = self.pad.buffer.revision
        for value in values:
            button = QPushButton(value.replace('&', '&&'))
            button.setMinimumWidth(32)
            button.setAccessibleName(f'複製候選字 {value}')
            button.setStyleSheet('font-size: 22px; padding: 4px;')
            button.clicked.connect(lambda checked=False, text=value, token=revision: self._copy(text, token))
            self.candidate_layout.addWidget(button)
            self._buttons.append(button)
        self.status.setText('點選候選字即可複製' if values else '沒有候選字，請重寫')

    def _copy(self, value, revision):
        if self._shutting_down or self.pad.buffer.revision != revision or not self.isVisible():
            return
        # Exact model label: no OCR postprocessing, zhconv or implicit SendInput.
        if write_text_to_clipboard(value):
            self.status.setText(f'已複製：{value}')

    def _stopped(self):
        self.stopped.emit()
        if self.isVisible() and not self._shutting_down:
            self.controller.resume()

    def showEvent(self, event):
        self.controller.resume()
        super().showEvent(event)

    def closeEvent(self, event):
        self._debounce.stop()
        self.pad.buffer.cancel()
        self.pad._mouse_active = False
        self.pad._touch_id = None
        self._changed()
        self.controller.stop()
        super().closeEvent(event)

    def shutdown(self):
        self._shutting_down = True
        self.close()
