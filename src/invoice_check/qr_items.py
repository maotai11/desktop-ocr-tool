"""Read the item triples actually carried by a Taiwan e-invoice QR pair."""
from dataclasses import dataclass
import re

import cv2
from einvoice_tw import InvoiceFormatError, parse


class InvoiceQrError(ValueError):
    """A user-facing message that contains no invoice payload."""


HEADERS = ('品名', '數量', '單價')
MAX_QR_PIXELS = 2_000_000


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


def parse_items(payloads):
    texts = [_read_text(value) for value in payloads if value]
    if not texts:
        raise InvoiceQrError('沒有讀到 QR Code。請放大發票，再框住條碼和周圍白邊。')
    lefts = [s for s in texts if re.match(r'^[A-Za-z]{2}[0-9]{15}', s)]
    rights = [s for s in texts if s.startswith('**')]
    if len(texts) > 2 or len(lefts) > 1 or len(rights) > 1:
        raise InvoiceQrError('框內有多個條碼。請只框同一張發票的左右兩碼。')
    if not lefts:
        if rights:
            raise InvoiceQrError('只讀到右碼。請把同一張發票的左碼也框進來。')
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
        invoice = parse(left, right, strict=True)
    except InvoiceFormatError:
        raise InvoiceQrError('品項資料無法解讀。請將同張發票的左右兩碼一起框選。') from None

    rows = tuple((_item_name(item.raw[0], invoice.encoding_code), *item.raw[1:])
                 for item in invoice.items if item.name)
    count, encoded, total = len(rows), invoice.items_in_qr, invoice.items_total
    if (encoded is not None and count > encoded) or (total is not None and count > total):
        raise InvoiceQrError('讀到的品項超過發票記載筆數，請確認框內只有同張發票。')

    # Without the continuation the last field could end mid-price. Only keep
    # a definitively terminated triple while more encoded items are outstanding.
    if (not right and encoded is not None and count < encoded and rows
            and not invoice.extra_fields and not left.endswith(':')):
        rows = rows[:-1]
        count = len(rows)
    summary = f'已讀 {count} 項' if total is None else f'已讀 {count}／{total} 項'
    if not right and (encoded is None or count < encoded or invoice.extra_fields):
        summary += '　請將同張發票的左右兩碼一起框選。'
    elif invoice.extra_fields or (encoded is not None and count != encoded):
        summary += '　條碼內有未能讀全的明細。'
    elif not rows:
        summary += '　條碼未附品項明細。'
    elif total is not None and count < total:
        summary += '　條碼只收錄部分品項。'
    if not right and count and (count == encoded) and not invoice.extra_fields:
        summary += '（僅讀到左碼）'
    return InvoiceItems(rows, summary)


def read_invoice_items(image):
    detector = cv2.QRCodeDetector()
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    ok, codes, _, _ = detector.detectAndDecodeBytesMulti(gray)
    if not ok or not any(codes):
        code, _, _ = detector.detectAndDecodeBytes(gray)
        codes = (code,)
    return parse_items(codes)
