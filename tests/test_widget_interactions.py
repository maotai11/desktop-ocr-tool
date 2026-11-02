# -*- coding: utf-8 -*-
from unittest.mock import MagicMock

from PySide6.QtCore import QPoint, QPointF, QEvent, Qt


class _Cfg:
    def __init__(self):
        self._values = {
            ("ui", "widget_position"): "remember",
            ("ui", "widget_click_action"): "select",
            ("ui", "widget_max_items"): 20,
            ("ui", "widget_saved_x"): None,
            ("ui", "widget_saved_y"): None,
        }

    def get(self, *keys, default=None):
        return self._values.get(tuple(keys), default)

    def set(self, *keys_and_value):
        *keys, value = keys_and_value
        self._values[tuple(keys)] = value


class _Item:
    def __init__(self, item_id: int, text: str):
        self.id = item_id
        self._text = text
        self.thumbnail_path = None
        self.raw_image_path = None
        self.created_at = "2026-04-09 00:00:00"
        self.source_mode = "clipboard_text"
        self.ocr_status = "done"
        self.is_pinned = False
        self.item_type = "text"

    def get_effective_text(self):
        return self._text


def _make_widget(tmp_path):
    from src.data.file_manager import FileManager
    from src.ui.widget import FloatingWidget

    items = [_Item(1, "alpha"), _Item(2, "beta")]
    item_repo = MagicMock()
    item_repo.list_recent.return_value = items
    item_repo.search_fulltext.return_value = items

    widget = FloatingWidget(
        item_repo=item_repo,
        tag_repo=MagicMock(),
        file_mgr=FileManager(str(tmp_path / "data")),
        db_worker=MagicMock(),
        ocr_worker=MagicMock(),
        cfg=_Cfg(),
        data_dir=str(tmp_path),
    )
    return widget


class _MouseEvent:
    def __init__(self, event_type, local_pos: QPoint, global_pos: QPoint, button, buttons):
        self._event_type = event_type
        self._local_pos = QPointF(local_pos)
        self._global_pos = QPointF(global_pos)
        self._button = button
        self._buttons = buttons
        self.accepted = False

    def type(self):
        return self._event_type

    def position(self):
        return self._local_pos

    def globalPosition(self):
        return self._global_pos

    def button(self):
        return self._button

    def buttons(self):
        return self._buttons

    def accept(self):
        self.accepted = True


def _mouse_event(event_type, local_pos: QPoint, global_pos: QPoint, button, buttons):
    return _MouseEvent(event_type, local_pos, global_pos, button, buttons)


def test_batch_mode_click_toggles_selected_items(qapp, tmp_path):
    widget = _make_widget(tmp_path)

    widget._btn_batch_select.setChecked(True)
    widget._toggle_batch_mode()
    widget._handle_card_click(1)

    assert widget._selected_items == {1}
    assert widget._btn_batch_action.text() == "操作 (1)"

    widget._handle_card_click(1)

    assert widget._selected_items == set()
    assert widget._btn_batch_action.text() == "操作 (0)"
    widget.close()


def test_drag_host_moves_widget_and_persists_position(qapp, tmp_path):
    widget = _make_widget(tmp_path)
    widget.move(100, 100)

    press = _mouse_event(
        QEvent.Type.MouseButtonPress,
        QPoint(40, 10),
        QPoint(140, 110),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
    )
    move = _mouse_event(
        QEvent.Type.MouseMove,
        QPoint(90, 60),
        QPoint(190, 160),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton,
    )
    release = _mouse_event(
        QEvent.Type.MouseButtonRelease,
        QPoint(90, 60),
        QPoint(190, 160),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.NoButton,
    )

    drag_target = widget._ocr_status_lbl

    assert widget.eventFilter(drag_target, press) is True
    assert widget.eventFilter(drag_target, move) is True
    assert widget.pos() == QPoint(150, 150)
    assert widget.eventFilter(drag_target, release) is True
    assert widget._cfg.get("ui", "widget_saved_x") == 150
    assert widget._cfg.get("ui", "widget_saved_y") == 150
    widget.close()


def test_settings_repeated_close_releases_qobjects(qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import QCoreApplication
    from src.ui.settings_dialog import SettingsDialog
    widget=_make_widget(tmp_path)
    # Only the human wait is substituted; real dialogs/children/cleanup run.
    monkeypatch.setattr(SettingsDialog,'exec',lambda self: 0)
    try:
        for _ in range(500):
            widget.open_settings()
            QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
        assert len(widget.findChildren(SettingsDialog))==0
    finally:
        widget.deleteLater()
        QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)


def test_editor_close_releases_dictionary_and_qobject(qapp, tmp_path, item_repo, make_text_dto):
    from PySide6.QtCore import QCoreApplication
    from src.ui.editor_window import EditorWindow
    widget=_make_widget(tmp_path);widget._item_repo=item_repo
    try:
        for i in range(100):
            item=item_repo.get_by_id(item_repo.insert(make_text_dto(f'item {i}')))
            widget._open_editor(item)
            editor=widget._editor_windows[item.id]
            editor.close()
            QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
        assert not widget._editor_windows
        assert not widget.findChildren(EditorWindow)
    finally:
        widget.deleteLater()
        QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)


def test_card_literal_text_and_keyboard(qtbot,tmp_path):
    from src.ui.components.item_card import ItemCard
    from PySide6.QtWidgets import QLabel
    item=_Item(1,'<b>token</b>');card=ItemCard(item,str(tmp_path));qtbot.addWidget(card)
    labels=[x for x in card.findChildren(QLabel) if x.text()=='<b>token</b>']
    assert len(labels)==1 and labels[0].textFormat()==Qt.TextFormat.PlainText
    selected=[];opened=[];card.clicked.connect(selected.append);card.double_clicked.connect(opened.append)
    qtbot.keyClick(card,Qt.Key.Key_Space);qtbot.keyClick(card,Qt.Key.Key_Return)
    assert selected==[1] and opened==[1]


def test_floating_window_has_no_search_and_filters_recent_items(qtbot, tmp_path):
    from PySide6.QtWidgets import QLineEdit, QTextEdit
    widget = _make_widget(tmp_path)
    qtbot.addWidget(widget)
    assert not widget.findChildren(QLineEdit)
    assert not widget.findChildren(QTextEdit)
    widget._item_repo.reset_mock()

    widget._filter_buttons['image'].click()

    widget._item_repo.list_recent.assert_called_once_with(limit=20, item_type='image')
    widget._item_repo.search_fulltext.assert_not_called()
    assert widget._filter_buttons['image'].isChecked()
    assert not widget._filter_buttons['all'].isChecked()


def test_quick_search_opens_history_and_focuses_its_search(qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QWidget, QLineEdit, QVBoxLayout
    widget = _make_widget(tmp_path)
    qtbot.addWidget(widget)
    console = QWidget()
    qtbot.addWidget(console)
    console._search = QLineEdit("原有查詢", console)
    QVBoxLayout(console).addWidget(console._search)
    widget._console = console
    opened = []

    def open_console():
        opened.append(True)
        console.show()
        console.activateWindow()

    monkeypatch.setattr(widget, 'open_console', open_console)
    widget.focus_search()
    qtbot.waitUntil(lambda: console._search.hasFocus())
    assert opened == [True]
    assert console._search.selectedText() == "原有查詢"


def test_storage_button_opens_active_local_directory(qtbot, tmp_path, monkeypatch):
    from src.ui.widget import QDesktopServices
    widget = _make_widget(tmp_path)
    qtbot.addWidget(widget)
    custom_path = tmp_path / "自訂 儲存 & 截圖"
    custom_path.mkdir()
    widget._data_dir = str(custom_path)
    urls = []
    monkeypatch.setattr(QDesktopServices, 'openUrl', lambda url: urls.append(url) or True)

    widget._btn_data_dir.click()

    assert len(urls) == 1
    assert urls[0].isLocalFile()
    assert urls[0].toLocalFile() == str(custom_path)


def test_storage_open_failure_shows_manual_path(qtbot, tmp_path, monkeypatch):
    from src.ui.widget import QDesktopServices, QMessageBox
    widget = _make_widget(tmp_path)
    qtbot.addWidget(widget)
    monkeypatch.setattr(QDesktopServices, 'openUrl', lambda _url: False)
    notices = []
    monkeypatch.setattr(QMessageBox, 'information', lambda *args: notices.append(args[1:]))

    widget.open_data_directory()

    assert notices == [("無法開啟資料夾", f"請在檔案總管開啟：\n{tmp_path}")]


def test_ocr_status_is_brief_with_details_in_tooltip(qtbot, tmp_path):
    widget = _make_widget(tmp_path)
    qtbot.addWidget(widget)
    detailed = "OCR #124 待確認；請核對候選與原圖"

    widget.set_ocr_status(detailed)

    assert widget._ocr_status_lbl.text() == "請核對文字"
    assert widget._ocr_status_lbl.toolTip() == detailed


def test_long_history_preview_stays_inside_card(qtbot, tmp_path):
    from src.ui.components.item_card import ItemCard, _PreviewLabel
    text = "這是一段很長的會議紀錄" * 20
    card = ItemCard(_Item(1, text), str(tmp_path))
    qtbot.addWidget(card)
    card.resize(280, 60)
    card.show()
    qtbot.waitExposed(card)
    preview = card.findChildren(_PreviewLabel)[0]

    assert preview.text().endswith("…")
    assert preview.toolTip() == text[:60]
    assert preview.geometry().right() < card.width()
    assert preview.geometry().left() >= 60


def test_disabled_filter_motion_snaps_to_full_opacity(qtbot, tmp_path):
    from PySide6.QtCore import QAbstractAnimation
    from PySide6.QtWidgets import QProxyStyle, QStyle

    class ReducedMotionStyle(QProxyStyle):
        disabled_hint = QStyle.StyleHint.SH_Widget_Animate

        def styleHint(self, hint, option=None, widget=None, return_data=None):
            if hint == self.disabled_hint:
                return 0
            if hint == QStyle.StyleHint.SH_Widget_Animate:
                return 1
            return super().styleHint(hint, option, widget, return_data)

    widget = _make_widget(tmp_path)
    qtbot.addWidget(widget)
    style = ReducedMotionStyle()
    style.setParent(widget)
    widget.setStyle(style)
    widget.show()
    for hint in (QStyle.StyleHint.SH_Widget_Animate, QStyle.StyleHint.SH_Widget_Animation_Duration):
        style.disabled_hint = hint
        widget._list_opacity.setOpacity(0.5)
        widget._set_filter('image')
        assert widget._list_reveal.state() == QAbstractAnimation.State.Stopped
        assert widget._list_opacity.opacity() == 1.0


def test_empty_ocr_status_keeps_short_visible_message(qtbot, tmp_path):
    widget = _make_widget(tmp_path)
    qtbot.addWidget(widget)
    widget.set_ocr_status('未找到文字，已保留先前內容')
    assert widget._ocr_status_lbl.text() == '未找到文字'
    assert widget._ocr_status_lbl.toolTip() == '未找到文字，已保留先前內容'
