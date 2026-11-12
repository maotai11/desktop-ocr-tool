"""Literal clipboard substitutions do not cascade or mutate original text."""
import pytest
from src.clipboard.text_cleanup import prepare_copy_text, copy_batch_separator

class Config:
    def __init__(self, **values): self.values = values
    def get(self, section, key, default=None): return self.values.get(key, default)

def cfg(**values): return Config(**values)

@pytest.mark.parametrize('mode,value', [('unknown', ''), ('replace', None)])
def test_invalid_broad_rule_cannot_activate_record_separator_removal(mode, value):
    settings = cfg(copy_remove_newlines=True, copy_remove_punctuation=True,
                   copy_cleanup_mode_punctuation=mode, copy_replace_punctuation=value)
    assert prepare_copy_text('甲\n乙', settings) == '甲乙'
    assert copy_batch_separator(settings) == '\n\n'
    settings.values.update(copy_cleanup_mode_newlines='replace', copy_replace_newlines=' / ')
    assert copy_batch_separator(settings) == ' /  / '

def test_unicode_line_breaks_and_crlf_are_single_replacement_units():
    source = '甲\r\n乙\r丙\n丁\u0085戊\u2028己\u2029庚'
    settings = cfg(copy_remove_newlines=True, copy_cleanup_mode_newlines='replace', copy_replace_newlines='+')
    assert prepare_copy_text(source, settings) == '甲+乙+丙+丁+戊+己+庚'
    assert '\r\n' in source

def test_specific_rules_win_and_inserted_punctuation_is_not_reprocessed():
    settings = cfg(copy_remove_commas=True, copy_cleanup_mode_commas='replace', copy_replace_commas='[，+]',
                   copy_remove_newlines=True, copy_cleanup_mode_newlines='replace', copy_replace_newlines='/',
                   copy_remove_punctuation=True, copy_cleanup_mode_punctuation='replace', copy_replace_punctuation='。')
    assert prepare_copy_text('甲，乙\r\n丙！ 丁', settings) == '甲[，+]乙/丙。。丁'

def test_disabled_and_remove_modes_preserve_dormant_replacement_without_applying_it():
    settings = cfg(copy_remove_commas=True, copy_replace_commas='NEW', copy_cleanup_mode_commas='remove')
    assert prepare_copy_text('甲，乙', settings) == '甲乙'
    assert settings.values['copy_replace_commas'] == 'NEW'
    settings.values['copy_remove_commas'] = False
    assert prepare_copy_text('甲，乙', settings) == '甲，乙'

@pytest.mark.parametrize('replacement', ['+', '$1\\g<0>', '🧑\u200d💻', '替代文字', ''])
def test_replacement_is_literal_arbitrary_unicode(replacement):
    settings = cfg(copy_remove_newlines=True, copy_cleanup_mode_newlines='replace', copy_replace_newlines=replacement)
    assert prepare_copy_text('甲\n乙', settings) == '甲' + replacement + '乙'

@pytest.mark.parametrize('replacement', [None, True, 123, 'x'*65, '\n', '\x00', '\ud800'])
def test_invalid_replacement_preserves_original_characters(replacement):
    assert prepare_copy_text('甲\n乙', cfg(copy_remove_newlines=True, copy_cleanup_mode_newlines='replace',
                                          copy_replace_newlines=replacement)) == '甲\n乙'

def test_batch_record_separator_preserves_legacy_and_formats_explicit_replacements():
    assert copy_batch_separator(cfg(copy_remove_newlines=True)) == '\n\n'
    assert copy_batch_separator(cfg(copy_remove_newlines=True, copy_cleanup_mode_newlines='replace', copy_replace_newlines='+')) == '++'
    assert copy_batch_separator(cfg(copy_remove_punctuation=True)) == ''
    assert copy_batch_separator(cfg(copy_remove_punctuation=True, copy_cleanup_mode_punctuation='replace', copy_replace_punctuation='|')) == '||'


def test_invalid_specific_rule_preserves_characters_even_with_broad_cleanup():
    settings = cfg(copy_remove_commas=True, copy_cleanup_mode_commas='unknown',
                   copy_remove_punctuation=True)
    assert prepare_copy_text('甲， 乙！', settings) == '甲，乙'


def test_real_batch_writer_formats_boundaries_without_cascading(qapp):
    from src.clipboard.writer import copy_text_batch
    settings = cfg(copy_remove_newlines=True, copy_cleanup_mode_newlines='replace',
                   copy_replace_newlines='+', copy_remove_punctuation=True)
    assert copy_text_batch(['甲\n乙', '丙'], settings)
    assert qapp.clipboard().text() == '甲+乙++丙'


def test_invalid_specific_newline_setting_preserves_record_separator_with_broad_replacement():
    settings = cfg(copy_remove_newlines=True, copy_cleanup_mode_newlines='replace',
                   copy_replace_newlines=None, copy_remove_punctuation=True,
                   copy_cleanup_mode_punctuation='replace', copy_replace_punctuation='|')
    assert prepare_copy_text('甲\r\n乙！', settings) == '甲\r\n乙|'
    assert copy_batch_separator(settings) == '\n\n'
