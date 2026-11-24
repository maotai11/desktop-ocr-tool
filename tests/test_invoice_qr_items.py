"""Synthetic receipts only. Exercise real QR decoding as well as partial payloads."""
import base64

import cv2
import numpy as np
import pytest
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QGuiApplication

from src.invoice_check.qr_items import InvoiceQrError, parse_items, read_invoice_items
from test_floating_shutdown_entries import running_pipeline  # noqa: F401

HEADER = 'AB112233441020523999900000144000001540000000001234567' + 'A' * 22 + '=='


def left(tail, encoding=1, encoded=3, total=3):
    return f'{HEADER}:**********:{encoded}:{total}:{encoding}:{tail}'


def screen(*codes):
    symbols = []
    for code in codes:
        modules = cv2.QRCodeEncoder.create().encode(code)
        bordered = cv2.copyMakeBorder(modules, 4, 4, 4, 4, cv2.BORDER_CONSTANT, value=255)
        symbols.append(cv2.resize(bordered, None, fx=5, fy=5, interpolation=cv2.INTER_NEAREST))
    image = np.full((max(s.shape[0] for s in symbols) + 20,
                     sum(s.shape[1] + 20 for s in symbols)), 255, np.uint8)
    x = 0
    for symbol in symbols:
        h, w = symbol.shape
        image[10:h+10, x:x+w] = symbol
        x += w + 20
    return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)


@pytest.mark.parametrize('codec,flag', [('big5', 0), ('utf8', 1), ('utf8', 3)])
def test_dual_qr_reads_items_in_invoice_order_and_preserves_values(codec, flag):
    image = screen(left('乾電池:1:105:', flag).encode(codec), '**口罩:1:210:鮮奶:0.5:25.00'.encode(codec))
    result = read_invoice_items(image)
    assert result.rows == (('乾電池', '1', '105'), ('口罩', '1', '210'), ('鮮奶', '0.5', '25.00'))
    assert '3／3' in result.summary


def test_single_big5_item_and_base64_continuation():
    assert read_invoice_items(screen(left('一:1:2', 0, 1, 1).encode('big5'))).rows == (('一','1','2'),)
    raw = base64.b64encode('烏龍茶:2:30.50'.encode()).decode()
    parts = ('**' + raw[7:], left(raw[:7], 2, 1, 1))
    assert parse_items([c.encode() for c in parts]).rows == (('烏龍茶', '2', '30.50'),)


def test_partial_codes_keep_only_available_items():
    result = parse_items([left('麵包:1:35:茶:2').encode()])
    assert result.rows == (('麵包','1','35'),)
    assert '1／3' in result.summary and '左右兩碼' in result.summary
    truncated_price = parse_items([left('麵包:1:35:茶:2:1').encode()])
    assert truncated_price.rows == result.rows
    partial = parse_items([left('麵包:1:35:', encoded=1).encode(), b'**'])
    assert '條碼只收錄部分品項' in partial.summary
    assert not parse_items([HEADER.encode()]).rows
    empty = parse_items([left('', encoded=0, total=2).encode()])
    assert not empty.rows and '未附' in empty.summary


@pytest.mark.parametrize('codes,message', [
    ([], '沒有讀到'), ([b'**'], '右碼'), ([b'https://example.org'], '格式'),
    ([left(''), left('')], '同一張'), ([left(''), b'https://example.org'], '其他'),
    ([left(''), b'**', b'**'], '同一張'),
])
def test_unsupported_or_ambiguous_codes(codes, message):
    with pytest.raises(InvoiceQrError, match=message):
        parse_items([s.encode() if isinstance(s, str) else s for s in codes])


def test_no_code_on_screen():
    with pytest.raises(InvoiceQrError, match='沒有讀到'):
        read_invoice_items(np.full((100,120,3),255,np.uint8))


def test_floating_entry_through_worker_preview_copy_retry_escape(qtbot, running_pipeline, monkeypatch):
    pipeline, widget, overlay = running_pipeline
    image = screen(left('茶-葉:1:25.50:', encoded=1, total=1).encode())
    h, w = image.shape[:2]
    def capture(regions, **kwargs):
        assert regions == {'invoice': (0, 0, w, h, 1)}
        assert kwargs['expected_monitor'] is not None
        return {'images': {'invoice': image}}
    monkeypatch.setattr('src.invoice_check.capture.capture_regions', capture)
    captures = []
    pipeline.capture.capture_done.connect(lambda *args: captures.append(args))
    copies = []
    monkeypatch.setattr('src.ui.invoice_items_dialog.write_text_to_clipboard', copies.append)
    widget.show()
    qtbot.mouseClick(widget._btn_invoice_items, Qt.MouseButton.LeftButton)
    assert overlay.isVisible() and not widget.isVisible()
    # Feed physical selection via the selector's normal compositor-delayed delivery.
    scale = QGuiApplication.primaryScreen().devicePixelRatio()
    rect = QRect(0, 0, round(w / scale), round(h / scale))
    # Physical dimensions can round to an adjacent pixel at 200%/fractional DPI.
    w, h = int(rect.width()*scale), int(rect.height()*scale)
    overlay._finish_capture(rect)
    qtbot.waitUntil(lambda: pipeline._qr_dialog is not None and pipeline._qr_dialog.copy_button.isEnabled())
    dialog = pipeline._qr_dialog
    assert widget.isVisible() and dialog.table.item(0,0).text() == '茶-葉'
    assert not captures and not copies
    qtbot.mouseClick(dialog.copy_button, Qt.MouseButton.LeftButton)
    assert copies == ['品名\t數量\t單價\n茶-葉\t1\t25.50']
    request = pipeline._qr_request
    dialog.retry_requested.emit()
    assert request.is_set() and overlay.isVisible() and pipeline._qr_dialog is None
    pipeline._qr_finished(request, None, 'stale')
    assert pipeline._qr_dialog is None
    qtbot.keyClick(overlay, Qt.Key.Key_Escape)
    assert widget.isVisible() and pipeline._qr_request is None


def test_closed_busy_preview_ignores_completion(qtbot, running_pipeline, monkeypatch):
    pipeline, widget, overlay = running_pipeline
    pending = []
    monkeypatch.setattr(pipeline.capture, 'capture_invoice_items', lambda *args: pending.append(args[-1]))
    widget.show()
    widget._btn_invoice_items.click()
    overlay._finish_capture(QRect(0, 0, 100, 100))
    qtbot.waitUntil(lambda: len(pending) == 1)
    request = pending[0]
    pipeline.capture.invoice_frame_ready.emit(request)
    assert pipeline._qr_dialog.isVisible()
    pipeline._qr_dialog.close()
    assert request.is_set()
    pipeline.capture.invoice_items_ready.emit(request, None, 'late')
    assert pipeline._qr_dialog is None and widget.isVisible()


def test_error_without_capture_restores_launcher_and_allows_retry(qtbot, running_pipeline, monkeypatch):
    pipeline, widget, overlay = running_pipeline
    def reject(*_):
        raise RuntimeError('工作佇列已滿')
    monkeypatch.setattr(pipeline.capture, 'capture_invoice_items', reject)
    widget.show()
    widget._btn_invoice_items.click()
    overlay._finish_capture(QRect(0, 0, 100, 100))
    qtbot.waitUntil(lambda: pipeline._qr_dialog is not None)
    dialog = pipeline._qr_dialog
    assert dialog.summary.text() == '工作佇列已滿'
    assert not dialog.copy_button.isEnabled() and widget.isVisible()
    dialog.retry_requested.emit()
    pipeline.start_region_capture('region_image')
    assert pipeline._qr_request is None and overlay.selection_prompt == ''
