"""Read-only numeric choices that retain a valid value saved by an older UI."""

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QPalette, QPen, QPolygon
from PySide6.QtWidgets import QComboBox, QStyle, QStyleOptionComboBox, QStylePainter


class _NumericComboBox(QComboBox):
    def paintEvent(self, event):
        painter = QStylePainter(self)
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        arrow = self.style().subControlRect(QStyle.ComplexControl.CC_ComboBox, option,
                                           QStyle.SubControl.SC_ComboBoxArrow, self).center()
        option.subControls &= ~QStyle.SubControl.SC_ComboBoxArrow
        painter.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, option)
        painter.drawControl(QStyle.ControlElement.CE_ComboBoxLabel, option)
        painter.setRenderHint(QStylePainter.RenderHint.Antialiasing)
        pen = QPen(option.palette.color(QPalette.ColorRole.ButtonText), 1.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawPolyline(QPolygon([arrow + QPoint(-4, -2), arrow + QPoint(0, 2), arrow + QPoint(4, -2)]))


def numeric_choices(values, current, format_value):
    combo = _NumericComboBox()
    for value in sorted({*values, current}):
        combo.addItem(format_value(value), value)
    combo.setCurrentIndex(combo.findData(current))
    return combo
