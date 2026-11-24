"""Synthetic receipts only. Exercise real QR decoding as well as partial payloads."""
import base64
from threading import Event

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


def _receipt_pair():
    return left('乾電池:1:105:').encode(), '**口罩:1:210:鮮奶:0.5:25.00'.encode()


@pytest.mark.parametrize('scale,sigma', [(.40, .7), (.48, .5), (.56, .9)],
                         ids=['neither-code', 'left-only', 'right-only'])
def test_small_blurred_invoice_recovers_both_symbols(scale, sigma):
    # rc.21 respectively read zero, one left, or one right symbol at these
    # three viewer scales. Success needs the entire ordered item list.
    image = cv2.resize(screen(*_receipt_pair()), None, fx=scale, fy=scale,
                       interpolation=cv2.INTER_AREA)
    blurred = cv2.GaussianBlur(image, (0, 0), sigma)
    assert read_invoice_items(blurred).rows == (
        ('乾電池', '1', '105'), ('口罩', '1', '210'), ('鮮奶', '0.5', '25.00'))


@pytest.mark.parametrize('codec,flag', [('big5', 0), ('utf8', 1)])
def test_qr_split_in_middle_of_product_character(codec, flag):
    tail = '茶葉:1:35.50'.encode(codec)
    first = left('', flag, encoded=1, total=1).encode() + tail[:1]
    second = b'**' + tail[1:]
    assert read_invoice_items(screen(first, second)).rows == (('茶葉', '1', '35.50'),)


def test_tightly_cropped_small_symbol_with_all_modules_present():
    image = screen(left('收納盒:1:39.00', encoded=1, total=1).encode())
    ys, xs = np.nonzero(image[:, :, 0] == 0)
    cropped = image[ys.min():ys.max()+1, xs.min():xs.max()+1]
    small = cv2.resize(cropped, None, fx=.6, fy=.6, interpolation=cv2.INTER_AREA)
    soft = cv2.GaussianBlur(small, (0, 0), .7)
    assert read_invoice_items(soft).rows == (('收納盒', '1', '39.00'),)


def test_unrecoverable_continuation_keeps_only_confirmed_left_items():
    first, second = _receipt_pair()
    image = screen(first, second)
    split = screen(first).shape[1]
    # Destroy the continuation's data while leaving its finder patterns.
    side = image[:, split:]
    ys, xs = np.nonzero(side[:, :, 0] == 0)
    x1, x2 = xs.min(), xs.max()+1
    y1, y2 = ys.min(), ys.max()+1
    side[y1+40:y2, x1+40:x2] = 255
    partial = read_invoice_items(image)
    assert partial.rows == (('乾電池', '1', '105'),)
    assert '1／3' in partial.summary


@pytest.mark.parametrize('has_prior', [False, True])
def test_damaged_continuation_never_publishes_part_of_last_price(has_prior):
    prior_fields = '前項:2:30.00:' if has_prior else ''
    prior_rows = (('前項', '2', '30.00'),) if has_prior else ()
    count = len(prior_rows) + 1
    first = left(prior_fields + '烏龍:1:1', encoded=count, total=count).encode()
    image = screen(first, b'**99')
    assert read_invoice_items(image).rows == (*prior_rows, ('烏龍', '1', '199'))

    # Keep the right symbol's finders intact but break its checksum by
    # occluding the central quarter of its data area.
    side = image[:, screen(first).shape[1]:]
    ys, xs = np.nonzero(side[:, :, 0] == 0)
    cy = (ys.min() + ys.max() + 1) // 2
    cx = (xs.min() + xs.max() + 1) // 2
    radius = round((ys.max() - ys.min() + 1) / 8)
    side[cy-radius:cy+radius, cx-radius:cx+radius] = 127

    result = read_invoice_items(image)
    assert result.rows == prior_rows
    assert f'{len(prior_rows)}／{count}' in result.summary
    assert '框' in result.summary


def test_detected_but_damaged_code_reports_unreadable_data():
    image = screen(left('麵包:1:10:', encoded=1, total=1).encode())
    ys, xs = np.nonzero(image[:, :, 0] == 0)
    start_x, start_y = xs.min(), ys.min()
    end_x, end_y = xs.max() + 1, ys.max() + 1
    keep = round((end_x - start_x) * .35)
    # Keep all three finder patterns, overwrite most of the lower right data.
    image[start_y+keep:end_y-7, start_x+keep:end_x-7] = 255
    with pytest.raises(InvoiceQrError, match='未能解碼') as error:
        read_invoice_items(image)
    assert '放大' in str(error.value)


def test_already_cancelled_invoice_does_not_publish_a_readable_pair():
    event = Event()
    event.set()
    with pytest.raises(InvoiceQrError, match='取消'):
        read_invoice_items(screen(*_receipt_pair()), cancel_event=event)


def test_worker_cancellation_after_first_decode_skips_retries_and_publication(qtbot, file_mgr, monkeypatch):
    from src.invoice_check import qr_items
    from src.workers.capture_worker import CaptureWorker

    event = Event()
    worker = CaptureWorker(file_mgr)
    image = screen(*_receipt_pair())
    seen, published = [], []

    def stop_after_read(*_):
        seen.append('original')
        event.set()
        return [], False

    def unexpected_retry(*_):
        seen.append('retry')
        return [], False

    monkeypatch.setattr(qr_items, '_zxing_symbols', stop_after_read)
    monkeypatch.setattr(qr_items, '_opencv_symbols', unexpected_retry)
    monkeypatch.setattr('src.invoice_check.capture.capture_regions',
                        lambda *args, **kwargs: {'images': {'invoice': image}})
    monkeypatch.setattr('src.core.resource_budget.memory_preflight', lambda **_: None)
    worker.invoice_items_ready.connect(lambda *args: published.append(args))
    h, w = image.shape[:2]
    try:
        with qtbot.waitSignal(worker.finished, timeout=5000):
            worker.capture_invoice_items((0, 0, w, h, 1), None, event)
            worker.stop()
        assert seen == ['original']
        assert event.is_set() and not published
    finally:
        worker.stop()
        assert worker.wait(5000)


def test_same_position_read_by_both_decoders_requires_equivalent_bytes():
    from src.invoice_check.qr_items import _Symbol, _merge_symbols

    corners = np.array([(10, 10), (60, 10), (60, 60), (10, 60)], dtype=np.float32)
    payload = left('茶葉:1:35.50', 0, encoded=1, total=1)
    raw = _Symbol(payload.encode('big5'), corners, True)
    converted = raw.payload.decode('latin-1').encode('utf-8')
    found = [raw]
    _merge_symbols(found, [_Symbol(converted, corners + 1, False)])
    assert len(found) == 1 and found[0].payload == raw.payload
    with pytest.raises(InvoiceQrError, match='不同內容'):
        _merge_symbols(found, [_Symbol(converted.replace(b'35.50', b'85.50'), corners + 1, False)])


def test_distant_continuation_from_another_row_cannot_complete_receipt():
    first, second = _receipt_pair()
    top, bottom = screen(first), screen(second)
    image = np.full((top.shape[0] + bottom.shape[0] + 80,
                     top.shape[1] + bottom.shape[1], 3), 255, np.uint8)
    image[:top.shape[0], :top.shape[1]] = top
    image[-bottom.shape[0]:, -bottom.shape[1]:] = bottom
    with pytest.raises(InvoiceQrError, match='配對'):
        read_invoice_items(image)


def test_two_identical_left_symbols_are_not_deduplicated_across_positions():
    first, second = _receipt_pair()
    with pytest.raises(InvoiceQrError):
        read_invoice_items(screen(first, second, first))


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
