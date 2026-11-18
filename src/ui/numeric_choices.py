"""Read-only numeric choices that retain a valid value saved by an older UI."""

from PySide6.QtWidgets import QComboBox


def numeric_choices(values, current, format_value):
    combo = QComboBox()
    for value in sorted({*values, current}):
        combo.addItem(format_value(value), value)
    combo.setCurrentIndex(combo.findData(current))
    return combo
