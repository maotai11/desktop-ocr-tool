"""Locate a selected table row without reading any neighbouring row."""
import numpy as np


class NoSelectedBlue(ValueError):
    """No supported selection remains; explicit manual row fallback is safe."""


def _solid_blue_background(blue):
    """Keep solid 5-by-3 colour support, excluding thin ClearType fringes.

    Opening restores the supported background's original edges; using only
    erosion would move row boundaries. Shifted views avoid a per-pixel window
    tensor, and working storage remains linear in the bounded capture size.
    """
    height, width = blue.shape
    opened = np.zeros_like(blue)
    if height < 3 or width < 5:
        return opened
    core = blue[1:-1, 2:-2].copy()
    for dy in range(3):
        for dx in range(5):
            core &= blue[dy:dy+height-2, dx:dx+width-4]
    for dy in range(3):
        for dx in range(5):
            opened[dy:dy+height-2, dx:dx+width-4] |= core
    return opened


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
    raw_blue = (b > 110) & (b-r > 45) & (b-g > 15)
    blue = _solid_blue_background(raw_blue)
    neutral = (np.max(image, axis=2).astype(int)-np.min(image, axis=2).astype(int) < 35)
    dark = (image.mean(axis=2) < 155) & neutral
    lines = np.flatnonzero(dark.mean(axis=1) > .65)
    raw_indexes = np.flatnonzero(raw_blue.sum(axis=1) >= max(3, width*.008))
    raw_groups = np.split(raw_indexes, np.where(np.diff(raw_indexes) > 1)[0]+1)
    plausible_rows = []
    for group in raw_groups:
        if len(group) < 3:
            continue
        if np.any((lines > group[0]) & (lines < group[-1])):
            raise ValueError('Ambiguous blue selection crosses row boundaries; select one row manually')
        above, below = lines[lines < group[0]], lines[lines > group[-1]]
        if not len(above) or not len(below):
            continue
        tolerance = max(4, len(group)*.25)
        if (group[0]-above[-1] <= tolerance
                and below[0]-group[-1] <= tolerance):
            plausible_rows.append((int(above[-1]+1), int(below[0])))
    # A full-row blue background is positive selection evidence regardless of
    # width, gradient, palette, or nearby ink. Fringes must not erase a second
    # such row; short mid-cell glyph bands do not meet this geometric test.
    if len(set(plausible_rows)) > 1:
        raise ValueError('Ambiguous selected rows; select one row manually')
    # Thin blue is not automatically text: it can be a second real selected
    # column. Only discard fringe next to neutral dark glyph ink horizontally;
    # a grid line above/below a narrow cell is not evidence of ClearType.
    glyph_ink = dark.copy()
    glyph_ink[:, dark.mean(axis=0) > .65] = False
    nearby_ink = np.zeros_like(dark)
    for distance in range(1, 5):
        nearby_ink[:, distance:] |= glyph_ink[:, :-distance]
        nearby_ink[:, :-distance] |= glyph_ink[:, distance:]
    # ClearType can render a thin glyph entirely as opposing red/blue edges,
    # with no neutral black core. Eight physical pixels bounds the context at
    # 200% scale; uniform blue patches below always override this exception.
    red_edge = (r > 110) & (r-b > 45) & (r-g > 15)
    for distance in range(1, 9):
        nearby_ink[:, distance:] |= red_edge[:, :-distance]
        nearby_ink[:, :-distance] |= red_edge[:, distance:]
    unexplained = raw_blue & ~blue & ~nearby_ink
    protected_rows = np.zeros(height, dtype=bool)
    if height >= 3 and width >= 3:
        # Any uniform blue patch is positive evidence of another background,
        # independently of the main selection's palette. Even next to black
        # ink, such a narrow cell must not be erased as a text fringe.
        uniform_blue = raw_blue[1:-1, 1:-1].copy()
        for dy in range(3):
            for dx in range(3):
                for channel in range(3):
                    uniform_blue &= (image[dy:dy+height-2, dx:dx+width-2, channel]
                                     == image[1:-1, 1:-1, channel])
        for dy in range(3):
            for dx in range(3):
                protected = uniform_blue & ~blue[dy:dy+height-2, dx:dx+width-2]
                unexplained[dy:dy+height-2, dx:dx+width-2] |= protected
                protected_rows[dy:dy+height-2] |= protected.any(axis=1)
    suspect = np.flatnonzero(unexplained.sum(axis=1) >= max(3, width*.008))
    if len(suspect):
        suspect_groups = np.split(suspect, np.where(np.diff(suspect) > 1)[0]+1)
        for group in suspect_groups:
            if len(group) < 3:
                continue
            # Scaled antialiasing can leave a short nonuniform ink fragment.
            # Ignore it only deep inside a known, similarly sized row. Positive
            # uniform-background evidence always wins over this noise exception.
            above, below = lines[lines < group[0]], lines[lines > group[-1]]
            ignorable = False
            if (not protected_rows[group].any() and len(plausible_rows) == 1
                    and len(above) and len(below)):
                enclosing_height = int(below[0]-above[-1]-1)
                selected_height = plausible_rows[0][1]-plausible_rows[0][0]
                tolerance = max(4, len(group)*.25)
                ignorable = (0.5*selected_height <= enclosing_height <= 2*selected_height
                             and len(group) <= .1*min(enclosing_height, selected_height)
                             and group[0]-above[-1] > tolerance
                             and below[0]-group[-1] > tolerance)
            if not ignorable:
                raise ValueError('Ambiguous thin blue selection; select one row manually')
    active = blue.sum(axis=1) >= max(3, width*.008)
    indexes = np.flatnonzero(active)
    if not len(indexes):
        if plausible_rows:
            raise ValueError('Ambiguous thin blue selection; select one row manually')
        raise NoSelectedBlue('No selected blue cell; select a row manually')
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
    if plausible_rows and (y0, y1) != plausible_rows[0]:
        raise ValueError('Ambiguous selected rows; select one row manually')
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
