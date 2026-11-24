"""Read the item triples actually carried by a Taiwan e-invoice QR pair."""
from dataclasses import dataclass
import math
import re

import cv2
import numpy as np
import zxingcpp
from einvoice_tw import InvoiceFormatError, parse


class InvoiceQrError(ValueError):
    """A user-facing message that contains no invoice payload."""


MAX_QR_PIXELS = 2_000_000
MAX_QR_RECOVERY_PIXELS = 1_000_000


@dataclass(frozen=True)
class InvoiceItems:
    rows: tuple[tuple[str, str, str], ...]
    summary: str


def _read_text(payload):
    # OpenCV may return UTF-8 for scanner-interpreted Latin-1. The invoice
    # parser uses its encoding flag to repair the item data after joining codes.
    try:
        return payload.decode('utf-8')
    except UnicodeDecodeError:
        return payload.decode('latin-1')


def _item_name(text, encoding):
    # OpenCV sometimes interprets Big5 as Latin-1. A one-character product
    # such as 一 (A4 40) has only one high byte, below the parser's heuristic.
    if encoding == '0' and not text.isascii():
        try:
            return text.encode('latin-1').decode('cp950')
        except UnicodeError:
            pass
    return text


def _has_unparsed_data(invoice):
    # Empty trailing separators can remain after the last complete item.
    return any(invoice.extra_fields) or any(not item.name and any(item.raw) for item in invoice.items)


def _complete_numeric_item(item):
    return bool(item.name and item.quantity is not None and item.unit_price is not None)


def _repair_item_boundary(left, right, invoice, *, raw_bytes):
    """Recover one misplaced separator only at a provable complete-row boundary."""
    count = invoice.items_in_qr
    if not right or not count or invoice.encoding_code not in ('0', '1', '3'):
        return invoice
    if sum(bool(item.name) for item in invoice.items) == count and not _has_unparsed_data(invoice):
        return invoice

    continuation = right[2:]
    if left.endswith(':') and continuation.startswith(':'):
        joined = '**' + continuation[1:]
    elif not left.endswith(':') and continuation and not continuation.startswith(':'):
        # A digit, sign, decimal separator or exponent may belong to the price
        # at the end of the left code. Never choose which code owns that digit.
        name = continuation.partition(':')[0]
        try:
            codec = 'cp950' if invoice.encoding_code == '0' else 'utf-8'
            name = name.encode('latin-1').decode(codec)
        except UnicodeError:
            pass  # OpenCV may already have returned this name as Unicode.
        if name[0].isdigit() or name[0].isspace() or name[0] in '.+-_,eE':
            return invoice
        joined = '**:' + continuation
    else:
        return invoice

    prefix = parse(left, strict=True, fix_mojibake=not raw_bytes)
    if (not 0 < len(prefix.items) < count or _has_unparsed_data(prefix)
            or not all(_complete_numeric_item(item) for item in prefix.items)):
        return invoice
    candidate = parse(left, joined, strict=True, fix_mojibake=not raw_bytes)
    if (len(candidate.items) != count or _has_unparsed_data(candidate)
            or not all(_complete_numeric_item(item) for item in candidate.items)):
        return invoice
    return candidate


def parse_items(payloads, *, raw_bytes=False, unreadable_partner=False):
    # Latin-1 is a reversible byte transport. Join both QR halves before
    # decoding their item fields, including a split inside a Chinese character.
    texts = [(value.decode('latin-1') if raw_bytes else _read_text(value))
             for value in payloads if value]
    if not texts:
        raise InvoiceQrError('沒有讀到 QR Code。請放大發票，再框住條碼和周圍白邊。')
    lefts = [s for s in texts if re.match(r'^[A-Za-z]{2}[0-9]{15}', s)]
    rights = [s for s in texts if s.startswith('**')]
    if len(texts) > 2 or len(lefts) > 1 or len(rights) > 1:
        raise InvoiceQrError('框內有多個條碼。請只框同一張發票的左右兩碼。')
    if not lefts:
        if rights:
            raise InvoiceQrError('只讀到右碼。請在來源軟體放大發票，再框住同張發票的左右兩碼。')
        raise InvoiceQrError('這個 QR Code 沒有台灣電子發票的品項格式。')
    if len(texts) != len(lefts) + len(rights):
        raise InvoiceQrError('框內包含其他 QR Code。請只框同一張發票的條碼。')
    left = lefts[0]
    right = rights[0] if rights else None
    if len(left) == 77 and right in (None, '**'):
        # A fixed header without an item section is valid on some receipts.
        try:
            parse(left)
        except InvoiceFormatError:
            raise InvoiceQrError('發票條碼格式有誤，請重新框選。') from None
        return InvoiceItems((), '左碼只有發票基本資料，沒有品項明細。')
    try:
        invoice = parse(left, right, strict=True, fix_mojibake=not raw_bytes)
        if right and left[-1].isspace() and invoice.encoding_code in ('0', '1', '3'):
            # The library strips each scanner input before joining it. At an
            # internal byte boundary this can remove a space in a product name
            # or a Latin-1 transport byte such as the final UTF-8 byte of 雅.
            invoice = parse(left + right[2:], strict=True, fix_mojibake=not raw_bytes)
        invoice = _repair_item_boundary(left, right, invoice, raw_bytes=raw_bytes)
    except InvoiceFormatError:
        raise InvoiceQrError('品項資料無法解讀。請將同張發票的左右兩碼一起框選。') from None

    incomplete_fields = _has_unparsed_data(invoice)
    items = [item for item in invoice.items if item.name]
    encoded, total = invoice.items_in_qr, invoice.items_total
    if (encoded is not None and len(items) > encoded) or (total is not None and len(items) > total):
        raise InvoiceQrError('讀到的品項超過發票記載筆數，請確認框內只有同張發票。')
    if right and (incomplete_fields or (encoded is not None and len(items) < encoded)):
        # A shifted group can look like a valid name with a price containing
        # the next item's name. Publish only the verified numeric prefix.
        items = []
        for item in invoice.items:
            if not _complete_numeric_item(item):
                break
            items.append(item)

    if raw_bytes and invoice.encoding_code in ('0', '1', '3'):
        codec = 'cp950' if invoice.encoding_code == '0' else 'utf-8'
        try:
            rows = tuple(tuple(field.encode('latin-1').decode(codec) for field in item.raw)
                         for item in items)
        except UnicodeError:
            raise InvoiceQrError('品項編碼不完整，請將同張發票的左右兩碼一起框選。') from None
    else:
        rows = tuple((_item_name(item.raw[0], invoice.encoding_code), *item.raw[1:])
                     for item in items)
    count = len(rows)

    # Without the continuation the last field could end mid-price. Only keep
    # a definitively terminated triple while more encoded items are outstanding.
    end_unconfirmed = not right and not incomplete_fields and not left.endswith(':')
    if ((unreadable_partner or encoded is not None and count < encoded) and rows
            and end_unconfirmed):
        rows = rows[:-1]
        count = len(rows)
    summary = f'已讀 {count} 項' if total is None else f'已讀 {count}／{total} 項'
    if unreadable_partner:
        summary += '　另一個條碼未能解碼，請在來源軟體放大發票，再框住左右兩碼。'
    elif not right and (encoded is None or count < encoded or incomplete_fields):
        summary += '　僅讀到左碼，請在來源軟體放大發票，再框住同張發票的左右兩碼。'
    elif incomplete_fields or (encoded is not None and count != encoded):
        if encoded is not None and count < encoded:
            summary += f'　已讀左右兩碼，條碼記載 {encoded} 項，只有 {count} 筆完整明細。請對照票面。'
        else:
            summary += '　已讀左右兩碼，末尾欄位未組成完整品項。請對照票面。'
    elif not rows:
        summary += '　條碼未附品項明細。'
    elif total is not None and count < total:
        summary += '　條碼只收錄部分品項。'
    if not right and not unreadable_partner and count == encoded and count and not incomplete_fields:
        summary += '（僅讀到左碼，末筆單價請對照右碼）' if end_unconfirmed else '（僅讀到左碼）'
    return InvoiceItems(rows, summary)


@dataclass
class _Symbol:
    payload: bytes
    corners: np.ndarray
    raw_bytes: bool

    def __post_init__(self):
        self.center = self.corners.mean(axis=0)
        self.side = max(1., float(np.linalg.norm(self.corners[1] - self.corners[0])))

    def same_position(self, other):
        return np.linalg.norm(self.center - other.center) < .35 * min(self.side, other.side)


def _check_cancel(cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        raise InvoiceQrError('已取消讀取發票品項。')


def _opencv_symbols(gray, cancel_event):
    detector = cv2.QRCodeDetector()
    _, codes, points, _ = detector.detectAndDecodeBytesMulti(gray)
    detected = points is not None
    symbols = []
    if detected:
        for i, quad in enumerate(points):
            payload = codes[i] if i < len(codes) else b''
            symbols.append(_Symbol(payload, quad, False))
    if not any(s.payload for s in symbols):
        _check_cancel(cancel_event)
        code, quad, _ = detector.detectAndDecodeBytes(gray)
        if quad is not None:
            detected = True
            _merge_symbols(symbols, [_Symbol(code, quad.reshape(4, 2), False)])
    return symbols, detected


def _zxing_symbols(gray, scale, padding):
    # Keep geometry in the source capture's coordinate system across both reads.
    results = zxingcpp.read_barcodes(gray, formats=zxingcpp.BarcodeFormat.QRCode,
                                     try_invert=False, return_errors=True)
    symbols = []
    for result in results:
        pos = result.position
        quad = np.array([(p.x, p.y) for p in (pos.top_left, pos.top_right,
                        pos.bottom_right, pos.bottom_left)], dtype=np.float32)
        # ZXing can additionally report an error-only record at (0,0) with
        # no located symbol. It cannot establish a separate physical QR.
        if abs(cv2.contourArea(quad)) < 1.:
            continue
        payload = result.bytes if result.valid else b''
        symbols.append(_Symbol(payload, (quad - padding) / scale, True))
    return symbols, bool(symbols)


def _opencv_byte_forms(payload):
    """Original bytes that OpenCV's text conversion could represent.

    ASCII invoice digits and punctuation are invariant in all three encodings.
    Do not combine incompatible headers, quantities or prices across passes.
    """
    forms = {payload}
    try:
        text = payload.decode('utf-8')
    except UnicodeError:
        return forms
    for codec in ('latin-1', 'cp950'):
        try:
            forms.add(text.encode(codec))
        except UnicodeError:
            pass
    return forms


def _merge_symbols(found, observed):
    for symbol in observed:
        for idx, old in enumerate(found):
            if not old.same_position(symbol):
                continue
            # OpenCV sometimes transcodes non-ASCII bytes. At the same physical
            # position prefer ZXing's original bytes; reject conflicting scans.
            if not symbol.payload:
                break
            if not old.payload:
                found[idx] = symbol
                break
            if old.raw_bytes == symbol.raw_bytes:
                equivalent = old.payload == symbol.payload
            else:
                raw, cv = (old, symbol) if old.raw_bytes else (symbol, old)
                equivalent = raw.payload in _opencv_byte_forms(cv.payload)
            if not equivalent:
                raise InvoiceQrError('同一個條碼讀出不同內容，請放大發票再框選。')
            if symbol.raw_bytes:
                found[idx] = symbol
            break
        else:
            found.append(symbol)


def _separate_unreadable(candidate, decoded):
    # A damaged finder can generate additional error-only polygons inside
    # a decoded QR (or straddling its corners). These are not distinct codes.
    polygon = np.asarray(candidate.corners, dtype=np.float32)
    area = cv2.contourArea(polygon)
    if area < 1.:
        return False
    for symbol in decoded:
        valid = np.asarray(symbol.corners, dtype=np.float32)
        # OpenCV can report zero intersection for nested polygons sharing an
        # edge. Check containment with a half-pixel rounding allowance.
        if all(cv2.pointPolygonTest(valid, tuple(map(float, point)), True) >= -.5
               for point in polygon):
            return False
        overlap, _ = cv2.intersectConvexConvex(polygon, valid)
        if overlap > .15 * min(area, cv2.contourArea(valid)):
            return False
    return True


def _read_symbols(symbols):
    if len(symbols) != 2:
        return parse_items([s.payload for s in symbols], raw_bytes=all(s.raw_bytes for s in symbols))
    # An invoice continuation has no identifier. Do not attach one from a
    # different row/sheet; use the QR's local horizontal axis for rotated paper.
    ordered = sorted(symbols, key=lambda s: s.payload.startswith(b'**'))
    left, right = ordered
    if re.match(rb'^[A-Za-z]{2}[0-9]{15}', left.payload) and right.payload.startswith(b'**'):
        offset = right.center - left.center
        side = max(left.side, right.side)
        left_axis = (left.corners[1] - left.corners[0]) / left.side
        right_axis = (right.corners[1] - right.corners[0]) / right.side

        def same_row(axis):
            along = float(np.dot(offset, axis))
            across = abs(float(offset[0]*axis[1] - offset[1]*axis[0]))
            return 0 < along <= 3.5 * side and across <= .65 * side

        # Both codes are printed in the same orientation on one invoice. A
        # differently rotated continuation from the next page must not make
        # the separation look horizontal in just its own coordinate system.
        if (float(np.dot(left_axis, right_axis)) < math.cos(math.radians(30))
                or not same_row(left_axis) or not same_row(right_axis)):
            raise InvoiceQrError('兩個條碼的位置無法配對，請只框同張發票並排的左右兩碼。')
    if left.raw_bytes == right.raw_bytes:
        return parse_items([left.payload, right.payload], raw_bytes=left.raw_bytes)

    # Join complete byte streams before interpreting the invoice encoding. If
    # reversing OpenCV's conversion permits two different item lists, abstain.
    raw, cv = (left, right) if left.raw_bytes else (right, left)
    results = set()
    for value in _opencv_byte_forms(cv.payload):
        try:
            results.add(parse_items([raw.payload, value], raw_bytes=True))
        except InvoiceQrError:
            continue
    if len(results) != 1:
        raise InvoiceQrError('品項內容無法完整確認，請放大同張發票的左右兩碼再框選。')
    return results.pop()


def _enhance(gray):
    # Fit the padded canvas, not only the unpadded source. Each dimension
    # inequality also holds for extremely wide or tall capture regions.
    h, w = gray.shape
    padding = 16
    border = 2 * padding
    height_limit = round(math.sqrt(MAX_QR_RECOVERY_PIXELS * h / w))
    height_limit = max(border+1, min(height_limit, MAX_QR_RECOVERY_PIXELS // (border+1)))
    width_limit = MAX_QR_RECOVERY_PIXELS // height_limit
    scale = min(3., (height_limit-border)/h, (width_limit-border)/w)
    size = max(1, int(w*scale)), max(1, int(h*scale))
    source = cv2.resize(gray, size, interpolation=cv2.INTER_AREA) if scale < 1. else gray
    # Restore local edges at the capture's sampling scale before interpolating.
    # Stretching washed-out scans helps ZXing's local threshold preserve them.
    norm = cv2.normalize(source, None, 0, 255, cv2.NORM_MINMAX)
    blurred = cv2.GaussianBlur(norm, (0, 0), .85)
    sharp = cv2.addWeighted(norm, 3., blurred, -2., 0.)
    if scale > 1.:
        sharp = cv2.resize(sharp, size, interpolation=cv2.INTER_CUBIC)
    padded = cv2.copyMakeBorder(sharp, padding, padding, padding, padding, cv2.BORDER_CONSTANT, value=255)
    return padded, np.array([size[0]/w, size[1]/h]), padding


def read_invoice_items(image, *, cancel_event=None):
    _check_cancel(cancel_event)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    # A constant frame cannot contain finder patterns. Skip all native retries.
    if gray.min() == gray.max():
        return parse_items(())
    found = []
    observed, detected = _zxing_symbols(gray, 1., 0)
    _merge_symbols(found, observed)
    if sum(bool(s.payload) for s in found) < 2:
        _check_cancel(cancel_event)
        observed, located = _opencv_symbols(gray, cancel_event)
        detected |= located
        _merge_symbols(found, observed)
    if sum(bool(s.payload) for s in found) < 2:
        _check_cancel(cancel_event)
        enhanced, scale, padding = _enhance(gray)
        _check_cancel(cancel_event)
        observed, located = _zxing_symbols(enhanced, scale, padding)
        detected |= located
        _merge_symbols(found, observed)
    if sum(bool(s.payload) for s in found) < 2:
        from .qr_recovery import recover_patches
        decoded_corners = [symbol.corners for symbol in found if symbol.payload]
        for payload, corners in recover_patches(gray, decoded_corners, lambda: _check_cancel(cancel_event)):
            _merge_symbols(found, [_Symbol(payload, corners, True)])
    _check_cancel(cancel_event)
    decoded = [s for s in found if s.payload]
    if not decoded and detected:
        raise InvoiceQrError('有找到 QR Code，但未能解碼。請在來源軟體放大發票，保留四周白邊再框選。')
    unreadable_partner = any(not s.payload and _separate_unreadable(s, decoded) for s in found)
    if len(decoded) == 2 and unreadable_partner:
        raise InvoiceQrError('框內有多個條碼。請只框同一張發票的左右兩碼。')
    if len(decoded) == 1 and unreadable_partner:
        symbol = decoded[0]
        return parse_items([symbol.payload], raw_bytes=symbol.raw_bytes, unreadable_partner=True)
    return _read_symbols(decoded)
