# -*- coding: utf-8 -*-
from types import SimpleNamespace
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
