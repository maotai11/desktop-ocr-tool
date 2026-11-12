"""The current source row remains distinct from invoice match annotations."""
import pytest
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QPolygonF
from PySide6.QtCore import QPointF

from src.ui.search_highlight_overlay import SearchHighlightOverlay


@pytest.fixture
def overlay(qtbot):
    widget = SearchHighlightOverlay()
    qtbot.addWidget(widget)
    return widget


def _rect(points):
    return QPolygonF([QPointF(*point) for point in points]).boundingRect()


def _mark(scale, score=.8):
    return {'a_text': '12345', 'status': 'matched', 'similarity': score,
            'anchor': (40 * scale, 100 * scale, 120 * scale, 24 * scale),
            # Fit the 400-logical-pixel offscreen display at 200% as well.
            'bounds': (220 * scale, 40 * scale, 170 * scale, 200 * scale),
            'boxes': [[[240 * scale, 70 * scale], [340 * scale, 70 * scale],
                       [340 * scale, 90 * scale], [240 * scale, 90 * scale]]]}


def test_current_row_arrow_shows_without_invoice_results_and_follows_row(overlay, qapp):
    scale = qapp.primaryScreen().devicePixelRatio()
    for y in (50, 100, 160):
        assert overlay.set_source_row((40 * scale, y * scale, 200 * scale, 24 * scale, 1))
        qapp.processEvents()
        assert overlay.isVisible()
        rectangle = _rect(overlay.source_row_arrow)
        assert rectangle.center().y() == y + 12
        assert rectangle.right() < 40
        assert overlay.overflow_count == 0 and not overlay.callouts
        image = overlay.grab().toImage()
        assert any(image.pixelColor(x, yy).name() == '#175cd3'
                   for yy in range(round(rectangle.top() * scale), round(rectangle.bottom() * scale))
                   for x in range(round(rectangle.left() * scale), round(rectangle.right() * scale)))
    assert overlay.windowFlags() & Qt.WindowType.WindowTransparentForInput
    assert overlay.windowFlags() & Qt.WindowType.WindowStaysOnTopHint


def test_arrow_stays_in_source_row_at_screen_edge_and_does_not_occlude_invoice(overlay, qapp):
    scale = qapp.primaryScreen().devicePixelRatio()
    assert overlay.set_source_row((0, 20 * scale, 200 * scale, 24 * scale))
    assert _rect(overlay.source_row_arrow).left() == 0
    mark = _mark(scale)
    mark['bounds'] = (0, 40 * scale, 200 * scale, 200 * scale)
    mark['boxes'] = [[[20 * scale, 70 * scale], [120 * scale, 70 * scale],
                      [120 * scale, 90 * scale], [20 * scale, 90 * scale]]]
    mark['anchor'] = (205 * scale, 100 * scale, 100 * scale, 24 * scale)
    assert overlay.set_marks([mark])
    assert overlay.set_source_row((205 * scale, 100 * scale, 100 * scale, 24 * scale))
    rectangle = _rect(overlay.source_row_arrow)
    assert rectangle.left() == 205  # Exterior-left position would cover B.
    assert not rectangle.intersects(QRectF(0, 40, 200, 200))


def test_capture_pin_opacity_clear_and_late_restore_do_not_resurrect_arrow(overlay, qapp):
    scale = qapp.primaryScreen().devicePixelRatio()
    assert overlay.set_source_row((40 * scale, 50 * scale, 200 * scale, 24 * scale))
    original = overlay.source_row_arrow
    overlay.hide_for_capture()
    assert overlay.set_background_opacity(50)
    assert overlay.set_pinned(False)
    qapp.processEvents()
    assert not overlay.isVisible() and overlay.source_row_arrow == original
    assert overlay.restore_after_capture() and overlay.isVisible()
    overlay.hide_for_capture()
    overlay.clear()
    assert overlay.set_background_opacity(100)
    assert overlay.restore_after_capture()
    assert overlay.source_row is None and overlay.source_row_arrow is None and not overlay.isVisible()


def test_clearing_arrow_preserves_real_invoice_results_and_near_scores(overlay, qapp):
    scale = qapp.primaryScreen().devicePixelRatio()
    for score in (.6, .8, .999999):
        assert overlay.set_marks([_mark(scale, score)])
        assert overlay.set_source_row((40 * scale, 100 * scale, 120 * scale, 24 * scale))
        assert overlay.callouts[0]['status'] == 'similar'
        assert not overlay.callouts[0]['display_lines'][0].endswith('100%')
        assert len(overlay.callouts) == 1 and overlay.overflow_count == 0
        assert overlay.set_source_row(None)
        assert overlay.isVisible() and len(overlay.callouts) == 1


@pytest.mark.parametrize('region', [(0, 0, 20, 20, 2), (0, 0, 20, 20, True),
                                    (-1, 0, 20, 20), (0, 0, -1, 20),
                                    (0, 0, float('nan'), 20), (True, 0, 20, 20),
                                    (0, 0, 100000, 20), (), 'row'])
def test_invalid_row_clears_all_visible_state(overlay, qapp, region):
    scale = qapp.primaryScreen().devicePixelRatio()
    assert overlay.set_marks([_mark(scale)])
    assert overlay.set_source_row((40 * scale, 50 * scale, 200 * scale, 24 * scale))
    assert not overlay.set_source_row(region)
    assert not overlay.marks and overlay.source_row is None and not overlay.isVisible()
    assert overlay.restore_after_capture() and not overlay.isVisible()


def test_invalid_invoice_marks_also_clear_current_row_arrow(overlay):
    assert overlay.set_source_row((40, 50, 200, 24))
    assert not overlay.set_marks([{'boxes': 'invalid'}])
    assert overlay.source_row is None and not overlay.isVisible()
