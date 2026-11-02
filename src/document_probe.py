"""Synthetic PDFium/image/region/template packaging probe, without OCR inference.

The fixture is generated locally, has no fonts or external resources, and is
removed before success is recorded. The report contains no paths or input text.
"""
from __future__ import annotations

import hashlib
import sys
import tempfile
from pathlib import Path

from PIL import Image

from .core.validation_report import executable_sha256, write_validation_report
from .core.version import APP_VERSION
from .documents.loader import enumerate_pages, load_sources, render_page
from .documents.template import Field, Region, TemplateLayout, build_output

CHECK_NAMES = (
    'pdf_rendering',
    'image_rendering',
    'region_inheritance',
    'page_override',
    'template_merge',
    'source_unchanged',
    'temporary_files_cleaned',
)


def _write_fixture_pdf(path: Path) -> None:
    """Two different page sizes, each with solid red/blue halves."""
    first = b'1 0 0 rg 0 0 60 80 re f\n0 0 1 rg 60 0 60 80 re f\n'
    second = b'0 0 1 rg 0 0 120 160 re f\n1 0 0 rg 120 0 120 160 re f\n'

    def stream(content):
        return f'<< /Length {len(content)} >>\nstream\n'.encode() + content + b'endstream'

    objects = (
        b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R 5 0 R] /Count 2 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 120 80] /Resources << >> /Contents 4 0 R >>',
        stream(first),
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 240 160] /Resources << >> /Contents 6 0 R >>',
        stream(second),
    )
    document = bytearray(b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n')
    offsets = []
    for index, body in enumerate(objects, 1):
        offsets.append(len(document))
        document.extend(f'{index} 0 obj\n'.encode() + body + b'\nendobj\n')
    xref = len(document)
    document.extend(f'xref\n0 {len(objects) + 1}\n0000000000 65535 f \n'.encode())
    for offset in offsets:
        document.extend(f'{offset:010d} 00000 n \n'.encode())
    document.extend((f'trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n'
                     f'startxref\n{xref}\n%%EOF\n').encode())
    path.write_bytes(document)


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _color_label(image: Image.Image, region: Region) -> str:
    """A fixed pixel oracle, deliberately independent of OCR/model inference."""
    expected = {
        ((255, 255), (0, 0), (0, 0)): '紅色',
        ((0, 0), (0, 0), (255, 255)): '藍色',
        ((0, 0), (255, 255), (0, 0)): '綠色',
    }
    with image.crop(region.pixel_bounds(*image.size)) as cropped:
        return expected[cropped.getextrema()]


def run_document_probe(report_path) -> int:
    """Exercise the real bundled PDFium renderer and local template lifecycle."""
    report = {
        'schema': 1,
        'probe': 'document_template_lifecycle',
        'version': APP_VERSION,
        'platform': sys.platform,
        'frozen': bool(getattr(sys, 'frozen', False)),
        'executable_sha256': None,
        'clean_machine_verified': False,
        'passed': False,
        'fixture_kind': 'SYNTHETIC_SOLID_RECTANGLES',
        'ocr_accuracy_tested': False,
        'model_inference_tested': False,
        'checks': {key: False for key in CHECK_NAMES},
    }
    directory = None
    try:
        report['executable_sha256'] = executable_sha256()
        # A native crash or interrupted probe must not leave an older PASS.
        write_validation_report(report_path, report)
        with tempfile.TemporaryDirectory(prefix='inkpick-doc-probe-') as temporary:
            directory = Path(temporary)
            pdf = directory / 'synthetic-pages.pdf'
            png = directory / 'synthetic-image.png'
            _write_fixture_pdf(pdf)
            with Image.new('RGB', (180, 120), (0, 255, 0)) as image:
                image.save(png)
            before = {path.name: _hash(path) for path in (pdf, png)}
            sources = load_sources([pdf, png])
            pages = enumerate_pages(sources)
            if ([source.kind for source in sources] != ['pdf', 'image'] or
                    [source.page_count for source in sources] != [2, 1] or len(pages) != 3):
                raise ValueError('Unexpected synthetic document shape')
            left = Region(.125, .25, .25, .5)
            right = Region(.625, .25, .25, .5)
            layout = TemplateLayout([Field('辨識1', left), Field('辨識2', right)])
            values = []
            sizes = [(120, 80), (240, 160), (180, 120)]
            expected = [('紅色', '藍色'), ('紅色', '紅色'), ('綠色', '綠色')]
            for index, page in enumerate(pages):
                with render_page(page, dpi=72) as rendered:
                    if rendered.mode != 'RGB' or rendered.size != sizes[index]:
                        raise ValueError('Unexpected synthetic raster')
                    if index == 1:
                        if (layout.region_for('辨識1', index) != left or
                                left.pixel_bounds(*rendered.size) != (30, 40, 90, 120) or
                                _color_label(rendered, left) != '藍色'):
                            raise ValueError('First-page selection was not inherited')
                        layout.set_region('辨識1', right, page_index=index)
                        report['checks']['page_override'] = (
                            layout.region_for('辨識1', index) == right and
                            layout.region_for('辨識2', index) == right and
                            layout.has_override('辨識1', index) and
                            not layout.has_override('辨識2', index))
                    current = {name: _color_label(rendered, region)
                               for name, region in layout.effective_regions(index).items()}
                    if tuple(current.values()) != expected[index]:
                        raise ValueError('Synthetic crop differs from fixed color oracle')
                    values.append(current)
                    if index == 1:
                        report['checks']['pdf_rendering'] = True
                    elif index == 2:
                        report['checks']['image_rendering'] = True
                        report['checks']['region_inheritance'] = (
                            layout.region_for('辨識1', index) == left and
                            layout.region_for('辨識2', index) == right and
                            not layout.has_override('辨識1', index))
            result = build_output('文件｛辨識1}／{辨識2｝\n完成', values)
            report['checks']['template_merge'] = result == (
                '文件紅色／藍色\n完成\n\n文件紅色／紅色\n完成\n\n文件綠色／綠色\n完成')
            for source in sources:
                source.assert_unchanged()
            report['checks']['source_unchanged'] = all(
                _hash(path) == before[path.name] for path in (pdf, png))
        report['checks']['temporary_files_cleaned'] = not directory.exists()
        report['passed'] = all(report['checks'].values())
        if not report['passed']:
            report['error_code'] = 'DOCUMENT_PROBE_CHECK_FAILED'
    except Exception:  # noqa: BLE001 - native/runtime failures become fixed public error codes
        # The report is public-capable. Never export native errors, temporary
        # filenames, user directories or a traceback into this small probe.
        report['error_code'] = 'DOCUMENT_PROBE_CHECK_FAILED'
        report['passed'] = False
        if directory is not None:
            report['checks']['temporary_files_cleaned'] = not directory.exists()
    try:
        write_validation_report(report_path, report)
    except (OSError, TypeError, ValueError):
        return 1
    return 0 if report['passed'] else 1
