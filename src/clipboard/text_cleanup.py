"""Opt-in formatting at the clipboard boundary; never change stored OCR text."""
import unicodedata

COPY_CLEANUP_OPTIONS = (
    ('copy_remove_commas', '複製時移除逗號（,，）', ',，'),
    ('copy_remove_apostrophes', "複製時移除撇號（'‘’＇）", "'‘’＇"),
    ('copy_remove_newlines', '複製時移除換行（合併同筆文字）', '\r\n\u0085\u2028\u2029'),
    ('copy_remove_punctuation', '複製時移除所有標點與空白', ''),
)


MAX_REPLACEMENT_LENGTH = 64
COPY_REPLACEMENT_KEYS = {
    key: key.replace('copy_remove_', 'copy_replace_')
    for key, _, _ in COPY_CLEANUP_OPTIONS
}

COPY_CLEANUP_MODE_KEYS = {key: key.replace('copy_remove_', 'copy_cleanup_mode_')
                          for key, _, _ in COPY_CLEANUP_OPTIONS}


def validate_copy_replacement(value):
    """A short literal string; preserve combining/format marks used by emoji."""
    return (isinstance(value, str) and len(value) <= MAX_REPLACEMENT_LENGTH
            and not any(unicodedata.category(char) in ('Cc', 'Cs', 'Zl', 'Zp') for char in value))


def cleanup_replacement(cfg, key):
    """Read a bounded literal replacement, never a regex/template program."""
    if cfg.get('clipboard', COPY_CLEANUP_MODE_KEYS[key], default='remove') != 'replace':
        return ''
    value = cfg.get('clipboard', COPY_REPLACEMENT_KEYS[key], default='')
    if not validate_copy_replacement(value):
        return ''
    return value


def prepare_copy_text(text: str, cfg=None) -> str:
    """Format original characters once, keeping inserted replacements literal.

    Specific enabled rules take precedence over the broad punctuation rule.
    CRLF is a single line break; other Unicode line separators are individual
    breaks. Original stored OCR text is never changed.
    """
    if cfg is None:
        return text
    enabled = {}
    for key, _, _ in COPY_CLEANUP_OPTIONS:
        if cfg.get('clipboard', key, default=False) is not True:
            continue
        mode = cfg.get('clipboard', COPY_CLEANUP_MODE_KEYS[key], default='remove')
        value = cfg.get('clipboard', COPY_REPLACEMENT_KEYS[key], default='')
        # An invalid explicit setting must not silently erase clipboard text.
        enabled[key] = ('' if mode == 'remove' else value
                        if mode == 'replace' and validate_copy_replacement(value) else None)
    if not enabled:
        return text
    output = []
    index = 0
    while index < len(text):
        char = text[index]
        key = None
        if char in ',，' and 'copy_remove_commas' in enabled:
            key = 'copy_remove_commas'
        elif char in "'‘’＇" and 'copy_remove_apostrophes' in enabled:
            key = 'copy_remove_apostrophes'
        elif char in '\r\n\u0085\u2028\u2029' and 'copy_remove_newlines' in enabled:
            key = 'copy_remove_newlines'
        elif ('copy_remove_punctuation' in enabled
              and (unicodedata.category(char).startswith('P') or char.isspace())):
            key = 'copy_remove_punctuation'
        if key is None or enabled[key] is None:
            output.append(char)
        else:
            output.append(enabled[key])
            if char == '\r' and index + 1 < len(text) and text[index + 1] == '\n':
                index += 1
        index += 1
    return ''.join(output)


def copy_batch_separator(cfg=None):
    """Preserve legacy record boundaries unless replacement explicitly applies."""
    if cfg is not None:
        broad = 'copy_remove_punctuation'
        mode = cfg.get('clipboard', COPY_CLEANUP_MODE_KEYS[broad], default='remove')
        valid_broad = (mode == 'remove' or (mode == 'replace' and validate_copy_replacement(
            cfg.get('clipboard', COPY_REPLACEMENT_KEYS[broad], default=''))))
        if cfg.get('clipboard', broad, default=False) is True and valid_broad:
            return prepare_copy_text('\n\n', cfg)
        if (cfg.get('clipboard', 'copy_remove_newlines', default=False) is True
                and cleanup_replacement(cfg, 'copy_remove_newlines')):
            return prepare_copy_text('\n\n', cfg)
    return '\n\n'
