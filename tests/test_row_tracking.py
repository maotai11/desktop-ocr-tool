"""Calibrated row tracking must stop trusting lost/ambiguous selections."""
import numpy as np
import pytest

from src.comparison.row_tracking import RowTemplate, RowTracker, translate_regions

TABLE = (20, 30, 200, 120, 1)
BASELINE = (20, 55, 200, 10, 1)
REGIONS = {'name': (30, 55, 50, 10, 1), 'amount': (120, 55, 60, 10, 1)}
B = (250, 30, 200, 120, 1)
BLUE = (220, 115, 40)
GREEN = (70, 160, 40)


def frame(rows=(20,), *, color=BLUE, height=20, content=0):
    pixels = np.full((120, 200, 3), 245, dtype=np.uint8)
    for y in rows:
        pixels[y:y + height, :] = color
        pixels[y + 7:y + 11, 15 + content:25 + content] = 0
    return pixels


def tracker():
    return RowTracker(TABLE, BASELINE)


def test_template_keeps_relative_offsets_and_uses_explicit_pitch():
    template = RowTemplate(TABLE, BASELINE, REGIONS, B, 25)
    assert template.offsets == {'name': (10, 0, 50, 10), 'amount': (100, 0, 60, 10)}
    moved = template.at_row(2)
    for name, region in moved.items():
        assert region == (REGIONS[name][0], REGIONS[name][1] + 50, *REGIONS[name][2:])
    assert template.at_row(0) == REGIONS
    assert template.at_row(-1)['name'][1] == 30
    assert template.regions is not REGIONS


@pytest.mark.parametrize('delta', [-26, 86])
def test_move_beyond_visible_table_rejected_without_mutation(delta):
    original = dict(REGIONS)
    with pytest.raises(ValueError):
        translate_regions(REGIONS, delta, table_region=TABLE, b_region=B)
    assert REGIONS == original


def test_cross_monitor_overlap_and_out_of_monitor_bounds_are_rejected():
    with pytest.raises(ValueError):
        translate_regions(REGIONS, 0, table_region=TABLE, b_region=(*B[:4], 2))
    with pytest.raises(ValueError):
        translate_regions(REGIONS, 0, table_region=TABLE, b_region=REGIONS['name'])
    overlapping = {'a': REGIONS['name'], 'b': REGIONS['name']}
    assert translate_regions(overlapping, 0, table_region=TABLE, b_region=B) == overlapping
    with pytest.raises(ValueError):
        translate_regions(REGIONS, 0, table_region=TABLE, b_region=B, monitor_bounds=(300, 200))
    assert translate_regions(REGIONS, 0, table_region=TABLE, b_region=B,
                             monitor_bounds=(7680, 4320)) == REGIONS
    assert translate_regions(REGIONS, 25, table_region=(0, 0, 7680, 4320, 1),
                             b_region=B, monitor_bounds=(7680, 4320))['name'][1] == 80


@pytest.mark.parametrize('pitch', [True, 0, -1, 2001])
def test_invalid_row_pitch_rejected(pitch):
    with pytest.raises(ValueError):
        RowTemplate(TABLE, BASELINE, REGIONS, B, pitch)


@pytest.mark.parametrize('color', [BLUE, GREEN, (50, 50, 210)])
def test_calibration_learns_actual_highlight_not_only_blue(color):
    follow = tracker()
    assert follow.calibrate(frame(color=color))['status'] == 'pending'
    assert follow.update(frame(color=color))['status'] == 'pending'
    stable = follow.update(frame(color=color))
    assert stable['status'] == 'stable'
    assert stable['row'] == (20, 50, 200, 20, 1)
    assert stable['delta_y'] == 0
    assert stable['content_changed']
    assert not follow.update(frame(color=color))['content_changed']
    assert follow.update(frame((45,), color=color))['status'] == 'pending'
    moved = follow.update(frame((45,), color=color))
    assert moved['status'] == 'stable'
    assert moved['delta_y'] == 25
    positions = translate_regions(REGIONS, moved['delta_y'], table_region=TABLE, b_region=B)
    assert positions['name'][1] == 80


@pytest.mark.parametrize('pixels', [frame(color=(100, 100, 100)), frame(rows=()), frame(rows=(20, 60))])
def test_gray_missing_or_multiple_rows_do_not_calibrate(pixels):
    follow = tracker()
    assert follow.calibrate(pixels)['status'] in ('lost', 'ambiguous')
    assert follow.update(frame())['status'] == 'lost'


def test_calibration_must_intersect_user_selected_row():
    follow = tracker()
    assert follow.calibrate(frame((60,)))['status'] == 'lost'


def test_small_colored_object_cannot_be_a_row():
    pixels = frame()
    pixels[:, 80:] = 245
    assert tracker().calibrate(pixels)['status'] == 'lost'


def test_ambiguous_or_lost_frame_invalidates_stability_no_nearest_y_guess():
    follow = tracker()
    follow.calibrate(frame())
    follow.update(frame())
    assert follow.update(frame())['status'] == 'stable'
    for pixels, status in [(frame((20, 60)), 'ambiguous'), (frame(()), 'lost')]:
        output = follow.update(pixels)
        assert output['status'] == status
        assert output['row'] is None
        assert output['delta_y'] is None
        assert follow.update(frame())['status'] == 'pending'
        assert follow.update(frame())['status'] == 'stable'


def test_same_y_scroll_content_change_does_not_reuse_old_identity():
    follow = tracker()
    follow.calibrate(frame())
    follow.update(frame())
    previous = follow.update(frame())
    changed = follow.update(frame(content=35))
    assert changed['status'] == 'stable'
    assert changed['row'] == previous['row']
    assert changed['delta_y'] == 0
    assert changed['fingerprint'] != previous['fingerprint']
    assert changed['content_changed']
    assert not follow.update(frame(content=35))['content_changed']


@pytest.mark.parametrize('pixels', [frame((20,), height=25), frame((110,)), frame(color=GREEN)])
def test_changed_height_partial_row_or_changed_highlight_is_lost(pixels):
    follow = tracker()
    follow.calibrate(frame())
    follow.update(frame())
    follow.update(frame())
    assert follow.update(pixels)['status'] == 'lost'


def test_wrong_raster_shape_or_dtype_rejected_and_no_pixels_retained():
    follow = tracker()
    with pytest.raises(ValueError):
        follow.calibrate(np.zeros((10, 10, 3), np.uint8))
    with pytest.raises(ValueError):
        follow.calibrate(frame().astype(float))
    pixels = frame()
    follow.calibrate(pixels)
    assert not any(isinstance(value, np.ndarray) for value in vars(follow).values())
    pixels[:] = 0
    assert follow.update(frame())['status'] == 'pending'
