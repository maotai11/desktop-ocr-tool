"""Expose bounded visual alternatives from passes that already ran.

This module never performs inference or selects a corrected transcript. Only
one-to-one, substantially coextensive boxes are offered as line alternatives;
split/merged/partial readings remain in the complete pass provenance instead.
"""
import math
from numbers import Real


MAX_ROWS = 512
MAX_ALTERNATIVES = 8
MAX_TEXT_LENGTH = 2048


def _valid_rows(rows):
    if not isinstance(rows, list) or len(rows) > MAX_ROWS:
        return []
    valid = []
    for row in rows:
        try:
            text, box = row['raw_text'], row['box']
            if isinstance(row['confidence'], bool):
                continue
            confidence = float(row['confidence'])
            if (not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT_LENGTH
                    or not math.isfinite(confidence) or not .1 < confidence <= 1):
                continue
            if len(box) == 4 and all(isinstance(v, Real) and not isinstance(v, bool) for v in box):
                x1, y1, x2, y2 = box
                box = [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]
            if len(box) != 4 or any(len(p) != 2 for p in box):
                continue
            if not all(isinstance(v, Real) and not isinstance(v, bool) and math.isfinite(v)
                       for p in box for v in p):
                continue
            bounds = (min(p[0] for p in box), min(p[1] for p in box),
                      max(p[0] for p in box), max(p[1] for p in box))
            area = (bounds[2]-bounds[0]) * (bounds[3]-bounds[1])
            if area <= 0:
                continue
            valid.append({'raw_text': text, 'box': [list(p) for p in box],
                          'confidence': confidence, 'bounds': bounds, 'area': area})
        except (TypeError, ValueError, KeyError, IndexError, OverflowError):
            continue
    return valid


def validated_alternatives(items):
    """Copy only bounded visual-pass metadata; malformed evidence is optional."""
    if not isinstance(items, list) or len(items) > MAX_ALTERNATIVES:
        return []
    valid = []
    for item in items:
        if (not isinstance(item, dict) or item.get('source') != 'visual_retry'
                or item.get('pass_name') not in ('second_pass', 'third_pass')):
            continue
        rows = _valid_rows([item])
        if rows:
            row = rows[0]
            valid.append({key: row[key] for key in ('raw_text', 'box', 'confidence')} |
                         {'source': 'visual_retry', 'pass_name': item['pass_name']})
    return valid


def _overlaps(left, right):
    a, b = left['bounds'], right['bounds']
    intersection = max(0, min(a[2], b[2])-max(a[0], b[0])) * max(0, min(a[3], b[3])-max(a[1], b[1]))
    return intersection / min(left['area'], right['area']) >= .6


def collect_retry_candidates(first, second, pass_name):
    """Return review evidence without changing full-pass recovery/selection."""
    if pass_name not in ('second_pass', 'third_pass'):
        return []
    prior, current = _valid_rows(first), _valid_rows(second)
    links = [[j for j, b in enumerate(current) if _overlaps(a, b)] for a in prior]
    reverse = [[] for _ in current]
    for i, targets in enumerate(links):
        for j in targets:
            reverse[j].append(i)
    evidence = []
    for i, targets in enumerate(links):
        if len(targets) != 1 or len(reverse[targets[0]]) != 1:
            continue
        a, b = prior[i], current[targets[0]]
        if (a['raw_text'] == b['raw_text']
                or not .8 <= b['area'] / a['area'] <= 1.25):
            continue
        evidence.append({'box': a['box'], 'original_text': a['raw_text'],
            'alternatives': [{'raw_text': b['raw_text'], 'confidence': b['confidence'],
                              'box': b['box'], 'source': 'visual_retry', 'pass_name': pass_name}]})
        if len(evidence) >= MAX_ALTERNATIVES:
            break
    return evidence
