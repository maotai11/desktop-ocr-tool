"""Real nested settings dialogs must not commit after their owner has finished."""
from pathlib import Path

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QColorDialog, QDialog, QLineEdit, QMessageBox, QWidget

from src.core.config import ConfigManager
from src.ui.settings_dialog import SettingsDialog


@pytest.fixture
def settings(qtbot, tmp_path, monkeypatch, tag_repo):
    monkeypatch.setattr('src.core.config.get_project_root', lambda: str(tmp_path))
    owner = QWidget()
    owner._selected_items = set()
    qtbot.addWidget(owner)
    tag_repo.create('existing', '#123456')
    dialog = SettingsDialog(ConfigManager(), owner, tag_repo=tag_repo)
    dialog._test_owner = owner
    qtbot.addWidget(dialog)
    dialog.show()
    dialog._tag_list.setCurrentRow(0)
    return dialog


@pytest.mark.parametrize('action', ['add', 'edit', 'delete'])
def test_nested_tag_accept_after_settings_rejected_does_not_write(settings, tag_repo, monkeypatch, action):
    before = [(tag.id, tag.name, tag.color) for tag in tag_repo.list_all()]
    changed, notices = [], []
    settings.history_changed.connect(lambda: changed.append(True))
    monkeypatch.setattr(QMessageBox, 'information', lambda *args: notices.append(args))

    def close_parent_then_accept_child():
        child = next(dialog for dialog in settings.findChildren(QDialog) if dialog.isVisible())
        if action != 'delete':
            child.findChild(QLineEdit).setText('should not be saved')
        settings.reject()
        if action == 'delete':
            child.button(QMessageBox.StandardButton.Yes).click()
        else:
            child.accept()

    QTimer.singleShot(0, close_parent_then_accept_child)
    {'add': settings._add_tag_dialog, 'edit': settings._edit_tag_dialog,
     'delete': settings._delete_tag_confirm}[action]()
    assert [(tag.id, tag.name, tag.color) for tag in tag_repo.list_all()] == before
    assert changed == notices == []
    assert settings.result() == QDialog.DialogCode.Rejected


def test_finished_settings_rejects_queued_save_and_accept(settings, monkeypatch):
    path = Path(settings._cfg._settings_path)
    before = path.read_bytes()
    autostart = []
    monkeypatch.setattr('src.core.autostart.set_autostart', autostart.append)
    settings._cb_autostart.setChecked(not settings._cb_autostart.isChecked())
    settings.reject()
    settings._save()
    settings.accept()
    assert path.read_bytes() == before
    assert autostart == []
    assert settings.result() == QDialog.DialogCode.Rejected


def test_finished_settings_cannot_apply_tag_to_items(settings, tag_repo, item_repo, make_text_dto):
    item_id = item_repo.insert(make_text_dto('keep untagged'))
    tag_id = tag_repo.list_all()[0].id
    settings.parent()._selected_items.add(item_id)
    settings.reject()
    settings._apply_tag_to_selected(tag_id)
    assert tag_repo.usage_count(tag_id) == 0


def test_shutdown_unwinds_both_tag_and_color_modal_loops_without_saving(settings, tag_repo):
    seen = []

    def stop_from_color_dialog():
        visible = [child for child in settings.findChildren(QDialog) if child.isVisible()]
        assert len(visible) == 2
        assert any(isinstance(child, QColorDialog) for child in visible)
        settings.shutdown()
        seen.append(all(not child.isVisible() for child in visible))

    def open_color_dialog():
        settings._new_tag_name.setText('not saved')
        QTimer.singleShot(0, stop_from_color_dialog)
        settings._new_tag_color.click()

    QTimer.singleShot(0, open_color_dialog)
    settings._add_tag_dialog()
    assert seen == [True]
    assert [tag.name for tag in tag_repo.list_all()] == ['existing']
    assert not settings.isVisible()
    assert settings.result() == QDialog.DialogCode.Rejected


@pytest.mark.parametrize('action', ['add', 'edit', 'delete'])
def test_active_settings_tag_dialogs_still_commit_explicit_accept(settings, tag_repo, monkeypatch, action):
    monkeypatch.setattr(QMessageBox, 'information', lambda *args: None)

    def accept_child():
        child = next(dialog for dialog in settings.findChildren(QDialog) if dialog.isVisible())
        if action == 'delete':
            child.button(QMessageBox.StandardButton.Yes).click()
        else:
            child.findChild(QLineEdit).setText('changed')
            child.accept()

    QTimer.singleShot(0, accept_child)
    {'add': settings._add_tag_dialog, 'edit': settings._edit_tag_dialog,
     'delete': settings._delete_tag_confirm}[action]()
    names = {tag.name for tag in tag_repo.list_all()}
    assert names == {'add': {'existing', 'changed'}, 'edit': {'changed'}, 'delete': set()}[action]
    assert settings.isVisible()


def test_active_settings_save_persists_once_and_finishes(settings, monkeypatch):
    autostart = []
    monkeypatch.setattr('src.core.autostart.set_autostart', autostart.append)
    settings._cb_autostart.setChecked(True)
    settings._save()
    assert settings._cfg.get('general', 'start_with_windows') is True
    assert settings.result() == QDialog.DialogCode.Accepted
    settings._save()
    assert autostart == [True]
