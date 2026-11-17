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


@pytest.mark.parametrize('whole_directory', [False, True])
def test_actual_windows_file_disappears_between_stat_and_attribute_handle(tmp_path, monkeypatch, whole_directory):
    from src.core import resource_budget as budget
    retired = tmp_path / '_MEI-owned-fixture'; retired.mkdir()
    target = retired / 'retiring.bin'; target.write_bytes(b'x' * 100_000)
    kept = tmp_path / 'keep.bin'; kept.write_bytes(b'preserved')
    original = budget._windows_allocated_bytes
    errors = []

    def remove_before_native_open(path):
        if path == target:
            path.unlink()
            if whole_directory:
                retired.rmdir()
            try:
                return original(path)
            except budget.ResourceInspectionError as exc:
                errors.append(exc.native_error)
                raise
        return original(path)

    monkeypatch.setattr(budget, '_windows_allocated_bytes', remove_before_native_open)
    usage = budget.managed_disk_usage([tmp_path])
    assert errors and all(code in (2, 3) for code in errors)
    assert usage.files == 1 and usage.bytes < 100_000
    assert kept.read_bytes() == b'preserved'


def test_cached_windows_bindings_still_measure_live_file_growth(tmp_path):
    from src.core import resource_budget as budget
    target = tmp_path / 'growing.bin'; target.write_bytes(b'x')
    bindings = budget._windows_file_api()
    before = budget._windows_allocated_bytes(target)
    target.write_bytes(b'y' * 100_000)
    after = budget._windows_allocated_bytes(target)
    assert budget._windows_file_api() is bindings
    assert after >= 100_000 and after > before


def test_actual_windows_replacement_cannot_reuse_a_live_alias_identity(tmp_path, monkeypatch):
    from src.core import resource_budget as budget
    survivor = tmp_path / 'survivor.bin'; survivor.write_bytes(b'x' * 200_000)
    replaced = tmp_path / 'replaced.bin'; os.link(survivor, replaced)
    original = budget._windows_allocated_bytes

    def replace_before_native_open(path):
        if path == replaced:
            path.unlink()
            path.write_bytes(b'new bytes!')
        return original(path)

    monkeypatch.setattr(budget, '_windows_allocated_bytes', replace_before_native_open)
    with pytest.raises(budget.ResourceInspectionError):
        budget.managed_disk_usage([survivor, replaced])
    assert survivor.stat().st_size == 200_000 and replaced.read_bytes() == b'new bytes!'


@pytest.mark.parametrize('continuous', [False, True])
def test_actual_windows_same_inode_growth_is_remeasured_once(tmp_path, monkeypatch, continuous):
    from src.core import resource_budget as budget
    target = tmp_path / 'growing.bin'; target.write_bytes(b'x')
    original = budget._windows_allocated_bytes
    calls = []

    def grow_after_query(path):
        result = original(path)
        calls.append(result)
        if continuous or len(calls) == 1:
            with path.open('ab') as stream:
                stream.write(b'x' * 200_000)
        return result

    monkeypatch.setattr(budget, '_windows_allocated_bytes', grow_after_query)
    if continuous:
        with pytest.raises(budget.ResourceInspectionError):
            budget.managed_disk_usage([target])
    else:
        assert budget.managed_disk_usage([target]).bytes >= 200_001
    assert len(calls) == 2
