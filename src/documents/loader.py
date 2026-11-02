"""Read-only, bounded offline PDF/image pages for document template OCR.

No input is copied into history or temporary storage. A source records its file
identity and page dimensions; rendering opens only the requested page and returns
an independent RGB image which the caller owns and must close.
"""
from __future__ import annotations

import math
import os
import stat
import threading
from collections.abc import Callable, Iterable
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

DEFAULT_MAX_FILE_SIZE_MB = 100
MAX_FILE_SIZE_MB = 1024
MAX_PAGES = 500
MAX_IMAGE_PIXELS = 64_000_000
MAX_IMAGE_SIDE = 32_768
MAX_PDF_PAGE_POINTS = 200_000
MAX_RENDER_PIXELS = 16_000_000
PREVIEW_LONG_SIDE = 1600
OCR_LONG_SIDE = 4096
SUPPORTED_IMAGE_FORMATS = frozenset({'PNG', 'JPEG', 'BMP', 'TIFF', 'WEBP'})

# PDFium's process-global state is not thread safe, including object destruction.
_PDF_LOCK = threading.RLock()


class DocumentLoadError(ValueError):
    def __init__(self, message: str, code: str = 'invalid_document'):
        super().__init__(message)
        self.code = code


class DocumentCancelled(DocumentLoadError):
    def __init__(self):
        super().__init__('已取消匯入。', 'cancelled')


@dataclass(frozen=True)
class FileIdentity:
    size: int
    mtime_ns: int
    device: int
    inode: int

    @classmethod
    def from_stat(cls, value: os.stat_result) -> FileIdentity:
        return cls(value.st_size, value.st_mtime_ns, value.st_dev, value.st_ino)


@dataclass(frozen=True)
class DocumentSource:
    path: Path
    kind: str
    page_count: int
    identity: FileIdentity
    page_sizes: tuple[tuple[float, float], ...]

    def assert_unchanged(self) -> None:
        validate_source(self)


@dataclass(frozen=True)
class DocumentPage:
    source: DocumentSource
    index: int

    def __post_init__(self):
        if (isinstance(self.index, bool) or not isinstance(self.index, int)
                or not 0 <= self.index < self.source.page_count):
            raise DocumentLoadError('找不到指定頁面。', 'invalid_page')

    @property
    def width(self) -> float:
        return self.source.page_sizes[self.index][0]

    @property
    def height(self) -> float:
        return self.source.page_sizes[self.index][1]

    @property
    def label(self) -> str:
        return f'{self.source.path.name} · 第 {self.index + 1} 頁'


def _cancelled(check: Callable[[], bool] | None) -> None:
    if check is not None and check():
        raise DocumentCancelled()


def _file_limit(value: float) -> int:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not 1 <= value <= MAX_FILE_SIZE_MB):
        raise DocumentLoadError('單檔上限必須介於 1 與 1024 MB。', 'invalid_limit')
    return int(value * 1024 * 1024)


def _page_limit(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_PAGES:
        raise DocumentLoadError('頁數上限必須介於 1 與 500 頁。', 'invalid_limit')
    return value


def _image_size(size: tuple[int, int]) -> None:
    width, height = size
    if (width <= 0 or height <= 0 or max(size) > MAX_IMAGE_SIDE
            or width * height > MAX_IMAGE_PIXELS):
        raise DocumentLoadError('圖片解析度過高，請先縮小圖片再匯入。', 'image_dimensions')


def _pdf_size(size: tuple[float, float]) -> None:
    if (len(size) != 2 or any(not math.isfinite(value) or value <= 0 for value in size)
            or max(size) > MAX_PDF_PAGE_POINTS):
        raise DocumentLoadError('PDF 頁面尺寸無效或過大。', 'page_dimensions')


def _identity(stream) -> FileIdentity:
    value = os.fstat(stream.fileno())
    if not stat.S_ISREG(value.st_mode):
        raise DocumentLoadError('請選擇一般的 PDF 或圖片檔案。', 'unsupported_file')
    return FileIdentity.from_stat(value)


def _open_readonly(path):
    # O_NONBLOCK also prevents a swapped-in FIFO from hanging an import between
    # the path stat and descriptor stat. It does not change regular-file reads.
    flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NONBLOCK', 0)
    descriptor = os.open(path, flags)
    try:
        return os.fdopen(descriptor, 'rb')
    except BaseException:
        os.close(descriptor)
        raise


def validate_source(source: DocumentSource) -> None:
    """Refuse to use a different file after the user positioned fields on it."""
    try:
        current = FileIdentity.from_stat(source.path.stat())
    except OSError:
        raise DocumentLoadError('原始檔案已移動或無法讀取，請重新匯入。', 'source_changed') from None
    if current != source.identity:
        raise DocumentLoadError('原始檔案已變更，請重新匯入。', 'source_changed')


@contextmanager
def _source_stream(source: DocumentSource):
    validate_source(source)
    try:
        stream = _open_readonly(source.path)
    except OSError:
        raise DocumentLoadError('原始檔案已移動或無法讀取，請重新匯入。', 'source_changed') from None
    try:
        if _identity(stream) != source.identity:
            raise DocumentLoadError('原始檔案已變更，請重新匯入。', 'source_changed')
        yield stream
        if _identity(stream) != source.identity:
            raise DocumentLoadError('原始檔案已變更，請重新匯入。', 'source_changed')
        validate_source(source)
    finally:
        stream.close()


def _pdf_error(exc: Exception) -> DocumentLoadError:
    if getattr(exc, 'err_code', None) == 4:
        return DocumentLoadError('這份 PDF 需要密碼，請先另存為未加密的 PDF。', 'encrypted_pdf')
    return DocumentLoadError('無法讀取這份 PDF，檔案可能已損壞。', 'invalid_pdf')


def _pdf_document(stream):
    import pypdfium2 as pdfium
    import pypdfium2_raw as pdfium_raw

    try:
        document = pdfium.PdfDocument(stream, autoclose=False)
    except (RuntimeError, OSError, ValueError) as exc:
        raise _pdf_error(exc) from None
    try:
        # Reject even owner-password PDFs whose blank user password opens them.
        if pdfium_raw.FPDF_GetSecurityHandlerRevision(document.raw) != -1:
            raise DocumentLoadError('這份 PDF 已加密，請先另存為未加密的 PDF。', 'encrypted_pdf')
        if document.get_formtype() in (pdfium_raw.FORMTYPE_XFA_FULL, pdfium_raw.FORMTYPE_XFA_FOREGROUND):
            raise DocumentLoadError(
                '目前不支援 XFA 動態表單，請先列印或另存為一般 PDF 再匯入。', 'unsupported_xfa')
        # PDFium does not draw filled AcroForm values until a form environment
        # exists. Initialize before any page/length access, with XFA disabled
        # and no JavaScript platform, action or external-resource callbacks.
        try:
            config = pdfium_raw.FPDF_FORMFILLINFO(version=2, xfa_disabled=True)
            document.init_forms(config=config)
        except (RuntimeError, ValueError, TypeError):
            raise DocumentLoadError(
                '無法顯示 PDF 表單內容，請先列印或另存為一般 PDF 再匯入。', 'form_render_failed') from None
    except Exception:
        document.close()
        raise
    return document


def _inspect_pdf(stream, max_pages, cancelled):
    with _PDF_LOCK, _pdf_document(stream) as document:
        if len(document) > max_pages:
            raise DocumentLoadError(f'這批檔案最多可匯入 {max_pages} 頁。', 'page_limit')
        sizes = []
        for index in range(len(document)):
            _cancelled(cancelled)
            try:
                size = tuple(float(v) for v in document.get_page_size(index))
            except (RuntimeError, TypeError, ValueError):
                raise DocumentLoadError('無法讀取 PDF 的頁面尺寸。', 'invalid_pdf') from None
            _pdf_size(size)
            sizes.append(size)
        return tuple(sizes)


def _inspect_image(stream, max_pages, cancelled):
    try:
        with Image.open(stream) as image:
            if image.format not in SUPPORTED_IMAGE_FORMATS:
                raise DocumentLoadError('請選擇 PNG、JPG、BMP、TIFF 或 WebP 圖片。', 'unsupported_image')
            sizes = []
            # Bounded seek also handles TIFF/APNG without asking Pillow to count
            # an unbounded number of frames before our page limit is applied.
            for index in range(max_pages + 1):
                _cancelled(cancelled)
                try:
                    image.seek(index)
                except EOFError:
                    break
                if index == max_pages:
                    raise DocumentLoadError(f'這批檔案最多可匯入 {max_pages} 頁。', 'page_limit')
                _image_size(image.size)
                width, height = image.size
                # Pillow's TIFF plugin already reports orientation-adjusted
                # dimensions before load; swapping again would distort ROIs.
                if image.format != 'TIFF' and image.getexif().get(274, 1) in (5, 6, 7, 8):
                    width, height = height, width
                sizes.append((float(width), float(height)))
            return tuple(sizes)
    except DocumentLoadError:
        raise
    except Image.DecompressionBombError:
        raise DocumentLoadError('圖片解析度過高，請先縮小圖片再匯入。', 'image_dimensions') from None
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        raise DocumentLoadError('無法讀取這張圖片，檔案可能已損壞。', 'invalid_image') from None


def load_sources(paths: Iterable[str | Path], *, max_file_size_mb: float = DEFAULT_MAX_FILE_SIZE_MB,
                 max_pages: int = MAX_PAGES, cancelled: Callable[[], bool] | None = None
                 ) -> tuple[DocumentSource, ...]:
    """Inspect files in order; the page limit applies to the whole batch.

    All size checks use MiB (1024² bytes), matching the desktop's MB limit label.
    Files stay in place and are reopened read-only for each requested render.
    """
    size_limit, page_limit = _file_limit(max_file_size_mb), _page_limit(max_pages)
    if isinstance(paths, (str, Path)):
        paths = (paths,)
    sources = []
    page_count = 0
    for value in paths:
        _cancelled(cancelled)
        if page_count >= page_limit:
            raise DocumentLoadError(f'這批檔案最多可匯入 {page_limit} 頁。', 'page_limit')
        try:
            path = Path(value).expanduser().resolve(strict=True)
            if not stat.S_ISREG(path.stat().st_mode):
                raise DocumentLoadError('請選擇一般的 PDF 或圖片檔案。', 'unsupported_file')
            with _open_readonly(path) as stream:
                identity = _identity(stream)
                if identity.size == 0:
                    raise DocumentLoadError('檔案是空的，請重新選擇。', 'empty_file')
                if identity.size > size_limit:
                    raise DocumentLoadError(f'單一檔案不可超過 {max_file_size_mb:g} MB。', 'file_limit')
                header = stream.read(1024)
                stream.seek(0)
                kind = 'pdf' if b'%PDF-' in header else 'image'
                inspect = _inspect_pdf if kind == 'pdf' else _inspect_image
                sizes = inspect(stream, page_limit - page_count, cancelled)
                if not sizes:
                    raise DocumentLoadError('檔案沒有可用的頁面。', 'empty_document')
                if _identity(stream) != identity:
                    raise DocumentLoadError('原始檔案已變更，請重新匯入。', 'source_changed')
            source = DocumentSource(path, kind, len(sizes), identity, sizes)
            validate_source(source)
            sources.append(source)
            page_count += len(sizes)
        except DocumentLoadError:
            raise
        except (OSError, TypeError, ValueError):
            raise DocumentLoadError('無法開啟檔案，請確認檔案仍在且可讀取。', 'unreadable_file') from None
    if not sources:
        raise DocumentLoadError('請先匯入 PDF 或圖片。', 'empty_selection')
    return tuple(sources)


def enumerate_pages(sources: Iterable[DocumentSource]) -> tuple[DocumentPage, ...]:
    return tuple(DocumentPage(source, index) for source in sources for index in range(source.page_count))


def _target_size(width: float, height: float, max_long_side: int, scale: float = 1.) -> tuple[int, int]:
    scale = min(scale, max_long_side / max(width, height),
                math.sqrt(MAX_RENDER_PIXELS / (width * height)))
    # Floor both dimensions so native ceil(width*scale) never exceeds our cap.
    return max(1, int(width * scale)), max(1, int(height * scale))


def _rgb_copy(image: Image.Image) -> Image.Image:
    if image.mode in ('RGBA', 'LA') or (image.mode == 'P' and 'transparency' in image.info):
        with image.convert('RGBA') as rgba:
            output = Image.new('RGB', rgba.size, 'white')
            with rgba.getchannel('A') as alpha:
                output.paste(rgba, mask=alpha)
            return output
    return image.convert('RGB')


def _render_pdf(source, index, stream, dpi, max_long_side):
    with _PDF_LOCK, _pdf_document(stream) as document:
        if len(document) != source.page_count:
            raise DocumentLoadError('原始檔案已變更，請重新匯入。', 'source_changed')
        with closing(document.get_page(index)) as page:
            width, height = page.get_size()
            _pdf_size((width, height))
            target = _target_size(width, height, max_long_side, dpi / 72.)
            scale = math.nextafter(min(target[0] / width, target[1] / height), 0.)
            # PDFium rounds the native bitmap up; target derives from a
            # floor and the final thumbnail enforces the public exact cap.
            with (closing(page.render(scale=scale, fill_color=(255, 255, 255, 255))) as bitmap,
                  bitmap.to_pil() as view):
                output = _rgb_copy(view)
            output.thumbnail(target, Image.Resampling.LANCZOS)
            return output


def _render_image(source, index, stream, max_long_side):
    with Image.open(stream) as image:
        image.seek(index)
        _image_size(image.size)
        target = _target_size(*source.page_sizes[index], max_long_side)
        # JPEG can decode directly near the desired size; other formats are
        # still bounded by their inspected source pixel budget.
        image.draft('RGB', target)
        with ImageOps.exif_transpose(image) as oriented:
            oriented.thumbnail(target, Image.Resampling.LANCZOS)
            return _rgb_copy(oriented)


def render_page(page: DocumentPage, *, preview: bool = False, dpi: float = 150,
                max_long_side: int | None = None) -> Image.Image:
    """Render one RGB page, closing PDFium/Pillow/stream resources immediately."""
    bound = PREVIEW_LONG_SIDE if preview else OCR_LONG_SIDE
    if max_long_side is not None:
        if isinstance(max_long_side, bool) or not isinstance(max_long_side, int) or max_long_side <= 0:
            raise DocumentLoadError('預覽尺寸無效。', 'invalid_render')
        bound = min(bound, max_long_side)
    if isinstance(dpi, bool) or not isinstance(dpi, (int, float)) or not math.isfinite(dpi) or dpi <= 0:
        raise DocumentLoadError('PDF 解析度無效。', 'invalid_render')
    output = None
    try:
        with _source_stream(page.source) as stream:
            if page.source.kind == 'pdf':
                output = _render_pdf(page.source, page.index, stream, dpi, bound)
            else:
                output = _render_image(page.source, page.index, stream, bound)
        return output
    except DocumentLoadError:
        if output is not None:
            output.close()
        raise
    except (OSError, ValueError, RuntimeError, IndexError):
        if output is not None:
            output.close()
        raise DocumentLoadError('無法顯示這一頁，請確認檔案完整後重新匯入。', 'render_failed') from None
