# -*- coding: utf-8 -*-
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


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
        line_text = ' '.join(r.get('text', '') for r in line)
        paragraphs.append(line_text)
        prev_y = cy

    return '\n'.join(paragraphs)


def calculate_avg_confidence(results: List[Dict]) -> float:
    if not results:
        return 0.0
    confs = [r.get('confidence', 0.0) for r in results if r.get('confidence', 0) > 0]
    return sum(confs) / len(confs) if confs else 0.0
