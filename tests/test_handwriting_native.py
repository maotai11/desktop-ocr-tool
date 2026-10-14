"""Real pinned helper and real Qt ownership. Replays are training smoke only."""
import json
from pathlib import Path
import sys

import pytest

from src.core.branding import resource_root
from src.handwriting.assets import verified_assets
from src.handwriting.controller import HandwritingController


def test_real_helper_candidates_latest_queue_and_normal_stop(qtbot):
    assets = verified_assets()
    fixtures = json.loads((resource_root() / 'models/handwriting/validation.json').read_text(encoding='utf-8'))['cases']
    controller = HandwritingController(assets=assets)
    results, failures = [], []
    controller.candidates.connect(lambda *args: results.append(args))
    controller.failed.connect(failures.append)
    try:
        first = controller.submit(fixtures[9]['strokes'])
        qtbot.waitUntil(lambda: bool(results) or bool(failures), timeout=12000)
        assert not failures
        assert results[0][0] == first and results[0][1][0] == fixtures[9]['label']
        if sys.platform == 'win32':
            assert controller._job.handle is not None
        controller.submit(fixtures[0]['strokes'])
        controller.submit(fixtures[1]['strokes'])
        latest = controller.submit(fixtures[9]['strokes'])
        qtbot.waitUntil(lambda: len(results) == 2 or bool(failures), timeout=5000)
        assert not failures
        assert results[-1][0] == latest and results[-1][1][0] == fixtures[9]['label']
    finally:
        controller.stop()
        qtbot.waitUntil(lambda: controller.is_stopped, timeout=3000)


@pytest.mark.skipif(sys.platform != 'win32', reason='Actual Windows Job parent-death gate')
def test_windows_parent_crash_kills_owned_native_helper(tmp_path):
    import ctypes
    from ctypes import wintypes
    import os
    import subprocess

    # Launch only our source parent and the exact built helper; inspect its own
    # process handle, never enumerate unrelated processes or private user data.
    output = tmp_path / 'owned-child.json'
    source = tmp_path / 'parent.py'
    source.write_text('''
import json,os,sys
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from src.handwriting.assets import verified_assets
from src.handwriting.controller import HandwritingController
app=QApplication([])
controller=HandwritingController(assets=verified_assets())
def ready(*args):
 if controller._ready and controller._job and controller._job.handle:
  with open(sys.argv[1],'w') as file: json.dump({'pid':controller._process.processId()},file)
  os._exit(23)
timer=QTimer();timer.timeout.connect(ready);timer.start(10)
QTimer.singleShot(10000,lambda:os._exit(24))
controller.submit((((0,0),(1000,1000)),))
app.exec()
''')
    env = os.environ.copy()
    env['PYTHONPATH'] = str(resource_root())
    parent = subprocess.Popen([sys.executable, str(source), str(output)], env=env)
    try:
        assert parent.wait(timeout=15) == 23
        pid = json.loads(output.read_text())['pid']
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x00100000, False, pid)
        if handle:
            try:
                assert kernel.WaitForSingleObject(handle, 3000) == 0
            finally:
                kernel.CloseHandle(handle)
        else:
            assert ctypes.get_last_error() == 87  # Process already gone, not access denied.
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=5)


@pytest.mark.skipif(sys.platform != 'win32', reason='Native Windows clipboard ownership/persistence gate')
def test_windows_copy_process_exit_preserves_exact_clipboard(qtbot, qapp, tmp_path):
    import os
    import subprocess
    source = tmp_path / 'copy_exit.py'
    source.write_text('''
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from src.clipboard.writer import write_text_to_clipboard
app=QApplication([])
write_text_to_clipboard('臺𠮷禰\\U000e0100')
QTimer.singleShot(100,app.quit)
app.exec()
# Deliberately leave the synthetic self-MIME intact across native process exit.
''', encoding='utf-8')
    env = os.environ.copy()
    env['PYTHONPATH'] = str(resource_root())
    result = subprocess.run([sys.executable, str(source)], env=env,
                            capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr.decode(errors='replace')
    qtbot.waitUntil(lambda: qapp.clipboard().text() == '臺𠮷禰\U000e0100', timeout=3000)
    from src.core.constants import CUSTOM_MIME_TYPE
    assert qapp.clipboard().mimeData().hasFormat(CUSTOM_MIME_TYPE)
