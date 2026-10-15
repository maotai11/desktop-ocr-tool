"""Real Qt mouse/clipboard/protocol tests; fake helper tests are not model accuracy."""
from pathlib import Path
import struct
import sys

import pytest
from PySide6.QtCore import QPoint, Qt

from src.handwriting.assets import HelperAssets
from src.handwriting.controller import HandwritingController
from src.handwriting.protocol import (HEADER, MAX_POINTS, ProtocolError, READY, RESULT,
                                      ReplyDecoder, decode_candidates, encode_strokes, frame)
from src.handwriting.strokes import StrokeBuffer


def payload(label='臺', score=1):
    data = label.encode('utf-8')
    return struct.pack('<HHB', 0, 1, len(data)) + data + struct.pack('<f', score)


@pytest.mark.parametrize('label', ['臺', '𠮷', '禰\U000e0100'])
def test_reply_fragmentation_and_unicode_exact(label):
    message = frame(RESULT, 8, payload(label))
    for split in range(1, len(message)):
        decoder = ReplyDecoder()
        assert decoder.feed(message[:split]) is None
        reply = decoder.feed(message[split:])
        assert reply.generation == 8
        assert decode_candidates(reply.payload, {label}) == (0, (label,))


@pytest.mark.parametrize('data', [b'', b'\0\0', b'\0\0\x0b\0', payload(score=float('nan')),
                                payload() + b'x', struct.pack('<HHB', 0, 1, 1) + b'\xff' + b'\0' * 4])
def test_malformed_reply_rejected(data):
    with pytest.raises(ProtocolError):
        decode_candidates(data, {'臺'})


@pytest.mark.parametrize('strokes', [[], [[(-1, 0)]], [[(1001, 0)]], [[(True, 0)]],
                                   [[]], [[(0, 0)] * 513], [[(0, 0)]] * 129])
def test_stroke_protocol_caps(strokes):
    with pytest.raises(ProtocolError):
        encode_strokes(strokes)


def test_buffer_bounds_immutable_snapshot_and_latest_endpoint():
    buffer = StrokeBuffer()
    assert buffer.begin(0, 0)
    for i in range(20000):
        buffer.append(i % 1001, (i * 17) % 1001)
    assert buffer.active[-1] == (19999 % 1001, (19999 * 17) % 1001)
    buffer.finish()
    snapshot = buffer.snapshot()
    assert len(snapshot[0]) <= 512
    encode_strokes(snapshot)
    buffer.clear()
    assert snapshot and not buffer.snapshot()


def test_pad_mouse_square_mapping_undo_and_clear(qtbot):
    from src.ui.handwriting_pad import HandwritingPad
    pad = HandwritingPad()
    qtbot.addWidget(pad)
    pad.resize(400, 300)
    pad.show()
    qtbot.mouseClick(pad, Qt.MouseButton.LeftButton, pos=QPoint(1, 1))
    assert not pad.buffer.strokes
    rect = pad.canvas_rect()
    start = rect.topLeft().toPoint()
    qtbot.mousePress(pad, Qt.MouseButton.LeftButton, pos=start)
    qtbot.mouseRelease(pad, Qt.MouseButton.LeftButton, pos=rect.bottomRight().toPoint())
    assert pad.buffer.strokes[0][0] == (0, 0)
    assert pad.buffer.strokes[0][-1] == (1000, 1000)
    pad.undo()
    assert not pad.buffer.strokes
    qtbot.mouseClick(pad, Qt.MouseButton.LeftButton, pos=rect.center().toPoint())
    assert len(pad.buffer.strokes) == 1
    pad.clear()
    assert not pad.buffer.strokes


FAKE_HELPER = r'''
import struct,sys,time
H=struct.Struct('<4sBBHQI')
def read():
 h=sys.stdin.buffer.read(20)
 if not h: return None
 magic,version,kind,reserved,generation,size=H.unpack(h)
 return kind,generation,sys.stdin.buffer.read(size)
def send(kind,generation,payload):
 sys.stdout.buffer.write(H.pack(b'ZHW1',2,kind,0,generation,len(payload))+payload)
 sys.stdout.buffer.flush()
mode=sys.argv[1]
read()
send(2,0,struct.pack('<HH',0,0))
while True:
 request=read()
 if request is None or request[0]==5: break
 kind,generation,data=request
 if mode=='hang':
  sys.stdout.buffer.write(b'Z');sys.stdout.buffer.flush();time.sleep(10);break
 if mode=='stderr':
  sys.stderr.buffer.write(b'x'*20000);sys.stderr.buffer.flush();time.sleep(10);break
 if mode=='crash': sys.exit(2)
 if mode=='delay': time.sleep(.08)
 label='臺'.encode()
 output=struct.pack('<HHB',0,1,len(label))+label+struct.pack('<f',1.)
 send(4,generation+(1 if mode=='generation' else 0),output)
'''


@pytest.fixture
def fake_controller(qtbot, tmp_path):
    script = tmp_path / 'fake_helper.py'
    script.write_text(FAKE_HELPER, encoding='utf-8')
    created = []
    def make(mode='delay'):
        assets = HelperAssets(Path(sys.executable).resolve(), (str(script), mode), frozenset({'臺'}))
        controller = HandwritingController(assets=assets, load_timeout_ms=1500, request_timeout_ms=250)
        created.append(controller)
        return controller
    yield make
    for controller in created:
        controller.stop()
        qtbot.waitUntil(lambda c=controller: c.is_stopped, timeout=3000)


def test_controller_one_active_one_replaceable_pending(qtbot, fake_controller):
    controller = fake_controller()
    results = []
    controller.candidates.connect(lambda *value: results.append(value))
    first = controller.submit((((1, 1),),))
    qtbot.waitUntil(lambda: controller._active is not None)
    second = controller.submit((((2, 2),),))
    third = controller.submit((((3, 3),),))
    qtbot.waitUntil(lambda: bool(results), timeout=3000)
    assert first < second < third
    assert results[0][0] == third
    assert tuple(item.label for item in results[0][1]) == ('臺',)


def test_failed_start_during_immediate_shutdown_releases_owner(qtbot, tmp_path):
    controller = HandwritingController(assets=HelperAssets(tmp_path / 'missing-helper', (), frozenset({'臺'})))
    controller.submit((((1, 1),),))
    controller.stop()
    qtbot.waitUntil(lambda: controller.is_stopped, timeout=3000)


@pytest.mark.parametrize('mode,phrase', [('hang', '逾時'), ('stderr', '限制'),
                                       ('generation', '格式'), ('crash', '停止')])
def test_controller_failures_bounded_and_isolated(qtbot, fake_controller, mode, phrase):
    controller = fake_controller(mode)
    errors = []
    controller.failed.connect(errors.append)
    controller.submit((((1, 1),),))
    qtbot.waitUntil(lambda: controller.is_stopped, timeout=3000)
    assert any(phrase in error for error in errors)


def test_window_candidate_exact_copy_and_stale_revision(qtbot, qapp, fake_controller):
    from src.clipboard.writer import get_clipboard_epoch, get_last_write_id
    from src.core.constants import CUSTOM_MIME_TYPE
    from src.ui.handwriting_window import HandwritingWindow
    window = HandwritingWindow(controller=fake_controller())
    qtbot.addWidget(window)
    window.show()
    window.pad.buffer.begin(1, 1)
    window.pad.buffer.finish()
    window.recognize()
    qtbot.waitUntil(lambda: bool(window._buttons))
    before = get_clipboard_epoch()
    qtbot.mouseClick(window._buttons[0], Qt.MouseButton.LeftButton)
    assert qapp.clipboard().text() == '臺'
    assert get_clipboard_epoch() == before + 1
    assert bytes(qapp.clipboard().mimeData().data(CUSTOM_MIME_TYPE)).decode() == get_last_write_id()
    old_revision = window.pad.buffer.revision
    window.pad.clear()
    window._copy('灣', old_revision)
    assert qapp.clipboard().text() == '臺'
    window.close()


def test_runtime_raster_icon_available(qapp):
    from src.core.branding import ICON_SIZES, application_icon
    icon = application_icon()
    assert not icon.isNull()
    assert all(not icon.pixmap(size, size).isNull() for size in ICON_SIZES)


def test_branding_preserves_compatibility_identifiers():
    from src.core import constants
    from src.core.version import APP_DISPLAY_NAME, APP_NAME
    assert APP_NAME == 'InkPick' and APP_DISPLAY_NAME == 'InkPick 拾字筆'
    assert constants.APP_MUTEX_NAME == 'Global\\DesktopOCRToolInstance'
    assert constants.APP_REGISTRY_KEY == 'DesktopOCRTool'
    assert constants.CUSTOM_MIME_TYPE == 'application/x-desktopocrtool-id'
