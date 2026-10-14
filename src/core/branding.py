"""One raster icon source for the application, windows and tray."""
from pathlib import Path
import sys

ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def resource_root():
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[2]))


def application_icon():
    from PySide6.QtGui import QIcon
    icon = QIcon()
    for size in ICON_SIZES:
        icon.addFile(str(resource_root() / f'assets/branding/inkpick-{size}.png'))
    return icon
