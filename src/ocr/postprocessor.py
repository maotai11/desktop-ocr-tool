# -*- coding: utf-8 -*-
import logging
from typing import List, Dict, Any, Optional

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
    return ('\u3400' <= char <= '\u9fff') or ('\U00020000' <= char <= '\U000323af')


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
    x,y = a[-1],b[0]
    if y in '，。；：！？、,.!?:;%％)]}）】」』' or x in '([{（【「『$＄￥¥':
        return ''
    if x in '/.-,' and y.isdigit() or x.isdigit() and y in '/.-,':
        return ''
    if a.endswith(('NT$', 'US$', 'NT＄')) and y.isdigit():
        return ''
    if x.isdigit() and y.isdigit():
        return '' if gap <= height * 0.25 else ' '
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

    def get_center_y(r):
        box = r.get('box', [])
        if not box:
            return 0
        if isinstance(box[0], (list, tuple)):
            return sum(p[1] for p in box) / len(box)
        return (box[1] + box[3]) / 2 if len(box) >= 4 else 0

    def get_center_x(r):
        box = r.get('box', [])
        if not box:
            return 0
        if isinstance(box[0], (list, tuple)):
            return sum(p[0] for p in box) / len(box)
        return (box[0] + box[2]) / 2 if len(box) >= 4 else 0

    heights = []
    for r in results:
        box = r.get('box', [])
        if box and len(box) >= 4 and isinstance(box[0], (list, tuple)):
            ys = [p[1] for p in box]
            heights.append(max(ys) - min(ys))
    avg_height = sum(heights) / len(heights) if heights else 20

    sorted_results = sorted(results, key=get_center_y)
    lines = []
    current_line = []
    current_y = None

    for r in sorted_results:
        cy = get_center_y(r)
        if current_y is None:
            current_y = cy
            current_line.append(r)
        elif abs(cy - current_y) < avg_height * 0.7:
            current_line.append(r)
        else:
            lines.append(sorted(current_line, key=get_center_x))
            current_line = [r]
            current_y = cy
    if current_line:
        lines.append(sorted(current_line, key=get_center_x))

    paragraphs = []
    prev_y = None
    for line in lines:
        cy = get_center_y(line[0])
        if prev_y is not None and abs(cy - prev_y) > avg_height * 2:
            paragraphs.append('')
        line_text = line[0].get('text', '')
        for previous, current in zip(line, line[1:]):
            line_text += boundary_separator(previous, current) + current.get('text', '')
        paragraphs.append(line_text)
        prev_y = cy

    return '\n'.join(paragraphs)


def calculate_avg_confidence(results: List[Dict]) -> float:
    if not results:
        return 0.0
    confs = [r.get('confidence', 0.0) for r in results if r.get('confidence', 0) > 0]
    return sum(confs) / len(confs) if confs else 0.0
