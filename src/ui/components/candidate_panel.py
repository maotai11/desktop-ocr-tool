"""Bounded, keyboard-accessible native candidate grid with explicit expansion."""
from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QScrollArea, QFrame, QSizePolicy,
)

from ..design_system import apply_button, CANDIDATE_HEIGHT, TYPE_CANDIDATE, TYPE_META, TYPE_SECTION
from ..theme import _BG, _BG_RAISE, _BG_HOVER, _BORDER_L, _TEXT_PRI, _TEXT_SEC, _ACCENT, _TEAL


class CandidatePanel(QWidget):
    selected = Signal(str, int)
    expansion_changed = Signal(bool)
    COLUMNS = 5
    COLLAPSED_HEIGHT = 108
    EXPANDED_HEIGHT = 220
    MAX_CANDIDATES = 62

    def __init__(self, parent=None):
        super().__init__(parent)
        self.buttons = []
        self._expanded = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        heading = QHBoxLayout()
        title = QLabel('候選字')
        title.setStyleSheet(f'font-size: {TYPE_SECTION}px; font-weight: 600; color: {_TEXT_PRI};')
        self.count = QLabel('')
        self.count.setStyleSheet(f'font-size: {TYPE_META}px; color: {_TEXT_SEC};')
        self.key_hint = QLabel('方向鍵選字')
        self.key_hint.setStyleSheet(f'font-size: {TYPE_META}px; color: {_TEXT_SEC};')
        self.key_hint.setToolTip('Tab 切換候選；方向鍵移動；Enter 或空白鍵複製')
        self.expand_button = apply_button(QPushButton('展開'), 'ghost', compact=True)
        self.expand_button.setCheckable(True)
        self.expand_button.setAccessibleName('展開候選字區')
        self.expand_button.toggled.connect(self.set_expanded)
        heading.addWidget(title)
        heading.addWidget(self.count)
        heading.addStretch()
        heading.addWidget(self.key_hint)
        heading.addWidget(self.expand_button)
        layout.addLayout(heading)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setAccessibleName('候選字，可用 Tab 或方向鍵選字')
        self.scroll.setFixedHeight(self.COLLAPSED_HEIGHT)
        self.scroll.setStyleSheet(f'''
            QScrollArea, QScrollArea > QWidget > QWidget {{ background: {_BG}; }}
            QScrollBar:vertical {{ background: {_BG}; width: 8px; margin: 0; }}
            QScrollBar::handle:vertical {{ background: {_BORDER_L}; border-radius: 4px; min-height: 24px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        ''')
        self.contents = QWidget()
        self.grid = QGridLayout(self.contents)
        self.grid.setContentsMargins(1, 1, 8, 1)
        self.grid.setHorizontalSpacing(8)
        self.grid.setVerticalSpacing(8)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        for column in range(self.COLUMNS):
            self.grid.setColumnStretch(column, 1)
        self.scroll.setWidget(self.contents)
        self.empty = QLabel('寫完後，候選字會顯示在這裡')
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setWordWrap(True)
        self.empty.setFixedHeight(self.COLLAPSED_HEIGHT)
        self.empty.setStyleSheet(f'color: {_TEXT_SEC}; font-size: {TYPE_META}px;')
        layout.addWidget(self.scroll)
        layout.addWidget(self.empty)
        self.clear()

    def clear(self):
        for button in self.buttons:
            self.grid.removeWidget(button)
            button.hide()
            button.deleteLater()
        self.buttons.clear()
        self.count.clear()
        self.scroll.hide()
        self.empty.show()
        self.expand_button.hide()
        self.key_hint.hide()
        self.scroll.verticalScrollBar().setValue(0)

    def set_candidates(self, values, revision):
        self.clear()
        if len(values) > self.MAX_CANDIDATES:
            raise ValueError('Candidate display budget exceeded')
        unique = {}
        for value in values:
            label = value if isinstance(value, str) else value.label
            sources = ('筆畫',) if isinstance(value, str) else value.sources
            provenance = unique.setdefault(label, [])
            for source in sources:
                if source not in provenance:
                    provenance.append(source)
        for index, (label, sources) in enumerate(unique.items()):
            # The controller validates the original labels. Rich candidate
            # records may carry source attribution without changing copied text.
            button = QPushButton(label.replace('&', '&&'))
            button.setMinimumSize(48, CANDIDATE_HEIGHT)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.setProperty('candidate_label', label)
            button.setToolTip(f'第 {index+1} 候選 · {"、".join(sources)}')
            button.setAccessibleName(f'複製候選字 {label}，第 {index+1} 候選，{"、".join(sources)}')
            button.setStyleSheet(f'''
                QPushButton {{ background: {_BG_RAISE}; color: {_TEXT_PRI}; border: 1px solid {_BORDER_L};
                    border-radius: 6px; font-size: {TYPE_CANDIDATE}px; padding: 1px; }}
                QPushButton:hover {{ background: {_BG_HOVER}; border-color: {_ACCENT}; }}
                QPushButton:pressed {{ background: {_BG}; }}
                QPushButton:focus {{ border: 2px solid {_ACCENT}; }}
                QPushButton[copied="true"] {{ border: 2px solid {_TEAL}; }}
            ''')
            button.installEventFilter(self)
            button.clicked.connect(lambda checked=False, text=label, token=revision: self.selected.emit(text, token))
            self.grid.addWidget(button, index//self.COLUMNS, index%self.COLUMNS)
            self.buttons.append(button)
        self.empty.setVisible(not unique)
        self.scroll.setVisible(bool(unique))
        self.count.setText(f'{len(unique)} 個' if unique else '')
        self.expand_button.setVisible(len(unique) > self.COLUMNS*2)
        self.key_hint.setVisible(bool(unique))
        if len(unique) <= self.COLUMNS*2:
            self.expand_button.setChecked(False)

    def set_expanded(self, expanded):
        self._expanded = bool(expanded)
        self.scroll.setFixedHeight(self.EXPANDED_HEIGHT if expanded else self.COLLAPSED_HEIGHT)
        self.expand_button.setText('收合' if expanded else '展開')
        self.expand_button.setAccessibleName('收合候選字區' if expanded else '展開候選字區')
        self.expansion_changed.emit(self._expanded)

    def mark_copied(self, label):
        for button in self.buttons:
            button.setProperty('copied', button.property('candidate_label') == label)
            button.style().unpolish(button)
            button.style().polish(button)

    def eventFilter(self, watched, event):
        if watched in self.buttons and event.type() == QEvent.Type.FocusIn:
            self.scroll.ensureWidgetVisible(watched, 4, 4)
            ancestor = self.parentWidget()
            while ancestor:
                if isinstance(ancestor, QScrollArea):
                    ancestor.ensureWidgetVisible(watched, 4, 4)
                ancestor = ancestor.parentWidget()
        if watched in self.buttons and event.type() == QEvent.Type.KeyPress:
            index = self.buttons.index(watched)
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                watched.click()
                return True
            moves = {Qt.Key.Key_Left:-1, Qt.Key.Key_Right:1,
                     Qt.Key.Key_Up:-self.COLUMNS, Qt.Key.Key_Down:self.COLUMNS}
            if event.key() in moves:
                target = max(0, min(len(self.buttons)-1, index+moves[event.key()]))
                self.buttons[target].setFocus(Qt.FocusReason.TabFocusReason)
                self.scroll.ensureWidgetVisible(self.buttons[target], 4, 4)
                return True
        return super().eventFilter(watched, event)
