"""Restored OCR evidence may not supply two independently confirmed fields."""
from copy import deepcopy

import pytest

from src.invoice_check.fields import mark_shared_provenance


def field(raw,box,*,parts=None,ambiguous=False):
    candidate={'raw':raw,'value':raw,'box':box,'confidence':.99}
    if parts is not None:
        candidate['parts']=parts
    return {'value':raw,'raw':raw,'box':box,'candidates':[candidate],'ambiguous':ambiguous}


def test_exact_evidence_same_roi_marks_both_without_clearing_existing_review():
    fields={'net_amount':field('1000',[100,100,60,20]),'tax_amount':field('1000',[100,100,60,20]),
            'invoice_number':field('AB12345678',[10,10,80,20],ambiguous=True)}
    assert mark_shared_provenance(fields) is fields
    assert all(value['ambiguous'] for value in fields.values())


@pytest.mark.parametrize('secondbox', [[102,101,59,19],[125,100,60,20],[110,105,20,10]])
def test_partial_roi_or_whole_pass_crop_jitter_marks_both_only_with_opt_in(secondbox):
    original={'net_amount':field('1000',[100,100,60,20]),'tax_amount':field('100O',secondbox)}
    exact=mark_shared_provenance(deepcopy(original))
    assert not any(value['ambiguous'] for value in exact.values())
    geometry=mark_shared_provenance(original,overlap=True)
    assert all(value['ambiguous'] for value in geometry.values())


def test_equal_values_at_distinct_real_positions_are_independent():
    values={'net_amount':field('1000',[10,10,60,20]),'tax_amount':field('1000',[10,80,60,20])}
    mark_shared_provenance(values,overlap=True)
    assert not any(value['ambiguous'] for value in values.values())


def test_component_positions_win_over_large_joined_bounding_rectangle():
    parts=[{'raw_text':'AB','box':[10,10,20,20]}, {'raw_text':'12345678','box':[100,10,80,20]}]
    values={'invoice_number':field('AB12345678',[10,10,170,20],parts=parts),
            'seller_tax_id':field('12345678',[102,10,78,20]),
            'tax_amount':field('50',[50,10,20,20])}
    mark_shared_provenance(values,overlap=True)
    assert values['invoice_number']['ambiguous'] and values['seller_tax_id']['ambiguous']
    assert not values['tax_amount']['ambiguous']  # Empty gap inside joined bbox.


def test_less_than_half_component_overlap_does_not_invent_shared_token():
    values={'net_amount':field('1000',[10,10,60,20]),'tax_amount':field('50',[55,10,60,20])}
    mark_shared_provenance(values,overlap=True)
    assert not any(value['ambiguous'] for value in values.values())
