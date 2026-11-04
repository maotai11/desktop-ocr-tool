"""History mutations update both real Qt views without losing user context."""
from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import QItemSelectionModel, Qt, Signal
from PySide6.QtWidgets import QDialog, QMessageBox, QWidget

from src.ui.widget import FloatingWidget
from test_widget_interactions import _Cfg


@pytest.fixture
def views(qtbot, tmp_path, item_repo, tag_repo, file_mgr, db_worker, make_text_dto):
    ids = [item_repo.insert(make_text_dto(text)) for text in ('alpha one', 'alpha two', 'beta')]
    widget = FloatingWidget(item_repo, file_mgr, db_worker, MagicMock(), _Cfg(),
                            str(tmp_path), tag_repo=tag_repo)
    qtbot.addWidget(widget)
    widget.open_console()
    console = widget._console
    qtbot.addWidget(console)
    yield widget, console, ids
    widget.close()


def ids_in_console(console):
    return {item.id for item in console._current_items}


def test_float_delete_updates_open_console_and_clears_deleted_detail(views):
    widget, console, ids = views
    row = next(row for row, item in enumerate(console._current_items) if item.id == ids[0])
    console._table.setCurrentCell(row, 0)
    assert console._selected_item.id == ids[0]
    widget._soft_delete(ids[0])
    assert ids[0] not in ids_in_console(console)
    assert ids[0] not in {item.id for item in widget._items}
    assert console._selected_item is None
    assert console._text_view.toPlainText() == ''


def test_worker_pin_archive_delete_updates_both_views_and_keeps_filter(views):
    widget, console, ids = views
    console._search.setText('alpha')
    console._do_search()
    widget._set_filter('pinned')
    assert not widget._items
    widget._db_worker.set_pinned(ids[0], True)
    assert {item.id for item in widget._items} == {ids[0]}
    assert console._search.text() == 'alpha'
    assert ids_in_console(console) == {ids[0], ids[1]}
    widget._db_worker.soft_delete_items([ids[0]])
    assert not widget._items
    assert ids_in_console(console) == {ids[1]}
    assert widget._current_filter == 'pinned'


def test_shared_refresh_preserves_console_batch_selection_and_current_record(views):
    widget, console, ids = views
    console._search.setText('alpha')
    console._do_search()
    row = next(row for row, item in enumerate(console._current_items) if item.id == ids[0])
    console._table.setCurrentCell(row, 0)
    console._toggle_batch_mode(True)
    console._table.selectionModel().select(
        console._table.model().index(row, 0),
        QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
    widget.refresh_history_views()
    selected = {console._current_items[index.row()].id
                for index in console._table.selectionModel().selectedRows()}
    assert selected == {ids[0]}
    assert console._selected_item.id == ids[0]
    assert console._search.text() == 'alpha'
    assert console._batch_mode


def test_removed_selected_item_clears_detail_even_during_batch_selection(views):
    widget, console, ids = views
    row = next(row for row, item in enumerate(console._current_items) if item.id == ids[0])
    console._table.setCurrentCell(row, 0)
    assert console._text_view.toPlainText() == 'alpha one'
    console._toggle_batch_mode(True)
    widget._soft_delete(ids[0])
    assert console._selected_item is None
    assert console._text_view.toPlainText() == ''


def test_archive_category_excludes_unarchived_and_refreshes_after_restore(views):
    widget, console, ids = views
    widget._db_worker.set_archived(ids[0], True)
    archived_row = next(index for index in range(console._side_list.count())
                        if console._side_list.item(index).data(Qt.ItemDataRole.UserRole) == 'archived')
    console._side_list.setCurrentRow(archived_row)
    assert ids_in_console(console) == {ids[0]}
    widget._db_worker.set_archived(ids[0], False)
    assert not console._current_items
    assert console._side_list.currentItem().data(Qt.ItemDataRole.UserRole) == 'archived'


def test_console_editor_update_refreshes_float_and_console(views, monkeypatch):
    widget, console, ids = views
    class Editor(QWidget):
        item_updated = Signal(int)

        def __init__(self, **kwargs):
            super().__init__(kwargs.get('parent'))

    monkeypatch.setattr('src.ui.editor_window.EditorWindow', Editor)
    item = widget._item_repo.get_by_id(ids[0])
    console._open_editor(item)
    editor = console._editor_windows[ids[0]]
    widget._item_repo.update_edited_text(ids[0], 'changed text')
    editor.item_updated.emit(ids[0])
    assert next(item for item in widget._items if item.id == ids[0]).get_effective_text() == 'changed text'
    assert next(item for item in console._current_items if item.id == ids[0]).get_effective_text() == 'changed text'
    editor.close()


def test_refresh_guard_does_not_recurse_if_console_notifies_again(views, monkeypatch):
    widget, console, _ = views
    calls = []
    monkeypatch.setattr(widget, 'refresh_list', lambda: calls.append('float'))
    monkeypatch.setattr(console, 'refresh', lambda: widget.refresh_history_views())
    widget.refresh_history_views()
    assert calls == ['float']


def test_purge_signal_refreshes_float_and_drops_stale_batch_ids(views, monkeypatch):
    widget, console, ids = views
    monkeypatch.setattr(QMessageBox, 'information', lambda *args: None)
    widget._toggle_batch_mode()
    widget._selected_items.add(ids[0])
    widget._db_worker.soft_delete_items([ids[0]])
    assert ids[0] not in widget._selected_items
    widget._db_worker.purge_soft_deleted()
    assert ids[0] not in ids_in_console(console)


def test_settings_saved_signal_only_fires_after_accept(qapp, tmp_path, monkeypatch):
    from test_widget_interactions import _make_widget
    from src.ui.settings_dialog import SettingsDialog
    widget = _make_widget(tmp_path)
    saved = []
    widget.settings_saved.connect(lambda: saved.append(True))
    monkeypatch.setattr(SettingsDialog, 'exec', lambda self: QDialog.DialogCode.Rejected)
    widget.open_settings()
    assert not saved
    monkeypatch.setattr(SettingsDialog, 'exec', lambda self: QDialog.DialogCode.Accepted)
    widget.open_settings()
    assert saved == [True]
    widget.close()


def test_settings_immediate_tag_change_refreshes_views_even_if_dialog_cancelled(views, monkeypatch):
    from src.ui.settings_dialog import SettingsDialog
    widget, console, _ = views
    refreshed, saved = [], []
    original_refresh = widget.refresh_history_views
    def refresh(*args):
        refreshed.append(True)
        original_refresh()
    monkeypatch.setattr(widget, 'refresh_history_views', refresh)
    widget.settings_saved.connect(lambda: saved.append(True))
    def edit_then_cancel(dialog):
        dialog._tag_repo.create('new tag', '#123456')
        dialog.history_changed.emit()
        return QDialog.DialogCode.Rejected
    monkeypatch.setattr(SettingsDialog, 'exec', edit_then_cancel)
    widget.open_settings()
    assert refreshed == [True] and not saved
    assert any(tag.name == 'new tag' for tag in widget._tag_repo.list_all())


def test_tray_and_float_pause_share_one_state(qapp, tmp_path):
    from src.ui.tray_manager import TrayManager
    from test_widget_interactions import _make_widget
    widget = _make_widget(tmp_path)
    watcher = MagicMock()
    widget._clip_watcher = watcher
    tray = TrayManager(widget)
    widget.toggle_clipboard_pause()
    assert widget._clip_paused and widget._btn_pause.isChecked() and tray._act_pause.isChecked()
    watcher.pause.assert_called_once_with(True)
    tray._act_pause.trigger()
    assert not widget._clip_paused and not widget._btn_pause.isChecked() and not tray._act_pause.isChecked()
    assert watcher.pause.call_count == 2
    watcher.pause.assert_called_with(False)
    widget.close()
