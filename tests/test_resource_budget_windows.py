"""Actual native aggregate commitment limits, isolated from the pytest host."""
import json
import os
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='Native Windows Job evidence')


def _run(source, timeout=30):
    environment = dict(os.environ)
    environment.pop('DESKTOP_OCR_RESOURCE_JOB', None)
    result = subprocess.run([sys.executable, '-c', source], env=environment,
                            capture_output=True, timeout=timeout, check=True, text=True)
    return json.loads(result.stdout)


def test_native_runtime_job_rejects_single_allocation_at_decimal_gb():
    report = _run('''
import json
from src.core.resource_budget import install_runtime_budget, runtime_memory_accounting, measure_process_tree
install_runtime_budget()
before = measure_process_tree()
try:
    allocation = bytearray(1_000_000_000)
except MemoryError:
    denied = True
else:
    denied = False
after = measure_process_tree()
print(json.dumps(dict(denied=denied, before_private=before.private_bytes,
                     after_private=after.private_bytes, after_rss=after.rss_bytes, **runtime_memory_accounting())))
''')
    assert report['denied'] and report['installed']
    assert 0 < report['limit_bytes'] < 1_000_000_000
    assert 0 < report['before_private'] < 1_000_000_000
    assert 0 < report['after_private'] < 1_000_000_000
    assert 0 < report['after_rss'] < 1_000_000_000


def test_native_runtime_job_is_shared_with_real_child_and_bounds_aggregate():
    report = _run('''
import json, subprocess, sys
from src.core.resource_budget import install_runtime_budget, runtime_memory_accounting, measure_process_tree
install_runtime_budget()
child_source = "from src.core.resource_budget import install_runtime_budget; install_runtime_budget(); x=bytearray(600_000_000); print('ready',flush=True); input()"
child = subprocess.Popen([sys.executable, '-c', child_source], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
try:
    assert child.stdout.readline().strip() == b'ready'
    actual = measure_process_tree()
    try:
        allocation = bytearray(450_000_000)
    except MemoryError:
        denied = True
    else:
        denied = False
    after = measure_process_tree()
    report = dict(denied=denied, children=len(actual.pids), rss=actual.rss_bytes,
                  after_rss=after.rss_bytes, after_private=after.private_bytes,
                  private=actual.private_bytes, **runtime_memory_accounting())
finally:
    child.stdin.write(b'\\n'); child.stdin.flush(); child.wait(timeout=10)
    child.stdin.close(); child.stdout.close()
report['reaped'] = child.returncode == 0
print(json.dumps(report))
''', timeout=45)
    assert report['denied'] and report['reaped'] and report['installed']
    assert report['children'] == 2 and report['active_processes'] == 2
    assert 600_000_000 < report['private'] < 1_000_000_000
    assert 0 < report['rss'] < 1_000_000_000
    assert 600_000_000 < report['after_private'] < 1_000_000_000
    assert 0 < report['after_rss'] < 1_000_000_000
    # Windows can include rejected commitment requests in this high-water mark.
    # This deliberately failing allocation is not a positive workload proof.
    # The actual frozen workload gate independently requires its raw peak <1GB.
    assert report['peak_job_private_bytes'] >= report['private']


def test_invalid_inherited_job_cannot_disable_runtime_budget():
    report = _run('''
import json, os
from src.core.resource_budget import install_runtime_budget, ResourceInspectionError
os.environ['DESKTOP_OCR_RESOURCE_JOB'] = 'Local\\\\DesktopOCR-invalid-absent-job'
try:
    install_runtime_budget()
except ResourceInspectionError:
    denied=True
else:
    denied=False
print(json.dumps(dict(denied=denied)))
''')
    assert report == {'denied': True}


def test_windows_filesystem_allocation_and_actual_cluster_reservation(tmp_path):
    from src.core.resource_budget import managed_disk_usage, storage_block_size, disk_write_reservation
    target = tmp_path / 'tiny.bin'; target.write_bytes(b'x')
    block = storage_block_size(target)
    usage = managed_disk_usage([tmp_path, target])
    assert block >= 512 and usage.files == 1 and usage.directories == 1
    assert usage.bytes >= 1
    assert disk_write_reservation(target, 1) == 3 * block
    before = usage.bytes
    target.write_bytes(b'x' * (block * 3 + 1))
    assert managed_disk_usage([tmp_path]).bytes >= before + block * 2
