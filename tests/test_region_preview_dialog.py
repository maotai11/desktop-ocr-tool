"""Preview edits physical rectangles without writing screen contents."""
import pytest
from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QDialog

from src.ui.region_preview_dialog import RegionPreviewDialog


def image():
    picture = QImage(800, 600, QImage.Format.Format_RGB32)
    picture.fill(QColor('white'))
    return picture


def test_preview_move_accept_retains_only_rectangle_after_snapshot_disposal(qtbot, tmp_path):
    dialog = RegionPreviewDialog(image(), (50, 60, 120, 80, 1))
    qtbot.addWidget(dialog)
    dialog.show()
    assert dialog.nudge(1, -1)
    assert dialog.selected_region == (51, 59, 120, 80, 1)
    assert not dialog.nudge(-1000, 0)
    assert dialog.selected_region == (51, 59, 120, 80, 1)
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.selected_region == (51, 59, 120, 80, 1)
    assert dialog._image.isNull() and not dialog._view.scene().items()
    assert not list(tmp_path.iterdir())


def test_preview_drag_move_and_corner_resize_change_actual_selection(qtbot):
    dialog = RegionPreviewDialog(image(), (100, 100, 180, 100, 1))
    qtbot.addWidget(dialog)
    dialog.show()
    view = dialog._view
    start = view.mapFromScene(150, 130)
    qtbot.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
    qtbot.mouseMove(view.viewport(), start + QPoint(20, 15))
    qtbot.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=start + QPoint(20, 15))
    moved = dialog.selected_region
    assert moved[0] > 100 and moved[1] > 100 and moved[2:4] == (180, 100)
    corner = view.mapFromScene(view.rectangle.bottomRight())
    qtbot.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=corner)
    qtbot.mouseMove(view.viewport(), corner + QPoint(20, 15))
    qtbot.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=corner + QPoint(20, 15))
    resized = dialog.selected_region
    assert resized[:2] == moved[:2] and resized[2] > moved[2] and resized[3] > moved[3]
    dialog.reject()
    assert dialog._image.isNull() and not view.scene().items()


@pytest.mark.parametrize('region', [(0, 0, 4, 5, 1), (0, 0, 10, 10, 2),
                                    (799, 0, 10, 10, 1), (0, 599, 10, 10, 1)])
def test_invalid_preview_regions_fail_without_clamping_or_guessing(region):
    with pytest.raises(ValueError):
        RegionPreviewDialog(image(), region)


def test_resizing_cannot_leave_image_or_exceed_capture_budget(qtbot):
    dialog = RegionPreviewDialog(image(), (20, 20, 100, 50, 1))
    qtbot.addWidget(dialog)
    assert not dialog._view.set_rectangle(QRectF(-1, 0, 100, 50))
    assert not dialog._view.set_rectangle(QRectF(0, 0, 801, 600))
    assert not dialog._view.set_rectangle(QRectF(0, 0, 0, 100))
    assert dialog.selected_region == (20, 20, 100, 50, 1)
    dialog.reject()


def test_fractional_drag_at_screen_edge_uses_same_pixels_as_crop(qtbot):
    picture = QImage(101, 60, QImage.Format.Format_RGB32)
    picture.fill(QColor('white'))
    dialog = RegionPreviewDialog(picture, (1, 0, 99, 10, 1))
    qtbot.addWidget(dialog)
    assert dialog._view.set_rectangle(QRectF(1.5, 0, 99.5, 10))
    assert dialog.selected_region == (1, 0, 100, 10, 1)
    assert dialog._view.rectangle == QRectF(1, 0, 100, 10)
    assert picture.copy(dialog._view.rectangle.toAlignedRect()).width() == 100
    dialog.reject()
