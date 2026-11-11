"""Bounded, side-effect-free searches in OCR *line* boxes.

Numeric values are atomic: notation is protected, and one-digit differences
are unverified candidates only. Boxes remain in B-crop pixels; a substring
does not provide a character-level box.
"""
from __future__ import annotations

import json
import math
import re
import unicodedata
from difflib import SequenceMatcher
from hashlib import blake2b

from .date_matching import find_dates, parse_date


MAX_FIELD_CHARS = 512
MAX_LINE_CHARS = 2048
MAX_DETAIL_ROWS = 2048
MAX_DETAIL_CHARS = 100_000
MAX_CANDIDATES = 64
MAX_FUZZY_WORK = 1_000_000
_TRUSTED = frozenset(('done', 'confirmed'))
_PREFIX = frozenset('+-−﹣－＋(（')
_SEPARATOR = frozenset('.,，．٬٫\'’_/:+-−﹣－＋')
_SUFFIX = frozenset('%％﹪٪‰‱')


def _number(char):
    return unicodedata.category(char).startswith('N')


def _prefix(char):
    return char in _PREFIX or unicodedata.category(char) in ('Sc', 'Pd')


def _separator(char):
    return char in _SEPARATOR or unicodedata.category(char) == 'Pd'


def _suffix(char):
    return char in _SUFFIX or unicodedata.category(char) == 'Sc'


def _tokenize(text, mode):
    """Keep source spans for safe Western-word/identifier boundaries."""
    if mode != 'exact':
        text = unicodedata.normalize('NFC', text)
    atoms = []
    spans = []
    index = 0
    while index < len(text):
        start = index
        while index < len(text) and _prefix(text[index]):
            index += 1
        # A separated sign/currency still belongs to the number. Keeping the
        # intervening whitespace is conservative: "- 100" cannot become 100.
        if index > start:
            while index < len(text) and (text[index].isspace() or _prefix(text[index])):
                index += 1
        if index < len(text) and text[index] in '.,，．٫' and index + 1 < len(text) and _number(text[index + 1]):
            index += 1
        if index < len(text) and _number(text[index]):
            index += 1
            while index < len(text):
                if _number(text[index]):
                    index += 1
                elif (_separator(text[index]) and index + 1 < len(text)
                      and _number(text[index + 1])):
                    index += 2
                elif text[index].isspace() or _separator(text[index]):
                    end = index
                    while end < len(text) and (text[end].isspace() or _separator(text[end])):
                        end += 1
                    between = text[index:end]
                    if (end < len(text) and _number(text[end])
                            and any(_separator(char) for char in between)):
                        index = end + 1
                    else:
                        break
                elif text[index] in 'eE' and index + 1 < len(text):
                    end = index + 1
                    if text[end] in '+-−':
                        end += 1
                    if end < len(text) and _number(text[end]):
                        index = end + 1
                    else:
                        break
                else:
                    break
            while index < len(text) and _suffix(text[index]):
                index += 1
            suffix = index
            while suffix < len(text) and text[suffix].isspace():
                suffix += 1
            if suffix < len(text) and _suffix(text[suffix]):
                index = suffix + 1
            if text[start:start + 1] in ('(', '（') and index < len(text) and text[index] in ')）':
                index += 1
            atoms.append(('number', text[start:index]))
            spans.append((start, index))
            continue
        index = start
        char = text[index]
        if mode == 'exact' or unicodedata.category(char)[0] in ('L', 'M'):
            atoms.append(char)
            spans.append((index, index + 1))
        index += 1
    return text, tuple(atoms), spans


def _atoms(text, mode):
    return _tokenize(text, mode)[1]


def _word_character(char):
    """CJK may be embedded in unspaced sentences; Western IDs may not."""
    category = unicodedata.category(char)[0]
    if char == '_' or category in ('N', 'M'):
        return True
    return category == 'L' and not unicodedata.name(char, '').startswith(
        ('CJK ', 'IDEOGRAPHIC ', 'HIRAGANA ', 'KATAKANA ', 'HANGUL '))


def _word_atom(atom):
    return isinstance(atom, tuple) or _word_character(atom)


def _boundaries(query, text, spans, start, width):
    left, right = spans[start][0], spans[start + width - 1][1]
    if _word_atom(query[0]) and left > 0 and _word_character(text[left - 1]):
        return False
    return not (_word_atom(query[-1]) and right < len(text) and _word_character(text[right]))


def _numeric_signature(atoms):
    return tuple(atom[1] for atom in atoms if isinstance(atom, tuple))


def _meaningful(text):
    return any(unicodedata.category(char)[0] in ('L', 'N') for char in text)


def _occurrences(needle, haystack):
    # KMP keeps repeated text (e.g. hundreds of identical digits/letters)
    # linear instead of slicing/re-comparing every potential substring.
    prefix = [0] * len(needle)
    matched = 0
    for index in range(1, len(needle)):
        while matched and needle[index] != needle[matched]:
            matched = prefix[matched - 1]
        if needle[index] == needle[matched]:
            matched += 1
        prefix[index] = matched
    positions = []
    matched = 0
    for index, atom in enumerate(haystack):
        while matched and atom != needle[matched]:
            matched = prefix[matched - 1]
        if atom == needle[matched]:
            matched += 1
        if matched == len(needle):
            positions.append(index - len(needle) + 1)
            matched = prefix[matched - 1]
    return positions


def _box(value):
    try:
        if len(value) != 4 or any(len(point) != 2 for point in value):
            return None
        result = [[float(number) for number in point] for point in value]
        if any(not math.isfinite(number) or not 0 <= number <= 16_000_000
               for point in result for number in point):
            return None
        xs, ys = zip(*result)
        if max(xs) <= min(xs) or max(ys) <= min(ys):
            return None
        return result
    except (TypeError, ValueError, OverflowError):
        return None


def _confidence(value):
    try:
        number = float(value)
        return number if math.isfinite(number) and 0 < number <= 1 else 0.0
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _similarity(query, line, threshold, budget, source, spans):
    """Try a bounded set of substrings aligned by shared text blocks.

    Numeric tokens must match exactly and in order. Resource limits never
    turn an unfinished search into a verified match or definite absence.
    """
    work = len(query) * len(line)
    if work > budget:
        return 0.0, budget, True
    budget -= work
    matcher = SequenceMatcher(None, query, line, autojunk=False)
    length = len(query)
    slack = min(16, max(1, math.ceil(length * (1 - threshold) * 2)))
    starts = {0}
    blocks = sorted(matcher.get_matching_blocks(), key=lambda block: block.size, reverse=True)
    for block in blocks[:4]:
        if block.size:
            starts.add(max(0, block.b - block.a))
    expected_numbers = _numeric_signature(query)
    best = 0.0
    for start in sorted(starts):
        for width in range(max(1, length - slack), length + slack + 1):
            candidate = line[start:start + width]
            if (len(candidate) != width or _numeric_signature(candidate) != expected_numbers
                    or not _boundaries(query, source, spans, start, width)):
                continue
            work = len(query) * len(candidate)
            if work > budget:
                return best, budget, True
            budget -= work
            score = SequenceMatcher(None, query, candidate, autojunk=False).ratio()
            best = max(best, score)
    return best, budget, False


def _decimal_signature(value):
    """Return digits plus protected notation for conservative near candidates."""
    body = value
    if body.startswith('(') and body.endswith(')'):
        body = body[1:-1]
    while body and (body[0] in '+-−' or unicodedata.category(body[0]) == 'Sc'):
        body = body[1:]
    while body and (body[-1] == '%' or unicodedata.category(body[-1]) == 'Sc'):
        body = body[:-1]
    if not re.fullmatch(r'(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.[0-9]+)?', body):
        return None
    digits = ''.join(char for char in body if '0' <= char <= '9')
    if len(digits) < 2:
        return None
    integer = body.split('.')[0].replace(',', '')
    leading_zeroes = len(integer) - len(integer.lstrip('0') or '0')
    shape = ''.join('#' if '0' <= char <= '9' else char for char in value)
    return digits, shape, leading_zeroes


def _numeric_similarity(query, atoms, source, spans):
    if len(query) != 1 or not isinstance(query[0], tuple):
        return 0.0, 0
    signature = _decimal_signature(query[0][1])
    if signature is None:
        return 0.0, 0
    digits, shape, leading = signature
    occurrences = 0
    for index, atom in enumerate(atoms):
        if not isinstance(atom, tuple) or not _boundaries(query, source, spans, index, 1):
            continue
        other = _decimal_signature(atom[1])
        if other is None or other[1:] != (shape, leading) or len(other[0]) != len(digits):
            continue
        if sum(a != b for a, b in zip(digits, other[0])) == 1:
            occurrences += 1
    return ((len(digits) - 1) / len(digits) if occurrences else 0.0), occurrences


def match_field(a_result, b_result, *, threshold=0.95, selected_id=None, mode='normalized',
                date_equivalence=True):
    """Find a verified value or explicitly unverified near candidate in B.

    Scores measure text/digit agreement, never OCR certainty. Decimal numbers
    differing in one digit may be candidates only; equivalent ROC/Gregorian
    dates are exact calendar values. All boxes come from current B OCR rows.
    """
    if mode not in ('normalized', 'exact'):
        raise ValueError('mode must be normalized or exact')
    if type(date_equivalence) is not bool:
        raise ValueError('date_equivalence must be a boolean')
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not 0.80 <= threshold <= 1:
        raise ValueError('threshold must be between 0.80 and 1.00')
    if not isinstance(a_result, dict) or not isinstance(b_result, dict):
        raise TypeError('OCR results must be dictionaries')
    text = a_result.get('text', '')
    text = text if isinstance(text, str) else ''
    output = {'status': 'uncertain', 'text': text[:MAX_FIELD_CHARS], 'candidates': [],
              'selected_id': None, 'score': None, 'reason': ''}
    if not _meaningful(text) or len(text) > MAX_FIELD_CHARS:
        output['reason'] = '輸入欄位沒有可核對的文字，或文字過長，請縮小範圍。'
        return output
    query = _atoms(text, mode)
    if not query:
        output['reason'] = '輸入欄位沒有可核對的文字。'
        return output
    query_date = parse_date(text) if mode != 'exact' and date_equivalence else None
    details = b_result.get('detail')
    if not isinstance(details, (list, tuple)):
        output['reason'] = '發票未提供可定位的文字行，請重新辨識。'
        return output
    exact_candidates, near_candidates = [], []
    overflow_exact = overflow_near = False
    seen = set()
    truncated = len(details) > MAX_DETAIL_ROWS
    budget = MAX_FUZZY_WORK
    total_chars = 0
    a_review = (a_result.get('status') not in _TRUSTED
                or ('confidence' in a_result and _confidence(a_result['confidence']) == 0))
    b_review = (b_result.get('status') not in _TRUSTED
                or ('confidence' in b_result and _confidence(b_result['confidence']) == 0))
    for row in details[:MAX_DETAIL_ROWS]:
        if not isinstance(row, dict):
            truncated = True
            continue
        line = row.get('text')
        box = _box(row.get('box'))
        if not isinstance(line, str) or not line or box is None:
            truncated = True
            continue
        total_chars += len(line)
        if total_chars > MAX_DETAIL_CHARS:
            truncated = True
            break
        if len(line) > MAX_LINE_CHARS:
            truncated = True
            continue
        source, atoms, spans = _tokenize(line, mode)
        occurrences = [start for start in _occurrences(query, atoms)
                       if _boundaries(query, source, spans, start, len(query))]
        count = len(occurrences)
        kind, similarity_kind = 'exact', 'literal'
        score = 1.0 if occurrences else 0.0
        if query_date is not None:
            equivalent_dates = [date for date in find_dates(line)
                                if date.canonical == query_date.canonical]
            # Unsupported eras/invalid full dates may contain a literal
            # numeric substring; they must not bypass date validation.
            if not equivalent_dates:
                continue
            count = len(equivalent_dates)
            score = 1.0
            literal_date = False
            for date in equivalent_dates:
                date_source, date_atoms, date_spans = _tokenize(date.text, mode)
                if any(_boundaries(query, date_source, date_spans, start, len(query))
                       for start in _occurrences(query, date_atoms)):
                    literal_date = True
                    break
            if not literal_date:
                kind, similarity_kind = 'date_equivalent', 'calendar'
        limited = False
        if not score and mode != 'exact' and threshold < 1:
            score, budget, limited = _similarity(query, atoms, threshold, budget, source, spans)
            kind, similarity_kind = 'similar', 'text'
        numeric_score, numeric_count = (0.0, 0)
        if not count and mode != 'exact' and threshold < 1:
            numeric_score, numeric_count = _numeric_similarity(query, atoms, source, spans)
            if numeric_score:
                score, count = numeric_score, numeric_count
                kind, similarity_kind = 'similar', 'numeric'
        truncated |= limited
        if not numeric_score and score < threshold:
            continue
        candidate_id = blake2b(json.dumps([line, box], ensure_ascii=False,
                                         separators=(',', ':')).encode('utf-8'), digest_size=12).hexdigest()
        if candidate_id in seen:
            continue
        seen.add(candidate_id)
        confidence = _confidence(row.get('confidence'))
        review = a_review or b_review or confidence == 0 or row.get('status', 'done') not in _TRUSTED
        candidate = {'id': candidate_id, 'text': line, 'boxes': [box],
                     'confidence': confidence, 'score': round(score, 6),
                     'match_type': kind, 'similarity_kind': similarity_kind,
                     'needs_review': review or count > 1 or kind == 'similar',
                     'position_kind': 'line', 'occurrences': count}
        if kind in ('exact', 'date_equivalent'):
            if len(exact_candidates) < MAX_CANDIDATES:
                exact_candidates.append(candidate)
            else:
                overflow_exact = True
        elif len(near_candidates) < MAX_CANDIDATES:
            near_candidates.append(candidate)
        else:
            overflow_near = True
    # Search all bounded rows before choosing the class: a late exact value
    # must not be hidden by earlier near values, nor made ambiguous by them.
    candidates = exact_candidates or near_candidates
    truncated |= overflow_exact if exact_candidates else overflow_near
    candidates.sort(key=lambda item: (-item['score'], item['boxes'][0][0][1], item['boxes'][0][0][0], item['id']))
    output['candidates'] = candidates
    selected = next((item for item in candidates if item['id'] == selected_id), None)
    if selected_id is not None and selected is None:
        output['reason'] = '原先候選已失效，請重新選擇。'
        return output
    if not candidates:
        output['status'] = 'uncertain' if a_review or b_review or truncated else 'missing'
        output['reason'] = ('辨識結果需要確認，尚無可確定的位置。' if output['status'] == 'uncertain'
                            else '發票中沒有找到符合的數值或文字行。')
        return output
    if selected is None and (len(candidates) > 1 or candidates[0]['occurrences'] > 1):
        output['status'] = 'ambiguous'
        output['reason'] = '找到多個可能位置，請選擇候選；框線只精確到文字行。'
        return output
    selected = selected or candidates[0]
    output['selected_id'], output['score'] = selected['id'], selected['score']
    if selected['needs_review'] or selected['match_type'] not in ('exact', 'date_equivalent') or truncated:
        output['reason'] = '近似數值、文字、辨識信心或搜尋完整性需要人工確認。'
        return output
    output['status'] = 'matched'
    output['reason'] = ('日期格式或民國／西元換算後為同一天。' if selected['match_type'] == 'date_equivalent'
                        else '找到符合的文字行。')
    return output
