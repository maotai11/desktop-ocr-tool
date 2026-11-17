"""Synthetic Windows-style blue controls must not turn into multiple rows."""
import numpy as np
import pytest

from src.invoice_check.rows import detect_active_row


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
    with pytest.raises(ValueError, match='vertical blue control'):
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
