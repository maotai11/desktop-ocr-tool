"""A refused export preserves previous output and remains visible to the user."""
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PySide6.QtWidgets import QFileDialog, QMessageBox

from src.core import resource_budget as budget
from src.ui.editor_window import EditorWindow
from src.ui.main_window import MainWindow
from test_document_template_window import ready, window  # noqa: F401
from test_widget_interactions import _make_widget


def refuse_disk(monkeypatch):
    monkeypatch.setattr(budget, 'managed_disk_usage',
                        lambda roots: budget.DiskUsage(budget.RESOURCE_LIMIT_BYTES, 1, 0))


@pytest.mark.parametrize('surface', ['floating', 'history', 'editor'])
def test_image_save_budget_refusal_preserves_previous_and_notifies(
        qtbot, tmp_path, item_repo, tag_repo, file_mgr, db_worker,
        make_image_dto, monkeypatch, surface):
    source = Path(file_mgr.get_abs_path('captures/source.png'))
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b'original image')
    item = item_repo.get_by_id(item_repo.insert(make_image_dto(rel_path='captures/source.png')))
    destination = tmp_path / 'previous.png'
    destination.write_bytes(b'previous export')
    if surface == 'floating':
        view = _make_widget(tmp_path)
        view._file_mgr = file_mgr
        action = lambda: view._save_item_image(item)
    elif surface == 'history':
        view = MainWindow(item_repo, tag_repo, file_mgr, db_worker, MagicMock(),
                          MagicMock(), str(tmp_path))
        action = lambda: view._save_item_image(item)
    else:
        view = EditorWindow(item, item_repo, file_mgr, MagicMock())
        action = view._save_image_as
    qtbot.addWidget(view)
    notices = []
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *args: (str(destination), ''))
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: notices.append(args))
    refuse_disk(monkeypatch)
    action()
    assert destination.read_bytes() == b'previous export'
    assert source.read_bytes() == b'original image'
    assert len(notices) == 1 and '1 GB' in str(notices[0][-1])
    assert not list(tmp_path.glob('.ocr-output-*.tmp'))


def test_template_export_refusal_preserves_previous_and_result(window, tmp_path, monkeypatch):
    ready(window, 1)
    window.set_recognition_results([{'辨識1': '新結果'}])
    destination = tmp_path / 'previous.txt'
    destination.write_bytes(b'previous text')
    refuse_disk(monkeypatch)
    with pytest.raises(budget.ResourceBudgetExceeded):
        window.export_to_path(destination)
    assert destination.read_bytes() == b'previous text'
    assert window._output.toPlainText() == '新結果'
    assert not list(tmp_path.glob('.ocr-output-*.tmp'))
