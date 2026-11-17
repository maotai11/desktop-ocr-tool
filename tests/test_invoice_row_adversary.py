"""Synthetic Windows-style blue controls must not turn into multiple rows."""
import numpy as np
import pytest

from src.invoice_check.rows import NoSelectedBlue, detect_active_row


def table():
    image = np.full((140, 400, 3), 255, np.uint8)
    image[[0, 30, 60, 90, 120, 139]] = 0
    return image


def test_scrollbar_joined_to_actual_cell_never_expands_selected_row():
    image = table()
    image[33:58, 60:180] = (240, 60, 0)
    image[3:118, 392:400] = (240, 60, 0)
    with pytest.raises(ValueError, match='Ambiguous'):
        detect_active_row(image)


def test_wide_cell_joined_to_scrollbar_cannot_cross_grid_lines():
    image = table()
    image[33:58, 10:350] = (240, 60, 0)
    image[3:118, 392:400] = (240, 60, 0)
    with pytest.raises(ValueError, match='crosses row boundaries'):
        detect_active_row(image)


def test_scrollbar_without_selection_requires_manual_row():
    image = table()
    image[3:118, 392:400] = (240, 60, 0)
    with pytest.raises(ValueError, match='vertical blue control|crosses row boundaries'):
        detect_active_row(image)


def test_blue_header_and_selected_cell_require_manual_disambiguation():
    image = table()
    image[3:28, 1:399] = (240, 60, 0)
    image[33:58, 60:180] = (240, 60, 0)
    with pytest.raises(ValueError, match='Ambiguous'):
        detect_active_row(image)


def test_plain_selected_cell_and_explicit_manual_band_remain_available():
    image = table()
    image[33:58, 60:180] = (240, 60, 0)
    assert detect_active_row(image)['row'] == [31, 60]
    image[3:118, 392:400] = (240, 60, 0)
    assert detect_active_row(image, manual_row=(31, 60))['row'] == [31, 60]


@pytest.mark.parametrize('scale', [1, 2])
@pytest.mark.parametrize('fringe_width', [1, 2])
def test_thin_cleartype_edges_are_not_additional_selected_rows(scale, fringe_width):
    image = table()
    image[33:58, 60:180] = (240, 60, 0)
    # Coloured subpixel strokes on black text in the header and another row
    # exceed the former per-row count threshold but are not solid backgrounds.
    for y in (8, 100):
        for x in range(15, 360, 12):
            image[y:y+16, x:x+fringe_width] = (220, 80, 15)
            image[y:y+16, x+fringe_width:x+fringe_width+2] = 0
    image = np.repeat(np.repeat(image, scale, axis=0), scale, axis=1)
    if fringe_width * scale >= 3:
        # A nearest-neighbour enlarged uniform stripe is indistinguishable
        # from a real narrow selection; keep this case manual and safe.
        with pytest.raises(ValueError, match='Ambiguous'):
            detect_active_row(image)
        return
    assert detect_active_row(image)['row'] == [31*scale, 60*scale]


@pytest.mark.parametrize('scale', [1, 2])
def test_text_fringe_without_solid_selection_requires_manual_row(scale):
    image = table()
    for x in range(15, 360, 12):
        image[8:24, x:x+2] = (220, 80, 15)
        image[8:24, x+2:x+4] = 0
    image = np.repeat(np.repeat(image, scale, axis=0), scale, axis=1)
    with pytest.raises(ValueError, match='No selected blue cell|Ambiguous'):
        detect_active_row(image)


@pytest.mark.parametrize('scale', [1, 2])
def test_actual_qimage_resource_fixture_preserves_selected_row_with_native_text(qapp, scale):
    from src.resource_probe import fixture_image

    image, _, region, _, _ = fixture_image()
    # Keep the fixture's platform text rendering (including Windows ClearType).
    pixels = np.asarray(image.constBits(), dtype=np.uint8).reshape(
        image.height(), image.bytesPerLine() // 4, 4)
    x, y, width, height = region
    target = pixels[y:y+height, x:x+width, :3].copy()
    target = np.repeat(np.repeat(target, scale, axis=0), scale, axis=1)
    assert detect_active_row(target)['row'] == [46*scale, 95*scale]


@pytest.mark.parametrize('scale', [1, 2])
def test_thin_second_real_selection_cannot_be_discarded_as_text_fringe(scale):
    image = table()
    image[33:58, 60:180] = (240, 60, 0)
    image[63:88, 200:204] = (240, 60, 0)
    image = np.repeat(np.repeat(image, scale, axis=0), scale, axis=1)
    with pytest.raises(ValueError, match='Ambiguous'):
        detect_active_row(image)


@pytest.mark.parametrize('scale', [1, 2])
def test_thin_second_selected_column_next_to_grid_is_not_glyph_fringe(scale):
    image = table()
    image[33:58, 60:180] = (240, 60, 0)
    image[:, 204] = 0
    image[63:88, 200:204] = (240, 60, 0)
    image = np.repeat(np.repeat(image, scale, axis=0), scale, axis=1)
    with pytest.raises(ValueError, match='Ambiguous'):
        detect_active_row(image)


@pytest.mark.parametrize('scale', [1, 2])
def test_thin_second_selection_touching_partial_dark_ink_still_requires_manual_row(scale):
    image = table()
    image[33:58, 60:180] = (240, 60, 0)
    image[63:88, 200:204] = (240, 60, 0)
    image[63:88, 204:206] = 0
    image = np.repeat(np.repeat(image, scale, axis=0), scale, axis=1)
    with pytest.raises(ValueError, match='Ambiguous'):
        detect_active_row(image)


@pytest.mark.parametrize('scale', [1, 2])
def test_different_palette_thin_selected_cell_with_black_ink_is_not_discarded(scale):
    image = table()
    image[33:58, 60:180] = (240, 60, 0)
    image[63:88, 200:204] = (220, 80, 15)
    image[63:88, 204:206] = 0
    image = np.repeat(np.repeat(image, scale, axis=0), scale, axis=1)
    with pytest.raises(ValueError, match='Ambiguous'):
        detect_active_row(image)


@pytest.mark.parametrize('scale', [1, 2])
def test_paired_chromatic_glyph_edges_without_black_core_are_not_selection(scale):
    image = table()
    image[33:58, 60:180] = (240, 60, 0)
    for y in (8, 100):
        for x in range(15, 360, 12):
            image[y:y+16, x:x+1] = (220, 80, 15)
            image[y:y+16, x+4:x+5] = (15, 80, 220)
    image = np.repeat(np.repeat(image, scale, axis=0), scale, axis=1)
    assert detect_active_row(image)['row'] == [31*scale, 60*scale]


@pytest.mark.parametrize('scale', [1, 2])
def test_uniform_second_blue_background_next_to_red_still_requires_manual_row(scale):
    image = table()
    image[33:58, 60:180] = (240, 60, 0)
    image[63:88, 200:204] = (220, 80, 15)
    image[63:88, 204:206] = (15, 80, 220)
    image = np.repeat(np.repeat(image, scale, axis=0), scale, axis=1)
    with pytest.raises(ValueError, match='Ambiguous'):
        detect_active_row(image)


@pytest.mark.parametrize('scale', [1, 2])
@pytest.mark.parametrize('adjacent', [(0, 60, 220), (0, 0, 0)])
def test_full_row_gradient_selection_is_preserved_before_fringe_filter(scale, adjacent):
    image = table()
    image[33:58, 60:180] = (240, 60, 0)
    for y in range(63, 88):
        image[y, 200:204] = (215+y-63, 60, 0)
    image[63:88, 204:206] = adjacent
    image = np.repeat(np.repeat(image, scale, axis=0), scale, axis=1)
    with pytest.raises(ValueError, match='Ambiguous'):
        detect_active_row(image)


def test_thin_gradient_bridge_across_separator_cannot_be_removed_as_glyph_noise():
    image = table()
    image[33:58, 60:180] = (240, 60, 0)
    for y in range(33, 88):
        image[y, 200:204] = (190+y%40, 60, 0)
    image[33:88, 204:206] = (0, 60, 220)
    with pytest.raises(ValueError, match='crosses row boundaries'):
        detect_active_row(image)


@pytest.mark.parametrize('scale', [1, 2, 3])
def test_short_nonuniform_midrow_antialiasing_residue_is_scale_bounded(scale):
    image = table()
    image[33:58, 60:180] = (240, 60, 0)
    image = np.repeat(np.repeat(image, scale, axis=0), scale, axis=1)
    for y in range(72*scale, 74*scale):
        for x in range(200*scale, 330*scale, 12*scale):
            image[y, x:x+2] = (200+y%20, 80, 15)
    assert detect_active_row(image)['row'] == [31*scale, 60*scale]


@pytest.mark.parametrize('broken_grid', [False, True])
def test_short_unknown_blue_near_border_or_missing_grid_is_not_noise(broken_grid):
    image = table()
    image[33:58, 60:180] = (240, 60, 0)
    for y in range(62, 65):
        image[y, 200:212] = (140+y, 80, 15)
    if broken_grid:
        image[90, :] = 255
    with pytest.raises(ValueError, match='Ambiguous'):
        detect_active_row(image)


@pytest.mark.parametrize('scale', [1, 2])
def test_gray_manual_row_with_only_text_fringes_has_typed_absent_selection(scale):
    image = np.full((125, 400, 3), 255, np.uint8)
    image[[0, 30, 60, 90, 120]] = 0
    image[32:59, 5:75] = (190, 190, 190)
    for x in (40, 80, 120, 160, 200):
        image[10:22, x] = (240, 60, 0)
        image[10:22, x+1:x+4] = 0
    image = np.repeat(np.repeat(image, scale, axis=0), scale, axis=1)
    with pytest.raises(NoSelectedBlue):
        detect_active_row(image)
    assert detect_active_row(image, manual_row=(31*scale, 60*scale))['row'] == [31*scale, 60*scale]


def test_ambiguous_real_blue_is_not_typed_as_absent_selection():
    image = table()
    image[33:58, 60:180] = (240, 60, 0)
    image[63:88, 200:204] = (220, 80, 15)
    with pytest.raises(ValueError) as error:
        detect_active_row(image)
    assert not isinstance(error.value, NoSelectedBlue)


@pytest.mark.parametrize('scale', [1, 2])
def test_single_real_gradient_outside_old_manual_row_is_never_absent_selection(scale):
    image = table()
    for y in range(63, 88):
        image[y, 200:204] = (215+y-63, 60, 0)
    image[63:88, 204:206] = (0, 60, 220)
    image = np.repeat(np.repeat(image, scale, axis=0), scale, axis=1)
    with pytest.raises(ValueError) as error:
        detect_active_row(image)
    assert not isinstance(error.value, NoSelectedBlue)
