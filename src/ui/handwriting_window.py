"""Native single-character workspace with bounded, attributable candidates."""
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QMessageBox, QScrollArea, QFrame,
)

from ..clipboard.writer import write_text_to_clipboard
from ..core.branding import application_icon
from ..core.version import APP_DISPLAY_NAME
from ..handwriting.controller import HandwritingController
from ..handwriting.image_controller import ImageHandwritingController
from ..handwriting.candidates import Candidate
from .components.candidate_panel import CandidatePanel
from .design_system import apply_button, TYPE_TITLE, TYPE_META
from .theme import _BG, _TEXT_PRI, _TEXT_SEC, _SUCCESS, _ERROR
from .handwriting_pad import HandwritingPad


class HandwritingWindow(QWidget):
    stopped = Signal()

    def __init__(self, parent=None, *, controller=None, image_controller=None):
        super().__init__(parent, Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle(f'{APP_DISPLAY_NAME} - 手寫')
        self.setWindowIcon(application_icon())
        self.setStyleSheet(f'HandwritingWindow {{ background: {_BG}; color: {_TEXT_PRI}; }}')
        self.resize(460, 640)
        self.setMinimumSize(360, 360)
        self.controller = controller or HandwritingController(self)
        self.image_controller = image_controller or ImageHandwritingController(self)
        self._image_submitted = None
        self._image_submitting = None
        self._stroke_values = ()
        self._submitted = None
        self._submitting_revision = None
        self._shutting_down = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.workspace_scroll = QScrollArea(self)
        self.workspace_scroll.setWidgetResizable(True)
        self.workspace_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.workspace_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.workspace_scroll.setStyleSheet(f'''
            QScrollArea, QScrollArea > QWidget > QWidget {{ background: {_BG}; }}
            QScrollBar:vertical {{ background: {_BG}; width: 8px; margin: 0; }}
            QScrollBar::handle:vertical {{ background: #3b4658; border-radius: 4px; min-height: 24px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        ''')
        workspace = QWidget()
        self.workspace_scroll.setWidget(workspace)
        outer.addWidget(self.workspace_scroll)
        layout = QVBoxLayout(workspace)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)
        header = QHBoxLayout()
        title = QLabel('手寫')
        title.setTextFormat(Qt.TextFormat.PlainText)
        title.setStyleSheet(f'color: {_TEXT_PRI}; font-size: {TYPE_TITLE}px; font-weight: 600;')
        self.help_button = apply_button(QPushButton('說明'), 'ghost', compact=True)
        self.help_button.setAccessibleName('手寫功能與字庫說明')
        self.help_button.clicked.connect(self._show_help)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(self.help_button)
        layout.addLayout(header)
        self.pad = HandwritingPad(self)
        layout.addWidget(self.pad, 1)
        tools = QHBoxLayout()
        tools.setSpacing(8)
        self.undo_button = apply_button(QPushButton('撤銷一筆'))
        self.clear_button = apply_button(QPushButton('清除'))
        self.image_button = apply_button(QPushButton('影像補辨'))
        self.image_button.setToolTip('將目前筆跡交給離線 OCR 補充候選，需要人工選字；再按一次取消')
        self.recognize_button = apply_button(QPushButton('辨識'), 'primary')
        self.undo_button.setToolTip('撤銷最後一筆 (Ctrl+Z)')
        self.clear_button.setToolTip('清除全部筆跡')
        self.recognize_button.setToolTip('重新辨識目前的字')
        tools.addWidget(self.undo_button)
        tools.addWidget(self.clear_button)
        tools.addStretch()
        tools.addWidget(self.image_button)
        tools.addWidget(self.recognize_button)
        layout.addLayout(tools)
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.status.setMinimumHeight(24)
        layout.addWidget(self.status)
        self.candidate_panel = CandidatePanel(self)
        self._buttons = self.candidate_panel.buttons
        self.candidate_layout = self.candidate_panel.grid
        self.candidate_panel.selected.connect(self._copy)
        self.candidate_panel.expansion_changed.connect(self._expanded)
        layout.addWidget(self.candidate_panel)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(350)
        self._debounce.timeout.connect(self.recognize)
        self._copy_feedback = QTimer(self)
        self._copy_feedback.setSingleShot(True)
        self._copy_feedback.setInterval(2200)
        self._copy_feedback.timeout.connect(self._ready_status)
        self.undo_button.clicked.connect(self.pad.undo)
        self.clear_button.clicked.connect(self.pad.clear)
        self.recognize_button.clicked.connect(self.recognize)
        self.image_button.clicked.connect(self.recognize_image)
        self.pad.changed.connect(self._changed)
        self.pad.stroke_finished.connect(self._debounce.start)
        self.pad.limit_reached.connect(lambda: self._set_status('筆畫已達上限，請清除後重寫', 'error'))
        self.controller.candidates.connect(self._candidates)
        if hasattr(self.controller, 'request_failed'):
            self.controller.request_failed.connect(self._failed)
        else:
            self.controller.failed.connect(self._legacy_failed)
        self.image_controller.candidates.connect(self._image_candidates)
        self.image_controller.request_failed.connect(self._image_failed)
        self.image_controller.busy_changed.connect(self._image_busy)
        self.image_controller.stopped.connect(self._stopped)
        self.controller.stopped.connect(self._stopped)
        self.controller.busy_changed.connect(self._busy)
        self._undo_shortcut = QShortcut(QKeySequence.StandardKey.Undo, self)
        self._undo_shortcut.activated.connect(self.pad.undo)
        self._close_shortcut = QShortcut(QKeySequence.StandardKey.Cancel, self)
        self._close_shortcut.activated.connect(self.close)
        self._changed()

    def _set_status(self, message, state='normal'):
        color = _ERROR if state == 'error' else (_SUCCESS if state == 'success' else _TEXT_SEC)
        self.status.setStyleSheet(f'font-size: {TYPE_META}px; color: {color}; background: transparent;')
        self.status.setText(message)
        self.status.setAccessibleName(message)

    def _show_help(self):
        QMessageBox.information(self, '手寫說明',
            '一次寫一個字，停筆後選字複製，再以 Ctrl+V 貼上。\n\n'
            '候選是模型排序，並非正確機率。筆順、連筆與字的大小都可能影響結果。\n\n'
            '目前筆畫字庫有 11,853 字，也缺少「內、吳、戶、辦、嚐」等常用字。'
            '影像補辨會另外用離線 OCR 補充最多兩個候選，仍需人工選字，不能保證補準缺字。')

    def _expanded(self, expanded):
        if expanded:
            screen = self.screen()
            available = screen.availableGeometry().height() if screen else 800
            self.resize(self.width(), min(max(self.height(), 720), max(360, available-48)))
            QTimer.singleShot(0, lambda: self.workspace_scroll.ensureWidgetVisible(self.candidate_panel, 0, 8))

    def _fit_screen(self):
        screen = self.screen()
        if screen:
            available = screen.availableGeometry()
            self.resize(min(self.width(), max(360, available.width()-32)),
                        min(self.height(), max(360, available.height()-48)))

    def _clear_candidates(self):
        self.candidate_panel.clear()

    def _changed(self):
        self._cancel_image()
        self._stroke_values = ()
        self._submitted = None
        self._debounce.stop()
        self._copy_feedback.stop()
        self._clear_candidates()
        self._set_status('書寫中…' if self.pad.buffer.active is not None else
                         ('等待辨識…' if self.pad.buffer.strokes else '在方格內寫一個字'))
        has_ink = bool(self.pad.buffer.strokes) or self.pad.buffer.active is not None
        self.recognize_button.setEnabled(bool(self.pad.buffer.strokes) and self.pad.buffer.active is None)
        self.image_button.setEnabled(bool(self.pad.buffer.strokes) and self.pad.buffer.active is None)
        self.undo_button.setEnabled(has_ink)
        self.clear_button.setEnabled(has_ink)

    def _busy(self, busy):
        if busy and (self._submitted is not None or self._submitting_revision is not None):
            self._set_status('辨識中…')

    def recognize(self):
        self._debounce.stop()
        if self._shutting_down or self.pad.buffer.active is not None or not self.pad.buffer.strokes:
            return
        self._cancel_image()
        revision = self.pad.buffer.revision
        self._copy_feedback.stop()
        self._submitted = None
        self._clear_candidates()
        self._submitting_revision = revision
        try:
            generation = self.controller.submit(self.pad.buffer.snapshot())
        finally:
            self._submitting_revision = None
        if generation is not None:
            self._submitted = generation, revision

    def _failed(self, generation, message):
        if not self.isVisible() or self._shutting_down:
            return
        revision = self.pad.buffer.revision
        if (self._submitted != (generation, revision)
                and self._submitting_revision != revision):
            return
        self._copy_feedback.stop()
        self._set_status(message, 'error')

    def _legacy_failed(self, message):
        if self._submitted is not None or self._submitting_revision is not None:
            self._failed(self._submitted[0] if self._submitted else -1, message)

    def _candidates(self, generation, values):
        if (not self.isVisible() or self._shutting_down or
                self._submitted != (generation, self.pad.buffer.revision)):
            return
        self._stroke_values = tuple(values)
        self.candidate_panel.set_candidates(values, self.pad.buffer.revision)
        QTimer.singleShot(0, lambda: self.workspace_scroll.ensureWidgetVisible(self.candidate_panel, 0, 4))
        self._ready_status()

    @property
    def is_stopped(self):
        return self.controller.is_stopped and self.image_controller.is_stopped

    def _cancel_image(self):
        self._image_submitted = None
        self._image_submitting = None
        if not self.image_controller.is_stopped:
            self.image_controller.stop()

    def _image_busy(self, busy):
        self.image_button.setText('取消補辨' if busy else '影像補辨')
        if busy and (self._image_submitted is not None or self._image_submitting is not None):
            self._set_status('影像補辨中，可再次按下取消…')

    def recognize_image(self):
        if self._image_submitted is not None:
            self._cancel_image()
            self._ready_status()
            return
        if (self._shutting_down or not self.image_controller.is_stopped
                or self.pad.buffer.active is not None or not self.pad.buffer.strokes):
            return
        self._debounce.stop()
        self._submitted = None  # Retire the native request without discarding its displayed candidates.
        revision = self.pad.buffer.revision
        self.image_controller.resume()
        self._image_submitting = revision
        try:
            generation = self.image_controller.submit(self.pad.buffer.snapshot())
        finally:
            self._image_submitting = None
        if generation is not None:
            self._image_submitted = generation, revision

    def _image_failed(self, generation, message):
        if (not self.isVisible() or self._shutting_down or
                (self._image_submitted != (generation, self.pad.buffer.revision)
                 and self._image_submitting != self.pad.buffer.revision)):
            return
        self._image_submitted = None
        self._set_status('影像補辨失敗，筆畫候選仍可使用' if self._buttons else '影像補辨失敗，請重試或重寫', 'error')

    def _image_candidates(self, generation, values):
        revision = self.pad.buffer.revision
        if (not self.isVisible() or self._shutting_down or
                self._image_submitted != (generation, revision)):
            return
        self._image_submitted = None
        combined = {}
        for index, value in enumerate(self._stroke_values):
            item = Candidate(value, raw_rank=index+1) if isinstance(value, str) else value
            combined[item.label] = item
        for image in values:
            old = combined.get(image.label)
            combined[image.label] = Candidate(image.label, old.raw_rank if old else 0,
                old.normalized_rank if old else 0, image.image_rank)
        self.candidate_panel.set_candidates(tuple(combined.values()), revision)
        self._set_status('已補入影像候選，請人工選字' if values else '影像未辨出單字，請選筆畫候選或重寫')
        QTimer.singleShot(0, lambda: self.workspace_scroll.ensureWidgetVisible(self.candidate_panel, 0, 4))

    def _ready_status(self):
        if self._buttons:
            self._set_status('點選或按 Enter 複製')
        elif self.pad.buffer.strokes:
            self._set_status('沒有候選字，請重寫')

    def _copy(self, value, revision):
        if self._shutting_down or self.pad.buffer.revision != revision or not self.isVisible():
            return
        if write_text_to_clipboard(value):
            self.candidate_panel.mark_copied(value)
            self._set_status(f'已複製「{value}」', 'success')
            self._copy_feedback.start()

    def _stopped(self):
        if self.is_stopped:
            self.stopped.emit()
        if self.isVisible() and not self._shutting_down:
            self.controller.resume()

    def showEvent(self, event):
        self.controller.resume()
        self._fit_screen()
        super().showEvent(event)

    def closeEvent(self, event):
        self._debounce.stop()
        self._copy_feedback.stop()
        self.pad.buffer.cancel()
        self.pad._mouse_active = False
        self.pad._touch_id = None
        self._changed()
        self.controller.stop()
        self.image_controller.stop()
        super().closeEvent(event)

    def shutdown(self):
        self._shutting_down = True
        self.close()
