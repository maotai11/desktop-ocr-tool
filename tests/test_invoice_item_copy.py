"""QR copies share the actual single-result clipboard path and a persisted field."""
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QApplication, QWidget

from src.clipboard.writer import get_clipboard_epoch
from src.core.config import ConfigManager
from src.core.constants import CUSTOM_MIME_TYPE
from src.ui.invoice_items_dialog import InvoiceItemsDialog
from src.ui.settings_dialog import SettingsDialog

ROWS = (('茶,葉', '0.50', '12.00'), ('茶,葉', '2', '3.50'))


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv('DESKTOP_OCR_HOME', str(tmp_path))
    return ConfigManager()


def show_items(qtbot, cfg, rows=ROWS, parent=None):
    dialog = InvoiceItemsDialog(parent, cfg=cfg)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.set_result(SimpleNamespace(rows=rows, summary='測試品項'), '')
    return dialog


@pytest.mark.parametrize('field,expected', [
    ('all', '品名\t數量\t單價\n茶,葉\t0.50\t12.00\n茶,葉\t2\t3.50'),
    ('name', '茶,葉\n茶,葉'),
    ('quantity', '0.50\n2'),
    ('unit_price', '12.00\n3.50'),
])
def test_explicit_copy_persists_field_and_increments_manual_intent_once(qtbot, cfg, field, expected):
    clipboard = QApplication.clipboard()
    clipboard.setText('先前文字')
    epoch = get_clipboard_epoch()
    dialog = show_items(qtbot, cfg)
    assert dialog.copy_field.currentData() == 'all'
    dialog.copy_field.setCurrentIndex(dialog.copy_field.findData(field))
    assert clipboard.text() == '先前文字' and get_clipboard_epoch() == epoch
    qtbot.mouseClick(dialog.copy_button, Qt.MouseButton.LeftButton)
    assert clipboard.text() == expected and clipboard.mimeData().hasFormat(CUSTOM_MIME_TYPE)
    assert get_clipboard_epoch() == epoch + 1
    dialog.reject()
    reopened = show_items(qtbot, ConfigManager())
    assert reopened.copy_field.currentData() == field
    assert get_clipboard_epoch() == epoch + 1


def test_live_cleanup_applies_to_row_separators_and_keeps_source_data(qtbot, cfg):
    dialog = show_items(qtbot, cfg)
    cfg.update_section('clipboard', {
        'copy_remove_newlines': True, 'copy_cleanup_mode_newlines': 'replace',
        'copy_replace_newlines': '+', 'copy_remove_commas': True,
    })
    dialog.copy_button.click()
    assert QApplication.clipboard().text() == '品名\t數量\t單價+茶葉\t0.50\t12.00+茶葉\t2\t3.50'
    cfg.update_section('clipboard', {'copy_remove_punctuation': True})
    dialog.copy_button.click()
    assert QApplication.clipboard().text() == '品名數量單價+茶葉0501200+茶葉2350'
    dialog.copy_field.setCurrentIndex(dialog.copy_field.findData('name'))
    dialog.copy_button.click()
    assert QApplication.clipboard().text() == '茶葉+茶葉'
    assert dialog.table.item(0, 0).text() == '茶,葉'
    assert dialog.table.item(0, 2).text() == '12.00'


def test_empty_cleaned_field_retains_clipboard_and_resets_acknowledgement(qtbot, cfg):
    dialog = show_items(qtbot, cfg, ((',,', '1', '5'),))
    dialog.copy_field.setCurrentIndex(dialog.copy_field.findData('name'))
    dialog.copy_button.click()
    assert dialog.copy_button.text() == '已複製品名'
    cfg.set('clipboard', 'copy_remove_commas', True)
    clipboard = QApplication.clipboard()
    clipboard.setText('保留剪貼簿')
    epoch = get_clipboard_epoch()
    dialog.copy_button.click()
    assert clipboard.text() == '保留剪貼簿' and get_clipboard_epoch() == epoch
    assert dialog.copy_notice.isVisible() and dialog.copy_button.text() == '複製品名'


def test_preference_write_failure_rolls_back_visible_field(qtbot, cfg, monkeypatch):
    dialog = show_items(qtbot, cfg)

    def fail_save():
        raise PermissionError('read only')

    monkeypatch.setattr(cfg, 'save', fail_save)
    epoch = get_clipboard_epoch()
    dialog.copy_field.setCurrentIndex(dialog.copy_field.findData('quantity'))
    assert dialog.copy_field.currentData() == ConfigManager().get('clipboard', 'invoice_item_copy_field') == 'all'
    assert dialog.copy_notice.isVisible() and dialog.copy_button.text() == '複製明細'
    assert get_clipboard_epoch() == epoch


def test_full_settings_cancel_save_and_open_preview_share_the_field(qtbot, cfg):
    class Owner(QWidget):
        settings_saved = Signal()

    owner = Owner()
    qtbot.addWidget(owner)
    preview = show_items(qtbot, cfg, parent=owner)
    for accept in (False, True):
        settings = SettingsDialog(cfg)
        qtbot.addWidget(settings)
        settings._invoice_copy_field.setCurrentIndex(settings._invoice_copy_field.findData('name'))
        if accept:
            settings._save()
            owner.settings_saved.emit()
        else:
            settings.reject()
        field = 'name' if accept else 'all'
        assert cfg.get('clipboard', 'invoice_item_copy_field') == field
        assert preview.copy_field.currentData() == field


@pytest.mark.parametrize('invalid', ['other', None, ['name']])
def test_unknown_saved_value_uses_full_details_without_rewriting(qtbot, cfg, invalid):
    cfg.set('clipboard', 'invoice_item_copy_field', invalid)
    dialog = show_items(qtbot, cfg)
    assert dialog.copy_field.currentData() == 'all'
    assert cfg.get('clipboard', 'invoice_item_copy_field') == invalid
