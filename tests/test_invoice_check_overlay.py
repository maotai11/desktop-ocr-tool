"""Only this snapshot's actual invoice boxes locate click-through annotations."""
from copy import deepcopy

import pytest
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QImage

from src.ui.invoice_check_overlay import InvoiceCheckOverlay


def snapshot(scale):
    return {'regions': {'invoice': (int(180 * scale), int(20 * scale), int(210 * scale), int(300 * scale), 1)},
            'checks': [{'label': '未稅金額', 'status': 'matched',
                        'invoice': {'box': [10 * scale, 20 * scale, 70 * scale, 20 * scale]},
                        'row': {'raw': '7,000'}}]}


@pytest.fixture
def overlay(qtbot):
    result = InvoiceCheckOverlay()
    qtbot.addWidget(result)
    return result


def test_annotations_follow_actual_moved_invoice_box_and_keep_source_value(overlay, qapp):
    scale = qapp.primaryScreen().devicePixelRatio()
    result = snapshot(scale)
    assert overlay.set_snapshot(result)
    first = overlay.callouts[0]
    assert first['anchor'] == (190 * scale, 40 * scale, 70 * scale, 20 * scale)
    assert '7,000' in first['text'] and '相符' in first['text']
    result['checks'][0]['invoice']['box'][1] += 70 * scale
    assert overlay.set_snapshot(result)
    assert overlay.callouts[0]['rect'][1] - first['rect'][1] == 70
    assert overlay.windowFlags() & Qt.WindowType.WindowTransparentForInput
    assert overlay.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    assert overlay.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)


def test_only_background_fades_and_capture_clear_cannot_restore_old_labels(overlay, qapp):
    font = overlay.font()
    font.setPixelSize(18)  # Solid glyph interiors independent of host default fonts.
    overlay.setFont(font)
    assert overlay.set_snapshot(snapshot(qapp.primaryScreen().devicePixelRatio()))
    before = overlay.callouts
    opaque_text = []
    for transparency in (0, 50, 100):
        assert overlay.set_background_transparency(transparency)
        assert overlay.callouts == before and overlay.windowOpacity() == 1
        image = overlay.grab().toImage()
        assert not image.isNull()
        rect = QRectF(*overlay.callouts[0]['rect'])
        scale = image.devicePixelRatio()
        background = image.pixelColor(round((rect.left() + 1) * scale), round((rect.top() + 1) * scale))
        assert background.alpha() == round(255 * (100 - transparency) / 100)
        if transparency == 0:
            assert background.lightness() < 45  # Visible over white invoice paper.
        opaque_text.append(sum(image.pixelColor(x, y).alpha() == 255
                               and image.pixelColor(x, y).name() == '#9cf2b9'
                               for y in range(round(rect.top() * scale), round(rect.bottom() * scale))
                               for x in range(round(rect.left() * scale), round(rect.right() * scale))))
    assert min(opaque_text) > 0 and len(set(opaque_text)) == 1
    overlay.hide_for_capture()
    assert overlay.set_background_transparency(10)
    assert not overlay.isVisible()
    overlay.clear()
    overlay.finish_capture(True)
    assert not overlay.isVisible() and not overlay.marks


@pytest.mark.parametrize('broken', ['regions', 'monitor', 'box', 'outside', 'nan', 'invoice', 'row', 'checks'])
def test_malformed_result_clears_whole_overlay(overlay, qapp, broken):
    valid = snapshot(qapp.primaryScreen().devicePixelRatio())
    assert overlay.set_snapshot(valid)
    bad = deepcopy(valid)
    if broken == 'regions': bad['regions'] = []
    elif broken == 'monitor': bad['regions']['invoice'] = (*bad['regions']['invoice'][:4], 2)
    elif broken == 'box': bad['checks'][0]['invoice']['box'] = [1, 2, 3]
    elif broken == 'outside': bad['checks'][0]['invoice']['box'] = [1000, 1000, 20, 20]
    elif broken == 'nan': bad['checks'][0]['invoice']['box'][0] = float('nan')
    elif broken == 'invoice': bad['checks'][0]['invoice'] = 123
    elif broken == 'row': bad['checks'][0]['row'] = ['invalid']
    elif broken == 'checks': bad['checks'] = None
    assert not overlay.set_snapshot(bad)
    assert not overlay.marks and not overlay.isVisible()


def test_missing_invoice_position_has_no_invented_annotation(overlay, qapp):
    result = snapshot(qapp.primaryScreen().devicePixelRatio())
    result['checks'][0]['invoice']['box'] = None
    result['checks'][0]['status'] = 'missing'
    assert overlay.set_snapshot(result)
    assert not overlay.callouts and not overlay.isVisible()


@pytest.mark.parametrize('cursor,expected', [((660, 330), True), ((300, 150), False)])
def test_nonactivation_hit_test_uses_native_physical_rect_at_high_dpi(monkeypatch, cursor, expected):
    import ctypes
    from ctypes import wintypes
    from src.ui import invoice_check_overlay as module

    class Message(ctypes.Structure):
        _fields_ = [('hwnd', wintypes.HWND), ('message', wintypes.UINT),
                    ('wParam', wintypes.WPARAM), ('lParam', wintypes.LPARAM),
                    ('time', wintypes.DWORD), ('pt', wintypes.POINT)]

    class Function:
        def __init__(self, action): self.action = action
        def __call__(self, *args): return self.action(*args)

    def position(pointer):
        point = ctypes.cast(pointer, ctypes.POINTER(wintypes.POINT)).contents
        point.x, point.y = cursor
        return True

    def rectangle(handle, pointer):
        assert handle == 100
        value = ctypes.cast(pointer, ctypes.POINTER(wintypes.RECT)).contents
        value.left, value.top, value.right, value.bottom = 600, 300, 800, 360
        return True

    class Native:
        GetCursorPos = Function(position)
        GetWindowRect = Function(rectangle)

    class Button:
        def isVisible(self): return True
        def winId(self): return 100
        def mapToGlobal(self, *_): raise AssertionError('Qt logical pixels cannot locate a physical cursor')

    message = Message(message=0x0021)
    with monkeypatch.context() as patch:
        patch.setattr(module.sys, 'platform', 'win32')
        patch.setattr(ctypes, 'WinDLL', lambda *args, **kwargs: Native(), raising=False)
        assert module.prevent_button_activation(ctypes.addressof(message), [Button()]) is expected


def test_overlay_prepares_native_clickthrough_and_nonactivation_before_each_show(overlay, qapp, monkeypatch):
    from src.ui import invoice_check_overlay as module
    calls = []
    monkeypatch.setattr(module, 'pin_without_activation',
                        lambda widget, enabled, click_through: calls.append(
                            (widget.isVisible(), enabled, click_through)) or True)
    assert overlay.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
    assert overlay.set_snapshot(snapshot(qapp.primaryScreen().devicePixelRatio()))
    assert calls == [(False, True, True), (True, True, True)]
    calls.clear()
    assert overlay.set_pinned(False)
    assert calls == [(False, False, True), (True, False, True)]
    assert overlay.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus




def test_scoped_native_noactivate_changes_only_activation_bit(monkeypatch):
    import ctypes
    from src.ui import invoice_check_overlay as module
    state = {'style': 0x00000088}
    class Function:
        def __init__(self, action): self.action = action
        def __call__(self, *args): return self.action(*args)
    def update(handle, index, value):
        previous = state['style']
        state['style'] = value
        return previous
    class Native:
        GetWindowLongPtrW = Function(lambda *args: state['style'])
        SetWindowLongPtrW = Function(update)
    class Window:
        def winId(self): return 100
    with monkeypatch.context() as patch:
        patch.setattr(module.sys, 'platform', 'win32')
        patch.setattr(ctypes, 'WinDLL', lambda *a, **kw: Native(), raising=False)
        patch.setattr(ctypes, 'set_last_error', lambda *a: None, raising=False)
        patch.setattr(ctypes, 'get_last_error', lambda: 0, raising=False)
        assert module.set_native_noactivate(Window(), True)
        assert state['style'] == 0x08000088
        assert module.set_native_noactivate(Window(), False)
        assert state['style'] == 0x00000088
