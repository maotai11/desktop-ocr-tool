"""Small shared native UI scale, using the existing offline Night Instrument palette."""
from .theme import (
    _BG, _BG_RAISE, _BG_HOVER, _BORDER, _BORDER_L,
    _TEXT_PRI, _TEXT_SEC, _ACCENT, _ACCENT_H, _ACCENT_T, _TEAL,
)

SPACE = (4, 8, 12, 16, 24)
TYPE_TITLE = 20
TYPE_SECTION = 14
TYPE_BODY = 13
TYPE_META = 12
TYPE_CANDIDATE = 24
CONTROL_HEIGHT = 36
COMPACT_HEIGHT = 30
CANDIDATE_HEIGHT = 48
RADIUS_CONTROL = 6
RADIUS_SURFACE = 10


def button_style(role='secondary', *, compact=False):
    """Consistent normal, hover, pressed, focus and disabled native button states."""
    if role not in ('primary', 'secondary', 'ghost'):
        raise ValueError('Unknown button role')
    background = _ACCENT if role == 'primary' else ('transparent' if role == 'ghost' else _BG_RAISE)
    foreground = _ACCENT_T if role == 'primary' else _TEXT_PRI
    hover = _ACCENT_H if role == 'primary' else _BG_HOVER
    border = 'transparent' if role in ('primary', 'ghost') else _BORDER_L
    padding = '3px 10px' if compact else '5px 12px'
    return f'''
        QPushButton {{
            background: {background}; color: {foreground};
            border: 1px solid {border}; border-radius: {RADIUS_CONTROL}px;
            padding: {padding}; font-size: {TYPE_BODY}px; font-weight: 500;
        }}
        QPushButton:hover {{ background: {hover}; }}
        QPushButton:pressed {{ background: {_BG}; color: {_TEXT_PRI}; }}
        QPushButton:focus {{ border: 1px solid {_ACCENT}; }}
        QPushButton:disabled {{ background: {_BG_RAISE}; color: {_TEXT_SEC}; border-color: {_BORDER}; }}
        QPushButton[copied="true"] {{ border-color: {_TEAL}; }}
    '''


def apply_button(button, role='secondary', *, compact=False):
    button.setMinimumHeight(COMPACT_HEIGHT if compact else CONTROL_HEIGHT)
    button.setStyleSheet(button_style(role, compact=compact))
    return button
