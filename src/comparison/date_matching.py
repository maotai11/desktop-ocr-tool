"""Conservative ROC/Gregorian dates with spans in the original OCR text.

This module recognises complete dates, never guesses a missing century or day,
and does not modify the OCR text. The caller decides whether calendar-equivalent
dates may match; literal comparison must not call this conversion implicitly.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import re
import unicodedata

MAX_DATE_TEXT_CHARS = 2048
_SPACE = r'[ \t\u3000]{0,8}'
_DATE = re.compile(
    rf'(?:(?P<era>中華民國|民國|民国|西元|公元){_SPACE}(?:[:：]{_SPACE})?)?'
    rf'(?P<year>[0-9]{{1,4}}){_SPACE}'
    rf'(?:'
    rf'(?P<separator>[/\-]){_SPACE}(?P<month>[0-9]{{1,2}}){_SPACE}'
    rf'(?P=separator){_SPACE}(?P<day>[0-9]{{1,2}})'
    rf'|年{_SPACE}(?P<zh_month>[0-9]{{1,2}}){_SPACE}月{_SPACE}'
    rf'(?P<zh_day>[0-9]{{1,2}}){_SPACE}日'
    rf')'
)
# Each replacement has exactly one code point, so OCR box/span provenance is
# unchanged. General NFKC would also rewrite ligatures and other identifiers.
_FULLWIDTH = str.maketrans('０１２３４５６７８９／－', '0123456789/-')
_ROC_ERAS = frozenset(('中華民國', '民國', '民国'))
_SIGNS = frozenset('+-−﹣－＋﹢±')
_ADJACENT_PUNCTUATION = frozenset('_/\\.,:%％‰‱／．，：') | _SIGNS
_ERA_SUFFIXES = (
    '中華民國', '民國', '民国', '西元', '公元', '民國前', '民国前',
    '西元前', '公元前', '紀元前', '纪元前', '公曆', '公历', '西曆', '西历',
    '國曆', '国历', '農曆', '农历', '陰曆', '阴历', '佛曆', '佛历', '回曆', '回历',
)
_CONTEXT_SEPARATORS = ' \t\r\n\u3000:：,，;；()（）[]【】{}'
_ERA_LABELS = ('日期', '年份', '年分', '年度')
_LATIN_ERA = re.compile(
    r'(?<![A-Za-z0-9_])(?:A\.?[ \t]{0,2}D\.?|'
    r'B\.?[ \t]{0,2}C\.?(?:[ \t]{0,2}E\.?)?|'
    r'C\.?[ \t]{0,2}E\.?|R\.?[ \t]{0,2}O\.?[ \t]{0,2}C\.?)$',
    re.IGNORECASE,
)
_FULLWIDTH_ERA = str.maketrans({chr(code + 0xFEE0): chr(code)
                              for code in range(ord('A'), ord('Z') + 1)}
                             | {chr(code + 0xFEE0): chr(code)
                                for code in range(ord('a'), ord('z') + 1)}
                             | {'．': '.'})


@dataclass(frozen=True, slots=True)
class DateToken:
    start: int
    end: int
    text: str
    canonical: date
    calendar: str

    @property
    def iso_date(self) -> str:
        return self.canonical.isoformat()


def _identifier_character(char: str) -> bool:
    category = unicodedata.category(char)
    if category[0] in ('N', 'M') or category in ('Sc', 'Pd', 'Cf'):
        return True
    if char in _ADJACENT_PUNCTUATION:
        return True
    # Chinese labels commonly touch a date (e.g. 開立日期115/10/12).
    # Western words/IDs must have a real boundary instead.
    return category[0] == 'L' and not unicodedata.name(char, '').startswith(
        ('CJK ', 'IDEOGRAPHIC ', 'HIRAGANA ', 'KATAKANA ', 'HANGUL '))


def _canonical(match: re.Match) -> tuple[date, str] | None:
    era, year_text = match.group('era', 'year')
    if era in _ROC_ERAS:
        if len(year_text) > 3:
            return None
        calendar = 'roc'
    elif era is not None:
        if len(year_text) != 4 or year_text.startswith('0'):
            return None
        calendar = 'gregorian'
    elif len(year_text) == 3 and not year_text.startswith('0'):
        calendar = 'roc'
    elif len(year_text) == 4 and not year_text.startswith('0'):
        calendar = 'gregorian'
    else:
        return None
    year = int(year_text)
    if year == 0:
        return None
    if calendar == 'roc':
        year += 1911
    month = int(match.group('month') or match.group('zh_month'))
    day = int(match.group('day') or match.group('zh_day'))
    try:
        return date(year, month, day), calendar
    except ValueError:
        return None


def _unconsumed_era(before: str) -> bool:
    """An era label separated from its year cannot silently switch calendars."""
    context = before.rstrip(_CONTEXT_SEPARATORS)
    for _ in range(2):
        label = next((word for word in _ERA_LABELS if context.endswith(word)), None)
        if label is None:
            break
        context = context[:-len(label)].rstrip(_CONTEXT_SEPARATORS)
    return (context.endswith(_ERA_SUFFIXES)
            or _LATIN_ERA.search(context.translate(_FULLWIDTH_ERA)) is not None)


def find_dates(text: str) -> tuple[DateToken, ...]:
    """Find complete valid dates without matching a slice of a numeric ID.

    Bare three-digit years mean ROC; bare one/two-digit years are ambiguous and
    are not converted. A ROC prefix allows one to three digits (including
    explicit zero-padding); a Gregorian year must contain four digits. An
    explicit supported era may be followed by a colon. Only
    Y/M/D, Y-M-D and Y年M月D日 are supported. Input over the bound is rejected,
    not truncated into a potentially different valid date.
    """
    if not isinstance(text, str) or len(text) > MAX_DATE_TEXT_CHARS:
        return ()
    normalized = text.translate(_FULLWIDTH)
    tokens = []
    for match in _DATE.finditer(normalized):
        start, end = match.span()
        if ((start and normalized[start - 1] not in ':：'
             and _identifier_character(normalized[start - 1]))
                or (end < len(normalized) and _identifier_character(normalized[end]))):
            continue
        before, after = normalized[:start].rstrip(), normalized[end:].lstrip()
        # A malformed era or a separated sign must not be discarded to obtain
        # a valid-looking bare date in another calendar. Date ranges/compound
        # numeric identifiers also need explicit handling, not substring guesses.
        if _unconsumed_era(before):
            continue
        if before and (before[-1] in _SIGNS or before[-1] in '/\\／'
                       or unicodedata.category(before[-1]) in ('Sc', 'Pd')):
            continue
        if after and (after[0] in _SIGNS or after[0] in '/\\／%％‰‱'
                      or unicodedata.category(after[0]) in ('Sc', 'Pd')):
            continue
        canonical = _canonical(match)
        if canonical is not None:
            value, calendar = canonical
            tokens.append(DateToken(start, end, text[start:end], value, calendar))
    return tuple(tokens)


def parse_date(text: str) -> DateToken | None:
    """Return a date only if the entire OCR value is one date plus whitespace."""
    tokens = find_dates(text)
    if len(tokens) != 1:
        return None
    token = tokens[0]
    return token if not text[:token.start].strip() and not text[token.end:].strip() else None
