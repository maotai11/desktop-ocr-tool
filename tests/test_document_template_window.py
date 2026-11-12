"""Native document-template workflow, geometry and explicit output boundaries."""
import os
from types import SimpleNamespace

import pytest
from PIL import Image
from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import QMessageBox

from src.clipboard.writer import copy_text, get_clipboard_epoch
from src.documents.template import Region
from src.ui.document_template_window import DocumentTemplateWindow


class Config:
    def __init__(self):
        self.values = {('documents', 'max_file_size_mb'): 100}

    def get(self, *keys, default=None):
        return self.values.get(keys, default)

    def set(self, *keys_and_value):
        *keys, value = keys_and_value
        self.values[tuple(keys)] = value


@pytest.fixture
def window(qtbot):
    widget = DocumentTemplateWindow(Config())
    qtbot.addWidget(widget)
    widget.show()
    return widget


def imported(window, count=2, source=None):
    pages = [SimpleNamespace(label=f'表格.pdf · 第 {index+1} 頁', source=source)
             for index in range(count)]
    window.set_document_pages(pages)
    preview = Image.new('RGB', (600, 800), 'white')
    window.set_page_preview(0, preview)
    return pages


def ready(window, count=2, source=None):
    imported(window, count, source)
    window._set_region(Region(.1, .15, .4, .1))
    return window


def test_import_requests_local_files_and_default_limit_can_change(window):
    requests = []
    window.import_requested.connect(lambda paths, limit: requests.append((paths, limit)))
    window.import_paths(['/tmp/名稱.pdf', '/tmp/第二張.png'])
    assert requests == [(['/tmp/名稱.pdf', '/tmp/第二張.png'], 100)]
    window._file_limit.setValue(25)
    assert window._cfg.get('documents', 'max_file_size_mb') == 25
    window.import_paths(['/tmp/其他.pdf'])
    assert requests[-1][1] == 25


def test_first_page_region_inherits_and_later_page_can_override(window):
    imported(window, 3)
    base = Region(.1, .2, .3, .15)
    window._set_region(base)
    assert [window._layout.region_for('辨識1', page) for page in range(3)] == [base] * 3
    window._page_combo.setCurrentIndex(1)
    assert window._scope.currentData() == 'page'
    window.set_page_preview(1, Image.new('RGB', (1200, 1600), 'white'))
    override = Region(.2, .3, .4, .1)
    window._set_region(override)
    assert window._layout.region_for('辨識1', 0) == base
    assert window._layout.region_for('辨識1', 1) == override
    assert window._layout.region_for('辨識1', 2) == base
    window._reset_override.click()
    assert window._layout.region_for('辨識1', 1) == base


def test_keyboard_position_controls_define_region_without_drag(window, qtbot):
    imported(window, 1)
    for control, value in zip(window._coordinates, (15, 25, 30, 10)):
        control.setValue(value)
    qtbot.mouseClick(window._apply_position, Qt.MouseButton.LeftButton)
    assert window._layout.region_for('辨識1', 0) == Region(.15, .25, .3, .1)
    assert window._recognize_button.isEnabled()


def test_rename_updates_mixed_brace_template_and_existing_results(window):
    ready(window)
    window._template.setPlainText('姓名：｛辨識1} / {辨識1｝')
    window.set_recognition_results([{'辨識1': '林𠮷'}, {'辨識1': '王乙'}])
    window._name_edit.setText(' 姓名 ')
    window._rename_button.click()
    assert window._layout.field_names == ('姓名',)
    assert '辨識1' not in window._template.toPlainText()
    assert window._output.toPlainText() == '姓名：林𠮷 / 林𠮷\n\n姓名：王乙 / 王乙'
    assert window._results[0] == {'姓名': '林𠮷'}


def test_unknown_template_and_missing_region_block_recognition(window):
    requests = []
    window.recognize_requested.connect(requests.append)
    imported(window)
    assert not window._recognize_button.isEnabled()
    window._set_region(Region(.1, .1, .3, .2))
    window._template.setPlainText('{不存在}')
    assert not window._recognize_button.isEnabled()
    window._start_recognition()
    assert requests == []
    window._template.setPlainText('{辨識1}')
    assert window._recognize_button.isEnabled()
    window._recognize_button.click()
    assert len(requests) == 1
    snapshot = requests[0]
    window._layout.set_region('辨識1', Region(.4, .4, .2, .2))
    assert snapshot['layout'].region_for('辨識1', 0) == Region(.1, .1, .3, .2)


def test_results_editing_builds_exact_template_and_preserves_ocr_braces(window):
    ready(window)
    window._template.setPlainText('文字｛辨識1}文字測試\n結束')
    window.set_recognition_results([{'辨識1': '甲{未替換}'}, {'辨識1': '乙'}])
    assert window._output.toPlainText() == '文字甲{未替換}文字測試\n結束\n\n文字乙文字測試\n結束'
    window._results_table.item(1, 1).setText('手動更正𠮷\n第二行')
    assert window._results[1]['辨識1'] == '手動更正𠮷\n第二行'
    assert '文字手動更正𠮷\n第二行文字測試' in window._output.toPlainText()


def test_template_copy_remains_literal_when_general_cleanup_replaces_newlines(window, qapp, tmp_path):
    ready(window, 1)
    window._template.setPlainText('{辨識1}\n結束')
    window.set_recognition_results([{'辨識1': '甲，乙'}])
    original = window._output.toPlainText()
    window._cfg.set('clipboard', 'copy_remove_newlines', True)
    window._cfg.set('clipboard', 'copy_cleanup_mode_newlines', 'replace')
    window._cfg.set('clipboard', 'copy_replace_newlines', ' / ')
    window._copy_results()
    assert qapp.clipboard().text() == original
    assert window._output.toPlainText() == original
    output = tmp_path / 'result.txt'
    window.export_to_path(str(output))
    assert output.read_text(encoding='utf-8-sig') == original


def test_failed_fields_block_output_until_explicit_correction(window, qapp):
    ready(window)
    copy_text('先前內容')
    window.set_recognition_results([{'辨識1': '甲'}, {'辨識1': ''}],
                                   [{'辨識1': 'done'}, {'辨識1': 'failed'}])
    assert not window._copy_button.isEnabled()
    assert not window._export_button.isEnabled()
    window._copy_results()
    assert qapp.clipboard().text() == '先前內容'
    window._results_table.item(1, 1).setText('補上乙')
    assert window._copy_button.isEnabled()
    assert window._export_button.isEnabled()


def test_multiline_keyboard_correction_and_explicit_empty_confirmation(window, qtbot, monkeypatch):
    from src.ui.document_template_window import QInputDialog
    ready(window, 1)
    window._template.setPlainText('地址：{辨識1}')
    window.set_recognition_results([{'辨識1': ''}], [{'辨識1': 'failed'}])
    monkeypatch.setattr(QInputDialog, 'getMultiLineText', lambda *args: ('第一行\n第二行', True))
    window._results_table.setCurrentCell(0, 1)
    qtbot.keyClick(window._results_table, Qt.Key.Key_F2)
    assert window._output.toPlainText() == '地址：第一行\n第二行'
    assert window._copy_button.isEnabled()
    window.set_recognition_results([{'辨識1': ''}], [{'辨識1': 'failed'}])
    monkeypatch.setattr(QInputDialog, 'getMultiLineText', lambda *args: ('', True))
    window._results_table.setCurrentCell(0, 1)
    qtbot.keyClick(window._results_table, Qt.Key.Key_Return)
    assert window._statuses[0]['辨識1'] == 'edited'
    assert window._copy_button.isEnabled()


def test_copy_and_utf8_txt_match_preview_without_cleanup(window, qapp, tmp_path):
    ready(window)
    window._template.setPlainText('金額：{辨識1}\n保留，逗號')
    window.set_recognition_results([{'辨識1': '1,234.5'}, {'辨識1': '6,789.0'}])
    expected = window._output.toPlainText()
    epoch = get_clipboard_epoch()
    window._copy_button.click()
    assert qapp.clipboard().text() == expected
    assert get_clipboard_epoch() == epoch + 1
    destination = tmp_path / '合併 結果.txt'
    assert window.export_to_path(destination)
    assert destination.read_bytes().startswith(b'\xef\xbb\xbf')
    assert destination.read_text(encoding='utf-8-sig') == expected


def test_cancelled_and_missing_pages_do_not_silently_export(window, qapp):
    ready(window, 3)
    copy_text('保留')
    window.set_recognition_results([{'辨識1': '甲'}], [{'辨識1': 'done'}], True)
    assert not window._export_button.isEnabled()
    assert '停止' in window._status.text()
    assert qapp.clipboard().text() == '保留'


def test_export_rejects_original_and_aliases_without_modifying_input(window, tmp_path):
    original = tmp_path / '原始資料.pdf'
    original.write_bytes(b'original document bytes')
    ready(window, 1, SimpleNamespace(path=original))
    window.set_recognition_results([{'辨識1': '完成'}])
    with pytest.raises(ValueError, match='原始文件'):
        window.export_to_path(original)
    assert original.read_bytes() == b'original document bytes'
    alias = tmp_path / '別名.txt'
    try:
        os.link(original, alias)
    except OSError:
        pytest.skip('Hard links unavailable in this filesystem')
    with pytest.raises(ValueError, match='原始文件'):
        window.export_to_path(alias)
    assert original.read_bytes() == alias.read_bytes() == b'original document bytes'


def test_export_failure_keeps_previous_txt_and_removes_partial(window, tmp_path, monkeypatch):
    ready(window, 1)
    window.set_recognition_results([{'辨識1': '新結果'}])
    destination = tmp_path / '結果.txt'
    destination.write_text('舊結果', encoding='utf-8')
    def fail_replace(*_):
        raise PermissionError('synthetic write refusal')
    monkeypatch.setattr('src.ui.document_template_window.os.replace', fail_replace)
    with pytest.raises(PermissionError):
        window.export_to_path(destination)
    assert destination.read_text(encoding='utf-8') == '舊結果'
    assert not list(tmp_path.glob('.inkpick-txt-*.tmp'))


def test_old_page_preview_is_cleared_and_stale_image_is_closed(window):
    ready(window)
    assert window._canvas.image_size == (600, 800)
    before = window._layout.region_for('辨識1', 1)
    window._page_combo.setCurrentIndex(1)
    assert window._canvas.image_size == (0, 0)
    assert not window._canvas.isEnabled()
    window._set_region(Region(.3, .3, .2, .2))
    assert window._layout.region_for('辨識1', 1) == before
    stale = Image.new('RGB', (10, 10))
    window.set_page_preview(0, stale)
    with pytest.raises(ValueError):
        stale.getpixel((0, 0))
    window.set_page_preview(1, Image.new('RGB', (300, 400), 'white'))
    assert window._canvas.isEnabled()
    assert window._canvas.image_size == (300, 400)


def test_real_drag_maps_to_source_coordinates_after_zoom(window, qtbot):
    imported(window, 1)
    canvas = window._canvas
    canvas._auto_fit = False
    canvas.scale(1.4, 1.4)
    start = canvas.mapFromScene(QPointF(100, 200))
    end = canvas.mapFromScene(QPointF(300, 400))
    qtbot.mousePress(canvas.viewport(), Qt.MouseButton.LeftButton, pos=start)
    qtbot.mouseMove(canvas.viewport(), pos=end)
    qtbot.mouseRelease(canvas.viewport(), Qt.MouseButton.LeftButton, pos=end)
    region = window._layout.region_for('辨識1', 0)
    assert region.x == pytest.approx(100 / 600, abs=.01)
    assert region.y == pytest.approx(200 / 800, abs=.01)
    assert region.width == pytest.approx(200 / 600, abs=.01)
    assert region.height == pytest.approx(200 / 800, abs=.01)


def test_clear_current_field_does_not_fall_back_and_reset_restores_inheritance(window):
    ready(window, 3)
    base = window._layout.region_for('辨識1', 0)
    assert not window._reset_override.isEnabled()
    window._page_combo.setCurrentIndex(1)
    window.set_page_preview(1, Image.new('RGB', (600, 800), 'white'))
    window.set_recognition_results([{'辨識1': '甲'}, {'辨識1': '乙'}, {'辨識1': '丙'}])
    window._clear_region_button.click()
    assert window._layout.region_for('辨識1', 1) is None
    assert window._layout.has_override('辨識1', 1)
    assert window._layout.region_for('辨識1', 0) == base
    assert window._layout.region_for('辨識1', 2) == base
    assert '此頁已清除' in window._field_list.currentItem().text()
    assert not window._clear_region_button.isEnabled()
    assert window._reset_override.isEnabled()
    assert not window._recognize_button.isEnabled()
    assert not window._copy_button.isEnabled()
    assert window._results == []
    assert window._canvas._overlays == []
    assert '第 2 頁' in window._validation.text()
    window._reset_override.click()
    assert window._layout.region_for('辨識1', 1) == base
    assert not window._reset_override.isEnabled()
    assert window._recognize_button.isEnabled()


def test_clear_all_pages_removes_selected_field_overrides_and_keeps_other_fields(window, qapp):
    ready(window, 3)
    base = window._layout.region_for('辨識1', 0)
    window._layout.add_field('金額', base)
    window._layout.set_region('辨識1', Region(.3, .3, .2, .2), 2)
    window._layout.set_region('金額', Region(.4, .4, .2, .2), 2)
    window._refresh_fields('辨識1')
    copy_text('保留剪貼簿')
    window._clear_region_button.click()
    assert all(window._layout.region_for('辨識1', page) is None for page in range(3))
    assert window._layout.overrides == {2: {'金額': Region(.4, .4, .2, .2)}}
    assert window._layout.region_for('金額', 0) == base
    assert window._layout.field_names == ('辨識1', '金額')
    assert window._template.toPlainText() == '{辨識1}'
    assert not window._clear_region_button.isEnabled()
    assert qapp.clipboard().text() == '保留剪貼簿'


def test_visible_zoom_controls_keyboard_bounds_and_fit_resize(window, qtbot):
    imported(window, 1)
    window._actual_size_button.click()
    assert window._canvas.zoom_percent == pytest.approx(100)
    assert window._zoom_label.text() == '100%'
    window._zoom_in.click()
    assert window._canvas.zoom_percent == pytest.approx(120)
    window._zoom_out.click()
    assert window._canvas.zoom_percent == pytest.approx(100)
    window.activateWindow()
    window._canvas.setFocus()
    qtbot.keyClick(window._canvas, Qt.Key.Key_Equal, Qt.KeyboardModifier.ControlModifier)
    assert window._canvas.zoom_percent == pytest.approx(120)
    qtbot.keyClick(window._canvas, Qt.Key.Key_Minus, Qt.KeyboardModifier.ControlModifier)
    assert window._canvas.zoom_percent == pytest.approx(100)
    window.resize(1000, 700)
    qtbot.waitUntil(lambda: window.width() == 1000)
    assert window._canvas.zoom_percent == pytest.approx(100)
    window._canvas.set_zoom(99999)
    assert window._canvas.zoom_percent == pytest.approx(1200)
    assert not window._zoom_in.isEnabled()
    window._canvas.set_zoom(.01)
    assert window._canvas.zoom_percent == pytest.approx(5)
    assert not window._zoom_out.isEnabled()
    qtbot.keyClick(window._canvas, Qt.Key.Key_0, Qt.KeyboardModifier.ControlModifier)
    assert window._canvas._auto_fit
    assert 5 < window._canvas.zoom_percent < 100


def test_zoomed_drag_maps_crop_and_pan_never_selects_a_region(window, qtbot):
    imported(window, 1)
    canvas = window._canvas
    canvas.set_zoom(200)
    canvas.centerOn(250, 325)
    start = canvas.mapFromScene(QPointF(200, 300))
    end = canvas.mapFromScene(QPointF(300, 350))
    qtbot.mousePress(canvas.viewport(), Qt.MouseButton.LeftButton, pos=start)
    qtbot.mouseMove(canvas.viewport(), pos=end)
    qtbot.mouseRelease(canvas.viewport(), Qt.MouseButton.LeftButton, pos=end)
    region = window._layout.region_for('辨識1', 0)
    bounds = region.pixel_bounds(1200, 1600)
    assert bounds == pytest.approx((400, 600, 600, 700), abs=2)
    previous_scroll = canvas.verticalScrollBar().value()
    midpoint = canvas.viewport().rect().center()
    qtbot.mousePress(canvas.viewport(), Qt.MouseButton.MiddleButton, pos=midpoint)
    qtbot.mouseMove(canvas.viewport(), pos=midpoint + QPointF(0, -40).toPoint())
    qtbot.mouseRelease(canvas.viewport(), Qt.MouseButton.MiddleButton, pos=midpoint + QPointF(0, -40).toPoint())
    assert canvas.verticalScrollBar().value() > previous_scroll
    assert window._layout.region_for('辨識1', 0) == region
    window._pan_button.click()
    qtbot.mousePress(canvas.viewport(), Qt.MouseButton.LeftButton, pos=start)
    qtbot.mouseMove(canvas.viewport(), pos=end)
    qtbot.mouseRelease(canvas.viewport(), Qt.MouseButton.LeftButton, pos=end)
    assert window._layout.region_for('辨識1', 0) == region


def test_manual_zoom_survives_page_change_but_pending_preview_disables_controls(window):
    ready(window)
    base = window._layout.region_for('辨識1', 0)
    window._canvas.set_zoom(150)
    window._page_combo.setCurrentIndex(1)
    assert window._zoom_label.text() == '—'
    assert not any(control.isEnabled() for control in window._zoom_controls)
    window.set_page_preview(1, Image.new('RGB', (300, 400), 'white'))
    assert window._canvas.zoom_percent == pytest.approx(150)
    assert window._layout.region_for('辨識1', 1) == base
    assert base.pixel_bounds(300, 400) == (30, 60, 150, 100)


def test_clear_documents_confirmation_cancel_and_accept_keep_sources_and_clipboard(
        window, qapp, tmp_path, monkeypatch):
    source = tmp_path / '原始.png'
    source.write_bytes(b'original file bytes')
    ready(window, source=SimpleNamespace(path=source))
    window._template.setPlainText('姓名：{辨識1}')
    window.set_recognition_results([{'辨識1': '甲'}, {'辨識1': '乙'}])
    copy_text('剪貼簿保留')
    cleared = []
    window.clear_requested.connect(lambda: cleared.append(True))
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.StandardButton.No)
    window._clear_documents_button.click()
    assert len(window._pages) == 2
    assert window._output.toPlainText() == '姓名：甲\n\n姓名：乙'
    assert cleared == []
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.StandardButton.Yes)
    window.set_busy(True, '辨識中')
    assert window._clear_documents_button.isEnabled()
    window._clear_documents_button.click()
    assert cleared == [True]
    assert window._pages == []
    assert window._current_page == -1
    assert window._page_combo.count() == 0
    assert window._canvas.image_size == (0, 0)
    assert window._results == []
    assert window._output.toPlainText() == ''
    assert window._layout.field_names == ('辨識1',)
    assert window._layout.fields[0].base_region is None
    assert window._layout.overrides == {}
    assert window._template.toPlainText() == '姓名：{辨識1}'
    assert not window._clear_documents_button.isEnabled()
    assert not window._busy
    assert source.read_bytes() == b'original file bytes'
    assert qapp.clipboard().text() == '剪貼簿保留'
    window.set_document_pages(())  # Controller's silent synchronization callback.
    assert cleared == [True]
    imported(window, 1)
    assert window._layout.region_for('辨識1', 0) is None
    assert not window._recognize_button.isEnabled()


def test_clear_busy_import_with_no_loaded_page_can_cancel_and_reset(window, monkeypatch):
    cleared = []
    window.clear_requested.connect(lambda: cleared.append(True))
    window.set_busy(True, '匯入中')
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.StandardButton.Yes)
    assert window._clear_documents_button.isEnabled()
    window._clear_documents_button.click()
    assert cleared == [True]
    assert not window._busy
    assert window._import_button.isEnabled()


def test_new_documents_preserve_base_template_but_reset_overrides(window):
    ready(window)
    base = window._layout.region_for('辨識1', 0)
    window._layout.set_region('辨識1', Region(.3, .3, .2, .2), page_index=1)
    window._template.setPlainText('姓名：{辨識1}')
    window.set_recognition_results([{'辨識1': '甲'}, {'辨識1': '乙'}])
    window.set_document_pages([{'label': '新的圖片.png'}])
    assert window._layout.region_for('辨識1', 0) == base
    assert window._layout.overrides == {}
    assert window._template.toPlainText() == '姓名：{辨識1}'
    assert window._output.toPlainText() == ''


def test_busy_close_requests_cancellation_and_never_copies(window, qapp):
    ready(window)
    copy_text('保留')
    cancelled = []
    window.cancel_requested.connect(lambda: cancelled.append(True))
    window.set_busy(True, '辨識中')
    assert not window._import_button.isEnabled()
    assert not window._template.isEnabled()
    assert window._cancel_button.isEnabled()
    window.close()
    assert cancelled == [True]
    assert not window.isVisible()
    assert qapp.clipboard().text() == '保留'
