"""Only fresh, unchanged, unreferenced capture assets may be reclaimed."""
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from src.core.resource_budget import ResourceBudgetExceeded
from src.data.models import _CaptureFileOwnership
from src.workers.capture_worker import CaptureWorker
from src.workers.db_worker import DbWorker


@pytest.fixture
def fresh_capture(qtbot, file_mgr, monkeypatch):
    class Backend:
        def capture_region(self, *args):
            return np.full((20,30,3),127,np.uint8)
    monkeypatch.setattr('src.workers.capture_worker.get_capture_backend', Backend)
    worker = CaptureWorker(file_mgr)
    received = []
    worker.capture_done.connect(lambda path,dto: received.append(dto))
    def create():
        count = len(received)
        worker.capture_region(0,0,30,20,1,'region_ocr')
        qtbot.waitUntil(lambda: len(received) == count+1)
        dto = received[-1]
        assert len(dto.transient_capture_files) == 2
        return dto
    yield create
    worker.stop()
    assert worker.wait(5000)


def paths(file_mgr,dto):
    return [Path(file_mgr.get_abs_path(path)) for path in (dto.raw_image_path,dto.thumbnail_path)]


def test_actual_sql_insert_failure_reclaims_both_owned_assets(item_repo,file_mgr,fresh_capture):
    dto = fresh_capture()
    item_repo._conn.execute("CREATE TRIGGER reject_capture BEFORE INSERT ON items BEGIN SELECT RAISE(ABORT,'fixture failure'); END")
    item_repo._conn.commit()
    worker = DbWorker(item_repo,file_mgr)
    failed = []
    worker.save_failed.connect(failed.append)
    worker.save_item(dto)
    assert failed and item_repo.count() == 0
    assert all(not path.exists() for path in paths(file_mgr,dto))


def test_quota_admission_failure_still_cleans_without_write_transaction(item_repo,file_mgr,fresh_capture,monkeypatch):
    dto = fresh_capture()
    @contextmanager
    def quota_failure():
        raise ResourceBudgetExceeded('disk')
        yield
    monkeypatch.setattr(item_repo._db,'transaction',quota_failure)
    DbWorker(item_repo,file_mgr).save_item(dto)
    assert item_repo.count() == 0
    assert all(not path.exists() for path in paths(file_mgr,dto))


def test_normal_dedup_cleans_only_new_owned_duplicate(item_repo,file_mgr,fresh_capture):
    worker = DbWorker(item_repo,file_mgr)
    first, second = fresh_capture(),fresh_capture()
    worker.save_item(first)
    worker.save_item(second)
    assert item_repo.count() == 1
    assert all(path.exists() for path in paths(file_mgr,first))
    assert all(not path.exists() for path in paths(file_mgr,second))


@pytest.mark.parametrize('mutation',['replace','rewrite','forged','import','referenced','check_failed'])
def test_cleanup_preserves_unproven_replaced_imported_or_referenced(item_repo,file_mgr,fresh_capture,monkeypatch,mutation):
    dto = fresh_capture()
    original_paths = paths(file_mgr,dto)
    if mutation == 'replace':
        original_paths[0].unlink()
        original_paths[0].write_bytes(b'replacement')
    elif mutation == 'rewrite':
        original_paths[0].write_bytes(b'user changed capture')
    elif mutation == 'forged':
        real = dto.transient_capture_files[0]
        forged = _CaptureFileOwnership(real.relative_path,real.absolute_path,real.identity,real.digest)
        dto = replace(dto,transient_capture_files=(forged,))
    elif mutation == 'import':
        dto = replace(dto,source_mode='import')
    elif mutation == 'referenced':
        assert item_repo.insert(replace(dto,transient_capture_files=()))
    else:
        def unreadable(*args):
            raise RuntimeError('unreadable references')
        monkeypatch.setattr(item_repo,'cleanup_unreferenced_capture',unreadable)
    # Exercise the failure path rather than bypassing it with a direct cleanup call.
    monkeypatch.setattr(item_repo,'insert',lambda value: None)
    DbWorker(item_repo,file_mgr,enable_dedup=False).save_item(dto)
    assert original_paths[0].exists()
    if mutation not in ('replace','rewrite','forged'):
        assert original_paths[1].exists()


def test_dedup_without_ownership_never_deletes_existing_user_file(item_repo,file_mgr,fresh_capture):
    dto = fresh_capture()
    item_repo.insert(dto)
    unproved = replace(dto,transient_capture_files=())
    DbWorker(item_repo,file_mgr).save_item(unproved)
    assert all(path.exists() for path in paths(file_mgr,dto))


def test_committed_callback_failure_preserves_assets(item_repo,file_mgr,fresh_capture,monkeypatch):
    dto = fresh_capture()
    worker = DbWorker(item_repo,file_mgr)
    class BrokenNotification:
        def emit(self,*args):
            raise RuntimeError('notification failed after commit')
    monkeypatch.setattr(worker,'item_saved_with_context',BrokenNotification())
    worker.save_item(dto)
    assert item_repo.count() == 1 and not dto.transient_capture_files
    assert all(path.exists() for path in paths(file_mgr,dto))


def test_forged_dictionary_cannot_authorize_cleanup(item_repo,file_mgr,fresh_capture,monkeypatch):
    dto = fresh_capture()
    dto.transient_capture_files = ({'relative_path':dto.raw_image_path},)
    monkeypatch.setattr(item_repo,'insert',lambda value: None)
    DbWorker(item_repo,file_mgr).save_item(dto)
    assert all(path.exists() for path in paths(file_mgr,dto))


def test_matching_size_and_timestamp_still_requires_matching_hash(item_repo,file_mgr,fresh_capture,monkeypatch):
    import os
    dto = fresh_capture()
    path = paths(file_mgr,dto)[0]
    original = path.stat()
    payload = bytearray(path.read_bytes())
    payload[-1] ^= 1
    path.write_bytes(payload)
    os.utime(path,ns=(original.st_atime_ns,original.st_mtime_ns))
    monkeypatch.setattr(item_repo,'insert',lambda value: None)
    DbWorker(item_repo,file_mgr).save_item(dto)
    assert path.exists() and path.read_bytes() == payload


def test_reference_alias_is_preserved_without_exact_string_match(item_repo,file_mgr,fresh_capture,monkeypatch):
    dto = fresh_capture()
    alias = str(Path(dto.raw_image_path).parent / '..' / Path(dto.raw_image_path).parent.name / Path(dto.raw_image_path).name)
    assert alias != dto.raw_image_path
    assert item_repo.insert(replace(dto,raw_image_path=alias,transient_capture_files=()))
    monkeypatch.setattr(item_repo,'insert',lambda value: None)
    DbWorker(item_repo,file_mgr,enable_dedup=False).save_item(dto)
    assert all(path.exists() for path in paths(file_mgr,dto))
