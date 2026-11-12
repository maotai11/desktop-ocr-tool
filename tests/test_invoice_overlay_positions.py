"""Invoice annotations must follow actual selected OCR positions and scores."""
import pytest
from PySide6.QtCore import QRectF

from src.ui.search_highlight_overlay import SearchHighlightOverlay


@pytest.fixture
def overlay(qtbot):
    widget = SearchHighlightOverlay()
    qtbot.addWidget(widget)
    return widget


def _mark(scale, *, y=120, score=1.0, status='matched'):
    return {'anchor': (20 * scale, 20 * scale, 80 * scale, 20 * scale),
            'bounds': (250 * scale, 80 * scale, 300 * scale, 300 * scale),
            'boxes': [[[270 * scale, y * scale], [350 * scale, y * scale],
                       [350 * scale, (y + 20) * scale], [270 * scale, (y + 20) * scale]]],
            'a_text': '12345', 'b_text': 'never show this', 'status': status,
            'similarity': score}


def test_moving_matched_invoice_box_moves_annotation_not_source_anchor(overlay, qapp):
    scale = qapp.primaryScreen().devicePixelRatio()
    mark = _mark(scale)
    assert overlay.set_marks([mark])
    first = overlay.callouts[0]
    assert first['anchor'] == (270.0, 120.0, 80.0, 20.0)
    assert first['source_anchor'] == (20.0, 20.0, 80.0, 20.0)
    assert first['rect'][1] == 143
    assert first['display_lines'] == ('12345 · 100%',)
    assert overlay.set_marks([_mark(scale, y=210)])
    second = overlay.callouts[0]
    assert second['rect'][1] - first['rect'][1] == 90
    assert second['source_anchor'] == first['source_anchor']


@pytest.mark.parametrize('score, suffix', [(0.8, '80%'), (0.999999, '99.9%'), (1.0, '100%')])
def test_percentage_is_actual_similarity_and_never_rounds_near_match_to_exact(overlay, qapp, score, suffix):
    mark = _mark(qapp.primaryScreen().devicePixelRatio(), score=score, status='similar')
    assert overlay.set_marks([mark])
    label = overlay.callouts[0]
    assert label['display_lines'] == (f'12345 · {suffix}',)
    assert label['status'] == 'similar' and label['kind'] == 'uncertain'


@pytest.mark.parametrize('score', [True, '1', -0.1, 1.1, float('nan'), float('inf')])
def test_invalid_scores_reject_whole_annotation_set(overlay, qapp, score):
    mark = _mark(qapp.primaryScreen().devicePixelRatio())
    assert overlay.set_marks([mark])
    invalid = {**mark, 'similarity': score}
    assert not overlay.set_marks([mark, invalid])
    assert not overlay.marks and not overlay.isVisible()


def test_same_invoice_field_collision_reports_unplaced_value_without_relocation(overlay, qapp):
    scale = qapp.primaryScreen().devicePixelRatio()
    first, second = _mark(scale), _mark(scale)
    second['a_text'] = '54321'
    assert overlay.set_marks([first, second])
    assert len(overlay.callouts) == 1 and overlay.overflow_count == 1
    assert overlay.callouts[0]['rect'][1] == 143


@pytest.mark.parametrize('reason', ['missing', 'ambiguous', 'no_candidate', 'bottom_edge', 'next_field'])
def test_no_safe_invoice_position_is_reported_to_controls(overlay, qapp, reason):
    scale = qapp.primaryScreen().devicePixelRatio()
    mark = _mark(scale)
    marks = [mark]
    if reason in ('missing', 'ambiguous'):
        mark['status'] = reason
    elif reason == 'no_candidate':
        mark['boxes'] = []
    elif reason == 'bottom_edge':
        mark['bounds'] = (250 * scale, 80 * scale, 300 * scale, 65 * scale)
    else:
        # The original text of the next actual field must not be covered.
        next_mark = _mark(scale, y=143)
        next_mark['status'] = 'missing'
        marks.append(next_mark)
    assert overlay.set_marks(marks)
    assert not overlay.callouts and overlay.overflow_count == len(marks)


def test_all_visible_annotations_remain_inside_invoice_and_screen(overlay, qapp):
    scale = qapp.primaryScreen().devicePixelRatio()
    marks = [_mark(scale, y=y) for y in (120, 185, 250)]
    for index, mark in enumerate(marks):
        mark['a_text'] = '1234567890' * (index + 1)
    assert overlay.set_marks(marks)
    labels = overlay.callouts
    assert len(labels) == 3
    for index, label in enumerate(labels):
        rectangle = QRectF(*label['rect'])
        assert QRectF(*(value / scale for value in label['bounds'])).contains(rectangle)
        assert overlay.rect().contains(rectangle.toAlignedRect())
        assert all(not rectangle.intersects(QRectF(*other['rect'])) for other in labels[index + 1:])


def test_near_value_remains_visible_beside_dense_invoice_line(overlay, qapp):
    scale = qapp.primaryScreen().devicePixelRatio()
    near = _mark(scale, score=.8, status='similar')
    next_field = _mark(scale, y=143)
    assert overlay.set_marks([near, next_field])
    label = next(card for card in overlay.callouts if card['status'] == 'similar')
    assert label['display_lines'] == ('12345 · 80%',)
    rectangle = QRectF(*label['rect'])
    anchor = QRectF(*label['anchor'])
    assert rectangle.left() == anchor.right() + 6 and rectangle.top() == anchor.top()
    assert not rectangle.intersects(QRectF(270, 143, 80, 20))
