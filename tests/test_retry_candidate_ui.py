"""Visual OCR alternatives are explicit, reversible copy drafts only."""
from copy import deepcopy

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMenu

from src.ui.ocr_result_dialog import OcrResultDialog


def dialog(qtbot):
    rows = [{'text': '100', 'box': [[0, 0], [60, 0], [60, 20], [0, 20]],
             'alternatives': [{'text': '700', 'confidence': .99, 'source': 'visual_retry'}]},
            {'text': '100', 'box': [[0, 40], [60, 40], [60, 60], [0, 60]]}]
    before = deepcopy(rows)
    owner = OcrResultDialog('  100\n\n100  \n', rows)
    qtbot.addWidget(owner); owner.show()
    return owner, rows, before


def test_real_candidate_menu_applies_only_selected_visual_occurrence(qtbot):
    owner, rows, before = dialog(qtbot)
    owner.select_indexes([0, 1])
    owner._list.setCurrentRow(0)
    assert owner._candidate_button.isVisible() and owner._candidate_button.isEnabled()
    assert '700' in owner._list.item(0).toolTip()

    def choose():
        menu = next(m for m in owner.findChildren(QMenu) if m.isVisible())
        menu.actions()[1].trigger()
        menu.close()

    QTimer.singleShot(0, choose)
    owner._show_candidate_menu()
    assert owner.selected_text == '  700\n\n100  \n'
    assert owner._text == '  100\n\n100  \n' and rows == before
    owner._apply_alternative(0, '100')
    assert owner.selected_text == owner._text


def test_close_during_candidate_menu_rejects_late_apply_and_copy(qtbot):
    owner, _, _ = dialog(qtbot)
    requested = []
    owner.copy_requested.connect(requested.append)
    owner.select_indexes([0])
    owner._list.setCurrentRow(0)
    QTimer.singleShot(0, owner.reject)
    owner._show_candidate_menu()
    owner._apply_alternative(0, '700')
    owner._request_copy()
    assert not requested and owner._entries[0]['text'] == '100'
    assert not any(m.isVisible() for m in owner.findChildren(QMenu))


def test_no_candidate_entry_keeps_original_ui_and_selection(qtbot):
    owner = OcrResultDialog('unchanged')
    qtbot.addWidget(owner); owner.show()
    assert not owner._candidate_button.isVisible()
    owner.select_indexes([0])
    assert owner.selected_text == 'unchanged'
