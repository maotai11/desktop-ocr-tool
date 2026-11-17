import numpy as np
import pytest

from src.invoice_check import (
    compare_fields,
    detect_active_row,
    extract_invoice_fields,
    extract_row_fields,
    normalize_value,
)


def observation(text, x, y, width=100):
    return {'raw_text': text, 'text': text, 'box': [[x,y],[x+width,y],[x+width,y+20],[x,y+20]], 'confidence': .95}


def test_typed_values_preserve_digits_and_validate_dates():
    assert normalize_value('issue_date', '115/10/12') == '2026-10-12'
    assert normalize_value('issue_date', '2026-02-30') is None
    assert normalize_value('net_amount', '-1,200.50') == '-1200.5'
    assert normalize_value('net_amount', '12O0') is None
    assert normalize_value('invoice_number', 'ab-12345678') == 'AB12345678'


def test_invoice_geometry_raw_and_missing_not_zero():
    detail = [observation('銷售額合計', 10, 100), observation('7,000', 200, 100), observation('營業稅',10,140), observation('350',200,140), observation('2026/10/12',10,10)]
    result = extract_invoice_fields(detail, (500,300))
    assert result['net_amount']['value'] == '7000'
    assert result['net_amount']['raw'] == '7,000'
    assert result['net_amount']['box'] == [200,100,100,20]
    assert result['tax_amount']['value'] == '350'
    assert result['total_amount']['value'] is None
    assert detail[1]['raw_text'] == '7,000'


def test_multiple_candidate_numbers_require_review():
    data = [observation('銷售額合計',0,10), observation('100',150,10), observation('101',300,10)]
    a = extract_invoice_fields(data,(500,100))
    b = extract_row_fields([observation('100',150,10)],(500,100),overrides={'net_amount':[.25,0,.3,1]})
    assert a['net_amount']['ambiguous']
    assert compare_fields(a,b)[0]['status'] == 'review'


def test_active_cell_defines_only_its_grid_row():
    image = np.full((140,400,3),255,np.uint8)
    image[[20,60,100,130],:]=0
    image[63:97,210:310] = [220,80,20]
    result = detect_active_row(image)
    assert result['row'] == [61,100]
    assert result['box'] == [0,61,400,39]
    image[23:57,210:310] = [220,80,20]
    with pytest.raises(ValueError,match='Ambiguous'):
        detect_active_row(image)
    assert detect_active_row(image,manual_row=[21,60])['row'] == [21,60]


def test_colliding_overrides_and_ocr_alternatives_do_not_turn_green():
    detail = [observation('12345675',10,10)]
    detail[0]['alternatives'] = ['12345674']
    fields = extract_invoice_fields(detail,(200,100),{'seller_tax_id':[0,0,1,1], 'buyer_tax_id':[0,0,1,1]})
    assert fields['seller_tax_id']['ambiguous']
    assert compare_fields(fields,fields)[-1]['status'] == 'review'


def test_missing_separators_cannot_swallow_neighbour_rows():
    image = np.full((100,200,3),255,np.uint8)
    image[[5,95],:] = 0
    image[[20,40,60,80],30:110] = 0
    image[40:55,5:25] = [200,80,20]
    with pytest.raises(ValueError,match='Uncertain'):
        detect_active_row(image)


def test_equal_low_confidence_still_requires_review():
    item = observation('1200',10,10)
    item['confidence'] = .11
    fields = extract_invoice_fields([item],(200,100),{'net_amount':[0,0,1,1]})
    assert compare_fields(fields,fields)[0]['status'] == 'review'


def test_headers_map_only_explicit_distinct_columns():
    from src.invoice_check import header_columns
    columns = header_columns([observation('未稅金額',10,0),observation('營業稅',200,0),observation('發票號碼',400,0)],(600,25))
    row = [observation('1000',20,0),observation('50',220,0),observation('AB12345678',450,0)]
    fields = extract_row_fields(row,(600,25),headers=columns)
    assert fields['net_amount']['value'] == '1000'
    assert fields['tax_amount']['value'] == '50'
    assert fields['invoice_number']['value'] == 'AB12345678'


@pytest.mark.parametrize('raw', ['000/1/1','115/13/2','115/2/29','2026/2/29','1911/0/1'])
def test_invalid_calendar_dates_are_missing(raw):
    assert normalize_value('issue_date',raw) is None


def test_format_only_tax_id_is_not_authentication():
    from src.invoice_check import valid_ubn
    assert valid_ubn('24536806')
    assert not valid_ubn('12345678')
    fields = extract_invoice_fields([observation('12345678',10,10)],(200,100),{'seller_tax_id':[0,0,1,1]})
    assert fields['seller_tax_id']['ambiguous']


def test_nearby_numbers_never_fuzzily_equal():
    one = extract_invoice_fields([observation('1200',10,10)],(200,100),{'net_amount':[0,0,1,1]})
    two = extract_row_fields([observation('1201',10,10)],(200,100),overrides={'net_amount':[0,0,1,1]})
    assert compare_fields(one,two)[0]['status'] == 'different'


def test_unknown_header_still_bounds_amount_column():
    from src.invoice_check import header_columns
    headers = [observation('未稅金額',0,0,40), observation('備註',100,0,40), observation('營業稅',200,0,40)]
    columns = header_columns(headers,(300,20))
    fields = extract_row_fields([observation('1200',80,0,40)],(300,20),headers=columns)
    assert fields['net_amount']['value'] is None


def test_second_row_header_does_not_include_first_data_row():
    image = np.full((100,300,3),255,np.uint8)
    image[[0,30,60,90],:] = 0
    image[63:88,100:200] = [220,80,20]
    result = detect_active_row(image)
    assert result['header'] == [1,30]
    assert result['row'] == [61,90]


def test_weak_header_or_anchor_does_not_assign_strong_number():
    from src.invoice_check import header_columns
    anchor = observation('未稅金額',0,0)
    anchor['confidence'] = .2
    assert header_columns([anchor],(300,30)) == {}
    result = extract_invoice_fields([anchor,observation('100',150,0)],(300,50))
    assert result['net_amount']['value'] is None


def test_same_centre_headers_are_ambiguous_not_sort_error():
    from src.invoice_check import header_columns
    assert header_columns([observation('未稅金額',0,0),observation('備註',0,0)],(300,30)) == {}


def test_plain_ubn_party_context_and_unanchored_does_not_guess():
    detail = [observation('買受人',10,10),observation('統一編號',10,40),observation('24536806',130,40),
              observation('發票專用章',300,200),observation('統一編號24536806',300,230,180)]
    fields = extract_invoice_fields(detail,(600,400))
    assert fields['buyer_tax_id']['value'] == '24536806'
    assert fields['seller_tax_id']['value'] == '24536806'
    assert extract_invoice_fields([observation('統一編號24536806',10,10)],(300,200))['seller_tax_id']['value'] is None


def test_conflicting_party_context_remains_missing():
    detail = [observation('買受人',10,10), observation('賣方',10,70),observation('統一編號24536806',10,40)]
    fields = extract_invoice_fields(detail,(400,200))
    assert fields['buyer_tax_id']['value'] is None
    assert fields['seller_tax_id']['value'] is None


def test_split_invoice_number_and_calendar_date_preserve_parts():
    detail = [observation('AB',10,10,25),observation('12345678',40,10,100),
              observation('115年',10,60,45),observation('10月',60,60,40),observation('12日',105,60,40)]
    fields = extract_invoice_fields(detail,(400,200))
    assert fields['invoice_number']['value'] == 'AB12345678'
    assert fields['issue_date']['value'] == '2026-10-12'
    assert len(fields['invoice_number']['candidates'][0]['parts']) == 2
    assert detail[0]['raw_text'] == 'AB'


def test_split_date_must_not_skip_intervening_text():
    detail = [observation('115年',10,10,40),observation('備註',55,10,40),observation('10月12日',100,10,90)]
    assert extract_invoice_fields(detail,(400,100))['issue_date']['value'] is None


@pytest.mark.parametrize(('raw','expected'), [('1150824','2026-08-24'),('20260824','2026-08-24'),('民國115年8月24日','2026-08-24'),('西元2026/8/24','2026-08-24')])
def test_compact_and_prefixed_dates(raw,expected):
    assert normalize_value('issue_date',raw) == expected


def test_decimal_does_not_round_long_identifiers_as_money():
    assert normalize_value('net_amount','123456789012345678901234567890.01') != normalize_value('net_amount','123456789012345678901234567890.02')
    assert normalize_value('net_amount','1200.00') == '1200'


def test_ubn_current_mod5_and_zero_not_a_valid_id():
    from src.invoice_check import valid_ubn
    assert valid_ubn('24536801')  # weighted digit sum 35, current mod5 rule
    assert not valid_ubn('00000000')


def test_actual_accounting_header_preset_compact_row_five_fields():
    from src.invoice_check import header_columns
    headers = [observation(label,i*150,0,100) for i,label in enumerate(('金額','稅額','日期','憑證號碼','統編'))]
    columns = header_columns(headers,(750,25))
    row = [observation(value,i*150,0,100) for i,value in enumerate(('7000','350','1150824','AB12345678','24536806'))]
    values = extract_row_fields(row,(750,25),headers=columns)
    assert [values[key]['value'] for key in ('net_amount','tax_amount','issue_date','invoice_number','seller_tax_id')] == ['7000','350','2026-08-24','AB12345678','24536806']
    invoice = extract_invoice_fields(row,(750,25),overrides=columns)
    assert all(item['status'] == 'matched' for item in compare_fields(invoice,values))
    alternate = header_columns(headers,(750,25),amount_key='total_amount',tax_id_key='buyer_tax_id')
    assert 'total_amount' in alternate and 'net_amount' not in alternate
    assert 'buyer_tax_id' in alternate and 'seller_tax_id' not in alternate


def test_generic_header_preset_never_changes_invoice_semantics():
    fields = extract_invoice_fields([observation('金額',0,0),observation('100',150,0)],(300,50))
    assert fields['net_amount']['value'] is None


def test_joined_invoice_number_cannot_reuse_seller_id_component_as_green():
    detail = [observation('賣方統編',0,0,75),observation('AB',100,0,30),observation('24536806',135,0,100)]
    fields = extract_invoice_fields(detail,(400,100))
    assert fields['invoice_number']['value'] == 'AB24536806'
    assert fields['seller_tax_id']['value'] == '24536806'
    assert fields['invoice_number']['ambiguous']
    assert fields['seller_tax_id']['ambiguous']


@pytest.mark.parametrize('raw',['西元115/08/24','民國2026/08/24'])
def test_explicit_calendar_prefix_cannot_be_reinterpreted(raw):
    assert normalize_value('issue_date',raw) is None


def test_row_without_header_or_override_is_not_confirmed_semantic_mapping():
    row = extract_row_fields([observation('AB12345678',10,10), observation('1150824',10,60)],(300,100))
    assert row['invoice_number']['ambiguous']
    assert row['issue_date']['ambiguous']
