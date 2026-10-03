# -*- coding: utf-8 -*-
import logging
from typing import List, Dict, Any
from statistics import median

logger = logging.getLogger(__name__)


def box_bounds(box):
    if box is None or len(box) == 0:
        return (0.0, 0.0, 0.0, 0.0)
    if hasattr(box, 'tolist'):
        box = box.tolist()
    if isinstance(box[0], (list, tuple)):
        return min(p[0] for p in box), min(p[1] for p in box), max(p[0] for p in box), max(p[1] for p in box)
    return tuple(box[:4])


def _cjk(char):
    return (('\u3400' <= char <= '\u9fff') or ('\uf900' <= char <= '\ufaff')
            or ('\U00020000' <= char <= '\U000323af'))


def boundary_separator(left, right):
    """Reconstruct only inter-box boundaries; preserve all in-box whitespace.

    Wide gaps retain a field separator. Tight CJK, paired punctuation, currency,
    percentages and date/amount fragments are attached. Latin words and SI units
    use a space. This is a testable horizontal-text policy, not table parsing.
    """
    a, b = left.get('text', ''), right.get('text', '')
    if not a or not b or a[-1].isspace() or b[0].isspace():
        return ''
    ab, bb = box_bounds(left.get('box')), box_bounds(right.get('box'))
    height = max(1, min(ab[3]-ab[1], bb[3]-bb[1]))
    gap = bb[0] - ab[2]
    if gap > height * 0.75:
        return ' '
    # Ideographic variation selectors belong to the preceding rare glyph.
    visible_a = a.rstrip(''.join(chr(i) for i in range(0xfe00, 0xfe10))
                         + ''.join(chr(i) for i in range(0xe0100, 0xe01f0)))
    x, y = (visible_a[-1] if visible_a else a[-1]), b[0]
    if y in '，。；：！？、,.!?:;%％)]}）】」』' or x in '([{（【「『$＄￥¥':
        return ''
    if x in '/.-,' and y.isdigit() or x.isdigit() and y in '/.-,':
        return ''
    if a.endswith(('NT$', 'US$', 'NT＄')) and y.isdigit():
        return ''
    if x.isdigit() and y.isdigit():
        # Adjacent numbers may be an account ID followed by a quantity.
        # A small gap alone is insufficient evidence to concatenate them.
        return ' '
    if _cjk(x) and _cjk(y):
        return ''
    if x.isdigit() and y in '元圓年月日時分秒億萬仟佰拾':
        return ''
    if x in '年月日時分秒' and y.isdigit():
        return ''
    return ' '


def sort_boxes_and_merge(results: List[Dict[str, Any]]) -> str:
    if not results:
        return ''

    def geometry(row):
        x1, y1, x2, y2 = box_bounds(row.get('box'))
        return ((x1 + x2) / 2, (y1 + y2) / 2, max(1., y2 - y1))

    # A large heading must not set the row tolerance for every small body line.
    # Compare with each line's local median, rather than a page-wide mean.
    lines = []
    for row in sorted(results, key=lambda r: geometry(r)[1]):
        _, cy, height = geometry(row)
        best, distance = None, float('inf')
        for line in lines:
            line_y = median(geometry(r)[1] for r in line)
            line_h = median(geometry(r)[2] for r in line)
            delta = abs(cy - line_y)
            if delta < min(height, line_h) * .6 and delta < distance:
                best, distance = line, delta
        if best is None:
            lines.append([row])
        else:
            best.append(row)

    paragraphs, previous_y, previous_h = [], None, None
    for line in sorted(lines, key=lambda ln: median(geometry(r)[1] for r in ln)):
        line.sort(key=lambda r: geometry(r)[0])
        cy = median(geometry(r)[1] for r in line)
        height = median(geometry(r)[2] for r in line)
        if previous_y is not None and cy - previous_y > max(height, previous_h) * 2:
            paragraphs.append('')
        line_text = line[0].get('text', '')
        for left, right in zip(line, line[1:]):
            line_text += boundary_separator(left, right) + right.get('text', '')
        paragraphs.append(line_text)
        previous_y, previous_h = cy, height
    return '\n'.join(paragraphs)


def calculate_avg_confidence(results: List[Dict]) -> float:
    if not results:
        return 0.0
    confs = [r.get('confidence', 0.0) for r in results if r.get('confidence', 0) > 0]
    return sum(confs) / len(confs) if confs else 0.0
