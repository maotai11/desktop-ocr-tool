"""Real, local PDF/image decoding and lifetime checks; no network fixtures."""
import hashlib
import math
import os
import struct
import threading
import time
import zlib
from concurrent.futures import ThreadPoolExecutor

import pytest
from PIL import Image

from src.documents import loader
from src.documents.loader import (
    DocumentCancelled,
    DocumentLoadError,
    DocumentPage,
    enumerate_pages,
    load_sources,
    render_page,
)


def _image(path, size=(120, 80), color='red', *, format=None):
    with Image.new('RGB', size, color) as image:
        image.save(path, format=format)
    return path


def _pdf(path, sizes=((240, 120), (120, 240))):
    images = [Image.new('RGB', size, color) for size, color in zip(sizes, ('red', 'blue', 'green'), strict=False)]
    try:
        images[0].save(path, 'PDF', save_all=True, append_images=images[1:], resolution=72)
    finally:
        for image in images:
            image.close()
    return path


def test_pdf_and_images_preserve_input_page_order_with_independent_rgb(tmp_path):
    pdf = _pdf(tmp_path / '對帳 單.pdf')
    image = _image(tmp_path / '第三張.png', (60, 100), 'green')
    before = {path: hashlib.sha256(path.read_bytes()).digest() for path in (pdf, image)}
    sources = load_sources([pdf, image])
    assert [source.kind for source in sources] == ['pdf', 'image']
    assert [source.page_count for source in sources] == [2, 1]
    pages = enumerate_pages(sources)
    assert [(page.width, page.height) for page in pages] == [(240, 120), (120, 240), (60, 100)]
    assert pages[0].label == '對帳 單.pdf · 第 1 頁'
    assert pages[2].source.path == image
    for page, channel in zip(pages, (0, 2, 1), strict=True):
        with render_page(page, dpi=72) as rendered:
            assert rendered.mode == 'RGB'
            assert rendered.getpixel((10, 10))[channel] >= 120
    assert all(hashlib.sha256(path.read_bytes()).digest() == digest for path, digest in before.items())
    assert set(tmp_path.iterdir()) == {pdf, image}  # No cached input or rendered copies.


def test_content_sniffing_supports_renamed_pdf_and_images(tmp_path):
    pdf = _pdf(tmp_path / '並非圖片.jpg')
    image = _image(tmp_path / '並非PDF.pdf', format='PNG')
    assert [source.kind for source in load_sources([pdf, image])] == ['pdf', 'image']


def test_tiff_frames_are_pages_and_aggregate_limit_is_enforced(tmp_path):
    path = tmp_path / 'two.tiff'
    with Image.new('RGB', (80, 40), 'red') as first, Image.new('RGB', (40, 80), 'blue') as second:
        first.save(path, save_all=True, append_images=[second])
    source, = load_sources([path])
    assert source.page_count == 2
    with render_page(DocumentPage(source, 1)) as rendered:
        assert rendered.size == (40, 80) and rendered.getpixel((10, 10))[2] == 255
    other = _image(tmp_path / 'third.png')
    with pytest.raises(DocumentLoadError) as caught:
        load_sources([other, path], max_pages=2)
    assert caught.value.code == 'page_limit'


def test_first_page_and_overridden_geometry_can_use_different_document_sizes(tmp_path):
    source, = load_sources([_pdf(tmp_path / 'sizes.pdf')])
    pages = enumerate_pages([source])
    with render_page(pages[0], dpi=150) as first, render_page(pages[1], dpi=150) as second:
        assert first.width > first.height and second.width < second.height
        assert abs(first.width / first.height - 2.) < .03
        assert abs(second.width / second.height - .5) < .03


@pytest.mark.parametrize('format,suffix', [('JPEG', 'jpg'), ('TIFF', 'tiff')])
def test_image_exif_orientation_is_consistent_with_page_dimensions(tmp_path, format, suffix):
    path = tmp_path / f'oriented.{suffix}'
    with Image.new('RGB', (80, 40), 'red') as image:
        for x in range(40, 80):
            for y in range(40):
                image.putpixel((x, y), (0, 0, 255))
        exif = Image.Exif()
        exif[274] = 6
        image.save(path, format=format, exif=exif, quality=100)
    source, = load_sources([path])
    page = DocumentPage(source, 0)
    assert (page.width, page.height) == (40, 80)
    with render_page(page) as rendered:
        assert rendered.size == (40, 80)
        assert rendered.getpixel((20, 10))[0] > 200
        assert rendered.getpixel((20, 70))[2] > 200


def test_transparent_image_becomes_white_rgb(tmp_path):
    path = tmp_path / 'alpha.png'
    with Image.new('RGBA', (20, 20), (0, 0, 0, 0)) as image:
        image.putpixel((10, 10), (0, 0, 0, 255))
        image.save(path)
    page, = enumerate_pages(load_sources([path]))
    with render_page(page) as rendered:
        assert rendered.mode == 'RGB'
        assert rendered.getpixel((0, 0)) == (255, 255, 255)
        assert rendered.getpixel((10, 10)) == (0, 0, 0)


def test_default_100_mib_file_limit_precedes_any_native_pdf_parse(tmp_path, monkeypatch):
    path = tmp_path / 'oversize.pdf'
    with path.open('wb') as handle:
        handle.write(b'%PDF-1.7\n')
        handle.seek(100 * 1024 * 1024)
        handle.write(b'x')
    monkeypatch.setattr(loader, '_inspect_pdf', lambda *args: pytest.fail('PDF must not be parsed'))
    with pytest.raises(DocumentLoadError) as caught:
        load_sources([path])
    assert caught.value.code == 'file_limit'


def test_size_limit_is_per_file_not_combined_batch_bytes(tmp_path):
    paths = [_image(tmp_path / f'{number}.png') for number in range(2)]
    for path in paths:
        with path.open('ab') as stream:
            stream.write(b'\0' * (700_000 - path.stat().st_size))
    assert len(load_sources(paths, max_file_size_mb=1)) == 2
    with pytest.raises(DocumentLoadError) as caught:
        load_sources(paths, max_file_size_mb=.5)
    assert caught.value.code == 'invalid_limit'


def test_configured_size_limit_is_applied_before_image_decode(tmp_path):
    path = _image(tmp_path / 'large.png')
    with path.open('ab') as stream:
        stream.write(b'\0' * (1024 * 1024 + 1))
    with pytest.raises(DocumentLoadError) as caught:
        load_sources([path], max_file_size_mb=1)
    assert caught.value.code == 'file_limit'


def test_image_header_budget_rejects_before_pixel_decode(tmp_path, monkeypatch):
    path = _image(tmp_path / 'bomb.png')
    data = bytearray(path.read_bytes())
    # Change only IHDR dimensions/CRC; opening reads the header, not its pixels.
    data[16:24] = struct.pack('>II', 9000, 8000)
    data[29:33] = struct.pack('>I', zlib.crc32(data[12:29]))
    path.write_bytes(data)
    monkeypatch.setattr(Image.Image, 'load', lambda *args: pytest.fail('Do not decode over-budget pixels'))
    with pytest.raises(DocumentLoadError) as caught:
        load_sources([path])
    assert caught.value.code == 'image_dimensions'


@pytest.mark.parametrize('kind', ['empty', 'bad_pdf', 'bad_image', 'gif'])
def test_empty_corrupt_and_unsupported_files_fail_clearly(tmp_path, kind):
    path = tmp_path / 'input.dat'
    if kind == 'empty':
        path.write_bytes(b'')
    elif kind == 'bad_pdf':
        path.write_bytes(b'%PDF-1.7\nnot a document')
    elif kind == 'bad_image':
        path.write_bytes(b'\x89PNG\r\n\x1a\nnot an image')
    else:
        _image(path, format='GIF')
    with pytest.raises(DocumentLoadError) as caught:
        load_sources([path])
    assert caught.value.code in {'empty_file', 'invalid_pdf', 'invalid_image', 'unsupported_image'}
    assert str(tmp_path) not in str(caught.value)


def _encrypted_pdf(path):
    # A minimal Standard Security PDF with a non-matching password, no private
    # fixture dependency. The password failure happens before page rendering.
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>',
               b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
               b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >>',
               b'<< /Filter /Standard /V 1 /R 2 /Length 40 /O <' + b'00' * 32
               + b'> /U <' + b'00' * 32 + b'> /P -4 >>']
    data = bytearray(b'%PDF-1.4\n')
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data += f'{index} 0 obj\n'.encode() + obj + b'\nendobj\n'
    start = len(data)
    data += b'xref\n0 5\n0000000000 65535 f \n'
    for offset in offsets[1:]:
        data += f'{offset:010d} 00000 n \n'.encode()
    data += (b'trailer\n<< /Size 5 /Root 1 0 R /Encrypt 4 0 R /ID [<' + b'01' * 16
             + b'><' + b'01' * 16 + b'>] >>\nstartxref\n' + str(start).encode() + b'\n%%EOF\n')
    path.write_bytes(data)
    return path


def test_encrypted_pdf_has_actionable_password_error(tmp_path):
    with pytest.raises(DocumentLoadError) as caught:
        load_sources([_encrypted_pdf(tmp_path / 'locked.pdf')])
    assert caught.value.code == 'encrypted_pdf'
    assert '密碼' in str(caught.value) or '加密' in str(caught.value)


def test_changed_replaced_or_missing_source_refuses_old_field_positions(tmp_path):
    path = _image(tmp_path / 'source.png')
    source, = load_sources([path])
    page = DocumentPage(source, 0)
    path.write_bytes(path.read_bytes() + b'changed')
    with pytest.raises(DocumentLoadError) as caught:
        render_page(page)
    assert caught.value.code == 'source_changed'
    path.unlink()
    with pytest.raises(DocumentLoadError) as caught:
        source.assert_unchanged()
    assert caught.value.code == 'source_changed'


def test_replacing_same_size_source_is_detected_by_identity(tmp_path):
    path = _image(tmp_path / 'source.bmp')
    source, = load_sources([path])
    replacement = _image(tmp_path / 'replacement.bmp', color='blue')
    assert replacement.stat().st_size == source.identity.size
    replacement.replace(path)
    with pytest.raises(DocumentLoadError) as caught:
        render_page(DocumentPage(source, 0))
    assert caught.value.code == 'source_changed'


def test_preview_and_ocr_renders_obey_long_side_and_pixel_caps(tmp_path):
    source, = load_sources([_pdf(tmp_path / 'large.pdf', sizes=((4000, 4000),))])
    page = DocumentPage(source, 0)
    with render_page(page, preview=True, dpi=600) as preview:
        assert max(preview.size) <= 1600
    with render_page(page, dpi=600) as rendered:
        assert max(rendered.size) <= 4096
        assert rendered.width * rendered.height <= loader.MAX_RENDER_PIXELS
    with render_page(page, max_long_side=100, dpi=600) as small:
        assert max(small.size) <= 100


def test_pdf_native_resources_are_closed_before_returned_pixels_are_used(tmp_path, monkeypatch):
    import pypdfium2 as pdfium
    closed = {'document': 0, 'page': 0, 'bitmap': 0}
    for cls, key in ((pdfium.PdfDocument, 'document'), (pdfium.PdfPage, 'page'), (pdfium.PdfBitmap, 'bitmap')):
        original = cls.close
        def closing(self, *args, _original=original, _key=key, **kwargs):
            closed[_key] += 1
            return _original(self, *args, **kwargs)
        monkeypatch.setattr(cls, 'close', closing)
    source, = load_sources([_pdf(tmp_path / 'resources.pdf', sizes=((100, 60),))])
    rendered = render_page(DocumentPage(source, 0))
    assert closed['document'] >= 2 and closed['page'] >= 1 and closed['bitmap'] >= 1
    try:
        assert rendered.getpixel((10, 10))[0] > 200
    finally:
        rendered.close()
    # Native/Python file handles must also permit Windows rename/delete now.
    source.path.rename(tmp_path / 'renamed.pdf')


def test_pdf_render_failure_still_closes_page_document_and_input(tmp_path, monkeypatch):
    import pypdfium2 as pdfium
    path = _pdf(tmp_path / 'failure.pdf', sizes=((100, 60),))
    source, = load_sources([path])
    closed = []
    original = pdfium.PdfPage.close
    def close(self, *args, **kwargs):
        closed.append(True)
        return original(self, *args, **kwargs)
    monkeypatch.setattr(pdfium.PdfPage, 'close', close)
    monkeypatch.setattr(pdfium.PdfPage, 'render', lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('fixture')))
    with pytest.raises(DocumentLoadError) as caught:
        render_page(DocumentPage(source, 0))
    assert caught.value.code == 'render_failed' and closed
    path.rename(tmp_path / 'closed.pdf')


def test_pdfium_calls_are_serialized_between_threads(tmp_path, monkeypatch):
    import pypdfium2 as pdfium
    source, = load_sources([_pdf(tmp_path / 'threads.pdf', sizes=((100, 60),))])
    original = pdfium.PdfPage.render
    active = 0
    peak = 0
    guard = threading.Lock()
    def render(self, *args, **kwargs):
        nonlocal active, peak
        with guard:
            active += 1
            peak = max(peak, active)
        try:
            time.sleep(.01)
            return original(self, *args, **kwargs)
        finally:
            with guard:
                active -= 1
    monkeypatch.setattr(pdfium.PdfPage, 'render', render)
    def task(_):
        with render_page(DocumentPage(source, 0), dpi=72) as image:
            return image.getpixel((10, 10))[0]
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert all(value > 200 for value in pool.map(task, range(4)))
    assert peak == 1


def test_cancelled_import_closes_resources(tmp_path):
    path = _pdf(tmp_path / 'cancel.pdf')
    calls = 0
    def cancelled():
        nonlocal calls
        calls += 1
        return calls >= 3
    with pytest.raises(DocumentCancelled):
        load_sources([path], cancelled=cancelled)
    path.rename(tmp_path / 'closed.pdf')


@pytest.mark.skipif(not hasattr(os, 'mkfifo'), reason='POSIX pipe only')
def test_special_input_file_is_rejected_without_blocking_open(tmp_path):
    path = tmp_path / 'not_a_document.pdf'
    os.mkfifo(path)
    with pytest.raises(DocumentLoadError) as caught:
        load_sources([path])
    assert caught.value.code == 'unsupported_file'


def test_source_changed_during_render_discards_and_closes_result(tmp_path, monkeypatch):
    path = _image(tmp_path / 'changes.png')
    source, = load_sources([path])
    original = loader._render_image
    outputs = []
    def render(*args):
        image = original(*args)
        outputs.append(image)
        path.write_bytes(path.read_bytes() + b'changed')
        return image
    monkeypatch.setattr(loader, '_render_image', render)
    with pytest.raises(DocumentLoadError) as caught:
        render_page(DocumentPage(source, 0))
    assert caught.value.code == 'source_changed'
    with pytest.raises(ValueError, match='closed image'):
        outputs[0].getpixel((0, 0))


@pytest.mark.parametrize('value', [True, 0, -1, 1025, math.inf, '100'])
def test_invalid_file_limit_fails_before_opening(value):
    with pytest.raises(DocumentLoadError) as caught:
        load_sources(['/missing'], max_file_size_mb=value)
    assert caught.value.code == 'invalid_limit'


@pytest.mark.parametrize('index', [-1, 1, True, .5])
def test_invalid_page_index_is_rejected(tmp_path, index):
    source, = load_sources([_image(tmp_path / 'page.png')])
    with pytest.raises(DocumentLoadError) as caught:
        DocumentPage(source, index)
    assert caught.value.code == 'invalid_page'


@pytest.mark.parametrize('dpi', [0, -1, True, math.inf, math.nan, '150'])
def test_invalid_render_dpi_is_rejected(tmp_path, dpi):
    page, = enumerate_pages(load_sources([_image(tmp_path / 'page.png')]))
    with pytest.raises(DocumentLoadError):
        render_page(page, dpi=dpi)
