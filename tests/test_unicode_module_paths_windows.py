"""Actual Unicode mapped module paths and owned-child extraction accounting."""
import json
import os
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='Native Windows module mapping')


def run_native(source, directory):
    environment = dict(os.environ)
    environment['RC19_NATIVE_FIXTURE'] = str(directory)
    result = subprocess.run([sys.executable, '-c', source], env=environment,
                            capture_output=True, text=True, timeout=60, check=True)
    return json.loads(result.stdout)


@pytest.mark.parametrize('long_path', [False, True], ids=['unicode', 'long-unicode'])
def test_actual_unicode_mapping_uses_physical_identity_and_closes_handles(tmp_path, long_path):
    report = run_native(r'''
import ctypes, json, os, shutil
from pathlib import Path
from ctypes import wintypes
from src.core.resource_budget import _windows_module_path
root = Path(os.environ['RC19_NATIVE_FIXTURE']) / '中文字 空間😀' / '_MEI-fixture'
if LONG_PATH:
    root = root / ('bounded-segment-' * 8) / ('another-segment-' * 8)
# Extended namespace permits the long fixture independently of MAX_PATH policy.
root = Path('\\\\?\\' + str(root)) if not str(root).startswith('\\\\?\\') else root
root.mkdir(parents=True)
target = root / 'rc19_unicode_mapping.dll'
shutil.copyfile(Path(os.environ['SystemRoot']) / 'System32' / 'version.dll', target)
module = ctypes.WinDLL(str(target))
observed = _windows_module_path(os.getpid(), module._handle)
assert os.path.samefile(observed, target)
assert '?' not in observed[4:] and '中文字' in observed and '😀' in observed
kernel = ctypes.WinDLL('kernel32', use_last_error=True)
kernel.GetCurrentProcess.restype = wintypes.HANDLE
kernel.GetProcessHandleCount.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
def count():
    value = wintypes.DWORD()
    assert kernel.GetProcessHandleCount(kernel.GetCurrentProcess(), ctypes.byref(value))
    return value.value
before = count()
for _ in range(20):
    assert os.path.samefile(_windows_module_path(os.getpid(), module._handle), target)
after = count()
assert before == after
print(json.dumps({'same_file': True, 'unicode_preserved': True, 'handles_stable': True,
                  'long_path': len(str(target).encode('utf-16-le')) // 2 > 260}))
'''.replace('LONG_PATH', repr(long_path)), tmp_path)
    assert report == {'same_file': True, 'unicode_preserved': True, 'handles_stable': True,
                      'long_path': long_path}


def test_actual_owned_child_unicode_mei_root_is_counted(tmp_path):
    report = run_native(r'''
import ctypes, json, os, shutil, subprocess, sys
from pathlib import Path
from src.core import resource_budget as budget
root = Path(os.environ['RC19_NATIVE_FIXTURE']) / '另一個 中文 空間'
parent = root / '_MEI-parent'; child = root / '_MEI-child'
parent.mkdir(parents=True); child.mkdir()
source = Path(os.environ['SystemRoot']) / 'System32' / 'version.dll'
a = parent / 'rc19_parent_mapping.dll'; b = child / 'rc19_child_mapping.dll'
shutil.copyfile(source, a); shutil.copyfile(source, b)
module = ctypes.WinDLL(str(a))
child_source = "import ctypes,sys; module=ctypes.WinDLL(sys.argv[1]); print('ready',flush=True); input()"
process = subprocess.Popen([sys.executable, '-c', child_source, str(b)], stdin=subprocess.PIPE,
                           stdout=subprocess.PIPE, text=True)
try:
    assert process.stdout.readline().strip() == 'ready'
    sys.frozen = True; sys._MEIPASS = str(parent)
    budget.current_process_root = lambda: os.getpid()
    roots = budget.active_extraction_roots()
    assert any(os.path.samefile(path, parent) for path in roots)
    assert any(os.path.samefile(path, child) for path in roots)
    assert all('?' not in str(path)[4:] for path in roots)
    print(json.dumps({'parent_retained':True,'child_retained':True,'unicode_preserved':True}))
finally:
    process.communicate('\n', timeout=10)
    assert process.returncode == 0
''', tmp_path)
    assert report == {'parent_retained': True, 'child_retained': True, 'unicode_preserved': True}
