"""Document merge rules preserve the template, page layout and recognized text."""
from dataclasses import FrozenInstanceError

import pytest

from src.documents.template import (
    MAX_TEMPLATE_CHARS,
    Field,
    Region,
    TemplateError,
    TemplateLayout,
    build_output,
    parse_template,
    placeholder_names,
    rename_placeholder,
    render_template,
    validate_field_name,
    validate_template,
)


def test_first_page_layout_inherits_per_field_across_other_page_sizes():
    name = Region.from_pixels(20, 10, 120, 30, 200, 100)
    amount = Region.from_pixels(80, 50, 180, 90, 200, 100)
    layout = TemplateLayout([Field('姓名', name), Field('金額', amount)])

    assert layout.region_for('姓名', 0) == name
    assert layout.region_for('姓名', 2).pixel_bounds(1000, 500) == (100, 50, 600, 150)
    assert layout.region_for('金額', 5).pixel_bounds(400, 400) == (160, 200, 360, 360)

    selected_second_page = Region(.2, .3, .4, .1)
    layout.set_region('姓名', selected_second_page, page_index=1)
    assert layout.region_for('姓名', 1) == selected_second_page
    assert layout.region_for('金額', 1) == amount
    assert layout.region_for('姓名', 2) == name
    assert layout.effective_regions(1) == {'姓名': selected_second_page, '金額': amount}


def test_changing_first_page_updates_inherited_pages_and_keeps_explicit_overrides():
    layout = TemplateLayout([Field('辨識1', Region(0, 0, .2, .2))])
    override = Region(.4, .3, .2, .2)
    layout.set_region('辨識1', override, 2)
    changed_base = Region(.1, .1, .3, .3)
    layout.set_region('辨識1', changed_base, 0)
    assert layout.region_for('辨識1', 0) == changed_base
    assert layout.region_for('辨識1', 1) == changed_base
    assert layout.region_for('辨識1', 2) == override
    assert not layout.has_override('辨識1', 0)
    assert layout.has_override('辨識1', 2)
    layout.clear_override('辨識1', 2)
    assert layout.region_for('辨識1', 2) == changed_base
    assert layout.overrides == {}


def test_empty_slots_and_page_reset_are_explicit_without_inventing_a_full_page_region():
    layout = TemplateLayout()
    assert layout.add_field('  辨識1  ') == Field('辨識1')
    assert layout.region_for('辨識1', 0) is None
    assert layout.region_for('辨識1', 4) is None
    layout.set_region('辨識1', Region(.2, .2, .5, .5), 1)
    layout.clear_page_overrides(1)
    assert layout.region_for('辨識1', 1) is None
    layout.set_region('辨識1', Region(.1, .1, .5, .5))
    layout.set_region('辨識1', Region(.3, .3, .5, .5), 1)
    layout.set_region('辨識1', None, 1)
    assert layout.region_for('辨識1', 1) == Region(.1, .1, .5, .5)


def test_snapshot_mappings_cannot_move_a_running_jobs_region():
    layout = TemplateLayout([Field('辨識1', Region(.1, .1, .2, .2))])
    layout.set_region('辨識1', Region(.2, .2, .2, .2), 1)
    snapshot = layout.copy()
    exported = layout.overrides
    exported[1]['辨識1'] = Region(.4, .4, .2, .2)
    layout.set_region('辨識1', Region(.3, .3, .2, .2), 1)
    assert snapshot.region_for('辨識1', 1) == Region(.2, .2, .2, .2)
    assert layout.region_for('辨識1', 1) == Region(.3, .3, .2, .2)
    with pytest.raises(FrozenInstanceError):
        snapshot.fields[0].name = '改名'


def test_rename_keeps_field_order_braces_whitespace_and_override_keys():
    layout = TemplateLayout([Field('姓名', Region(0, 0, .2, .2)), Field('金額')])
    override = Region(.3, .4, .2, .2)
    layout.set_region('姓名', override, 3)
    template = '｛ 姓名 }，{金額}／{姓名｝，{{姓名}}與｛｛姓名｝｝。'
    updated = layout.rename_field('姓名', ' 收件人 ', template)
    assert updated == '｛ 收件人 }，{金額}／{收件人｝，{{姓名}}與｛｛姓名｝｝。'
    assert layout.field_names == ('收件人', '金額')
    assert layout.overrides == {3: {'收件人': override}}
    assert layout.region_for('收件人', 3) == override
    assert render_template(updated, {'收件人': '臺𠮷', '金額': '1,234'}) == (
        '臺𠮷，1,234／臺𠮷，{姓名}與｛姓名｝。')


def test_failed_rename_and_duplicate_add_leave_the_layout_unchanged():
    layout = TemplateLayout([Field('姓名'), Field('金額')])
    layout.set_region('姓名', Region(0, 0, .1, .1), 2)
    before = layout.fields, layout.overrides
    for new, template in [('金額', '{姓名}'), ('新名', '{姓名'), ('{非法}', '{姓名}')]:
        with pytest.raises(TemplateError):
            layout.rename_field('姓名', new, template)
        assert (layout.fields, layout.overrides) == before
    with pytest.raises(TemplateError, match='已存在'):
        layout.add_field(' 姓名 ')
    assert (layout.fields, layout.overrides) == before


def test_remove_field_removes_its_overrides_only_and_unknown_template_is_not_silently_erased():
    layout = TemplateLayout([Field('姓名'), Field('金額')])
    region = Region(0, 0, .5, .5)
    layout.set_region('姓名', region, 1)
    layout.set_region('金額', region, 1)
    layout.remove_field('姓名')
    assert layout.overrides == {1: {'金額': region}}
    assert layout.field_names == ('金額',)
    with pytest.raises(TemplateError, match='尚未建立.*姓名'):
        validate_template('姓名：{姓名}', layout.field_names)


@pytest.mark.parametrize('text', ['', '  ', '{姓名}', '姓\n名', '\n姓名', '姓名\t',
                                   '姓\x00名', '姓\x85名', '姓\u2028名', '名' * 65, None])
def test_field_names_reject_empty_control_braces_and_oversize_values(text):
    with pytest.raises(TemplateError):
        validate_field_name(text)


def test_field_names_support_cjk_rare_characters_variation_selectors_and_internal_spaces():
    assert validate_field_name('  收件人 𠮷\U000e0100  ') == '收件人 𠮷\U000e0100'


@pytest.mark.parametrize('values', [(-.01, 0, .5, .5), (0, 0, 0, .5), (0, 0, .5, -1),
                                     (.9, .2, .2, .1), (1, 0, .1, .1),
                                     (float('nan'), 0, .2, .2), (0, 0, float('inf'), .1),
                                     (True, 0, .1, .1), ('0', 0, .1, .1)])
def test_regions_reject_invalid_or_outside_bounds(values):
    with pytest.raises(TemplateError):
        Region(*values)


def test_pixel_drag_clips_and_normalizes_both_directions_without_zero_area():
    assert Region.from_pixels(250, 80, -20, 10, 200, 100).pixel_bounds(200, 100) == (0, 10, 200, 80)
    with pytest.raises(TemplateError):
        Region.from_pixels(220, 10, 260, 40, 200, 100)
    assert Region(.1, .1, .2, .2).pixel_bounds(10, 10) == (1, 1, 3, 3)
    assert Region(.3, .3, 1e-14, 1e-14).pixel_bounds(100, 100) == (30, 30, 31, 31)
    assert Region(0, 0, 1, 1).pixel_bounds(1, 1) == (0, 0, 1, 1)


@pytest.mark.parametrize('page', [-1, True, 1.5, '2'])
def test_invalid_page_indices_do_not_add_overrides(page):
    layout = TemplateLayout([Field('姓名')])
    with pytest.raises(TemplateError):
        layout.set_region('姓名', Region(0, 0, .5, .5), page)
    assert layout.overrides == {}


@pytest.mark.parametrize('width,height', [(0, 20), (20, -1), (True, 20), (1.2, 20)])
def test_pixel_conversion_rejects_invalid_page_dimensions(width, height):
    with pytest.raises(TemplateError):
        Region(0, 0, .5, .5).pixel_bounds(width, height)


def test_literal_template_merges_all_page_values_and_preserves_newlines_unicode_and_whitespace():
    template = '  文字｛辨識1}文字測試{辨識2}\n結尾。  '
    pages = [{'辨識1': '臺𠮷\U000e0100', '辨識2': '-1,234.50'},
             {'辨識1': '下一頁', '辨識2': '第一行\n第二行'}]
    assert build_output(template, pages) == (
        '  文字臺𠮷\U000e0100文字測試-1,234.50\n結尾。  \n\n'
        '  文字下一頁文字測試第一行\n第二行\n結尾。  ')
    assert pages[0] == {'辨識1': '臺𠮷\U000e0100', '辨識2': '-1,234.50'}


def test_placeholder_forms_repeat_and_canonicalize_without_reparsing_ocr_values():
    template = '｛辨識1｝{辨識1}｛ 辨識1 }{辨識1｝'
    assert placeholder_names(template) == ('辨識1',)
    assert render_template(template, {'辨識1': '{其他欄位}'}) == '{其他欄位}' * 4
    assert [token.name for token in parse_template(template)] == ['辨識1'] * 4


def test_literal_brace_escapes_are_not_fields_or_renamed():
    template = '{{姓名}}：｛｛數字｝｝＝{姓名}'
    assert placeholder_names(template) == ('姓名',)
    assert rename_placeholder(template, '姓名', '收件人') == '{{姓名}}：｛｛數字｝｝＝{收件人}'
    assert render_template(template, {'姓名': '甲'}) == '{姓名}：｛數字｝＝甲'


@pytest.mark.parametrize('text', ['{', '｛姓名', '}', '甲｝', '{姓名{金額}}', '{姓名\n}', '{}', '｛ ｝'])
def test_malformed_placeholders_require_user_correction(text):
    with pytest.raises(TemplateError):
        parse_template(text)


def test_unknown_or_missing_fields_block_output_and_report_the_affected_page():
    with pytest.raises(TemplateError, match='尚未建立.*辨識2'):
        validate_template('文字{辨識1}文字{辨識2}', ['辨識1'])
    assert validate_template('{辨識2}{辨識1}{辨識2}', ['辨識1', '辨識2']) == ('辨識2', '辨識1')
    with pytest.raises(TemplateError, match='第 2 頁.*金額'):
        build_output('{姓名} {金額}', [{'姓名': '甲', '金額': '100'}, {'姓名': '乙'}])
    with pytest.raises(TemplateError, match='必須是文字'):
        render_template('{姓名}', {'姓名': None})


def test_empty_result_is_distinct_from_a_missing_result_and_literal_only_templates_work():
    assert render_template('姓名：{姓名}', {'姓名': ''}) == '姓名：'
    assert build_output('固定文字\n', [{}, {}]) == '固定文字\n\n\n固定文字\n'
    assert build_output('{姓名}', []) == ''
    with pytest.raises(TemplateError):
        build_output('{未關閉', [])


def test_template_and_output_bounds_fail_explicitly_without_truncating_text():
    with pytest.raises(TemplateError, match='模板過長'):
        parse_template('字' * (MAX_TEMPLATE_CHARS + 1))
    assert render_template('{姓名}', {'姓名': '甲乙'}, max_output_chars=2) == '甲乙'
    with pytest.raises(TemplateError, match='輸出內容超過'):
        render_template('{姓名}', {'姓名': '甲乙丙'}, max_output_chars=2)
    assert build_output('{姓名}', [{'姓名': '甲'}, {'姓名': '乙'}], max_output_chars=4) == '甲\n\n乙'
    with pytest.raises(TemplateError, match='輸出內容超過'):
        build_output('{姓名}', [{'姓名': '甲'}, {'姓名': '乙'}], max_output_chars=3)
    assert build_output('{姓名}', [{'姓名': '甲'}, {'姓名': '乙'}], separator='／') == '甲／乙'


def test_template_syntax_never_evaluates_expression_or_attribute_access():
    expression = "__import__('os').environ"
    with pytest.raises(TemplateError, match='缺少欄位'):
        render_template('{' + expression + '}', {})
    assert render_template('{' + expression + '}', {expression: '純文字'}) == '純文字'
