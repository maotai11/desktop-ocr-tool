"""Calendar conversion must not repair OCR or invent dates from identifiers."""
from datetime import date

import pytest

from src.comparison.date_matching import MAX_DATE_TEXT_CHARS, find_dates, parse_date


@pytest.mark.parametrize('value', [
    '民國115年10月12日', '民國115/10/12', '民國 115 - 10 - 12',
    '中華民國115年10月12日', '民国115/10/12', '115/10/12',
    '115-10-12', '115年10月12日', '西元2026/10/12', '公元2026-10-12',
    '2026-10-12', '2026/10/12', '2026年10月12日',
    '民國１１５／１０／１２', '２０２６－１０－１２',
    '民國：115/10/12', '西元:2026/10/12', '民國 ： 115年10月12日',
    '中華民國:115-10-12', '公元：２０２６／１０／１２',
])
def test_complete_roc_and_gregorian_dates_resolve_to_same_day(value):
    token = parse_date(value)
    assert token is not None
    assert token.canonical == date(2026, 10, 12)
    assert token.iso_date == '2026-10-12'
    assert token.text == value[token.start:token.end] == value


@pytest.mark.parametrize(('value', 'expected'), [
    ('民國1/1/1', date(1912, 1, 1)),
    ('民國001/01/01', date(1912, 1, 1)),
    ('民國99/1/1', date(2010, 1, 1)),
    ('民國099/1/1', date(2010, 1, 1)),
    ('民國999/12/31', date(2910, 12, 31)),
    ('113/2/29', date(2024, 2, 29)),
    ('民國89年2月29日', date(2000, 2, 29)),
    ('2024/2/29', date(2024, 2, 29)),
])
def test_conversion_uses_actual_gregorian_calendar(value, expected):
    assert parse_date(value).canonical == expected


@pytest.mark.parametrize('value', [
    '115/10/13', '115/11/12', '114/10/12', '2025/10/12',
])
def test_a_different_day_does_not_become_equivalent(value):
    assert parse_date(value).canonical != parse_date('2026/10/12').canonical


@pytest.mark.parametrize('value', [
    '民國0/1/1', '000/1/1', '0000/1/1', '2026/0/1', '2026/13/1',
    '2026/1/0', '2026/1/32', '2026/4/31', '115/2/29', '1900/2/29',
    '民國99/2/29', '2026/10', '2026/10/', '10/12', '26/10/12', '99/1/1',
    '099/1/1', '1/1/1', '0115/10/12', '西元115/10/12', '民國2026/10/12',
    '115/10-12', '2026-10/12', '2026年10月12', '二〇二六年十月十二日',
    '2026.10.12', '2026/1O/12', '2026/10/1Z', '民國前115/10/12',
    '-115/10/12', '+2026/10/12', '2026/10/12%', '2026/10/12元整',
])
def test_ambiguous_partial_invalid_or_unsupported_values_are_not_dates(value):
    assert parse_date(value) is None


@pytest.mark.parametrize('value', [
    'INV2026/10/12', 'X115/10/12', '2026/10/12ABC', '2026/10/12_001',
    '12026/10/12', '2026/10/123', '2026/10/12/001', '001-2026/10/12',
    '２０２６／１０／１２Ａ', '2026/10/12T09:30:00', '2026/10/12€',
    '€2026/10/12', '2026/10/12\u0301', '²2026/10/12',
])
def test_embedded_identifiers_and_numeric_extensions_are_not_scanned(value):
    assert find_dates(value) == ()


def test_scanning_preserves_original_unicode_text_and_positions():
    text = '開立日期：民國１１５／１０／１２；到期日2026-11-12。'
    tokens = find_dates(text)
    assert [token.canonical for token in tokens] == [date(2026, 10, 12), date(2026, 11, 12)]
    assert [token.calendar for token in tokens] == ['roc', 'gregorian']
    assert [text[token.start:token.end] for token in tokens] == ['民國１１５／１０／１２', '2026-11-12']
    assert all(text[token.start:token.end] == token.text for token in tokens)
    assert parse_date(text) is None


def test_outer_whitespace_is_allowed_without_losing_original_spans():
    text = '\t 民國115/10/12 \n'
    token = parse_date(text)
    assert token is not None
    assert token.start == 2
    assert text[token.start:token.end] == token.text == '民國115/10/12'


def test_separate_dates_stay_separate_and_do_not_form_one_value():
    value = '115/10/12 2026/10/12'
    assert len(find_dates(value)) == 2
    assert parse_date(value) is None
    assert find_dates('115/10\n/12') == ()


@pytest.mark.parametrize('value', [None, 20261012, {}, b'2026/10/12', 'x' * (MAX_DATE_TEXT_CHARS + 1)])
def test_non_text_and_excessive_input_is_rejected_without_truncation(value):
    assert find_dates(value) == ()
    assert parse_date(value) is None


def test_bounded_scan_handles_many_prefixes_and_does_not_make_new_dates():
    text = ('民國 / ' * 300) + '；2026/10/12'
    assert len(text) <= MAX_DATE_TEXT_CHARS
    assert find_dates(text)[0].iso_date == '2026-10-12'
    assert find_dates('1' * MAX_DATE_TEXT_CHARS) == ()
    assert find_dates('2026/' + ' ' * 1000 + '10/12') == ()


@pytest.mark.parametrize('value', [
    '民國         2026/10/12', '西元         115/10/12',
    '民國\n2026/10/12', '西元\n115/10/12', '民國前\n115/10/12',
    '民國前 115/10/12', '+ 115/10/12', '- 115/10/12',
    '€ 2026/10/12', '2026/10/12 - 001', '2026/10/12 / 001',
    '−115/10/12', '﹢115/10/12', '− 115/10/12', '115/10/12 ± 1',
])
def test_unconsumed_era_or_sign_cannot_turn_into_a_bare_date(value):
    assert find_dates(value) == ()


@pytest.mark.parametrize('value', [
    '西元前2026/10/12', '公元前 2026年10月12日',
    'AD 115/10/12', 'ROC 2026/10/12', 'BC 2026/10/12', 'BCE 2026/10/12',
    'CE 115/10/12', 'R.O.C. 2026/10/12', 'A.D. 115/10/12',
    '西元：115/10/12', '民國：2026/10/12', '公元前：2026/10/12',
    '西元(115/10/12)', '民國（2026/10/12）', '西元日期：115/10/12',
    '民國年份：2026/10/12', 'ＲＯＣ 2026/10/12', 'ＡＤ：115/10/12',
    '農曆115/10/12', '公曆115/10/12',
])
def test_explicit_unsupported_or_separated_era_is_not_discarded(value):
    assert find_dates(value) == ()


@pytest.mark.parametrize('value', [
    '開立日期115/10/12', '日期：115/10/12', '日期（115/10/12）',
    'Date: 2026/10/12', 'ROAD 2026/10/12', '編號：12345 日期：115/10/12',
])
def test_ordinary_labels_do_not_block_a_bare_date(value):
    assert [token.iso_date for token in find_dates(value)] == ['2026-10-12']
