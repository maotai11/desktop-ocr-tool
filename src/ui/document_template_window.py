"""Local document regions, reusable text templates and explicit TXT output."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFontMetrics,
    QImage,
    QKeySequence,
    QPainter,
    QPen,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGraphicsItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..documents.template import (
    Region,
    TemplateError,
    TemplateLayout,
    build_output,
    validate_template,
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
    _ERROR,
    _SUCCESS,
    _TEAL,
    _TEXT_PRI,
    _TEXT_SEC,
)

_FIELD_COLORS = (_ACCENT, _TEAL, '#77a9ef', '#c995e8', '#ed9299', '#82c58b')


def _plain_label(text=''):
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    return label


def _image_copy(image):
    """Detach a preview before releasing a worker-owned PIL image."""
    if isinstance(image, QImage):
        return image.copy()
    if isinstance(image, QPixmap):
        return image.toImage().copy()
    try:
        rgb = image.convert('RGB')
        try:
            data = rgb.tobytes('raw', 'RGB')
            return QImage(data, rgb.width, rgb.height, rgb.width * 3,
                          QImage.Format.Format_RGB888).copy()
        finally:
            if rgb is not image:
                rgb.close()
    finally:
        if hasattr(image, 'close'):
            image.close()


class _FieldLabel(QGraphicsSimpleTextItem):
    def paint(self, painter, option, widget=None):
        painter.fillRect(self.boundingRect().adjusted(-3, -1, 3, 1), QColor(_BG))
        super().paint(painter, option, widget)


class RegionCanvas(QGraphicsView):
    """Source-coordinate rectangles remain correct while fitting or zooming."""
    region_drawn = Signal(object)
    zoom_changed = Signal(float)
    MIN_ZOOM = .05
    MAX_ZOOM = 12.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._pixmap_item = None
        self._overlays = []
        self._labels = []
        self._regions = {}
        self._selected_name = None
        self._start = None
        self._start_view = None
        self._rubber = None
        self._auto_fit = True
        self._manual_zoom = 1.0
        self._pan_mode = False
        self._pan_start = None
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setBackgroundBrush(QColor(_BG_RAISE))
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setMinimumSize(240, 100)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAccessibleName('文件預覽，可拖曳框選欄位')
        self.setToolTip('拖曳框選；Ctrl 加滾輪縮放；按住滑鼠中鍵移動頁面。')
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.viewport().setCursor(Qt.CursorShape.CrossCursor)
        self._empty = self._scene.addText('匯入 PDF 或圖片，從第一頁框選欄位')
        self._empty.setDefaultTextColor(QColor(_TEXT_SEC))

    @property
    def image_size(self):
        if self._pixmap_item is None:
            return (0, 0)
        pixmap = self._pixmap_item.pixmap()
        return pixmap.width(), pixmap.height()

    def set_image(self, image):
        detached = _image_copy(image)
        self._start = self._start_view = self._pan_start = None
        self._scene.clear()
        self._empty = self._rubber = None
        self._overlays = []
        self._labels = []
        self._pixmap_item = self._scene.addPixmap(QPixmap.fromImage(detached))
        self._scene.setSceneRect(self._pixmap_item.boundingRect())
        self._draw_regions()
        if self._auto_fit:
            self.fit_page()
        else:
            self.set_zoom(self._manual_zoom * 100)

    def clear_image(self, message='載入頁面…'):
        self._start = self._start_view = self._rubber = self._pan_start = None
        self._pixmap_item = None
        self._overlays = []
        self._labels = []
        self._scene.clear()
        self.resetTransform()
        self._empty = self._scene.addText(message)
        self._empty.setDefaultTextColor(QColor(_TEXT_SEC))
        self._scene.setSceneRect(self._empty.boundingRect())
        self.zoom_changed.emit(0)

    def set_regions(self, regions, selected_name=None):
        self._regions = dict(regions)
        self._selected_name = selected_name
        self._draw_regions()

    def _draw_regions(self):
        for item in self._overlays:
            self._scene.removeItem(item)
        self._overlays.clear()
        self._labels.clear()
        width, height = self.image_size
        if not width or not height:
            return
        for index, (name, region) in enumerate(self._regions.items()):
            if region is None:
                continue
            left, top, right, bottom = region.pixel_bounds(width, height)
            color = QColor(_FIELD_COLORS[index % len(_FIELD_COLORS)])
            pen = QPen(color, 2 if name == self._selected_name else 1)
            pen.setCosmetic(True)
            fill = QColor(color)
            fill.setAlpha(45 if name == self._selected_name else 16)
            rectangle = self._scene.addRect(QRectF(left, top, right-left, bottom-top), pen, fill)
            rectangle.setZValue(2)
            label = _FieldLabel()
            label.setText(QFontMetrics(label.font()).elidedText(name, Qt.TextElideMode.ElideRight, 160))
            label.setToolTip(name)
            self._scene.addItem(label)
            label.setBrush(color)
            label.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
            label.setZValue(3)
            self._labels.append((label, left, top))
            self._overlays.extend((rectangle, label))
        self._position_labels()

    def _position_labels(self):
        scale = max(.0001, abs(self.transform().m22()))
        for label, left, top in self._labels:
            label.setPos(left + 2 / scale, max(0, top - (label.boundingRect().height()+3) / scale))

    def fit_page(self):
        self._auto_fit = True
        if self._pixmap_item is not None:
            self.resetTransform()
            self.fitInView(self._pixmap_item, Qt.AspectRatioMode.KeepAspectRatio)
            self._position_labels()
            self.zoom_changed.emit(self.zoom_percent)

    @property
    def zoom_percent(self):
        return abs(self.transform().m11()) * 100 if self._pixmap_item is not None else 0.0

    def set_zoom(self, percent, under_mouse=False):
        if self._pixmap_item is None:
            return
        target = min(self.MAX_ZOOM, max(self.MIN_ZOOM, percent / 100))
        current = abs(self.transform().m11())
        self._auto_fit = False
        self._manual_zoom = target
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse if under_mouse
                                     else QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.scale(target / current, target / current)
        self._position_labels()
        self.zoom_changed.emit(self.zoom_percent)

    def zoom_in(self):
        self.set_zoom(self.zoom_percent * 1.2)

    def zoom_out(self):
        self.set_zoom(self.zoom_percent / 1.2)

    def actual_size(self):
        self.set_zoom(100)

    def set_pan_mode(self, enabled):
        self._pan_mode = bool(enabled)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag if enabled
                         else QGraphicsView.DragMode.NoDrag)
        self.viewport().setCursor(Qt.CursorShape.OpenHandCursor if enabled
                                  else Qt.CursorShape.CrossCursor)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._auto_fit:
            self.fit_page()

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier and self._pixmap_item is not None:
            factor = 1.2 if event.angleDelta().y() > 0 else 1 / 1.2
            self.set_zoom(self.zoom_percent * factor, under_mouse=True)
            event.accept()
            return
        super().wheelEvent(event)

    def _bounded(self, point):
        width, height = self.image_size
        return QPointF(max(0, min(width, point.x())), max(0, min(height, point.y())))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton and self._pixmap_item is not None:
            self._pan_start = event.position().toPoint()
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if self._pan_mode:
            super().mousePressEvent(event)
            return
        if (event.button() == Qt.MouseButton.LeftButton and self._pixmap_item is not None
                and self._pixmap_item.boundingRect().contains(self.mapToScene(event.position().toPoint()))):
            self._start = self._bounded(self.mapToScene(event.position().toPoint()))
            self._start_view = event.position().toPoint()
            pen = QPen(QColor(_ACCENT), 2, Qt.PenStyle.DashLine)
            pen.setCosmetic(True)
            self._rubber = self._scene.addRect(QRectF(self._start, self._start), pen)
            self._rubber.setZValue(10)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._pan_start is not None:
            delta = event.position().toPoint() - self._pan_start
            self._pan_start = event.position().toPoint()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            event.accept()
            return
        if self._start is not None:
            current = self._bounded(self.mapToScene(event.position().toPoint()))
            self._rubber.setRect(QRectF(self._start, current).normalized())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton and self._pan_start is not None:
            self._pan_start = None
            self.viewport().setCursor(Qt.CursorShape.OpenHandCursor if self._pan_mode
                                      else Qt.CursorShape.CrossCursor)
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and self._start is not None:
            current = self._bounded(self.mapToScene(event.position().toPoint()))
            rect = QRectF(self._start, current).normalized()
            distance = (event.position().toPoint() - self._start_view).manhattanLength()
            self._scene.removeItem(self._rubber)
            self._rubber = None
            self._start = self._start_view = None
            if distance >= 6 and rect.width() > 0 and rect.height() > 0:
                width, height = self.image_size
                self.region_drawn.emit(Region.from_pixels(
                    rect.left(), rect.top(), rect.right(), rect.bottom(), width, height))
            event.accept()
            return
        super().mouseReleaseEvent(event)


class _ResultTable(QTableWidget):
    edit_requested = Signal(int, int)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_F2, Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.currentColumn() > 0:
            self.edit_requested.emit(self.currentRow(), self.currentColumn())
            event.accept()
            return
        super().keyPressEvent(event)


class DocumentTemplateWindow(QWidget):
    """Reusable form regions; workers own imports, page rendering and OCR."""
    import_requested = Signal(object, int)
    page_requested = Signal(int)
    recognize_requested = Signal(object)
    cancel_requested = Signal()
    clear_requested = Signal()

    def __init__(self, cfg, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self._cfg = cfg
        self._pages = []
        self._layout = TemplateLayout()
        self._results = []
        self._statuses = []
        self._messages = []
        self._current_page = -1
        self._busy = False
        self._updating = False
        self._shutting_down = False
        self.export_directory = ''
        self.setWindowTitle('文件套用文字 · 拾字筆')
        self.setAcceptDrops(True)
        self.setMinimumSize(760, 480)
        self.resize(1180, 820)
        self._setup_ui()
        self._refresh_actions()

    def _setup_ui(self):
        self.setStyleSheet(f'''
            DocumentTemplateWindow, QInputDialog {{ background: {_BG}; color: {_TEXT_PRI}; }}
            QLabel, QGroupBox {{ color: {_TEXT_PRI}; background: transparent; }}
            QPushButton {{ background: {_BG_RAISE}; color: {_TEXT_PRI}; border: 1px solid {_BORDER_L};
                border-radius: 7px; padding: 7px 12px; }}
            QPushButton:hover {{ background: {_BG_HOVER}; }}
            QPushButton:checked {{ background: {_ACCENT_18}; border-color: {_ACCENT}; color: {_ACCENT}; }}
            QPushButton:focus, QLineEdit:focus, QPlainTextEdit:focus {{ border-color: {_ACCENT}; }}
            QPushButton:disabled {{ color: {_TEXT_SEC}; border-color: {_BORDER}; }}
            QPushButton#recognizeDocuments {{ background: {_ACCENT}; color: {_ACCENT_T}; font-weight: 600; border: none; }}
            QPushButton#recognizeDocuments:hover {{ background: {_ACCENT_H}; }}
            QPushButton#recognizeDocuments:disabled {{ background: {_BG_RAISE}; color: {_TEXT_SEC}; }}
            QLineEdit, QPlainTextEdit, QListWidget, QTableWidget, QComboBox, QSpinBox, QDoubleSpinBox {{
                background: {_BG_RAISE}; color: {_TEXT_PRI}; border: 1px solid {_BORDER};
                border-radius: 6px; padding: 5px; selection-background-color: {_ACCENT_18}; }}
            QListWidget::item {{ padding: 6px; }}
            QListWidget::item:selected {{ background: {_ACCENT_18}; color: {_TEXT_PRI}; }}
            QGroupBox {{ border: 1px solid {_BORDER}; border-radius: 8px; margin-top: 9px; padding: 14px 10px 10px; }}
            QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; }}
            QHeaderView::section {{ background: {_BG_RAISE}; color: {_TEXT_SEC}; border: none; padding: 6px; }}
            QTabWidget::pane {{ border: 1px solid {_BORDER}; }}
            QTabBar::tab {{ background: {_BG_RAISE}; color: {_TEXT_SEC}; padding: 7px 16px; }}
            QTabBar::tab:selected {{ color: {_ACCENT}; border-bottom: 2px solid {_ACCENT}; }}
            QScrollArea, QScrollArea > QWidget > QWidget {{ background: {_BG}; border: none; }}
            QScrollBar:vertical {{ background: transparent; width: 7px; margin: 0; }}
            QScrollBar::handle:vertical {{ background: {_BORDER_L}; border-radius: 3px; min-height: 24px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
            QSplitter::handle {{ background: {_BG}; width: 10px; height: 10px; }}
            QProgressBar {{ background: {_BG_RAISE}; border: none; border-radius: 3px; color: {_TEXT_PRI}; }}
            QProgressBar::chunk {{ background: {_ACCENT}; border-radius: 3px; }}
        ''')
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 16, 18, 14)
        outer.setSpacing(10)
        heading = QHBoxLayout()
        title = _plain_label('文件套用文字')
        title.setStyleSheet('font-size: 21px; font-weight: 600;')
        heading.addWidget(title)
        heading.addStretch()
        note = _plain_label('檔案在本機辨識')
        note.setStyleSheet(f'color: {_TEXT_SEC};')
        heading.addWidget(note)
        outer.addLayout(heading)

        tools = QHBoxLayout()
        self._import_button = QPushButton('匯入 PDF／圖片')
        self._import_button.clicked.connect(self._choose_files)
        tools.addWidget(self._import_button)
        self._clear_documents_button = QPushButton('清空文件')
        self._clear_documents_button.setObjectName('clearDocuments')
        self._clear_documents_button.setToolTip('移除匯入文件、全部框選與辨識結果，保留欄位名稱和文字模板；原始檔案不會刪除。')
        self._clear_documents_button.clicked.connect(self._confirm_clear_documents)
        tools.addWidget(self._clear_documents_button)
        tools.addWidget(_plain_label('單檔上限'))
        self._file_limit = QSpinBox()
        self._file_limit.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self._file_limit.setRange(1, 1024)
        self._file_limit.setSuffix(' MB')
        limit = self._cfg.get('documents', 'max_file_size_mb', default=100) if self._cfg else 100
        self._file_limit.setValue(limit if isinstance(limit, int) and not isinstance(limit, bool) else 100)
        self._file_limit.setAccessibleName('每個匯入檔案的大小上限，MB')
        self._file_limit.valueChanged.connect(self._save_file_limit)
        tools.addWidget(self._file_limit)
        tools.addStretch()
        self._recognize_button = QPushButton('開始辨識')
        self._recognize_button.setObjectName('recognizeDocuments')
        self._recognize_button.clicked.connect(self._start_recognition)
        self._cancel_button = QPushButton('停止')
        self._cancel_button.clicked.connect(self.cancel_requested.emit)
        tools.addWidget(self._recognize_button)
        tools.addWidget(self._cancel_button)
        outer.addLayout(tools)
        self._progress = QProgressBar()
        self._progress.setFixedHeight(16)
        self._progress.hide()
        outer.addWidget(self._progress)

        vertical = QSplitter(Qt.Orientation.Vertical)
        workspace = QSplitter(Qt.Orientation.Horizontal)
        preview_panel = QWidget()
        preview_layout = QVBoxLayout(preview_panel)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        page_bar = QHBoxLayout()
        self._previous = QPushButton()
        self._previous.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowBack))
        self._previous.setAccessibleName('上一頁')
        self._previous.clicked.connect(lambda: self._page_combo.setCurrentIndex(self._current_page - 1))
        self._next = QPushButton()
        self._next.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowForward))
        self._next.setAccessibleName('下一頁')
        self._next.clicked.connect(lambda: self._page_combo.setCurrentIndex(self._current_page + 1))
        self._page_combo = QComboBox()
        self._page_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self._page_combo.setMinimumContentsLength(14)
        self._page_combo.currentIndexChanged.connect(self._select_page)
        self._page_combo.setAccessibleName('文件頁面')
        self._page_count = _plain_label('尚未匯入')
        page_bar.addWidget(self._previous)
        page_bar.addWidget(self._page_combo, 1)
        page_bar.addWidget(self._next)
        preview_layout.addLayout(page_bar)
        self._canvas = RegionCanvas()
        self._canvas.region_drawn.connect(self._set_region)
        self._canvas.zoom_changed.connect(self._update_zoom_controls)
        zoom_bar = QHBoxLayout()
        zoom_bar.setSpacing(5)
        self._zoom_out = QPushButton('−')
        self._zoom_out.setAccessibleName('縮小頁面')
        self._zoom_out.setToolTip('縮小（Ctrl＋−）')
        self._zoom_out.clicked.connect(self._canvas.zoom_out)
        self._zoom_in = QPushButton('＋')
        self._zoom_in.setAccessibleName('放大頁面')
        self._zoom_in.setToolTip('放大（Ctrl＋＋）')
        self._zoom_in.clicked.connect(self._canvas.zoom_in)
        self._zoom_label = _plain_label('—')
        self._zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._zoom_label.setMinimumWidth(44)
        self._zoom_label.setAccessibleName('目前頁面縮放比例')
        self._fit_button = QPushButton('適合視窗')
        self._fit_button.setToolTip('整頁顯示（Ctrl＋0）')
        self._fit_button.clicked.connect(self._canvas.fit_page)
        self._actual_size_button = QPushButton('100%')
        self._actual_size_button.setToolTip('原始大小（Ctrl＋1）')
        self._actual_size_button.clicked.connect(self._canvas.actual_size)
        self._pan_button = QPushButton('拖曳移動')
        self._pan_button.setCheckable(True)
        self._pan_button.setToolTip('開啟後拖曳可移動頁面，關閉後拖曳可框選；也可按住滑鼠中鍵移動。')
        self._pan_button.toggled.connect(self._canvas.set_pan_mode)
        self._zoom_controls = (self._zoom_out, self._zoom_in, self._fit_button,
                               self._actual_size_button, self._pan_button)
        for control in (self._zoom_out, self._zoom_label, self._zoom_in, self._fit_button,
                        self._actual_size_button, self._pan_button):
            if isinstance(control, QPushButton):
                control.setStyleSheet('QPushButton { padding: 5px 7px; }')
            zoom_bar.addWidget(control)
        zoom_bar.addStretch()
        preview_layout.addLayout(zoom_bar)
        self._zoom_shortcuts = []
        for key, action in (('Ctrl++', self._canvas.zoom_in), ('Ctrl+=', self._canvas.zoom_in),
                            ('Ctrl+-', self._canvas.zoom_out), ('Ctrl+0', self._canvas.fit_page),
                            ('Ctrl+1', self._canvas.actual_size)):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(action)
            self._zoom_shortcuts.append(shortcut)
        preview_layout.addWidget(self._canvas, 1)
        preview_layout.addWidget(self._page_count)
        workspace.addWidget(preview_panel)

        editor_scroll = QScrollArea()
        editor_scroll.setWidgetResizable(True)
        editor_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        editor_scroll.setMinimumWidth(340)
        editor_panel = QWidget()
        editor = QVBoxLayout(editor_panel)
        editor.setContentsMargins(0, 0, 0, 0)
        editor.setSpacing(10)
        fields = QGroupBox('辨識欄位')
        field_layout = QVBoxLayout(fields)
        self._field_list = QListWidget()
        self._field_list.setMinimumHeight(80)
        self._field_list.setMaximumHeight(125)
        self._field_list.setAccessibleName('辨識欄位，雙擊可插入文字模板')
        self._field_list.currentRowChanged.connect(self._field_selected)
        self._field_list.itemDoubleClicked.connect(lambda _: self._insert_field())
        field_layout.addWidget(self._field_list)
        field_actions = QHBoxLayout()
        self._add_field_button = QPushButton('新增欄位')
        self._add_field_button.clicked.connect(self._add_field)
        self._remove_field_button = QPushButton('移除')
        self._remove_field_button.clicked.connect(self._remove_field)
        field_actions.addWidget(self._add_field_button)
        field_actions.addWidget(self._remove_field_button)
        field_layout.addLayout(field_actions)
        rename = QHBoxLayout()
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText('欄位名稱，例如姓名')
        self._name_edit.setAccessibleName('所選欄位的名稱')
        self._rename_button = QPushButton('重新命名')
        self._rename_button.clicked.connect(self._rename_field)
        rename.addWidget(self._name_edit, 1)
        rename.addWidget(self._rename_button)
        field_layout.addLayout(rename)
        self._scope = QComboBox()
        self._scope.addItem('所有頁共用', 'all')
        self._scope.addItem('只改這一頁', 'page')
        self._scope.setAccessibleName('框選位置的套用範圍')
        self._scope.currentIndexChanged.connect(self._refresh_actions)
        field_layout.addWidget(self._scope)
        coordinates = QGridLayout()
        self._coordinates = []
        for column, label in enumerate(('左 %', '上 %', '寬 %', '高 %')):
            coordinates.addWidget(_plain_label(label), 0, column)
            value = QDoubleSpinBox()
            value.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
            value.setRange(0, 100)
            value.setDecimals(1)
            value.setSingleStep(.5)
            value.setKeyboardTracking(False)
            value.setAccessibleName('欄位位置' + label)
            self._coordinates.append(value)
            coordinates.addWidget(value, 1, column)
        field_layout.addLayout(coordinates)
        position_actions = QHBoxLayout()
        self._apply_position = QPushButton('套用位置')
        self._apply_position.clicked.connect(self._apply_coordinates)
        self._clear_region_button = QPushButton('清除框選')
        self._clear_region_button.setToolTip('依套用範圍清除所選欄位的位置；「所有頁共用」會清除此欄位在每頁的框選。')
        self._clear_region_button.clicked.connect(self._clear_region)
        self._reset_override = QPushButton('此頁套用第一頁')
        self._reset_override.clicked.connect(self._clear_page_overrides)
        position_actions.addWidget(self._apply_position)
        position_actions.addWidget(self._clear_region_button)
        field_layout.addLayout(position_actions)
        field_layout.addWidget(self._reset_override)
        self._region_note = _plain_label('在第一頁拖曳框選，其他頁會沿用位置。')
        self._region_note.setWordWrap(True)
        self._region_note.setStyleSheet(f'color: {_TEXT_SEC}; font-size: 11px;')
        field_layout.addWidget(self._region_note)
        editor.addWidget(fields)

        template_group = QGroupBox('文字模板')
        template_layout = QVBoxLayout(template_group)
        self._template = QPlainTextEdit()
        self._template.setPlaceholderText('姓名：{辨識1}\n電話：{辨識2}')
        self._template.setMinimumHeight(110)
        self._template.setAccessibleName('每頁套用的文字模板')
        self._template.textChanged.connect(self._template_changed)
        template_layout.addWidget(self._template)
        insert = QPushButton('插入所選欄位')
        insert.clicked.connect(self._insert_field)
        template_layout.addWidget(insert)
        self._validation = _plain_label()
        self._validation.setWordWrap(True)
        self._validation.setStyleSheet(f'color: {_ERROR}; font-size: 11px;')
        template_layout.addWidget(self._validation)
        template_hint = _plain_label('每頁套用一次，頁與頁以空行分開。')
        template_hint.setStyleSheet(f'color: {_TEXT_SEC}; font-size: 11px;')
        template_layout.addWidget(template_hint)
        editor.addStretch()
        editor_scroll.setWidget(editor_panel)
        template_scroll = QScrollArea()
        template_scroll.setWidgetResizable(True)
        template_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        template_scroll.setWidget(template_group)
        self._editor_tabs = QTabWidget()
        self._editor_tabs.setMinimumWidth(340)
        self._editor_tabs.addTab(editor_scroll, '框選欄位')
        self._editor_tabs.addTab(template_scroll, '文字模板')
        workspace.addWidget(self._editor_tabs)
        workspace.setStretchFactor(0, 2)
        workspace.setStretchFactor(1, 1)
        workspace.setSizes([660, 390])
        vertical.addWidget(workspace)

        self._result_tabs = QTabWidget()
        self._results_table = _ResultTable()
        self._results_table.setAccessibleName('每頁辨識結果，可修改文字')
        self._results_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self._results_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._results_table.setToolTip('雙擊欄位或按 F2 修改文字，可保留換行。')
        self._results_table.cellDoubleClicked.connect(self._edit_result)
        self._results_table.edit_requested.connect(self._edit_result)
        self._results_table.itemChanged.connect(self._result_edited)
        self._result_tabs.addTab(self._results_table, '逐頁核對')
        self._output = QPlainTextEdit()
        self._output.setReadOnly(True)
        self._output.setPlaceholderText('辨識完成後，這裡會顯示合併結果。')
        self._output.setAccessibleName('即將複製或匯出的 TXT 內容')
        self._result_tabs.addTab(self._output, 'TXT 預覽')
        self._result_tabs.setMinimumHeight(100)
        vertical.addWidget(self._result_tabs)
        vertical.setStretchFactor(0, 3)
        vertical.setStretchFactor(1, 1)
        vertical.setSizes([550, 180])
        outer.addWidget(vertical, 1)
        footer = QHBoxLayout()
        self._status = _plain_label('匯入文件後，從第一頁框選需要的文字。')
        self._status.setWordWrap(True)
        self._status.setStyleSheet(f'color: {_TEXT_SEC}; font-size: 12px;')
        footer.addWidget(self._status, 1)
        self._copy_button = QPushButton('複製結果')
        self._copy_button.clicked.connect(self._copy_results)
        self._export_button = QPushButton('匯出 TXT')
        self._export_button.clicked.connect(self._choose_export)
        footer.addWidget(self._copy_button)
        footer.addWidget(self._export_button)
        outer.addLayout(footer)
        self._edit_controls = (self._editor_tabs, self._page_combo, self._previous,
                               self._next, self._canvas, self._results_table)

    def _save_file_limit(self, value):
        if self._cfg is not None:
            try:
                self._cfg.set('documents', 'max_file_size_mb', value)
            except OSError:
                self.show_error('無法保存檔案上限；這次匯入仍會使用畫面上的設定。')

    def _choose_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, '匯入 PDF 或圖片', '',
            'PDF 與圖片 (*.pdf *.png *.jpg *.jpeg *.bmp *.tif *.tiff *.webp);;所有檔案 (*)')
        if paths:
            self.import_paths(paths)

    def import_paths(self, paths):
        if self._busy or self._shutting_down:
            return
        paths = [str(path) for path in paths]
        if paths:
            self.import_requested.emit(paths, self._file_limit.value())

    def _confirm_clear_documents(self):
        if not (self._pages or self._results or self._busy):
            return
        answer = QMessageBox.question(
            self, '清空文件',
            '移除匯入文件、全部框選與辨識結果？\n'
            '欄位名稱和文字模板會保留，原始檔案不會刪除。',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes:
            self.clear_documents()
            self.clear_requested.emit()

    def clear_documents(self):
        """Clear the local session without deleting sources or changing the clipboard."""
        self._layout = TemplateLayout(self._layout.field_names)
        self._pan_button.setChecked(False)
        self.set_document_pages(())
        self._canvas.fit_page()
        self.set_busy(False, '已清空文件；欄位名稱和文字模板已保留。')

    def dragEnterEvent(self, event):
        if not self._busy and event.mimeData().hasUrls() and all(
                url.isLocalFile() for url in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            self.import_paths([url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()])
            event.acceptProposedAction()

    @staticmethod
    def _page_label(page, index):
        label = page.get('label') if isinstance(page, dict) else getattr(page, 'label', None)
        return str(label) if label else f'第 {index + 1} 頁'

    def set_document_pages(self, pages):
        self._pages = list(pages)
        self._layout = TemplateLayout(fields=self._layout.fields)
        self._clear_results()
        self._page_combo.blockSignals(True)
        self._page_combo.clear()
        for index, page in enumerate(self._pages):
            self._page_combo.addItem(self._page_label(page, index))
        self._page_combo.blockSignals(False)
        self._current_page = -1
        if self._pages:
            if not self._layout.fields:
                self._add_field()
            self._page_combo.setCurrentIndex(0)
            self._select_page(0)
            self._status.setText(f'已匯入 {len(self._pages)} 頁。從第一頁設定欄位，其他頁會沿用。')
        else:
            self._page_count.setText('尚未匯入')
            self._canvas.clear_image('匯入 PDF 或圖片，從第一頁框選欄位')
            self._status.setText('匯入文件後，從第一頁框選需要的文字。')
        self._refresh_fields()
        self._refresh_actions()

    def _select_page(self, index):
        if index < 0 or index >= len(self._pages) or index == self._current_page:
            return
        self._current_page = index
        self._canvas.clear_image()
        self._scope.setCurrentIndex(0 if index == 0 else 1)
        self._scope.model().item(1).setEnabled(index > 0)
        self._page_count.setText(f'第 {index + 1}／{len(self._pages)} 頁 · 拖曳框選欄位')
        self._refresh_fields()
        self._refresh_actions()
        self.page_requested.emit(index)

    def set_page_preview(self, index, image):
        if index != self._current_page or self._shutting_down:
            if hasattr(image, 'close'):
                image.close()
            return
        self._canvas.set_image(image)
        self._refresh_canvas()
        self._refresh_actions()

    def _selected_field(self):
        item = self._field_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _refresh_fields(self, selected=None):
        selected = selected or self._selected_field()
        self._field_list.blockSignals(True)
        self._field_list.clear()
        for field in self._layout.fields:
            region = self._layout.region_for(field.name, max(0, self._current_page))
            if region is None:
                state = ('此頁已清除' if self._current_page > 0
                         and self._layout.has_override(field.name, self._current_page) else '未框選')
            elif self._current_page > 0 and self._layout.has_override(field.name, self._current_page):
                state = '此頁位置'
            else:
                state = '共用位置'
            item = QListWidgetItem(f'{field.name}  ·  {state}')
            item.setData(Qt.ItemDataRole.UserRole, field.name)
            self._field_list.addItem(item)
            if field.name == selected:
                self._field_list.setCurrentItem(item)
        if self._field_list.currentRow() < 0 and self._field_list.count():
            self._field_list.setCurrentRow(0)
        self._field_list.blockSignals(False)
        self._field_selected()

    def _field_selected(self, *_):
        name = self._selected_field()
        self._name_edit.setText(name or '')
        region = self._layout.region_for(name, max(0, self._current_page)) if name else None
        values = (region.x, region.y, region.width, region.height) if region else (.1, .1, .3, .1)
        for control, value in zip(self._coordinates, values):
            control.setValue(value * 100)
        if self._current_page > 0:
            self._region_note.setText('未調整的欄位會沿用第一頁；清除此頁後，需重新框選或套用第一頁。')
        else:
            self._region_note.setText('第一頁的位置會套用到所有頁，也可逐頁調整。')
        self._refresh_canvas()
        self._refresh_actions()

    def _refresh_canvas(self):
        self._canvas.set_regions(
            self._layout.effective_regions(max(0, self._current_page)), self._selected_field())

    def _add_field(self):
        number = 1
        while f'辨識{number}' in self._layout.field_names:
            number += 1
        name = f'辨識{number}'
        self._layout.add_field(name)
        template = self._template.toPlainText()
        self._template.setPlainText(template + (' ' if template and not template[-1].isspace() else '') + '{' + name + '}')
        self._clear_results()
        self._refresh_fields(name)

    def _remove_field(self):
        name = self._selected_field()
        if name:
            self._layout.remove_field(name)
            self._clear_results()
            self._refresh_fields()

    def _rename_field(self):
        old = self._selected_field()
        if not old:
            return
        try:
            template = self._layout.rename_field(old, self._name_edit.text(), self._template.toPlainText())
            new = self._name_edit.text().strip()
        except (TemplateError, ValueError) as exc:
            self.show_error(str(exc))
            return
        for result, status in zip(self._results, self._statuses):
            if old in result:
                result[new] = result.pop(old)
            if old in status:
                status[new] = status.pop(old)
        self._template.setPlainText(template)
        self._refresh_fields(new)
        self._populate_results()
        self._rebuild_output()

    def _insert_field(self):
        name = self._selected_field()
        if name:
            self._template.insertPlainText('{' + name + '}')
            self._editor_tabs.setCurrentIndex(1)
            self._template.setFocus()

    def _set_region(self, region):
        name = self._selected_field()
        if not name or self._current_page < 0 or not self._canvas.image_size[0]:
            return
        page = None if self._scope.currentData() == 'all' else self._current_page
        self._layout.set_region(name, region, page_index=page)
        self._clear_results()
        self._refresh_fields(name)

    def _apply_coordinates(self):
        try:
            self._set_region(Region(*(control.value() / 100 for control in self._coordinates)))
        except (TemplateError, ValueError) as exc:
            self.show_error(str(exc))

    def _clear_region(self):
        name = self._selected_field()
        if not name or self._current_page < 0 or self._busy:
            return
        if self._scope.currentData() == 'all':
            self._layout.set_region(name, None)
            for page in self._layout.overrides:
                self._layout.clear_override(name, page)
            message = f'已清除「{name}」所有頁面的框選。'
        else:
            self._layout.set_region(name, None, self._current_page)
            message = f'已清除「{name}」這一頁的框選，其他頁不變。'
        self._clear_results()
        self._refresh_fields(name)
        self._status.setText(message)

    def _clear_page_overrides(self):
        if self._current_page > 0:
            self._layout.clear_page_overrides(self._current_page)
            self._clear_results()
            self._refresh_fields()

    def _template_changed(self):
        self._rebuild_output()
        self._refresh_actions()

    def _validation_error(self):
        template = self._template.toPlainText()
        if not template.strip():
            return '請輸入每頁要輸出的文字模板。'
        try:
            names = validate_template(template, self._layout.field_names)
        except (TemplateError, ValueError) as exc:
            return str(exc)
        for page in range(len(self._pages)):
            for name in names:
                if self._layout.region_for(name, page) is None:
                    return f'請先框選「{name}」的位置（第 {page + 1} 頁）。'
        return ''

    def _refresh_actions(self):
        if not hasattr(self, '_recognize_button'):
            return
        error = self._validation_error()
        self._validation.setText(error if self._pages else '')
        self._recognize_button.setEnabled(bool(self._pages) and not error and not self._busy)
        self._cancel_button.setEnabled(self._busy)
        self._import_button.setEnabled(not self._busy)
        self._clear_documents_button.setEnabled(bool(self._pages or self._results or self._busy))
        self._file_limit.setEnabled(not self._busy)
        name = self._selected_field()
        selected = name in self._layout.field_names
        for button in (self._rename_button, self._remove_field_button):
            button.setEnabled(selected and not self._busy)
        self._apply_position.setEnabled(selected and not self._busy and bool(self._canvas.image_size[0]))
        if selected and self._current_page >= 0:
            if self._scope.currentData() == 'all':
                has_region = (self._layout.region_for(name, 0) is not None
                              or any(regions.get(name) is not None for regions in self._layout.overrides.values()))
            else:
                has_region = self._layout.region_for(name, self._current_page) is not None
        else:
            has_region = False
        self._clear_region_button.setEnabled(has_region and not self._busy)
        self._canvas.setEnabled(not self._busy and bool(self._canvas.image_size[0]))
        self._add_field_button.setEnabled(bool(self._pages) and not self._busy)
        self._reset_override.setEnabled(self._current_page > 0 and not self._busy
                                       and self._current_page in self._layout.overrides)
        self._previous.setEnabled(not self._busy and self._current_page > 0)
        self._next.setEnabled(not self._busy and 0 <= self._current_page < len(self._pages)-1)
        output_ready = bool(self._output.toPlainText().strip()) and not self._busy and not self._has_failed_results()
        self._copy_button.setEnabled(output_ready)
        self._export_button.setEnabled(output_ready)
        self._update_zoom_controls()

    def _update_zoom_controls(self, percent=None):
        if not hasattr(self, '_zoom_controls'):
            return
        percent = self._canvas.zoom_percent if percent is None else percent
        ready = bool(self._canvas.image_size[0]) and not self._busy
        self._zoom_label.setText(f'{percent:.0f}%' if percent else '—')
        for control in self._zoom_controls:
            control.setEnabled(ready)
        self._zoom_out.setEnabled(ready and percent > self._canvas.MIN_ZOOM * 100 + .01)
        self._zoom_in.setEnabled(ready and percent < self._canvas.MAX_ZOOM * 100 - .01)
        for shortcut in self._zoom_shortcuts:
            shortcut.setEnabled(ready)

    def _start_recognition(self):
        error = self._validation_error()
        if error:
            self.show_error(error)
            return
        if not self._pages or self._busy:
            return
        snapshot = {'layout': self._layout.copy(), 'template': self._template.toPlainText(),
                    'page_count': len(self._pages)}
        self._clear_results()
        self.recognize_requested.emit(snapshot)

    def set_busy(self, busy, message=''):
        self._busy = bool(busy)
        for control in self._edit_controls:
            control.setEnabled(not self._busy)
        self._progress.setVisible(self._busy)
        if self._busy:
            self._progress.setRange(0, 0)
        if message:
            self._status.setText(message)
        self._refresh_actions()

    def set_progress(self, done, total, message=''):
        if total > 0:
            self._progress.setRange(0, total)
            self._progress.setValue(done)
            self._progress.setFormat(f'{done}／{total}')
        if message:
            self._status.setText(message)

    def show_error(self, message):
        self._status.setStyleSheet(f'color: {_ERROR}; font-size: 12px;')
        self._status.setText(str(message))

    def _clear_results(self):
        self._results = []
        self._statuses = []
        self._messages = []
        self._results_table.setRowCount(0)
        self._output.clear()
        self._refresh_actions()

    def set_recognition_results(self, results, statuses=None, messages=None, cancelled=False):
        if isinstance(messages, bool):
            cancelled, messages = messages, None
        self._results = [dict(row) for row in results]
        self._statuses = [dict(row) for row in statuses] if statuses is not None else [
            {name: 'done' for name in row} for row in self._results]
        self._messages = [dict(row) for row in messages] if messages else [{} for _ in self._results]
        while len(self._statuses) < len(self._results):
            self._statuses.append({})
        self.set_busy(False)
        self._populate_results()
        self._rebuild_output()
        self._status.setStyleSheet(f'color: {_TEXT_SEC}; font-size: 12px;')
        if cancelled:
            self._status.setText('已停止。尚未完成的欄位可手動補上，或重新辨識。')
        elif self._has_failed_results():
            self._status.setText('部分欄位未完成，請修改標示的欄位，或重新辨識。')
        elif any('needs_review' in row.values() for row in self._statuses):
            self._status.setText('辨識完成，有些欄位請先核對。可直接修改表格中的文字。')
        else:
            self._status.setText(f'已完成 {len(self._results)} 頁，可複製或匯出 TXT。')
        self._result_tabs.setCurrentIndex(0 if self._has_failed_results() else 1)
        self._refresh_actions()

    def _populate_results(self):
        self._updating = True
        names = self._layout.field_names
        self._results_table.setColumnCount(len(names) + 1)
        self._results_table.setHorizontalHeaderLabels(['頁面', *names])
        self._results_table.setRowCount(len(self._results))
        for row, values in enumerate(self._results):
            page = QTableWidgetItem(self._page_label(self._pages[row], row) if row < len(self._pages) else f'第 {row+1} 頁')
            page.setFlags(page.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._results_table.setItem(row, 0, page)
            for column, name in enumerate(names, 1):
                item = QTableWidgetItem(values.get(name, ''))
                status = self._statuses[row].get(name, 'done')
                if status in ('failed', 'cancelled'):
                    item.setBackground(QColor('#542e34'))
                    item.setToolTip('未完成，請手動補上文字或重新辨識。')
                elif status == 'needs_review':
                    item.setBackground(QColor('#4b3b26'))
                    item.setToolTip('請對照原圖核對文字。')
                self._results_table.setItem(row, column, item)
        self._results_table.resizeColumnsToContents()
        self._results_table.horizontalHeader().setStretchLastSection(True)
        self._updating = False

    def _result_edited(self, item):
        if self._updating or item.column() == 0 or item.row() >= len(self._results):
            return
        name = self._layout.field_names[item.column()-1]
        self._results[item.row()][name] = item.text()
        self._statuses[item.row()][name] = 'edited'
        self._updating = True
        item.setBackground(QColor(_BG_RAISE))
        item.setToolTip('已手動修改')
        self._updating = False
        self._rebuild_output()
        self._refresh_actions()

    def _edit_result(self, row, column):
        if self._busy or column <= 0 or not 0 <= row < len(self._results):
            return
        item = self._results_table.item(row, column)
        if item is None:
            return
        name = self._layout.field_names[column-1]
        original = item.text()
        text, accepted = QInputDialog.getMultiLineText(
            self, f'修改文字 · {name}', '確認這一頁的辨識文字：', original)
        if accepted:
            item.setText(text)
            if text == original:
                # Accepting an intentionally empty failed cell is still a
                # manual confirmation; it need not invent replacement text.
                self._result_edited(item)

    def _has_failed_results(self):
        try:
            names = validate_template(self._template.toPlainText(), self._layout.field_names)
        except (TemplateError, ValueError):
            return True
        return bool(self._results) and (len(self._results) != len(self._pages) or any(
            name not in row or self._statuses[index].get(name) in ('failed', 'cancelled')
            for index, row in enumerate(self._results) for name in names))

    def _rebuild_output(self):
        if not hasattr(self, '_output'):
            return
        try:
            validate_template(self._template.toPlainText(), self._layout.field_names)
            text = build_output(self._template.toPlainText(), self._results) if self._results else ''
        except (TemplateError, ValueError):
            text = ''
        self._output.setPlainText(text)

    def _copy_results(self):
        if not self._copy_button.isEnabled():
            return
        from ..clipboard.writer import copy_text
        if copy_text(self._output.toPlainText()):
            self._status.setStyleSheet(f'color: {_SUCCESS}; font-size: 12px;')
            self._status.setText('已複製 TXT 預覽的全部內容。')

    def _choose_export(self):
        if not self._export_button.isEnabled():
            return
        suggested = str(Path(self.export_directory) / '文件套用結果.txt') if self.export_directory else '文件套用結果.txt'
        path, _ = QFileDialog.getSaveFileName(self, '匯出 TXT', suggested, '文字檔案 (*.txt)')
        if path:
            try:
                self.export_to_path(path)
            except ValueError as exc:
                self.show_error(str(exc))
            except OSError:
                self.show_error('無法儲存文字檔，請確認資料夾權限或選擇其他位置。')

    def export_to_path(self, path):
        if not self._export_button.isEnabled():
            return False
        destination = Path(path)
        # Save dialogs allow a typed path outside their filter. Never replace an
        # input document, including an alias through a symlink or hard link.
        for page in self._pages:
            source = page.get('source') if isinstance(page, dict) else getattr(page, 'source', None)
            original = source.get('path') if isinstance(source, dict) else getattr(source, 'path', None)
            if original is None:
                continue
            original = Path(original)
            try:
                same_file = destination.resolve() == original.resolve()
                if destination.exists() and original.exists():
                    same_file = same_file or destination.samefile(original)
            except (OSError, RuntimeError) as exc:
                raise ValueError('無法確認輸出位置，請選擇其他資料夾。') from exc
            if same_file:
                raise ValueError('不能覆寫匯入的原始文件，請選擇其他 TXT 檔案。')
        if destination.suffix.lower() != '.txt':
            raise ValueError('請使用 .txt 副檔名儲存文字結果。')
        descriptor, temporary = tempfile.mkstemp(prefix='.inkpick-txt-', suffix='.tmp', dir=destination.parent)
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8-sig', newline='') as stream:
                stream.write(self._output.toPlainText())
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
        self._status.setStyleSheet(f'color: {_SUCCESS}; font-size: 12px;')
        self._status.setText('已匯出 TXT。')
        return True

    def showEvent(self, event):
        super().showEvent(event)
        screen = self.screen()
        if screen:
            area = screen.availableGeometry()
            self.resize(min(self.width(), max(self.minimumWidth(), area.width()-32)),
                        min(self.height(), max(self.minimumHeight(), area.height()-48)))

    def closeEvent(self, event):
        if self._busy:
            self.cancel_requested.emit()
        event.ignore()
        self.hide()

    def shutdown(self):
        self._shutting_down = True
        if self._busy:
            self.cancel_requested.emit()
        self.setEnabled(False)
        self.hide()
