"""Opt-in formatting at the clipboard boundary; never change stored OCR text."""
import unicodedata

COPY_CLEANUP_OPTIONS = (
    ('copy_remove_commas', '複製時移除逗號（,，）', ',，'),
    ('copy_remove_apostrophes', "複製時移除撇號（'‘’＇）", "'‘’＇"),
    ('copy_remove_newlines', '複製時移除換行（合併同筆文字）', '\r\n\u0085\u2028\u2029'),
    ('copy_remove_punctuation', '複製時移除所有標點符號', ''),
)


def prepare_copy_text(text: str, cfg=None) -> str:
    """Apply explicit cleanup without changing the source text.

    This is literal formatting, not a numeric parser or an OCR correction. It
    also removes the selected characters from prose, as disclosed in the UI.
    Newlines are separate opt-in because joining lines can join distinct values.
    All-punctuation cleanup uses Unicode P categories, including hyphens and
    decimal points. Symbols (math/currency/emoji), letters and spaces survive.
    """
    if cfg is None:
        return text
    removed = ''.join(chars for key, _, chars in COPY_CLEANUP_OPTIONS
                      if cfg.get('clipboard', key, default=False) is True)
    prepared = text.translate(str.maketrans('', '', removed)) if removed else text
    if cfg.get('clipboard', 'copy_remove_punctuation', default=False) is True:
        prepared = ''.join(char for char in prepared
                           if not unicodedata.category(char).startswith('P'))
    return prepared
