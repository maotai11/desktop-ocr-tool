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
