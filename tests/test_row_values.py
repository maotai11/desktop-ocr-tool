"""Whole-region extraction preserves numbers, review states and real geometry."""
from copy import deepcopy

import pytest

from src.comparison.row_values import extract_source_rows, match_row_values
from src.comparison.search_matching import match_field


def line(text, x=0, y=0, width=300, confidence=.98):
    return {'text': text, 'confidence': confidence,
            'box': [[x, y], [x + width, y], [x + width, y + 20], [x, y + 20]]}


def result(*lines, status='done'):
    return {'status': status, 'confidence': .98, 'detail': list(lines)}


def values(text):
    return extract_source_rows(result(line(text)))['rows'][0]['values']


@pytest.mark.parametrize(('text', 'expected'), [
    ('日期115/10/12 金額12345 87654', ['115/10/12', '12345', '87654']),
    ('1.23 -100 1,000 01', ['1.23', '-100', '1,000', '01']),
    ('100 -200', ['100', '-200']), ('100 +200', ['100', '+200']),
    ('100 − 200', ['100', '− 200']), ('100 (200)', ['100', '(200)']),
    ('$ 100 200%', ['$ 100', '200%']),
    ('民國：115/10/12 FS12345678 12,580', ['民國：115/10/12', 'FS12345678', '12,580']),
    ('AD 2026/10/12 民國2026/10/12', ['AD 2026/10/12', '民國2026/10/12']),
    ('AD2026/10/12', ['AD2026/10/12']),
    ('115 - 02 - 30', ['115 - 02 - 30']),
    ('民國115年2月30日', ['民國115年2月30日']),
])
def test_numeric_columns_dates_and_identifiers_keep_original_content(text, expected):
    extracted = values(text)
    assert [value['text'] for value in extracted] == expected
    assert all(value['source_boxes'] == [line(text)['box']] for value in extracted)
    assert len({value['id'] for value in extracted}) == len(extracted)
    assert all(text[slice(*value['source_span'])] == value['text'] for value in extracted)


def test_visual_rows_group_side_by_side_ocr_boxes_without_fixed_pitch():
    source = result(line('11111', x=0, y=30, width=70), line('22222', x=90, y=31, width=70),
                    line('33333', x=0, y=110, width=70))
    original = deepcopy(source)
    extracted = extract_source_rows(source)
    assert [[v['text'] for v in row['values']] for row in extracted['rows']] == [['11111', '22222'], ['33333']]
    assert extracted['rows'][0]['region'] == (0, 30, 160, 21)
    assert source == original


@pytest.mark.parametrize('status', ['needs_review', 'error', None])
def test_source_review_status_is_never_upgraded(status):
    extracted = extract_source_rows(result(line('12345'), status=status))
    matched = match_row_values(extracted['rows'][0]['values'], result(line('12345')))
    assert matched[0]['status'] == 'uncertain'


def test_truncated_or_malformed_source_forces_review_for_every_extracted_value():
    source = result(line(' '.join(str(10000 + i) for i in range(21))), {'text': 'bad'})
    extracted = extract_source_rows(source)
    assert extracted['truncated'] and len(extracted['rows'][0]['values']) == 20
    assert all(value['status'] == 'needs_review' for value in extracted['rows'][0]['values'])


def test_source_is_bounded_to_100_rows_and_duplicate_ocr_is_not_counted_twice():
    extracted = extract_source_rows(result(*(line(str(10000 + i), y=i * 30) for i in range(101))))
    assert len(extracted['rows']) == 100 and extracted['truncated']
    assert len(extract_source_rows(result(line('12345'), line('12345')))['rows'][0]['values']) == 1


def test_best_near_candidate_is_visible_even_with_multiple_alternatives():
    output = match_row_values(values('12345'), result(line('12346'), line('12399', y=40)))[0]
    assert output['status'] == 'uncertain' and output['selected_id']
    assert output['score'] == .8 and output['provisional']
    assert len(output['candidates']) == 2


def test_same_format_multiple_digit_difference_is_review_only():
    output = match_row_values(values('12345'), result(line('12399')))[0]
    assert output['score'] == .6 and output['status'] == 'uncertain'
    assert match_field({'text': '12345', 'status': 'done'}, result(line('12399')))['status'] == 'missing'


def test_legacy_shared_mode_can_preserve_one_digit_rule_while_showing_one_near_position():
    output = match_row_values(values('12'), result(line('13'), line('14', y=40)),
                              numeric_near_threshold=None)[0]
    assert output['status'] == 'uncertain' and output['selected_id']
    assert output['score'] == .5 and output['provisional']
    assert not match_row_values(values('12'), result(line('13')))[0]['selected_id']


@pytest.mark.parametrize('other', ['-12345', '1234', '123456', '12,345', '123.45', '012345', '12345%'])
def test_row_near_does_not_erase_protected_numeric_format(other):
    assert not match_row_values(values('12345'), result(line(other)))[0]['candidates']


def test_global_exact_is_reserved_before_near_even_if_near_was_manually_requested():
    source = values('12345 12346')
    invoice = result(line('12346'))
    candidate_id = match_row_values(source[:1], invoice)[0]['selected_id']
    output = match_row_values(source, invoice, selected_ids={source[0]['id']: candidate_id})
    assert output[1]['status'] == 'matched'
    assert output[0]['status'] == 'uncertain' and output[0]['selected_id'] is None


def test_same_invoice_position_is_not_used_twice_even_with_different_ocr_text_and_vertex_order():
    a, b = line('12345'), line('98765')
    b['box'] = b['box'][1:] + b['box'][:1]
    output = match_row_values(values('12345 98765'), result(a, b))
    assert sum(row['selected_id'] is not None for row in output) == 1
    assert output[1]['status'] == 'uncertain' and output[1]['selected_id'] is None


def test_slightly_shifted_contradictory_ocr_boxes_share_one_position_and_cannot_be_green():
    output = match_row_values(values('12345 98765'),
                              result(line('12345', width=100), line('98765', x=1, width=100)))
    assert sum(row['selected_id'] is not None for row in output) == 1
    assert all(row['status'] == 'uncertain' for row in output)
    assert '重疊' in output[0]['reason']


def test_distinct_nearby_invoice_lines_are_not_merged_by_overlap_clustering():
    output = match_row_values(values('12345 98765'),
                              result(line('12345', width=100), line('98765', x=5, y=20, width=100)))
    assert all(row['status'] == 'matched' and row['selected_id'] for row in output)


def test_tied_exact_suggestions_are_not_green_until_user_selects_one():
    source = values('12345')
    invoice = result(line('12345'), line('12345', y=40))
    output = match_row_values(source, invoice)[0]
    assert output['status'] == 'ambiguous' and output['selected_id'] is None
    chosen = match_row_values(source, invoice, selected_ids={source[0]['id']: output['candidates'][0]['id']})[0]
    assert chosen['status'] == 'matched'


def test_stale_manual_candidate_does_not_fall_back_to_another_position():
    source = values('12345')
    output = match_row_values(source, result(line('12346')), selected_ids={source[0]['id']: 'old'})[0]
    assert output['status'] == 'uncertain' and output['selected_id'] is None


def test_ambiguous_exact_keeps_manual_choice_without_starving_a_unique_exact():
    # The first line contains both values. A1 can also use line 2, while A2
    # has only line 1. Assign both without inventing character positions.
    source = values('12345 98765')
    invoice = result(line('12345 98765'), line('12345', y=40))
    output = match_row_values(source, invoice)
    assert output[0]['selected_id'] is None
    assert output[0]['status'] == 'ambiguous'
    assert output[1]['status'] == 'matched'


def test_duplicate_manual_pins_never_move_or_reuse_same_invoice_line():
    source = values('12345 12345')
    invoice = result(line('12345'), line('12345', y=40))
    candidate = match_row_values(source[:1], invoice)[0]['candidates'][0]['id']
    output = match_row_values(source, invoice, selected_ids={v['id']: candidate for v in source})
    assert sum(row['selected_id'] is not None for row in output) == 1
    assert output[0]['status'] == 'uncertain' and output[0]['selected_id'] is None
    assert output[1]['selected_id'] == candidate


@pytest.mark.parametrize('bad', [True, '0.6', float('nan'), float('inf'), .49, 1, -1])
def test_invalid_numeric_near_threshold_is_rejected(bad):
    with pytest.raises(ValueError):
        match_field({'text': '12345'}, result(line('12346')), numeric_near_threshold=bad)


def test_newest_exact_manual_pin_reassigns_only_conflicting_invoice_location():
    source = values('12345 12345 7000')
    invoice = result(line('12345'), line('12345', y=40), line('7000', y=80))
    initial = match_row_values(source, invoice)
    shared = initial[0]['candidates'][0]['id']
    independent = initial[2]['candidates'][0]['id']
    pins = {source[0]['id']: shared, source[2]['id']: independent, source[1]['id']: shared}
    output = match_row_values(source, invoice, selected_ids=pins)
    assert output[0]['status'] == 'uncertain' and output[0]['selected_id'] is None
    assert output[1]['status'] == 'matched' and output[1]['selected_id'] == shared
    assert output[2]['status'] == 'matched' and output[2]['selected_id'] == independent
    # Reversing manual recency reverses only the conflicting ownership.
    pins.pop(source[0]['id'])
    pins[source[0]['id']] = shared
    output = match_row_values(source, invoice, selected_ids=pins)
    assert output[0]['status'] == 'matched' and output[0]['selected_id'] == shared
    assert output[1]['status'] == 'uncertain' and output[1]['selected_id'] is None
    assert output[2]['selected_id'] == independent


def test_newest_near_manual_pin_wins_but_remains_unverified():
    source = values('12345 12347')
    invoice = result(line('12346'))
    initial = match_row_values(source, invoice)
    shared = initial[0]['candidates'][0]['id']
    output = match_row_values(source, invoice,
                              selected_ids={source[0]['id']: shared, source[1]['id']: shared})
    assert output[0]['selected_id'] is None and output[0]['status'] == 'uncertain'
    assert output[1]['selected_id'] == shared and output[1]['status'] == 'uncertain'
    assert output[1]['score'] == .8


def test_latest_near_pin_cannot_displace_an_exact_value():
    source = values('12345 12346')
    invoice = result(line('12346'))
    initial = match_row_values(source, invoice)
    shared = initial[1]['candidates'][0]['id']
    output = match_row_values(source, invoice,
                              selected_ids={source[1]['id']: shared, source[0]['id']: shared})
    assert output[1]['selected_id'] == shared and output[1]['status'] == 'matched'
    assert output[0]['selected_id'] is None and output[0]['status'] == 'uncertain'
