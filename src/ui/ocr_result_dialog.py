"""Choose OCR text explicitly; the owner performs the clipboard write."""
import json
import math

from PySide6.QtCore import QEasingCurve, QPointF, QPropertyAnimation, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QKeySequence,
    QPainter,
    QPen,
    QPixmap,
    QPolygonF,
    QShortcut,
)
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QStyle,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .theme import (
    _ACCENT,
    _ACCENT_18,
    _ACCENT_H,
    _ACCENT_T,
    _BG,
    _BG_HOVER,
    _BG_RAISE,
    _BORDER,
    _BORDER_L,
    _TEXT_PRI,
    _TEXT_SEC,
)


def _polygon(box):
    """Accept source-image quadrilaterals and legacy rectangular bounds."""
    try:
        if len(box) == 4 and all(isinstance(v, (int, float)) for v in box):
            x1, y1, x2, y2 = box
            box = [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]
        if len(box) != 4 or any(len(point) != 2 for point in box):
            return None
        if not all(math.isfinite(float(value)) for point in box for value in point):
            return None
        polygon = QPolygonF([QPointF(float(x), float(y)) for x, y in box])
        bounds = polygon.boundingRect()
        if bounds.width() <= 0 or bounds.height() <= 0:
            return None
        return polygon
    except (TypeError, ValueError, OverflowError):
        return None


def _entries(text, detail_json):
    try:
        detail = json.loads(detail_json) if isinstance(detail_json, str) else detail_json
    except (TypeError, ValueError):
        detail = None
    rows = []
    if isinstance(detail, list):
        for row in detail:
            if not isinstance(row, dict) or not isinstance(row.get('text'), str):
                continue
            if not row['text'].strip():
                continue
            polygon = _polygon(row.get('box'))
            rows.append({'text': row['text'], 'polygon': polygon,
                         'box': [[p.x(), p.y()] for p in polygon] if polygon else None})
    if rows:
        # Native detail order may differ from the visible reading order.
        rows.sort(key=lambda row: (
            row['polygon'].boundingRect().center().y(),
            row['polygon'].boundingRect().left(),
        ) if row['polygon'] is not None else (float('inf'), 0))
        return rows
    return [{'text': line, 'polygon': None, 'box': None}
            for line in text.split('\n') if line.strip()]


class _ImageSelection(QWidget):
    selection_requested = Signal(object, bool)

    def __init__(self, image_path, entries, parent=None):
        super().__init__(parent)
        self._pixmap = QPixmap(image_path or '')
        self._entries = entries
        self._selected = set()
        self._press = None
        self._drag = None
        self.setMinimumSize(220, 180)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setAccessibleName('在原圖選取文字')
        self.setToolTip('點選文字，或拖曳框選；按住 Ctrl 可加選')

    @property
    def has_image(self):
        return not self._pixmap.isNull()

    def set_selected(self, indexes):
        self._selected = set(indexes)
        self.update()

    def image_rect(self):
        if self._pixmap.isNull():
            return QRectF()
        area = QRectF(self.rect()).adjusted(12, 12, -12, -12)
        scale = min(area.width() / self._pixmap.width(), area.height() / self._pixmap.height())
        width, height = self._pixmap.width() * scale, self._pixmap.height() * scale
        return QRectF(area.center().x() - width / 2, area.center().y() - height / 2, width, height)

    def _source_point(self, point):
        target = self.image_rect()
        if target.isEmpty():
            return QPointF()
        return QPointF((point.x() - target.x()) * self._pixmap.width() / target.width(),
                       (point.y() - target.y()) * self._pixmap.height() / target.height())

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(_BG_RAISE))
        target = self.image_rect()
        if target.isEmpty():
            return
        painter.drawPixmap(target, self._pixmap, QRectF(self._pixmap.rect()))
        painter.save()
        painter.translate(target.topLeft())
        scale = target.width() / self._pixmap.width()
        painter.scale(scale, scale)
        for index, entry in enumerate(self._entries):
            polygon = entry['polygon']
            if polygon is None:
                continue
            selected = index in self._selected
            pen = QPen(QColor(_ACCENT if selected else '#737d91'))
            pen.setCosmetic(True)
            pen.setWidth(2 if selected else 1)
            painter.setPen(pen)
            painter.setBrush(QColor(232, 160, 32, 70) if selected else QColor(0, 0, 0, 0))
            painter.drawPolygon(polygon)
        painter.restore()
        if self._drag is not None:
            painter.setPen(QPen(QColor(_ACCENT), 1, Qt.PenStyle.DashLine))
            painter.setBrush(QColor(232, 160, 32, 35))
            painter.drawRect(self._drag)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.image_rect().contains(event.position()):
            self._press = event.position()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._press is not None:
            self._drag = QRectF(self._press, event.position()).normalized().intersected(self.image_rect())
            self.update()
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or self._press is None:
            super().mouseReleaseEvent(event)
            return
        start, end = self._press, event.position()
        self._press = self._drag = None
        source = self._source_point(end)
        if (end - start).manhattanLength() < 5:
            indexes = [i for i, entry in enumerate(self._entries)
                       if entry['polygon'] is not None and
                       entry['polygon'].containsPoint(source, Qt.FillRule.OddEvenFill)]
            # A click toggles one box; a region replaces selection unless Ctrl is held.
            if indexes:
                selected = self._selected.symmetric_difference({indexes[-1]})
                self.selection_requested.emit(selected, False)
        else:
            region = QRectF(self._source_point(start), source).normalized()
            indexes = [i for i, entry in enumerate(self._entries)
                       if entry['polygon'] is not None and
                       region.intersects(entry['polygon'].boundingRect())]
            self.selection_requested.emit(indexes, bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier))
        self.update()
        event.accept()


class OcrResultDialog(QDialog):
    """Modeless review surface, with no clipboard or database side effects.

    Connect ``copy_requested(str)`` to the owner's guarded copy handler before
    showing the dialog. Accepted means the user explicitly pressed Copy.
    """
    copy_requested = Signal(str)

    def __init__(self, text, detail_json=None, image_path=None, needs_review=False, parent=None, animate=None):
        super().__init__(parent)
        self._text = text or ''
        self._entries = _entries(self._text, detail_json)
        self._syncing = False
        self._animate = animate
        self._entrance_shown = False
        self._entrance_animation = None
        self.setWindowTitle('選取要複製的文字 · 拾字筆')
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        self.setModal(False)
        self.resize(860, 580)
        self.setMinimumSize(560, 400)
        self._setup_ui(image_path, needs_review)
        self._update_copy_action()

    def _setup_ui(self, image_path, needs_review):
        self.setStyleSheet(f'''
            QDialog {{ background: {_BG}; color: {_TEXT_PRI}; }}
            QLabel {{ color: {_TEXT_PRI}; background: transparent; }}
            QPushButton {{ background: {_BG_RAISE}; color: {_TEXT_PRI};
                border: 1px solid {_BORDER}; border-radius: 7px; padding: 7px 13px; }}
            QPushButton:hover {{ background: {_BG_HOVER}; }}
            QPushButton:focus {{ border-color: {_ACCENT}; }}
            QPushButton:disabled {{ color: {_TEXT_SEC}; }}
            QPushButton#copySelection {{ background: {_ACCENT}; color: {_ACCENT_T};
                font-weight: 600; border: none; padding: 9px 24px; }}
            QPushButton#copySelection:hover {{ background: {_ACCENT_H}; }}
            QPushButton#copySelection:disabled {{ background: {_BG_RAISE}; color: {_TEXT_SEC}; }}
            QListWidget, QTextEdit {{ background: {_BG_RAISE}; color: {_TEXT_PRI};
                border: 1px solid {_BORDER}; border-radius: 8px; padding: 8px;
                selection-background-color: {_ACCENT_18}; font-size: 13px; }}
            QListWidget::item {{ padding: 7px 3px; }}
            QListWidget::item:selected {{ background: {_ACCENT_18}; color: {_TEXT_PRI}; }}
            QSplitter::handle {{ background: {_BG}; width: 12px; }}
            QScrollBar:vertical {{ background: transparent; width: 6px; margin: 3px 0; }}
            QScrollBar::handle:vertical {{ background: {_BORDER_L}; border-radius: 3px; min-height: 24px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
        ''')
        layout = self._main_layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)
        header = QLabel('選取要複製的文字')
        header.setStyleSheet('font-size: 21px; font-weight: 600;')
        layout.addWidget(header)
        self._hint = QLabel('框選原圖或勾選文字，再按「複製」。')
        self._hint.setStyleSheet(f'color: {_TEXT_SEC}; font-size: 12px;')
        self._hint.setWordWrap(True)
        layout.addWidget(self._hint)
        if needs_review:
            review = QLabel('部分文字需要核對，請對照原圖。')
            review.setStyleSheet(f'color: {_ACCENT}; padding: 4px 0;')
            layout.addWidget(review)

        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        image_panel = QWidget()
        image_layout = QVBoxLayout(image_panel)
        image_layout.setContentsMargins(0, 0, 0, 0)
        image_layout.setSpacing(8)
        image_layout.addWidget(QLabel('原圖'))
        self._image = _ImageSelection(image_path, self._entries)
        self._image.selection_requested.connect(self._select_from_image)
        image_layout.addWidget(self._image, 1)
        self._splitter.addWidget(image_panel)
        image_panel.setVisible(self._image.has_image)
        if not self._image.has_image or not any(e['polygon'] is not None for e in self._entries):
            self._hint.setText('勾選要複製的文字，也可以在下方修改或反白選取。')
        text_panel = QWidget()
        text_layout = QVBoxLayout(text_panel)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(8)
        selection_bar = QHBoxLayout()
        self._count = QLabel('尚未選取')
        selection_bar.addWidget(self._count, 1)
        self._select_all = QPushButton('全選')
        self._clear = QPushButton('清除選取')
        self._select_all.clicked.connect(lambda: self.select_indexes(range(len(self._entries))))
        self._clear.clicked.connect(lambda: self.select_indexes([]))
        selection_bar.addWidget(self._select_all)
        selection_bar.addWidget(self._clear)
        text_layout.addLayout(selection_bar)
        self._list = QListWidget()
        self._list.setAccessibleName('可複製的文字區塊')
        self._list.setWordWrap(True)
        for entry in self._entries:
            row = QListWidgetItem(entry['text'])
            row.setFlags(row.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            row.setCheckState(Qt.CheckState.Unchecked)
            self._list.addItem(row)
        self._list.itemChanged.connect(self._on_selection_changed)
        text_layout.addWidget(self._list, 1)
        preview_label = QLabel('複製內容')
        preview_label.setStyleSheet(f'color: {_TEXT_SEC}; font-size: 12px;')
        text_layout.addWidget(preview_label)
        self._preview = QTextEdit()
        self._preview.setAcceptRichText(False)
        self._preview.setPlaceholderText('選取後，可在這裡修改或反白文字')
        self._preview.setAccessibleName('即將複製的文字，可編輯')
        self._preview.setMinimumHeight(110)
        self._preview.textChanged.connect(self._update_copy_action)
        self._preview.selectionChanged.connect(self._update_copy_action)
        text_layout.addWidget(self._preview, 1)
        self._splitter.addWidget(text_panel)
        self._splitter.setSizes([400, 380])
        layout.addWidget(self._splitter, 1)

        footer = QHBoxLayout()
        saved = QLabel('完整結果已保存在歷史')
        saved.setStyleSheet(f'color: {_TEXT_SEC}; font-size: 11px;')
        footer.addWidget(saved, 1)
        self._cancel = QPushButton('稍後再選')
        self._cancel.clicked.connect(self.reject)
        footer.addWidget(self._cancel)
        self._copy = QPushButton('複製')
        self._copy.setObjectName('copySelection')
        self._copy.clicked.connect(self._request_copy)
        self._copy.setToolTip('複製（Ctrl+Enter）')
        self._copy_shortcuts = []
        for sequence in ('Ctrl+Return', 'Ctrl+Enter'):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(self._request_copy)
            self._copy_shortcuts.append(shortcut)
        footer.addWidget(self._copy)
        layout.addLayout(footer)
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
        self._select_all.setEnabled(bool(self._entries))
        self._clear.setEnabled(bool(self._entries))

    def resizeEvent(self, event):
        if hasattr(self, '_preview'):
            compact = self.height() < 460
            self._main_layout.setContentsMargins(*(16, 14, 16, 12) if compact else (22, 20, 22, 18))
            self._main_layout.setSpacing(8 if compact else 12)
            self._preview.setMinimumHeight(88 if compact else 110)
            self._image.setMinimumHeight(140 if compact else 180)
        super().resizeEvent(event)

    def _motion_enabled(self):
        if self._animate is not None:
            return bool(self._animate)
        # Native Windows styles reflect the system's animation preference here.
        return bool(self.style().styleHint(QStyle.StyleHint.SH_Widget_Animate, None, self)) and (
            self.style().styleHint(QStyle.StyleHint.SH_Widget_Animation_Duration, None, self) > 0)

    def showEvent(self, event):
        super().showEvent(event)
        if self._entrance_shown or not self._motion_enabled():
            self._entrance_shown = True
            self.setWindowOpacity(1.0)
            return
        self._entrance_shown = True
        self._entrance_animation = QPropertyAnimation(self, b'windowOpacity', self)
        self._entrance_animation.setDuration(160)
        self._entrance_animation.setStartValue(0.0)
        self._entrance_animation.setEndValue(1.0)
        self._entrance_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._entrance_animation.finished.connect(lambda: self.setWindowOpacity(1.0))
        self._entrance_animation.start()

    def hideEvent(self, event):
        if self._entrance_animation is not None:
            self._entrance_animation.stop()
        # A quick copy/cancel can interrupt the entrance without leaving a later
        # re-opened dialog transparent. Interactions are never delayed by motion.
        self.setWindowOpacity(1.0)
        super().hideEvent(event)

    @property
    def selected_indexes(self):
        return {i for i in range(self._list.count())
                if self._list.item(i).checkState() == Qt.CheckState.Checked}

    @property
    def selected_text(self):
        cursor = self._preview.textCursor()
        if cursor.hasSelection():
            return cursor.selectedText().replace('\u2029', '\n')
        return self._preview.toPlainText()

    def select_indexes(self, indexes):
        """Select zero-based blocks. Useful for image and keyboard consumers."""
        selected = set(indexes)
        self._syncing = True
        for i in range(self._list.count()):
            self._list.item(i).setCheckState(
                Qt.CheckState.Checked if i in selected else Qt.CheckState.Unchecked)
        self._syncing = False
        self._on_selection_changed()

    def _select_from_image(self, indexes, additive):
        selected = self.selected_indexes
        if additive:
            selected.update(indexes)
        else:
            selected = set(indexes)
        self.select_indexes(selected)

    def _on_selection_changed(self, *_):
        if self._syncing:
            return
        indexes = self.selected_indexes
        self._count.setText(f'已選 {len(indexes)} 段' if indexes else '尚未選取')
        self._image.set_selected(indexes)
        if indexes and len(indexes) == len(self._entries):
            text = self._text
        else:
            entries = [entry for i, entry in enumerate(self._entries) if i in indexes]
            if entries and all(entry['box'] is not None for entry in entries):
                from ..ocr.postprocessor import sort_boxes_and_merge
                text = sort_boxes_and_merge(entries)
            else:
                text = '\n'.join(entry['text'] for entry in entries)
        self._preview.setPlainText(text)
        self._update_copy_action()

    def _update_copy_action(self):
        if hasattr(self, '_copy'):
            self._copy.setEnabled(bool(self.selected_text.strip()))
            self._copy.setText('複製反白文字' if self._preview.textCursor().hasSelection() else '複製')

    def _request_copy(self):
        text = self.selected_text
        if text.strip():
            self.copy_requested.emit(text)
            self.accept()
