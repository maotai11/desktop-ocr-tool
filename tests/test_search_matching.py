"""Adversarial searches must never verify a numerically different value."""
import pytest

from src.comparison.search_matching import match_field


def result(text, *, status='done', rows=None):
    return {'text': text, 'status': status, 'confidence': .98, 'detail': rows or []}


def line(text, y=0, confidence=.98):
    return {'text': text, 'confidence': confidence,
            'box': [[0, y], [300, y], [300, y + 20], [0, y + 20]]}


def search(a, b, **kwargs):
    return match_field(result(a), result(b, rows=[line(b)]), **kwargs)


@pytest.mark.parametrize('mode', ['normalized', 'exact'])
@pytest.mark.parametrize(('a', 'b'), [
    ('123', '1234'), ('100', '-100'), ('100', '+100'), ('123', '1.23'),
    ('1.23', '123'), ('1000', '1,000'), ('01', '1'), ('10', '10%'),
    ('10', '10％'), ('100', '−100'), ('100', '- 100'), ('100', '$ 100'),
    ('10', '10 %'), ('23', '1. 23'), ('1', '1e3'), ('1.0', '1.00'),
    ('1', '１'), ('O1', '01'), ('100', '(100)'), ('100', '$100'),
    ('123', '12/3'), ('123', '12-3'), ('123', '12:3'),
    ('100', '–100'), ('100', '—100'), ('100', '（100）'), ('100', '₺100'),
    ('100', '€ -100'), ('10', '10٪'), ('10', '10﹪'), ('5', ',5'),
    ('Ann', 'Anna'), ('Martin', 'Martinez'), ('123', 'INV123'),
    ('ABC', 'ABC123'), ('ABC', 'XABC'), ('ABC', 'ABC_123'),
])
def test_numerical_collisions_do_not_match(a, b, mode):
    assert search(a, b, mode=mode)['status'] == 'missing'


@pytest.mark.parametrize('value', ['-1.23', '+100', '001', '1,234.56', '10%', '1e-3', '１２３', '(100)', '$100'])
def test_number_found_inside_line_without_inventing_token_box(value):
    output = search(value, f'金額 {value} 元')
    assert output['status'] == 'matched'
    assert output['candidates'][0]['boxes'] == [line('')['box']]
    assert output['candidates'][0]['position_kind'] == 'line'


def test_normalized_text_preserves_accents_case_and_full_width():
    assert search('é台北', 'e\u0301 台北')['status'] == 'matched'
    assert search('名稱：測試', '名稱 測試')['status'] == 'matched'
    assert search('É', 'e')['status'] == 'missing'
    assert search('Ａ', 'A')['status'] == 'missing'
    assert search('\u0301', '\u0301')['status'] == 'uncertain'


@pytest.mark.parametrize('mode', ['normalized', 'exact'])
def test_whole_western_words_keep_original_hyphen_space_boundaries(mode):
    assert search('Ann', 'Ann-Marie', mode=mode)['status'] == 'matched'
    assert search('Ann', 'Jane Ann Smith', mode=mode)['status'] == 'matched'
    assert search('台北', '地址台北市', mode=mode)['status'] == 'matched'
    assert search('123', '金額123元', mode=mode)['status'] == 'matched'
    assert search('INV123', 'Code: INV123', mode=mode)['status'] == 'matched'


def test_exact_preserves_non_numeric_spacing_and_punctuation():
    assert search('名稱：測試', '名稱 測試', mode='exact')['status'] == 'missing'
    assert search('é', 'e\u0301', mode='exact')['status'] == 'missing'


def test_duplicate_numbers_require_a_choice_but_other_as_can_reuse_it():
    b_result = result('123\n123', rows=[line('123', 0), line('123', 40)])
    output = match_field(result('123'), b_result)
    assert output['status'] == 'ambiguous'
    assert output['selected_id'] is None
    assert len(output['candidates']) == 2
    chosen = output['candidates'][1]['id']
    for _ in range(2):
        selected = match_field(result('123'), b_result, selected_id=chosen)
        assert selected['status'] == 'matched'
        assert selected['selected_id'] == chosen
    b_result['detail'][1]['box'][0][1] += 1
    assert match_field(result('123'), b_result, selected_id=chosen)['status'] == 'uncertain'


def test_two_occurrences_within_one_line_cannot_claim_precise_position():
    output = search('123', '123 和 123')
    assert output['status'] == 'ambiguous'
    candidate = output['candidates'][0]
    assert candidate['occurrences'] == 2
    output = search('123', '123 和 123', selected_id=candidate['id'])
    assert output['status'] == 'uncertain'


def test_identical_duplicate_ocr_boxes_are_deduplicated_not_spatial_positions():
    output = match_field(result('123'), result('123', rows=[line('123'), line('123')]))
    assert output['status'] == 'matched'
    assert len(output['candidates']) == 1


def test_similar_text_is_a_candidate_but_never_verified_and_numbers_cannot_fuzz():
    phrase = 'International accounting company'
    output = search(phrase, 'International accounting companv')
    assert output['status'] == 'uncertain'
    assert output['candidates'][0]['match_type'] == 'similar'
    assert output['candidates'][0]['score'] >= .95
    chosen = output['candidates'][0]['id']
    assert search(phrase, 'International accounting companv', selected_id=chosen)['status'] == 'uncertain'
    assert search(phrase + ' 123', phrase + ' 124')['status'] == 'missing'
    assert search(phrase, 'International accounting companv', mode='exact')['status'] == 'missing'


@pytest.mark.parametrize('side', ['a', 'b'])
@pytest.mark.parametrize('status', ['needs_review', 'failed', 'cancelled', None])
def test_status_is_separate_from_text_similarity(side, status):
    a, b = result('123'), result('123', rows=[line('123')])
    (a if side == 'a' else b)['status'] = status
    output = match_field(a, b)
    assert output['status'] == 'uncertain'
    assert output['candidates'][0]['score'] == 1.0
    assert output['candidates'][0]['needs_review']


@pytest.mark.parametrize('confidence', [float('nan'), float('inf'), -1, None, 0])
def test_invalid_confidence_never_verified(confidence):
    output = match_field(result('123'), result('123', rows=[line('123', confidence=confidence)]))
    assert output['status'] == 'uncertain'


@pytest.mark.parametrize('side', ['a', 'b'])
def test_invalid_aggregate_confidence_never_verified(side):
    a, b = result('123'), result('123', rows=[line('123')])
    (a if side == 'a' else b)['confidence'] = float('nan')
    assert match_field(a, b)['status'] == 'uncertain'


@pytest.mark.parametrize('box', [None, [[0, 0]], [[0, 0]] * 4,
                               [[0, 0], [5, 0], [float('nan'), 5], [0, 5]]])
def test_malformed_boxes_are_not_fabricated(box):
    row = line('123')
    row['box'] = box
    output = match_field(result('123'), result('123', rows=[row]))
    assert output['status'] == 'uncertain'
    assert output['candidates'] == []


def test_bounded_candidates_cannot_turn_truncated_search_green():
    rows = [line('123', index * 30) for index in range(100)]
    output = match_field(result('123'), result('123', rows=rows))
    assert len(output['candidates']) == 64
    chosen = output['candidates'][0]['id']
    assert match_field(result('123'), result('123', rows=rows), selected_id=chosen)['status'] == 'uncertain'


def test_empty_and_excessive_input_never_verified():
    assert search('', '')['status'] == 'uncertain'
    assert search('x' * 513, 'x' * 513)['status'] == 'uncertain'
    assert match_field(result('123'), result('123'))['status'] == 'missing'


@pytest.mark.parametrize('threshold', [True, float('nan'), 0, 1.1])
def test_invalid_threshold_rejected(threshold):
    with pytest.raises(ValueError):
        search('text', 'text', threshold=threshold)


@pytest.mark.parametrize(('a', 'b', 'score'), [
    ('12345', '12346', .8), ('12', '13', .5), ('-12345', '-12346', .8),
    ('1,234.50', '1,234.51', 5 / 6), ('00123', '00124', .8),
])
def test_numeric_near_has_actual_digit_agreement_and_never_becomes_verified(a, b, score):
    output = search(a, b)
    assert output['status'] == 'uncertain'
    candidate = output['candidates'][0]
    assert candidate['match_type'] == 'similar'
    assert candidate['similarity_kind'] == 'numeric'
    assert candidate['score'] == pytest.approx(score, abs=.000001)
    assert candidate['needs_review']
    assert candidate['boxes'] == [line(b)['box']]
    adopted = search(a, b, selected_id=candidate['id'])
    assert adopted['status'] == 'uncertain' and adopted['score'] == candidate['score']
    assert search(a, b, mode='exact')['status'] == 'missing'


@pytest.mark.parametrize(('a', 'b'), [
    ('1', '2'), ('12', '23'), ('12345', '123456'), ('12345', '1234'),
    ('12345', '-12346'), ('12.34', '123.5'), ('01', '11'),
    ('1e3', '1e4'), ('1/23', '1/24'), ('１２３４５', '12346'),
    ('12345', 'INV12346'), ('12345', '12346X'), ('1,23', '1,24'),
])
def test_numeric_near_preserves_format_boundaries_and_excludes_nondecimal_values(a, b):
    assert search(a, b)['status'] == 'missing'


def test_late_exact_value_wins_over_more_than_candidate_limit_near_values():
    rows = [line('12346', index * 30) for index in range(70)] + [line('12345', 3000)]
    output = match_field(result('12345'), result('invoice', rows=rows))
    assert output['status'] == 'matched'
    assert len(output['candidates']) == 1
    assert output['candidates'][0]['text'] == '12345'
    assert output['score'] == 1


@pytest.mark.parametrize(('a', 'b'), [
    ('115/10/12', '2026-10-12'), ('民國115年10月12日', '西元2026年10月12日'),
    ('2026/10/12', '115/10/12'),
])
def test_calendar_equivalence_is_exact_date_with_original_invoice_box(a, b):
    output = search(a, '日期：' + b)
    assert output['status'] == 'matched' and output['score'] == 1
    assert output['candidates'][0]['match_type'] == 'date_equivalent'
    assert output['candidates'][0]['boxes'] == [line(b)['box']]
    assert '民國' in output['reason'] and '西元' in output['reason']
    assert search(a, b, date_equivalence=False)['status'] == 'missing'
    assert search(a, b, mode='exact')['status'] == 'missing'


@pytest.mark.parametrize('b', ['西元前2026/10/12', 'AD 2026/10/12', '民國2026/10/12',
                              '115/10/13', '115/02/30'])
def test_date_mode_cannot_fall_back_to_literal_substring_or_near_date(b):
    assert search('2026/10/12', b)['status'] == 'missing'


def test_date_equivalence_preserves_review_and_duplicate_positions():
    invoice = result('dates', rows=[line('2026/10/12'), line('2026/10/12', 40)])
    output = match_field(result('115/10/12'), invoice)
    assert output['status'] == 'ambiguous' and output['score'] is None
    chosen = output['candidates'][1]['id']
    assert match_field(result('115/10/12'), invoice, selected_id=chosen)['status'] == 'matched'
    invoice['status'] = 'needs_review'
    assert match_field(result('115/10/12'), invoice, selected_id=chosen)['status'] == 'uncertain'
    output = search('115/10/12', '115/10/12 與 2026/10/12')
    assert output['status'] == 'ambiguous'
    assert output['candidates'][0]['occurrences'] == 2


@pytest.mark.parametrize(('a', 'b'), [
    ('115/10/12', '西元：115/10/12；西元2026/10/12'),
    ('2026/10/12', '民國：2026/10/12；民國115/10/12'),
])
def test_date_match_provenance_uses_only_valid_equivalent_tokens(a, b):
    output = search(a, b)
    assert output['status'] == 'matched'
    assert output['candidates'][0]['match_type'] == 'date_equivalent'
    assert output['candidates'][0]['occurrences'] == 1
    assert '民國' in output['reason'] and '西元' in output['reason']


@pytest.mark.parametrize(('a', 'b'), [('0.12', '1.12'), ('00000', '00001')])
def test_numeric_zero_value_is_not_confused_with_leading_zero_padding(a, b):
    assert search(a, b)['status'] == 'uncertain'
    assert search(a, b)['candidates'][0]['similarity_kind'] == 'numeric'


def test_perfect_threshold_does_not_enable_numeric_near_candidates():
    assert search('12345', '12346', threshold=1)['status'] == 'missing'
