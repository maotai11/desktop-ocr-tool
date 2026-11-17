"""Resource limits count owned trees and preserve data when admission fails."""
import os
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from src.core import resource_budget as budget


@pytest.mark.parametrize('native_error', [2, 3, 5])
def test_disappearing_owned_file_is_removed_as_a_whole_measurement(tmp_path, monkeypatch, native_error):
    removed = tmp_path / 'unpacking.tmp'
    removed.write_bytes(b'x' * 200_000)
    kept = tmp_path / 'keep.bin'
    kept.write_bytes(b'preserved')
    monkeypatch.setattr(budget, 'sys', SimpleNamespace(platform='win32'))

    def allocation(path):
        if path == removed:
            path.unlink()
            raise budget.ResourceInspectionError(native_error=native_error)
        return path.stat().st_size

    monkeypatch.setattr(budget, '_windows_allocated_bytes', allocation)
    usage = budget.managed_disk_usage([tmp_path])
    assert usage.files == 1 and usage.directories == 1
    assert len(kept.read_bytes()) <= usage.bytes < 200_000


@pytest.mark.parametrize('native_error,remove', [(2, False), (3, False), (5, False), (32, False),
                                               (33, False), (32, True), (33, True)])
def test_unconfirmed_or_unrelated_inspection_failure_is_never_skipped(tmp_path, monkeypatch, native_error, remove):
    target = tmp_path / 'owned.bin'
    target.write_bytes(b'keep original data')
    monkeypatch.setattr(budget, 'sys', SimpleNamespace(platform='win32'))

    def allocation(path):
        if remove:
            path.unlink()
        raise budget.ResourceInspectionError(native_error=native_error)

    monkeypatch.setattr(budget, '_windows_allocated_bytes', allocation)
    with pytest.raises(budget.ResourceInspectionError):
        budget.managed_disk_usage([target])
    if not remove:
        assert target.read_bytes() == b'keep original data'


@pytest.mark.parametrize('native_error', [2, 3, 5])
def test_recreated_path_after_native_not_found_still_blocks_admission(tmp_path, monkeypatch, native_error):
    target = tmp_path / 'recreated.bin'
    target.write_bytes(b'old')
    monkeypatch.setattr(budget, 'sys', SimpleNamespace(platform='win32'))

    def allocation(path):
        path.unlink()
        path.write_bytes(b'recreated live allocation')
        raise budget.ResourceInspectionError(native_error=native_error)

    monkeypatch.setattr(budget, '_windows_allocated_bytes', allocation)
    with pytest.raises(budget.ResourceInspectionError):
        budget.managed_disk_usage([target])
    assert target.read_bytes() == b'recreated live allocation'


def test_successful_replacement_cannot_hide_the_old_live_hardlink(tmp_path, monkeypatch):
    survivor = tmp_path / 'survivor.bin'; survivor.write_bytes(b'x' * 200_000)
    replaced = tmp_path / 'replaced.bin'; os.link(survivor, replaced)
    monkeypatch.setattr(budget, 'sys', SimpleNamespace(platform='win32'))

    def allocation(path):
        if path == replaced:
            path.unlink()
            path.write_bytes(b'new bytes!')
        return path.stat().st_size

    monkeypatch.setattr(budget, '_windows_allocated_bytes', allocation)
    with pytest.raises(budget.ResourceInspectionError):
        budget.managed_disk_usage([survivor, replaced])
    assert survivor.stat().st_size == 200_000
    assert replaced.read_bytes() == b'new bytes!'


@pytest.mark.parametrize('continuous', [False, True])
def test_same_inode_growth_gets_one_fresh_allocation_query_or_blocks(tmp_path, monkeypatch, continuous):
    target = tmp_path / 'growing.bin'; target.write_bytes(b'x')
    monkeypatch.setattr(budget, 'sys', SimpleNamespace(platform='win32'))
    calls = []

    def allocation(path):
        old_size = path.stat().st_size
        calls.append(old_size)
        if continuous or len(calls) == 1:
            path.write_bytes(b'x' * (old_size + 200_000))
        return old_size

    monkeypatch.setattr(budget, '_windows_allocated_bytes', allocation)
    if continuous:
        with pytest.raises(budget.ResourceInspectionError):
            budget.managed_disk_usage([target])
    else:
        usage = budget.managed_disk_usage([target])
        assert usage.files == 1 and usage.bytes >= 200_001
    assert len(calls) == 2


def test_same_size_write_timestamp_requires_fresh_native_measurement(tmp_path, monkeypatch):
    target = tmp_path / 'rewritten.bin'; target.write_bytes(b'old')
    monkeypatch.setattr(budget, 'sys', SimpleNamespace(platform='win32'))
    calls = []

    def allocation(path):
        calls.append(True)
        if len(calls) == 1:
            old = path.stat()
            path.write_bytes(b'new')
            os.utime(path, ns=(old.st_atime_ns, old.st_mtime_ns + 1_000_000_000))
        return 3

    monkeypatch.setattr(budget, '_windows_allocated_bytes', allocation)
    assert budget.managed_disk_usage([target]).files == 1
    assert len(calls) == 2 and target.read_bytes() == b'new'


def test_directory_removed_during_enumeration_has_no_phantom_allocation(tmp_path, monkeypatch):
    removed = tmp_path / 'retired'; removed.mkdir()
    (tmp_path / 'keep.bin').write_bytes(b'kept')
    original = Path.iterdir

    def enumerate_owned(path):
        if path == removed:
            path.rmdir()
        return original(path)

    monkeypatch.setattr(Path, 'iterdir', enumerate_owned)
    usage = budget.managed_disk_usage([tmp_path])
    assert usage.files == 1 and usage.directories == 1
    assert (tmp_path / 'keep.bin').read_bytes() == b'kept'


def test_disk_counts_overlapping_roots_and_hardlinks_once(tmp_path):
    data = tmp_path / 'data'; data.mkdir()
    original = data / 'original.bin'; original.write_bytes(b'x' * 12_000)
    os.link(original, data / 'same-bytes.bin')
    usage = budget.managed_disk_usage([data, original, data, data / 'same-bytes.bin'])
    assert usage.files == 1 and usage.directories == 1
    assert usage.bytes >= 12_000
    different = data / 'different.bin'; different.write_bytes(b'y' * 5000)
    assert budget.managed_disk_usage([data]).files == 2
    assert budget.managed_disk_usage([data]).bytes >= usage.bytes + 5000


def test_unexpected_internal_symlink_is_not_a_quota_bypass(tmp_path):
    owned = tmp_path / 'owned'; owned.mkdir()
    external = tmp_path / 'external'; external.mkdir()
    (external / 'private.bin').write_bytes(b'do not inspect or delete')
    try:
        (owned / 'linked').symlink_to(external, target_is_directory=True)
    except OSError:
        pytest.skip('This Windows account cannot create a directory symlink')
    with pytest.raises(budget.ResourceInspectionError):
        budget.managed_disk_usage([owned])
    assert (external / 'private.bin').read_bytes() == b'do not inspect or delete'


def test_sparse_oversize_legacy_file_blocks_new_write_without_deletion(tmp_path, monkeypatch):
    old = tmp_path / 'existing.db'
    with old.open('wb') as handle:
        handle.write(b'old data')
        handle.truncate(budget.RESOURCE_LIMIT_BYTES)
    monkeypatch.setattr(budget, 'managed_storage_roots', lambda *args, **kwargs: (tmp_path,))
    with pytest.raises(budget.ResourceBudgetExceeded):
        with budget.disk_quota_guard(reserve_bytes=1):
            (tmp_path / 'must-not-exist').write_bytes(b'new')
    assert old.stat().st_size == budget.RESOURCE_LIMIT_BYTES
    with old.open('rb') as handle:
        assert handle.read(8) == b'old data'
    assert not (tmp_path / 'must-not-exist').exists()


def test_strict_decimal_boundary_and_nested_reservations(monkeypatch):
    monkeypatch.setattr(budget, 'managed_disk_usage', lambda roots: budget.DiskUsage(900_000_000, 1, 0))
    with pytest.raises(budget.ResourceBudgetExceeded):
        with budget.disk_quota_guard(reserve_bytes=100_000_000):
            pytest.fail('Equality is not below the limit')
    with budget.disk_quota_guard(reserve_bytes=80_000_000):
        with pytest.raises(budget.ResourceBudgetExceeded):
            with budget.disk_quota_guard(reserve_bytes=20_000_000):
                pytest.fail('Concurrent reservations must share one budget')
    assert budget._reserved_bytes == 0
    with budget.disk_quota_guard(reserve_bytes=99_999_999):
        pass


def test_writer_guard_serializes_threads_and_releases_after_failure(monkeypatch):
    monkeypatch.setattr(budget, 'managed_disk_usage', lambda roots: budget.DiskUsage(0, 0, 0))
    started, entered = threading.Event(), threading.Event()
    def second_writer():
        started.set()
        with budget.disk_quota_guard(reserve_bytes=700_000_000):
            entered.set()
    with pytest.raises(RuntimeError):
        with budget.disk_quota_guard(reserve_bytes=700_000_000):
            worker = threading.Thread(target=second_writer)
            worker.start(); assert started.wait(1)
            assert not entered.wait(.05)
            raise RuntimeError('owned write failed')
    worker.join(2)
    assert not worker.is_alive() and entered.is_set() and budget._reserved_bytes == 0


@pytest.mark.parametrize('size', [(3840, 2160), (16000, 1000), (4000, 4000), (800, 600)])
def test_raster_budget_is_checked_before_capture_and_bounds_working_size(size):
    width, height = budget.raster_preflight(*size)
    assert width <= size[0] and height <= size[1]
    assert width * height <= budget.MAX_OCR_PIXELS and max(width, height) <= 4096


@pytest.mark.parametrize('size', [(0, 10), (True, 2), (4001, 4000), (10**100, 2)])
def test_raster_invalid_or_oversize_input_is_rejected_without_allocation(size):
    with pytest.raises(budget.ResourceBudgetExceeded):
        budget.raster_preflight(*size)


def test_actual_live_child_memory_is_included_and_child_is_reaped():
    source = "import sys,time; x=bytearray(16000000); x[::4096]=b'x'*len(x[::4096]); print('ready',flush=True); time.sleep(4)"
    child = subprocess.Popen([sys.executable, '-c', source], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        assert child.stdout.readline().strip() == b'ready'
        own = budget.measure_process_tree(child.pid)
        tree = budget.measure_process_tree()
        assert child.pid in tree.pids and os.getpid() in tree.pids
        assert own.rss_bytes >= 16_000_000 and tree.rss_bytes > own.rss_bytes
        if sys.platform == 'win32':
            assert own.private_bytes > 0 and tree.private_bytes >= own.private_bytes
        else:
            assert own.private_bytes is None
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)
        child.stdout.close()


def test_memory_preflight_uses_measured_tree_not_model_file_size(monkeypatch):
    monkeypatch.setattr(budget, 'measure_process_tree', lambda root=None:
                        budget.ProcessUsage(950_000_000, 700_000_000, (123, 456)))
    with pytest.raises(budget.ResourceBudgetExceeded):
        budget.memory_preflight(50_000_000)
    assert budget.memory_preflight(49_999_999).pids == (123, 456)
    monkeypatch.setattr(budget, 'measure_process_tree', lambda root=None:
                        budget.ProcessUsage(400_000_000, 950_000_000, (123,)))
    with pytest.raises(budget.ResourceBudgetExceeded):
        budget.memory_preflight(50_000_000)


def test_bounded_output_failure_preserves_existing_and_reaps_only_own_temp(tmp_path):
    target = tmp_path / 'saved.txt'; target.write_bytes(b'previous')
    other = tmp_path / '.ocr-output-user.tmp'; other.write_bytes(b'user-owned')
    with pytest.raises(budget.ResourceBudgetExceeded):
        with budget.bounded_output(target, 3, binary=True) as stream:
            stream.write(b'ab')
            stream.write(b'cd')
    assert target.read_bytes() == b'previous'
    assert other.read_bytes() == b'user-owned'
    assert sorted(p.name for p in tmp_path.iterdir()) == ['.ocr-output-user.tmp', 'saved.txt']


def test_bounded_output_utf8_sig_is_only_emitted_once(tmp_path):
    target = tmp_path / 'output.csv'
    with budget.bounded_output(target, 64, encoding='utf-8-sig') as stream:
        stream.write('甲,乙\n'); stream.write('丙,丁\n')
    assert target.read_bytes() == b'\xef\xbb\xbf' + '甲,乙\n丙,丁\n'.encode()


def test_thumbnail_repeated_creation_never_overwrites_existing_image(tmp_path):
    from PIL import Image
    from src.data.file_manager import FileManager
    manager = FileManager(str(tmp_path))
    raw, _ = manager.save_capture(Image.new('RGB', (16, 16), 'red'))
    first, _ = manager.create_thumbnail(raw)
    before = Path(first).read_bytes()
    Image.new('RGB', (16, 16), 'blue').save(raw)
    second, _ = manager.create_thumbnail(raw)
    assert first != second and Path(first).read_bytes() == before
    assert Image.open(second).getpixel((0, 0)) == (0, 0, 255)


def test_thumbnail_failed_encoder_deletes_only_new_owned_file(tmp_path, monkeypatch):
    from PIL import Image
    from src.data.file_manager import FileManager
    manager = FileManager(str(tmp_path))
    raw, _ = manager.save_capture(Image.new('RGB', (16, 16), 'red'))
    first, _ = manager.create_thumbnail(raw)
    before = Path(first).read_bytes()
    def fail(image, output, *args, **kwargs):
        output.write(b'partial')
        raise OSError('encoder stopped')
    monkeypatch.setattr(Image.Image, 'save', fail)
    assert manager.create_thumbnail(raw) == ('', '')
    assert list((tmp_path / 'thumbnails').rglob('*.png')) == [Path(first)]
    assert Path(first).read_bytes() == before


def test_database_oversize_transaction_rolls_back_and_preserves_old_record(tmp_path):
    import sqlite3
    from src.data.database import Database
    database = Database(str(tmp_path / 'data.db'))
    try:
        with database.transaction() as connection:
            connection.execute('CREATE TABLE quota_test (value BLOB)')
            connection.execute('INSERT INTO quota_test VALUES (?)', (b'previous',))
        with pytest.raises(sqlite3.OperationalError):
            with database.transaction() as connection:
                connection.execute('INSERT INTO quota_test VALUES (zeroblob(20000000))')
        assert [row[0] for row in database.get_connection().execute('SELECT value FROM quota_test')] == [b'previous']
        assert database.get_connection().execute('PRAGMA journal_mode').fetchone()[0] == 'delete'
        assert not Path(str(tmp_path / 'data.db') + '-journal').exists()
    finally:
        database.close()


def test_existing_overquota_database_is_readonly_and_content_survives(tmp_path, monkeypatch):
    from src.data.database import Database
    path = tmp_path / 'data.db'
    first = Database(str(path))
    with first.transaction() as connection:
        connection.execute("INSERT INTO tags(name) VALUES ('preserved')")
    first.close()
    before = path.read_bytes()
    monkeypatch.setattr(budget, 'managed_disk_usage', lambda roots: budget.DiskUsage(1_000_000_000, 1, 0))
    reopened = Database(str(path))
    try:
        assert reopened.read_only
        assert reopened.get_connection().execute('SELECT name FROM tags').fetchone()[0] == 'preserved'
        with pytest.raises(budget.ResourceBudgetExceeded):
            with reopened.transaction():
                pytest.fail('New write must not begin')
        assert path.read_bytes() == before
    finally:
        reopened.close()


def test_log_file_stays_bounded_and_does_not_wait_on_writer_lock(tmp_path):
    import logging
    from src.core.logger import QuotaRotatingHandler
    handler = QuotaRotatingHandler(tmp_path / 'app.log', maxBytes=1024, backupCount=2,
                                   encoding='utf-8', delay=True)
    budget.register_managed_root(tmp_path, owner=handler)
    record = logging.LogRecord('test', logging.INFO, '', 0, 'A' * 200, (), None)
    for _ in range(50):
        handler.handle(record)
    assert len(list(tmp_path.glob('app.log*'))) == 3
    assert all(path.stat().st_size < 1024 for path in tmp_path.glob('app.log*'))
    completed = threading.Event()
    with budget._storage_lock:
        worker = threading.Thread(target=lambda: (handler.handle(record), completed.set()))
        worker.start()
        assert completed.wait(1), 'Log handler must skip rather than deadlock a quota owner'
    worker.join(1); handler.close()


def test_output_does_not_create_unbudgeted_ancestor_tree(tmp_path):
    destination = tmp_path.joinpath(*[str(number) for number in range(40)], 'data.txt')
    with pytest.raises(FileNotFoundError):
        with budget.bounded_output(destination, 1) as output:
            output.write('x')
    assert list(tmp_path.iterdir()) == []


def test_log_actual_bytes_do_not_expand_newlines_after_admission(tmp_path):
    import logging
    from src.core.logger import QuotaRotatingHandler
    target = tmp_path / 'app.log'
    handler = QuotaRotatingHandler(target, maxBytes=8192, backupCount=2,
                                   encoding='utf-8', delay=True)
    budget.register_managed_root(tmp_path, owner=handler)
    text = '中文\n' * 700
    handler.handle(logging.LogRecord('test', logging.INFO, '', 0, text, (), None))
    handler.close()
    assert target.read_bytes() == (text + '\n').encode('utf-8')
    assert target.stat().st_size < 8192


@pytest.mark.parametrize('route', ['native', 'directory', 'initial'])
def test_windows_denied_but_gone_retires_whole_node_without_hiding_live_alias(tmp_path, monkeypatch, route):
    survivor = tmp_path / 'survivor.bin'
    survivor.write_bytes(b'x' * 200_000)
    retired = tmp_path / 'retired'
    if route == 'directory':
        retired.mkdir()
        (retired / 'child.bin').write_bytes(b'y' * 90_000)
    else:
        os.link(survivor, retired)
    original_lstat, original_iterdir = Path.lstat, Path.iterdir
    monkeypatch.setattr(budget, 'sys', SimpleNamespace(platform='win32'))
    def denied():
        error = PermissionError(13, 'access denied')
        error.winerror = 5
        return error
    def remove():
        if route == 'directory':
            (retired / 'child.bin').unlink()
            retired.rmdir()
        else:
            retired.unlink()
    def allocation(path):
        if path == retired and route == 'native':
            remove()
            raise budget.ResourceInspectionError(native_error=5)
        return original_lstat(path).st_size
    def listing(path):
        if path == retired:
            remove()
            raise denied()
        return original_iterdir(path)
    initial_seen = False
    def lstat(path, *args, **kwargs):
        nonlocal initial_seen
        if path == retired and route == 'initial' and not initial_seen:
            initial_seen = True
            remove()
            raise denied()
        return original_lstat(path, *args, **kwargs)
    monkeypatch.setattr(budget, '_windows_allocated_bytes', allocation)
    if route == 'directory':
        monkeypatch.setattr(Path, 'iterdir', listing)
    if route == 'initial':
        monkeypatch.setattr(Path, 'lstat', lstat)
    # LIFO makes the retired alias fail first: it must never enter seen_nodes.
    usage = budget.managed_disk_usage([survivor, retired])
    assert usage.files == 1 and usage.directories == 0
    assert usage.bytes >= 200_000 and survivor.read_bytes() == b'x' * 200_000


@pytest.mark.parametrize('route', ['native', 'directory', 'initial'])
@pytest.mark.parametrize('state', ['live', 'recreated', 'symlink', 'denied', 'unknown'])
def test_windows_access_denied_requires_fresh_proven_absence(tmp_path, monkeypatch, route, state):
    target = tmp_path / 'owned'
    target.mkdir() if route == 'directory' else target.write_bytes(b'original')
    survivor = tmp_path / 'keep.bin'; survivor.write_bytes(b'keep')
    original_lstat, original_iterdir = Path.lstat, Path.iterdir
    monkeypatch.setattr(budget, 'sys', SimpleNamespace(platform='win32'))
    failed = False
    def trigger():
        nonlocal failed
        failed = True
        if state in ('recreated', 'symlink'):
            target.rmdir() if route == 'directory' else target.unlink()
            if state == 'symlink':
                target.symlink_to(survivor)
            elif route == 'directory':
                target.mkdir()
            else:
                target.write_bytes(b'replacement')
        error = PermissionError(13, 'access denied'); error.winerror = 5
        raise error
    def lstat(path, *args, **kwargs):
        if path == target:
            if route == 'initial' and not failed:
                trigger()
            if failed and state == 'denied':
                raise PermissionError(13, 'fresh stat denied')
            if failed and state == 'unknown':
                raise OSError('fresh stat unknown')
        return original_lstat(path, *args, **kwargs)
    def allocation(path):
        nonlocal failed
        if path == target and route == 'native':
            try:
                trigger()
            except PermissionError:
                raise budget.ResourceInspectionError(native_error=5) from None
        return original_lstat(path).st_size
    def listing(path):
        if path == target:
            trigger()
        return original_iterdir(path)
    monkeypatch.setattr(Path, 'lstat', lstat)
    monkeypatch.setattr(budget, '_windows_allocated_bytes', allocation)
    if route == 'directory':
        monkeypatch.setattr(Path, 'iterdir', listing)
    with pytest.raises(budget.ResourceInspectionError):
        budget.managed_disk_usage([target])
    assert survivor.read_bytes() == b'keep'


@pytest.mark.parametrize('route', ['native', 'directory', 'initial'])
@pytest.mark.parametrize('code', [32, 33, None, True, 5.0, '5'])
def test_only_exact_windows_delete_denied_code_can_retire_absent_node(tmp_path, monkeypatch, route, code):
    target = tmp_path / 'owned'
    target.mkdir() if route == 'directory' else target.write_bytes(b'original')
    original_lstat, original_iterdir = Path.lstat, Path.iterdir
    failed = False
    def fail():
        nonlocal failed
        failed = True
        target.rmdir() if route == 'directory' else target.unlink()
        if route == 'native':
            raise budget.ResourceInspectionError(native_error=code)
        error = PermissionError(13, 'denied')
        if code is not None:
            error.winerror = code
        raise error
    def allocation(path):
        if path == target and route == 'native':
            fail()
        return original_lstat(path).st_size
    def listing(path):
        if path == target:
            fail()
        return original_iterdir(path)
    def lstat(path, *args, **kwargs):
        if path == target and route == 'initial' and not failed:
            fail()
        return original_lstat(path, *args, **kwargs)
    monkeypatch.setattr(budget, 'sys', SimpleNamespace(platform='win32'))
    monkeypatch.setattr(budget, '_windows_allocated_bytes', allocation)
    monkeypatch.setattr(Path, 'lstat', lstat)
    if route == 'directory':
        monkeypatch.setattr(Path, 'iterdir', listing)
    with pytest.raises(budget.ResourceInspectionError):
        budget.managed_disk_usage([target])


def test_retired_alias_cannot_bypass_quota_and_live_denial_never_admits_writer(tmp_path, monkeypatch):
    survivor = tmp_path / 'survivor.bin'; survivor.write_bytes(b'keep')
    retired = tmp_path / 'retired.bin'; os.link(survivor, retired)
    monkeypatch.setattr(budget, 'sys', SimpleNamespace(platform='win32'))
    monkeypatch.setattr(budget, 'managed_storage_roots', lambda *args, **kwargs: (survivor, retired))
    def allocation(path):
        if path == retired:
            retired.unlink()
            raise budget.ResourceInspectionError(native_error=5)
        return budget.RESOURCE_LIMIT_BYTES - 1
    monkeypatch.setattr(budget, '_windows_allocated_bytes', allocation)
    before = budget._reserved_bytes
    with pytest.raises(budget.ResourceBudgetExceeded):
        with budget.disk_quota_guard(reserve_bytes=1):
            pytest.fail('The live alias still occupies the quota')
    assert budget._reserved_bytes == before and survivor.read_bytes() == b'keep'
    def live_denial(path):
        raise budget.ResourceInspectionError(native_error=5)
    monkeypatch.setattr(budget, '_windows_allocated_bytes', live_denial)
    with pytest.raises(budget.ResourceInspectionError):
        with budget.disk_quota_guard(reserve_bytes=1):
            pytest.fail('A live access-denied node must prevent writing')
    assert budget._reserved_bytes == before and survivor.read_bytes() == b'keep'
