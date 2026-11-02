# -*- coding: utf-8 -*-
"""
Night Instrument theme — single source of truth for all design tokens.

Import example (from src/ui/):
    from .theme import _BG, _ACCENT, _ACCENT_15, ...

Import example (from src/ui/components/):
    from ..theme import _BG, _ACCENT, _ACCENT_15, ...
"""

# ── Base palette ───────────────────────────────────────────────────────────────
_BG       = "#1c1f28"   # deepest background
_BG_SIDE  = "#171a22"   # sidebar / menubar background
_BG_THUMB = "#1a1d25"   # thumbnail image cell background
_BG_RAISE = "#252a36"   # elevated surface, input background
_BG_HOVER = "#2e3447"   # hover state

_BORDER   = "#2a2f3d"   # default border
_BORDER_L = "#363d52"   # lighter border / scrollbar handle

_TEXT_PRI = "#dce0ec"   # primary text
_TEXT_SEC = "#a6aec4"   # secondary / meta text

# ── Accent colours ─────────────────────────────────────────────────────────────
_ACCENT   = "#e8a020"   # amber — primary action
_ACCENT_H = "#c98518"   # amber hover
_ACCENT_T = "#0e1016"   # dark text on amber background

_TEAL     = "#2db8b0"   # teal — secondary action
_TEAL_H   = "#229991"   # teal hover

_SUCCESS  = "#3cba6e"
_ERROR    = "#e05555"
_WARNING  = "#e8a020"   # same hue as accent intentionally
_INFO     = "#5b9bd5"

# ── RGBA opacity variants (pre-computed so changing the base hex stays in sync) ─
# Accent
_ACCENT_10 = "rgba(232, 160, 32, 0.10)"
_ACCENT_12 = "rgba(232, 160, 32, 0.12)"
_ACCENT_15 = "rgba(232, 160, 32, 0.15)"
_ACCENT_18 = "rgba(232, 160, 32, 0.18)"
_ACCENT_25 = "rgba(232, 160, 32, 0.25)"
_ACCENT_30 = "rgba(232, 160, 32, 0.30)"
_ACCENT_35 = "rgba(232, 160, 32, 0.35)"

# Teal
_TEAL_12 = "rgba(45, 184, 176, 0.12)"
_TEAL_15 = "rgba(45, 184, 176, 0.15)"
_TEAL_25 = "rgba(45, 184, 176, 0.25)"
_TEAL_30 = "rgba(45, 184, 176, 0.30)"

# Success
_SUCCESS_15 = "rgba(60, 186, 110, 0.15)"
_SUCCESS_25 = "rgba(60, 186, 110, 0.25)"
_SUCCESS_30 = "rgba(60, 186, 110, 0.30)"

# Error
_ERROR_15 = "rgba(224, 85, 85, 0.15)"
_ERROR_18 = "rgba(224, 85, 85, 0.18)"
_ERROR_25 = "rgba(224, 85, 85, 0.25)"
_ERROR_30 = "rgba(224, 85, 85, 0.30)"
_ERROR_35 = "rgba(224, 85, 85, 0.35)"


_COMBO_BOX_QSS = f"""
    QComboBox {{
        background: {_BG_RAISE}; color: {_TEXT_PRI};
        selection-background-color: {_ACCENT}; selection-color: {_ACCENT_T};
        border: 1px solid {_BORDER}; border-radius: 4px;
        padding: 3px 8px; min-width: 100px;
    }}
    QComboBox:hover {{ background: {_BG_HOVER}; }}
    QComboBox:focus {{ border: 1px solid {_ACCENT}; }}
    QComboBox:disabled {{
        background: {_BG_RAISE}; color: {_TEXT_SEC};
        selection-background-color: {_BG_HOVER}; selection-color: {_TEXT_SEC};
    }}
    QComboBox::drop-down {{ border: none; width: 20px; }}
"""

_COMBO_POPUP_QSS = f"""
    QAbstractItemView {{
        background: {_BG_RAISE}; color: {_TEXT_PRI};
        border: 1px solid {_BORDER};
        selection-background-color: {_ACCENT}; selection-color: {_ACCENT_T};
    }}
    QAbstractItemView::item {{
        background: {_BG_RAISE}; color: {_TEXT_PRI}; padding: 5px 8px;
    }}
    QAbstractItemView::item:hover:!selected {{ background: {_BG_HOVER}; color: {_TEXT_PRI}; }}
    QAbstractItemView::item:selected {{ background: {_ACCENT}; color: {_ACCENT_T}; }}
    QAbstractItemView::item:disabled {{ background: {_BG_RAISE}; color: {_TEXT_SEC}; }}
"""


def apply_combo_box_theme(combo):
    """Keep native focus painting and popup rows on the same readable palette."""
    from PySide6.QtGui import QColor, QPalette
    from PySide6.QtWidgets import QStyledItemDelegate

    palette = QPalette(combo.palette())
    for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive,
                  QPalette.ColorGroup.Disabled):
        disabled = group == QPalette.ColorGroup.Disabled
        foreground = _TEXT_SEC if disabled else _TEXT_PRI
        for role in (QPalette.ColorRole.Window, QPalette.ColorRole.Base,
                     QPalette.ColorRole.AlternateBase, QPalette.ColorRole.Button):
            palette.setColor(group, role, QColor(_BG_RAISE))
        for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text,
                     QPalette.ColorRole.ButtonText):
            palette.setColor(group, role, QColor(foreground))
        palette.setColor(group, QPalette.ColorRole.Highlight, QColor(_BG_HOVER if disabled else _ACCENT))
        palette.setColor(group, QPalette.ColorRole.HighlightedText, QColor(_TEXT_SEC if disabled else _ACCENT_T))
    combo.setPalette(palette)
    combo.setStyleSheet(_COMBO_BOX_QSS)
    view = combo.view()
    # Native menu delegates can use host menu roles instead of the view's Text
    # roles. The standard item delegate keeps row states and keyboard navigation.
    combo.setItemDelegate(QStyledItemDelegate(combo))
    view.setPalette(palette)
    view.viewport().setPalette(palette)
    view.setStyleSheet(_COMBO_POPUP_QSS)
