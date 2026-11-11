"""Invoice controls keep user-triggered refresh and annotation appearance separate."""
import pytest
from PySide6.QtCore import Qt

from src.ui.shared_search_window import SharedSearchWindow
from test_shared_search_ui import Controller, candidate


class InvoiceController(Controller):
    def __init__(self):
        super().__init__()
        self.date_equivalence = True
        self.refreshes = 0

    def set_date_equivalence(self, enabled):
        self.date_equivalence = enabled
        return True

    def refresh_now(self):
        self.refreshes += 1
        self.state_changed.emit('checking')
        return True


@pytest.fixture
def window(qtbot):
    widget = SharedSearchWindow(InvoiceController())
    qtbot.addWidget(widget)
    qtbot.addWidget(widget.highlight_overlay)
    qtbot.addWidget(widget.resume_button)
    qtbot.addWidget(widget.manual_button)
    yield widget
    widget.shutdown()


def test_manual_refresh_controls_hide_for_capture_and_cannot_restart_closed_tool(qtbot, window):
    window._start()
    assert window.manual_button.isVisible()
    assert window.resume_button.isVisible()
    qtbot.mouseClick(window.manual_button, Qt.MouseButton.LeftButton)
    assert window.controller.refreshes == 1 and not window.isVisible()
    assert window.before_capture()
    assert not window.manual_button.isVisible() and not window.resume_button.isVisible()
    window.after_capture()
    assert window.manual_button.isVisible() and window.resume_button.isVisible()
    window.close()
    window._manual_refresh()
    window.manual_button.click()  # A previously queued click must remain harmless.
    window.after_capture()
    assert window.controller.refreshes == 1
    assert not window.manual_button.isVisible() and not window.resume_button.isVisible()
    assert not window.controller.running


def test_transparency_control_never_triggers_recognition_or_capture_restore(window):
    window.controller.publish()
    assert window.before_capture()
    window._background_transparency.setValue(25)
    assert window.highlight_overlay.background_opacity == 75
    assert not window.highlight_overlay.isVisible()
    assert not window.manual_button.isVisible()
    window._background_transparency.setValue(100)
    assert window.highlight_overlay.background_opacity == 0
    assert window.controller.refreshes == 0
    window.pause()
    window._background_transparency.setValue(0)
    window.after_capture()
    assert not window.highlight_overlay.isVisible()
    assert not window.controller.running


def test_date_setting_retires_current_results_and_exact_mode_disables_conversion_control(window):
    window.controller.publish()
    window._date_equivalence.setChecked(False)
    assert not window.controller.date_equivalence
    assert not window.controller.running
    assert not window.highlight_overlay.marks
    window._mode.setCurrentIndex(window._mode.findData('exact'))
    assert not window._date_equivalence.isEnabled()
    window._mode.setCurrentIndex(window._mode.findData('normalized'))
    assert window._date_equivalence.isEnabled()
    assert not window.controller.running


@pytest.mark.parametrize('kind,score,status,expected', [
    ('similar', .8, 'uncertain', '80%'),
    ('similar', .999999, 'uncertain', '99.9%'),
    ('date_equivalent', 1.0, 'matched', '100%'),
])
def test_selected_candidate_percentage_and_date_reason_are_visible(window, kind, score, status, expected):
    row = candidate(text='synthetic invoice value')
    row.update(match_type=kind, score=score)
    window.controller.fields[0]['result'] = {
        'status': status, 'text': 'synthetic input value', 'selected_id': row['id'], 'candidates': [row]}
    window._refresh_fields()
    card = window._fields_cards['a1']
    assert expected in card._status.text()
    if score < 1:
        assert '100%' not in card._status.text()
        assert '需確認' in card._status.text()
    if kind == 'date_equivalent':
        assert '日期換算' in card._status.text()
