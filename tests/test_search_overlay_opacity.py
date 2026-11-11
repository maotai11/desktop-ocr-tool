"""Only result-label backgrounds fade; values and capture ownership do not."""
import pytest
from PySide6.QtCore import QRectF, Qt

from src.ui.search_highlight_overlay import SearchHighlightOverlay


@pytest.fixture
def overlay(qtbot, qapp):
    widget = SearchHighlightOverlay()
    qtbot.addWidget(widget)
    scale = qapp.primaryScreen().devicePixelRatio()
    assert widget.set_marks([{'a_text': '1250', 'status': 'matched', 'similarity': 1.0,
                              'boxes': [[[310 * scale, 50 * scale], [420 * scale, 50 * scale],
                                         [420 * scale, 80 * scale], [310 * scale, 80 * scale]]],
                              'bounds': (300 * scale, 40 * scale, 300 * scale, 160 * scale),
                              'anchor': (30 * scale, 40 * scale, 240 * scale, 24 * scale)}])
    return widget


def _pixel(image, x, y):
    scale = image.devicePixelRatio()
    return image.pixelColor(round(x * scale), round(y * scale))


def _opaque_value_pixels(image, rectangle):
    scale = image.devicePixelRatio()
    # Solid green glyph interiors remain opaque at every background setting.
    return sum(image.pixelColor(x, y).alpha() == 255
               and image.pixelColor(x, y).name() == '#087443'
               for y in range(round(rectangle.top() * scale), round(rectangle.bottom() * scale))
               for x in range(round(rectangle.left() * scale), round(rectangle.right() * scale)))


def test_background_opacity_renders_only_label_backing_and_preserves_values(overlay):
    rectangle = QRectF(*overlay.callouts[0]['rect'])
    glyph_counts = []
    for percent, expected_alpha in [(0, 0), (50, 128), (100, 255), (0, 0)]:
        assert overlay.set_background_opacity(percent)
        image = overlay.grab().toImage()
        blank = _pixel(image, rectangle.right() - 8, rectangle.center().y())
        assert blank.alpha() == expected_alpha
        if percent:
            assert blank.red() == blank.green() == blank.blue() == 255
        assert _pixel(image, rectangle.left() - 5, rectangle.center().y()).alpha() == 0
        glyph_counts.append(_opaque_value_pixels(image, rectangle))
        assert overlay.windowOpacity() == 1.0
        assert overlay.windowFlags() & Qt.WindowType.WindowTransparentForInput
    assert min(glyph_counts) > 0
    assert len(set(glyph_counts)) == 1


@pytest.mark.parametrize('percent, expected', [(-5, 0), (125, 100), (23.5, 23.5)])
def test_background_opacity_clamps_without_changing_result_or_geometry(overlay, percent, expected):
    marks, callouts = overlay.marks, overlay.callouts
    assert overlay.set_background_opacity(percent)
    assert overlay.background_opacity == expected
    assert overlay.marks == marks and overlay.callouts == callouts


@pytest.mark.parametrize('invalid', [None, '50', True, float('nan'), float('inf'), -float('inf')])
def test_invalid_background_opacity_preserves_previous_setting(overlay, invalid):
    assert overlay.set_background_opacity(40)
    assert not overlay.set_background_opacity(invalid)
    assert overlay.background_opacity == 40


def test_opacity_change_during_capture_or_after_clear_cannot_reshow_results(overlay, qapp):
    marks = overlay.marks
    overlay.hide_for_capture()
    assert overlay.set_background_opacity(100)
    qapp.processEvents()
    assert overlay._suspended and not overlay.isVisible()
    assert overlay.marks == marks
    assert overlay.restore_after_capture()
    assert overlay.isVisible()
    overlay.clear()
    assert overlay.set_background_opacity(0)
    qapp.processEvents()
    assert not overlay.isVisible() and not overlay.marks
