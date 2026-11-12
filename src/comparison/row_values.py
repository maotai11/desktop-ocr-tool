"""Bounded numeric-row extraction and exclusive invoice-line suggestions.

All positions are original OCR line polygons. Splitting a line into values
does not manufacture character positions, and suggestions never verify ties.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import blake2b
import json
import re

from .date_matching import find_dates
from .search_matching import (_box, _confidence, _tokenize, _TRUSTED,
                              MAX_DETAIL_CHARS, MAX_DETAIL_ROWS, MAX_FIELD_CHARS,
                              MAX_LINE_CHARS, match_field)

MAX_SOURCE_ROWS = 100
MAX_ROW_VALUES = 20
_ID = re.compile(r'(?<![A-Za-z0-9_])[A-Za-z][A-Za-z0-9_-]*[0-9][A-Za-z0-9_-]*'
                 r'(?:[/.:][A-Za-z0-9]+)*(?![A-Za-z0-9_])')
_COLUMN_BREAK = re.compile(r'(?<=[0-9])[ \t]+(?=[+\-−(（])')
_RAW_DATE = re.compile(r'(?<![A-Za-z0-9_])[0-9]{1,4}[ \t]*(?:'
                       r'[/\-][ \t]*[0-9]{1,2}[ \t]*[/\-][ \t]*[0-9]{1,2}'
                       r'|年[ \t]*[0-9]{1,2}[ \t]*月[ \t]*[0-9]{1,2}[ \t]*日)'
                       r'(?![A-Za-z0-9_])')
_FULLWIDTH = str.maketrans('０１２３４５６７８９／－', '0123456789/-')
_ERA = re.compile(r'(?:中華民國|民國前|民国前|西元前|公元前|紀元前|纪元前|民國|民国|西元|公元|'
                  r'公曆|公历|西曆|西历|國曆|国历|農曆|农历|陰曆|阴历|佛曆|佛历|回曆|回历|'
                  r'(?<![A-Za-z])(?:AD|BC|BCE|CE|ROC))[ \t:：]*$', re.IGNORECASE)


def _identity(value):
    return blake2b(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode(),
                   digest_size=12).hexdigest()


def _rect(box):
    xs, ys = zip(*box)
    return min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)


def _spans(text):
    dates = [(date.start, date.end) for date in find_dates(text)]
    for match in _RAW_DATE.finditer(text.translate(_FULLWIDTH)):
        start, end = match.span()
        if any(start < right and end > left for left, right in dates):
            continue
        era = _ERA.search(text[:start])
        dates.append((era.start() if era is not None else start, end))
    identifiers = [match.span() for match in _ID.finditer(text)]
    reserved = dates + [span for span in identifiers
                        if not any(span[0] < end and span[1] > start for start, end in dates)]
    _, atoms, spans = _tokenize(text, 'exact')
    numeric_spans = []
    for atom, (start, end) in zip(atoms, spans):
        if not isinstance(atom, tuple):
            continue
        boundaries = [start] + [start + match.end() for match in _COLUMN_BREAK.finditer(text[start:end])]
        for left, right in zip(boundaries, boundaries[1:] + [end]):
            token = text[left:right].rstrip()
            numeric_spans.append((left, left + len(token)))
    for start, end in numeric_spans:
        if any(start < right and end > left for left, right in reserved):
            continue
        # Preserve unsupported era context instead of turning its numeric
        # suffix into a supported bare Gregorian/ROC date.
        era = _ERA.search(text[:start])
        if era is not None:
            start = era.start()
        reserved.append((start, end))
    return sorted(set(reserved))


def extract_source_rows(a_result):
    """Return up to 100 visual rows with up to 20 values in each.

    ``region`` and every ``source_boxes`` polygon are crop-local. A line may
    supply several values with the same honest line-level provenance.
    Missing/malformed/truncated input cannot produce verified values.
    """
    if not isinstance(a_result, dict):
        raise TypeError('OCR result must be a dictionary')
    details = a_result.get('detail')
    if not isinstance(details, (list, tuple)):
        return {'rows': [], 'truncated': True, 'reason': '輸入區沒有可定位的文字行。'}
    truncated = len(details) > MAX_DETAIL_ROWS
    lines, seen, chars = [], set(), 0
    for line in details[:MAX_DETAIL_ROWS]:
        if not isinstance(line, dict):
            truncated = True
            continue
        text, box = line.get('text'), _box(line.get('box'))
        if not isinstance(text, str) or not text or box is None or len(text) > MAX_LINE_CHARS:
            truncated = True
            continue
        chars += len(text)
        if chars > MAX_DETAIL_CHARS:
            truncated = True
            break
        key = _identity([text, box])
        if key in seen:
            continue
        seen.add(key)
        lines.append((line, box, _rect(box), _spans(text)))
    lines.sort(key=lambda item: (item[2][1] + item[2][3] / 2, item[2][0]))
    groups = []
    for line in lines:
        if not line[3]:
            continue
        _, _, (x, y, width, height), _ = line
        group = next((group for group in groups
                      if min(y + height, group['reference'][1] + group['reference'][3])
                      - max(y, group['reference'][1]) >= min(height, group['reference'][3]) * .5
                      and abs(y + height / 2 - group['reference'][1] - group['reference'][3] / 2)
                      <= min(height, group['reference'][3]) * .5), None)
        if group is None:
            if len(groups) >= MAX_SOURCE_ROWS:
                truncated = True
                continue
            group = {'reference': (x, y, width, height), 'lines': []}
            groups.append(group)
        group['lines'].append(line)
    rows = []
    parent_review = (a_result.get('status') not in _TRUSTED
                     or ('confidence' in a_result and _confidence(a_result['confidence']) == 0))
    for group in groups:
        values, boxes = [], []
        for line, box, _, spans in sorted(group['lines'], key=lambda item: item[2][0]):
            boxes.append(box)
            for start, end in spans:
                text = line['text'][start:end]
                if len(values) >= MAX_ROW_VALUES or len(text) > MAX_FIELD_CHARS:
                    truncated = True
                    continue
                confidence = _confidence(line.get('confidence'))
                review = parent_review or line.get('status', 'done') not in _TRUSTED or confidence == 0
                detail = deepcopy(line)
                detail['box'] = deepcopy(box)
                detail['text'] = text
                values.append({'id': _identity([line['text'], box, start, end]), 'text': text,
                               'status': 'needs_review' if review else 'done', 'confidence': confidence,
                               'detail': [detail], 'source_boxes': [deepcopy(box)],
                               'source_span': (start, end), 'position_kind': 'line'})
        points = [point for box in boxes for point in box]
        region = _rect(points)
        rows.append({'id': _identity([value['id'] for value in values]), 'region': region,
                     'boxes': boxes, 'values': values})
    rows.sort(key=lambda row: (row['region'][1], row['region'][0]))
    if truncated:
        for row in rows:
            for value in row['values']:
                value['status'] = 'needs_review'
    return {'rows': rows, 'truncated': truncated,
            'reason': '內容過多或部分文字位置無效，請縮小輸入區後重試。' if truncated else ''}


def _position(candidate):
    # Different OCR texts at the same physical line cannot reserve it twice.
    return tuple(sorted(_rect(box) for box in candidate['boxes']))


def _position_groups(outputs):
    """Cluster duplicate OCR detections without shifting any displayed box."""
    keys = sorted({_position(candidate) for output in outputs for candidate in output['candidates']})
    roots = list(range(len(keys)))

    def root(index):
        while roots[index] != index:
            roots[index] = roots[roots[index]]
            index = roots[index]
        return index

    # At most 20×64 positions. OCR currently supplies one polygon per
    # candidate; bounding unions remain conservative if that ever expands.
    rectangles = []
    for key in keys:
        left = min(rect[0] for rect in key)
        top = min(rect[1] for rect in key)
        rectangles.append((left, top, max(rect[0] + rect[2] for rect in key) - left,
                           max(rect[1] + rect[3] for rect in key) - top))
    for index, (x, y, width, height) in enumerate(rectangles):
        for other in range(index):
            ox, oy, ow, oh = rectangles[other]
            intersection = max(0, min(x + width, ox + ow) - max(x, ox)) * max(
                0, min(y + height, oy + oh) - max(y, oy))
            if intersection >= .9 * min(width * height, ow * oh):
                first, second = root(index), root(other)
                roots[max(first, second)] = min(first, second)
    groups = {key: keys[root(index)] for index, key in enumerate(keys)}
    texts = {}
    for output in outputs:
        for candidate in output['candidates']:
            texts.setdefault(groups[_position(candidate)], set()).add(candidate['text'])
    return groups, {group for group, content in texts.items() if len(content) > 1}


def _assign_suggestions(values, outputs, selected_ids, exclusive, groups):
    """Bounded bipartite allocation: reserve exacts, then suggest near values.

    An augmenting path can move an automatic suggestion to another real line
    so a later value is not starved by an earlier greedy choice. Manual pins
    are not moved by automatic allocation; an explicit newer pin wins a
    conflict within the same phase. At most 20 × 64 candidates are visited.
    """
    assigned, reserved = {}, set()
    pin_rank = {field_id: rank for rank, field_id in enumerate(selected_ids)}
    for exact in (True, False):
        indices = [index for index, output in enumerate(outputs) if output['candidates']
                   and (output['candidates'][0]['match_type'] in ('exact', 'date_equivalent')) == exact
                   and not (exact and output['status'] == 'ambiguous'
                            and selected_ids.get(values[index].get('id')) is None)]
        local, pinned = {}, set()
        manual = sorted(indices, key=lambda index: pin_rank.get(values[index].get('id'), -1),
                        reverse=True)
        for index in manual:
            requested = selected_ids.get(values[index].get('id'))
            if requested is None:
                continue
            chosen = next((candidate for candidate in outputs[index]['candidates']
                           if candidate['id'] == requested), None)
            if chosen is not None and (not exclusive or groups[_position(chosen)] not in reserved):
                key = groups[_position(chosen)]
                if not exclusive or key not in local:
                    assigned[index] = chosen
                    local[key] = index
                    pinned.add(index)

        def visit(index, seen):
            for candidate in outputs[index]['candidates']:
                key = groups[_position(candidate)]
                if key in seen or (exclusive and key in reserved):
                    continue
                seen.add(key)
                owner = local.get(key) if exclusive else None
                if owner is None or (owner not in pinned and visit(owner, seen)):
                    assigned[index] = candidate
                    local[key] = index
                    return True
            return False

        automatic = [index for index in indices if selected_ids.get(values[index].get('id')) is None]
        automatic.sort(key=lambda index: (len(outputs[index]['candidates']),
                                          -outputs[index]['candidates'][0]['score'], index))
        for index in automatic:
            visit(index, set())
        if exclusive:
            reserved.update(local)
    return assigned


def match_row_values(values, b_result, *, selected_ids=None, mode='normalized',
                     date_equivalence=True, exclusive=True, numeric_near_threshold=.6):
    """Choose one display suggestion per value, reserving each B line once.

    Exact matches are allocated before near matches. Automatic tie-breaking
    is only a provisional display suggestion and remains uncertain.
    """
    if not isinstance(values, (list, tuple)) or len(values) > MAX_ROW_VALUES:
        raise ValueError('A row must contain at most 20 values')
    if type(exclusive) is not bool:
        raise ValueError('exclusive must be a boolean')
    selected_ids = selected_ids or {}
    outputs = [match_field(value, b_result, mode=mode, date_equivalence=date_equivalence,
                           numeric_near_threshold=numeric_near_threshold) for value in values]
    groups, contradictory = _position_groups(outputs)
    assigned = _assign_suggestions(values, outputs, selected_ids, exclusive, groups)
    for index in range(len(values)):
        output = outputs[index]
        original = output['candidates']
        requested = selected_ids.get(values[index].get('id'))
        selected = assigned.get(index)
        if requested is not None and selected is None:
            output.update(status='uncertain', selected_id=None, score=None,
                          reason='原先候選已變更或已分配給另一個數值，請重新核對。')
            continue
        if selected is None:
            if (requested is None and output['status'] == 'ambiguous' and original
                    and original[0]['match_type'] in ('exact', 'date_equivalent')):
                continue
            if original:
                output.update(status='uncertain', selected_id=None, score=None,
                              reason='符合位置已分配給另一個數值，請人工核對。')
            continue
        resolved = match_field(values[index], b_result, mode=mode, date_equivalence=date_equivalence,
                               numeric_near_threshold=numeric_near_threshold, selected_id=selected['id'])
        provisional = requested is None and (len(original) > 1 or selected['occurrences'] > 1)
        if provisional:
            resolved.update(status='uncertain', reason='暫列最佳候選，仍有其他可能位置，請人工核對。')
        if groups[_position(selected)] in contradictory:
            resolved.update(status='uncertain', reason='發票同一位置有重疊且不同的辨識結果，請人工核對。')
            for candidate in resolved['candidates']:
                if candidate['id'] == selected['id']:
                    candidate['needs_review'] = True
        resolved['provisional'] = provisional or selected['match_type'] == 'similar'
        outputs[index] = resolved
    return outputs
