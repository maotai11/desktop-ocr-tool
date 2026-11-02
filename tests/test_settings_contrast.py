"""Exercise resolved Qt palettes and painted text under native host palettes."""
import copy

import pytest
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPalette
from PySide6.QtWidgets import QComboBox, QStyle, QStyleFactory, QStyleOptionViewItem, QTabWidget

from src.core.config import DEFAULT_SETTINGS
from src.ui.settings_dialog import SettingsDialog
from src.ui.theme import _ACCENT, _ACCENT_T, _BG_HOVER, _BG_RAISE, _TEXT_PRI, _TEXT_SEC


class Config:
    def __init__(self):
        self.data = copy.deepcopy(DEFAULT_SETTINGS)

    def get(self, *keys, default=None):
        value = self.data
        for key in keys:
            if not isinstance(value, dict) or key not in value:
                return default
            value = value[key]
        return value


@pytest.fixture(params=QStyleFactory.keys())
def styled_app(qapp, request):
    original_style = qapp.style().objectName()
    original_palette = QPalette(qapp.palette())
    qapp.setStyle(request.param)
    yield qapp
    qapp.setStyle(original_style)
    qapp.setPalette(original_palette)


def host_palette(app, color):
    palette = QPalette(app.palette())
    # Reproduce the inherited same-color focus pair; cover light and dark hosts.
    for role in (QPalette.ColorRole.Window, QPalette.ColorRole.Base, QPalette.ColorRole.Button,
                 QPalette.ColorRole.Text, QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText,
                 QPalette.ColorRole.Highlight, QPalette.ColorRole.HighlightedText):
        palette.setColor(role, QColor(color))
    app.setPalette(palette)


def contrast(a, b):
    def luminance(color):
        values = [color.redF(), color.greenF(), color.blueF()]
        linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in values]
        return sum(v * weight for v, weight in zip(linear, (.2126, .7152, .0722)))
    light, dark = sorted((luminance(a), luminance(b)), reverse=True)
    return (light + .05) / (dark + .05)


def assert_painted_pair(image, rect, background, foreground):
    """Require real background and readable glyph pixels, allowing font antialiasing."""
    scale = image.devicePixelRatio()
    rect = QRect(round(rect.x() * scale), round(rect.y() * scale),
                 round(rect.width() * scale), round(rect.height() * scale))
    rect = rect.intersected(image.rect())
    background, foreground = QColor(background), QColor(foreground)
    backgrounds = glyphs = 0
    for y in range(rect.top(), rect.bottom() + 1):
        for x in range(rect.left(), rect.right() + 1):
            pixel = image.pixelColor(x, y)
            if pixel == background:
                backgrounds += 1
            distance = max(abs(pixel.red() - foreground.red()), abs(pixel.green() - foreground.green()),
                           abs(pixel.blue() - foreground.blue()))
            if distance <= 30 and contrast(pixel, background) >= 4.5:
                glyphs += 1
    assert backgrounds > rect.width(), 'No painted surface in the expected background color'
    assert glyphs > 3, 'No readable text pixels painted over the surface'


@pytest.mark.parametrize('host_color', ['#ffffff', '#101010'])
def test_every_settings_combo_has_readable_resolved_palette(styled_app, qtbot, host_color):
    host_palette(styled_app, host_color)
    dialog = SettingsDialog(Config())
    qtbot.addWidget(dialog)
    dialog.show()
    styled_app.processEvents()
    combos = dialog.findChildren(QComboBox)
    assert {dialog._model_profile, dialog._ocr_copy_mode, dialog._cb_theme} <= set(combos)
    for combo in combos:
        for widget in (combo, combo.view(), combo.view().viewport()):
            widget.ensurePolished()
            palette = widget.palette()
            for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive,
                          QPalette.ColorGroup.Disabled):
                for foreground, background in ((QPalette.ColorRole.Text, QPalette.ColorRole.Base),
                                               (QPalette.ColorRole.WindowText, QPalette.ColorRole.Window),
                                               (QPalette.ColorRole.ButtonText, QPalette.ColorRole.Button),
                                               (QPalette.ColorRole.HighlightedText, QPalette.ColorRole.Highlight)):
                    fg, bg = palette.color(group, foreground), palette.color(group, background)
                    assert fg.alpha() == bg.alpha() == 255
                    assert contrast(fg, bg) >= 4.5, (widget, group, foreground, fg.name(), bg.name())


def test_closed_and_popup_text_is_actually_painted_with_contrast(styled_app, qtbot):
    host_palette(styled_app, '#ffffff')
    dialog = SettingsDialog(Config())
    qtbot.addWidget(dialog)
    dialog.findChild(QTabWidget).setCurrentIndex(2)
    dialog.show()
    combo = dialog._model_profile
    combo.setItemText(0, 'Read normal')
    combo.setItemText(1, 'Read selected')
    combo.addItem('Read disabled')
    combo.model().item(2).setEnabled(False)
    combo.setFocus()
    styled_app.processEvents()
    assert_painted_pair(combo.grab().toImage(), combo.rect().adjusted(4, 2, -24, -2), _BG_RAISE, _TEXT_PRI)
    combo.showPopup()
    try:
        view = combo.view()
        view.setCurrentIndex(view.model().index(1, 0))
        styled_app.processEvents()
        painted = view.viewport().grab().toImage()
        for row, background, foreground in ((0, _BG_RAISE, _TEXT_PRI), (1, _ACCENT, _ACCENT_T),
                                            (2, _BG_RAISE, _TEXT_SEC)):
            rect = view.visualRect(view.model().index(row, 0)).adjusted(3, 1, -3, -1)
            assert_painted_pair(painted, rect, background, foreground)
    finally:
        combo.hidePopup()
    combo.setEnabled(False)
    styled_app.processEvents()
    assert_painted_pair(combo.grab().toImage(), combo.rect().adjusted(4, 2, -24, -2), _BG_RAISE, _TEXT_SEC)


def test_popup_hover_and_selected_hover_paint_readable_text(styled_app, qtbot):
    host_palette(styled_app, '#ffffff')
    dialog = SettingsDialog(Config())
    qtbot.addWidget(dialog)
    combo = dialog._model_profile
    combo.setItemText(0, 'Readable hover')
    view = combo.view()
    view.ensurePolished()
    for selected, background, foreground in ((False, _BG_HOVER, _TEXT_PRI), (True, _ACCENT, _ACCENT_T)):
        option = QStyleOptionViewItem()
        option.initFrom(view)
        option.widget = view
        option.rect = QRect(0, 0, 240, 32)
        option.state |= QStyle.StateFlag.State_Enabled | QStyle.StateFlag.State_MouseOver
        if selected:
            option.state |= QStyle.StateFlag.State_Selected
        else:
            option.state &= ~QStyle.StateFlag.State_Selected
        image = QImage(option.rect.size(), QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.magenta)
        painter = QPainter(image)
        try:
            view.itemDelegate().paint(painter, option, view.model().index(0, 0))
        finally:
            painter.end()
        assert_painted_pair(image, image.rect().adjusted(3, 1, -3, -1), background, foreground)


def test_themed_popup_keeps_keyboard_selection_and_cancel(styled_app, qtbot):
    dialog = SettingsDialog(Config())
    qtbot.addWidget(dialog)
    dialog.findChild(QTabWidget).setCurrentIndex(2)
    dialog.show()
    combo = dialog._model_profile
    combo.setCurrentIndex(0)
    combo.setFocus()
    combo.showPopup()
    qtbot.keyClick(combo.view(), Qt.Key.Key_Down)
    qtbot.keyClick(combo.view(), Qt.Key.Key_Return)
    assert combo.currentData() == 'v6-medium'
    combo.showPopup()
    qtbot.keyClick(combo.view(), Qt.Key.Key_Up)
    qtbot.keyClick(combo.view(), Qt.Key.Key_Escape)
    assert combo.currentData() == 'v6-medium'
