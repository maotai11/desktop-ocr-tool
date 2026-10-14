"""Deterministically derive runtime raster assets from the editable SVG.

Build/development only. QtSvg is never imported by the application; the app
loads PNG/ICO. Run with the pinned requirements-dev.txt environment.
"""
import hashlib
import io
import json
from pathlib import Path

from PIL import Image
from PySide6 import __version__ as qt_version
from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QRectF
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

ROOT = Path(__file__).resolve().parents[1]
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def render(root=ROOT):
    directory = Path(root) / 'assets/branding'
    source = directory / 'inkpick.svg'
    renderer = QSvgRenderer(str(source))
    if not renderer.isValid():
        raise ValueError('Invalid InkPick SVG source')
    images = []
    for size in SIZES:
        image = QImage(size * 4, size * 4, QImage.Format.Format_ARGB32)
        image.fill(0)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(painter, QRectF(0, 0, size * 4, size * 4))
        painter.end()
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not image.save(buffer, 'PNG'):
            raise RuntimeError('Unable to render InkPick PNG')
        raster = Image.open(io.BytesIO(bytes(data))).convert('RGBA')
        raster = raster.resize((size, size), Image.Resampling.LANCZOS)
        raster.save(directory / f'inkpick-{size}.png', optimize=False, compress_level=9)
        images.append(raster)
    images[-1].save(directory / 'inkpick.ico', format='ICO',
                    sizes=[(size, size) for size in SIZES], append_images=images[:-1])
    files = [source, directory / 'inkpick.ico',
             *(directory / f'inkpick-{size}.png' for size in SIZES)]
    manifest = {'schema': 1, 'concept': 'InkPick crop frame with fountain nib',
                'qt_renderer_version': qt_version, 'sizes': list(SIZES),
                'files': {path.name: {'size_bytes': path.stat().st_size,
                                      'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
                          for path in files}}
    (directory / 'manifest.json').write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return manifest


if __name__ == '__main__':
    render()
