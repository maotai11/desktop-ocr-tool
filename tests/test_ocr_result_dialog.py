"""Interaction coverage for explicit OCR region/text copy selection."""
import json

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QImage, QTextCursor

from src.ui.ocr_result_dialog import OcrResultDialog


def _detail(text, x, y, width=50, height=20):
    return {'text': text, 'box': [[x, y], [x + width, y],
                                 [x + width, y + height], [x, y + height]]}


def _dialog(qtbot, text='第一行\n第二行', details=None, **kwargs):
    dialog = OcrResultDialog(text, json.dumps(details) if details is not None else None, **kwargs)
    qtbot.addWidget(dialog)
    dialog.show()
    return dialog


def test_starts_unselected_and_cancel_never_requests_copy(qtbot):
    dialog = _dialog(qtbot)
    requested = []
    dialog.copy_requested.connect(requested.append)
    assert dialog.selected_indexes == set()
    assert dialog.selected_text == ''
    assert not dialog._copy.isEnabled()
    qtbot.mouseClick(dialog._cancel, Qt.MouseButton.LeftButton)
    assert requested == []
    assert dialog.result() == dialog.DialogCode.Rejected


def test_subset_uses_source_geometry_and_preserves_rare_characters(qtbot):
    details = [_detail('第二行', 0, 40), _detail('字', 51, 0), _detail('𠮷\U000e0100', 0, 0)]
    dialog = _dialog(qtbot, '𠮷\U000e0100字\n第二行', details)
    # The provider's raw ordering differs from reading order.
    assert [entry['text'] for entry in dialog._entries] == ['𠮷\U000e0100', '字', '第二行']
    dialog.select_indexes([0, 1])
    assert dialog.selected_text == '𠮷\U000e0100字'
    assert dialog._copy.isEnabled()
    requested = []
    dialog.copy_requested.connect(requested.append)
    qtbot.mouseClick(dialog._copy, Qt.MouseButton.LeftButton)
    assert requested == ['𠮷\U000e0100字']
    assert dialog.result() == dialog.DialogCode.Accepted


def test_all_preserves_exact_transcript_whitespace_and_newlines(qtbot):
    text = '  原文\n\n下一段  \n'
    dialog = _dialog(qtbot, text, [_detail('原文', 0, 0), _detail('下一段', 0, 70)])
    qtbot.mouseClick(dialog._select_all, Qt.MouseButton.LeftButton)
    assert dialog.selected_text == text
    qtbot.mouseClick(dialog._clear, Qt.MouseButton.LeftButton)
    assert dialog.selected_indexes == set()
    assert dialog.selected_text == ''
    assert not dialog._copy.isEnabled()


def test_selected_draft_can_be_edited_without_changing_original(qtbot):
    dialog = _dialog(qtbot)
    dialog.select_indexes([1])
    assert dialog.selected_text == '第二行'
    dialog._preview.setPlainText('  訂正𠮷\n保留換行  ')
    assert dialog.selected_text == '  訂正𠮷\n保留換行  '
    assert dialog._text == '第一行\n第二行'


def test_highlighted_substring_copies_only_that_range(qtbot):
    dialog = _dialog(qtbot, '甲乙\n丙丁')
    dialog.select_indexes(range(len(dialog._entries)))
    cursor = dialog._preview.textCursor()
    cursor.setPosition(1)
    cursor.setPosition(4, QTextCursor.MoveMode.KeepAnchor)
    dialog._preview.setTextCursor(cursor)
    assert dialog.selected_text == '乙\n丙'
    requested = []
    dialog.copy_requested.connect(requested.append)
    qtbot.mouseClick(dialog._copy, Qt.MouseButton.LeftButton)
    assert requested == ['乙\n丙']


def test_keyboard_checkboxes_work_without_original_image(qtbot):
    dialog = _dialog(qtbot, image_path='/missing/screenshot.png')
    dialog._list.setFocus()
    dialog._list.setCurrentRow(1)
    qtbot.keyClick(dialog._list, Qt.Key.Key_Space)
    assert dialog.selected_indexes == {1}
    assert dialog.selected_text == '第二行'


def test_malformed_geometry_and_json_keep_text_fallback_usable(qtbot):
    details = [{'text': '文字', 'box': [[float('nan'), 0]]}, {'text': None}]
    dialog = _dialog(qtbot, '文字', details)
    dialog.select_indexes([0])
    assert dialog.selected_text == '文字'
    assert dialog._entries[0]['polygon'] is None
    invalid = OcrResultDialog('第一行\n第二行', detail_json='not-json')
    qtbot.addWidget(invalid)
    invalid.select_indexes([1])
    assert invalid.selected_text == '第二行'


def test_empty_result_cannot_emit_a_copy_request(qtbot):
    dialog = _dialog(qtbot, ' \n ')
    requested = []
    dialog.copy_requested.connect(requested.append)
    assert dialog._list.count() == 0
    assert not dialog._select_all.isEnabled()
    assert not dialog._copy.isEnabled()
    dialog._request_copy()
    assert requested == []


def _image_dialog(qtbot, tmp_path):
    path = tmp_path / 'capture.png'
    image = QImage(400, 200, QImage.Format.Format_RGB32)
    image.fill(QColor('white'))
    assert image.save(str(path))
    details = [_detail('甲', 20, 20, 80, 30), _detail('乙', 220, 120, 80, 30)]
    return _dialog(qtbot, '甲\n乙', details, image_path=str(path))


def _at(dialog, x, y):
    rect = dialog._image.image_rect()
    return QPoint(round(rect.left() + x * rect.width() / 400),
                  round(rect.top() + y * rect.height() / 200))


def test_image_click_toggles_source_box_at_scaled_coordinates(qtbot, tmp_path):
    dialog = _image_dialog(qtbot, tmp_path)
    qtbot.mouseClick(dialog._image, Qt.MouseButton.LeftButton, pos=_at(dialog, 40, 35))
    assert dialog.selected_indexes == {0}
    qtbot.mouseClick(dialog._image, Qt.MouseButton.LeftButton, pos=_at(dialog, 40, 35))
    assert dialog.selected_indexes == set()


def test_image_drag_selects_region_and_ctrl_drag_adds(qtbot, tmp_path):
    dialog = _image_dialog(qtbot, tmp_path)
    qtbot.mousePress(dialog._image, Qt.MouseButton.LeftButton, pos=_at(dialog, 10, 10))
    qtbot.mouseMove(dialog._image, pos=_at(dialog, 110, 60))
    qtbot.mouseRelease(dialog._image, Qt.MouseButton.LeftButton, pos=_at(dialog, 110, 60))
    assert dialog.selected_indexes == {0}
    qtbot.mousePress(dialog._image, Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.ControlModifier, pos=_at(dialog, 210, 110))
    qtbot.mouseMove(dialog._image, pos=_at(dialog, 310, 160))
    qtbot.mouseRelease(dialog._image, Qt.MouseButton.LeftButton,
                       Qt.KeyboardModifier.ControlModifier, pos=_at(dialog, 310, 160))
    assert dialog.selected_indexes == {0, 1}
    assert dialog.selected_text == '甲\n乙'


def test_copy_shortcut_requires_nonempty_explicit_selection(qtbot):
    dialog = _dialog(qtbot)
    requested = []
    dialog.copy_requested.connect(requested.append)
    dialog.activateWindow()
    qtbot.waitUntil(dialog.isActiveWindow)
    dialog._preview.setFocus()
    qtbot.keyClick(dialog._preview, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    assert requested == []
    dialog.select_indexes([1])
    qtbot.keyClick(dialog._preview, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    assert requested == ['第二行']


def test_first_show_animation_finishes_and_does_not_replay(qtbot):
    from PySide6.QtCore import QAbstractAnimation
    dialog = _dialog(qtbot, animate=True)
    animation = dialog._entrance_animation
    assert animation is not None
    assert animation.duration() == 160
    qtbot.waitUntil(lambda: animation.state() == QAbstractAnimation.State.Stopped, timeout=1000)
    assert dialog.windowOpacity() == 1.0
    dialog.hide()
    dialog.show()
    assert dialog._entrance_animation is animation
    assert animation.state() == QAbstractAnimation.State.Stopped
    assert dialog.windowOpacity() == 1.0


def test_interrupted_entrance_restores_final_opacity(qtbot):
    from PySide6.QtCore import QAbstractAnimation
    dialog = _dialog(qtbot, animate=True)
    dialog.hide()
    assert dialog._entrance_animation.state() == QAbstractAnimation.State.Stopped
    assert dialog.windowOpacity() == 1.0


def test_native_reduced_motion_hint_disables_entrance(qtbot):
    from PySide6.QtWidgets import QProxyStyle, QStyle

    class ReducedMotionStyle(QProxyStyle):
        def styleHint(self, hint, option=None, widget=None, return_data=None):
            if hint == QStyle.StyleHint.SH_Widget_Animate:
                return 0
            return super().styleHint(hint, option, widget, return_data)

    dialog = OcrResultDialog('原文')
    qtbot.addWidget(dialog)
    style = ReducedMotionStyle()
    style.setParent(dialog)
    dialog.setStyle(style)
    dialog.show()
    assert dialog._entrance_animation is None
    assert dialog.windowOpacity() == 1.0


def test_minimum_size_keeps_copy_and_text_controls_inside_dialog(qtbot, tmp_path):
    dialog = _image_dialog(qtbot, tmp_path)
    dialog.resize(560, 400)
    qtbot.waitUntil(lambda: dialog._preview.minimumHeight() == 88)
    qtbot.waitUntil(lambda: dialog._preview.parentWidget().rect().contains(dialog._preview.geometry()))
    for widget in (dialog._copy, dialog._cancel, dialog._select_all, dialog._clear, dialog._preview):
        top_left = widget.mapTo(dialog, QPoint())
        bottom_right = widget.mapTo(dialog, widget.rect().bottomRight())
        assert dialog.rect().contains(top_left)
        assert dialog.rect().contains(bottom_right)
        assert widget.parentWidget().rect().contains(widget.geometry())
    assert dialog.size().width() == 560
    assert dialog.size().height() == 400
    assert dialog._preview.height() >= 88
