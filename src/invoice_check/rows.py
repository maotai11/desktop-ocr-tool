"""Locate a selected table row without reading any neighbouring row."""
import numpy as np


def detect_active_row(image, manual_row=None):
    if not isinstance(image, np.ndarray) or image.ndim != 3 or image.shape[2] != 3 or image.dtype != np.uint8:
        raise ValueError('Expected a BGR uint8 table image')
    height, width = image.shape[:2]
    if manual_row is not None:
        y0, y1 = map(int, manual_row)
        if not 0 <= y0 < y1 <= height:
            raise ValueError('Manual row is outside the table')
        return {'row': [y0, y1], 'header': None, 'box': [0, y0, width, y1-y0], 'source': 'manual'}
    b, g, r = [image[:, :, i].astype(np.int16) for i in range(3)]
    blue = (b > 110) & (b-r > 45) & (b-g > 15)
    active = blue.sum(axis=1) >= max(3, width*.008)
    indexes = np.flatnonzero(active)
    if not len(indexes):
        raise ValueError('No selected blue cell; select a row manually')
    groups = np.split(indexes, np.where(np.diff(indexes) > 1)[0]+1)
    groups = [group for group in groups if len(group) >= 3]
    if len(groups) != 1:
        raise ValueError('Ambiguous selected rows; select one row manually')
    selected = groups[0]
    # A narrow vertical thumb can have the same colour as a selected cell.
    # Fail closed rather than interpreting its height as a multi-row selection.
    support = blue[selected].sum(axis=1)
    if len(selected) > int(support.max()):
        raise ValueError('Ambiguous vertical blue control; select one row manually')
    # Long neutral dark grid lines bound the active cell's row at any pixel scale.
    neutral = (np.max(image, axis=2).astype(int)-np.min(image, axis=2).astype(int) < 35)
    dark = (image.mean(axis=2) < 155) & neutral
    lines = np.flatnonzero(dark.mean(axis=1) > .65)
    if np.any((lines > selected[0]) & (lines < selected[-1])):
        raise ValueError('Ambiguous blue selection crosses row boundaries; select one row manually')
    above, below = lines[lines < selected[0]], lines[lines > selected[-1]]
    # A missed separator must never expand the crop into a neighbouring row.
    if not len(above) or not len(below):
        raise ValueError('Missing row boundaries; select one row manually')
    band_height = len(selected)
    if (len(above) and selected[0]-above[-1] > max(4, band_height*.25)) or (len(below) and below[0]-selected[-1] > max(4, band_height*.25)):
        raise ValueError('Uncertain row boundaries; select one row manually')
    y0 = int(above[-1]+1) if len(above) else int(selected[0])
    y1 = int(below[0]) if len(below) else int(selected[-1]+1)
    line_groups = np.split(above, np.where(np.diff(above) > 1)[0]+1)
    # First bounded band is the header, never all rows above the selection.
    header = None
    if len(line_groups) >= 2:
        start, end = int(line_groups[0][-1]+1), int(line_groups[1][0])
        if 0 < end-start <= band_height*2:
            header = [start, end]
    elif len(line_groups) == 1 and 0 < line_groups[0][0] <= band_height*2:
        header = [0, int(line_groups[0][0])]
    return {'row': [y0, y1], 'header': header, 'box': [0, y0, width, y1-y0], 'source': 'blue'}
