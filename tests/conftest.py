# -*- coding: utf-8 -*-
"""
Pytest 共用 fixtures
"""
import pytest


# ---- 資料庫 ----------------------------------------------------------------

@pytest.fixture
def tmp_db(tmp_path):
    from src.data.database import Database
    db = Database(str(tmp_path / "test.db"))
    yield db
    db.close()


@pytest.fixture
def item_repo(tmp_db):
    from src.data.repository import ItemRepository
    return ItemRepository(tmp_db)


@pytest.fixture
def tag_repo(tmp_db):
    from src.data.repository import TagRepository
    return TagRepository(tmp_db)


@pytest.fixture
def file_mgr(tmp_path):
    from src.data.file_manager import FileManager
    return FileManager(str(tmp_path / "data"))


@pytest.fixture
def db_worker(tmp_db, file_mgr):
    """DbWorker 直接實例化（不移到 QThread），可在主執行緒直接呼叫 slots。"""
    from src.data.repository import ItemRepository
    from src.workers.db_worker import DbWorker
    repo = ItemRepository(tmp_db)
    return DbWorker(repo, file_mgr)


# ---- DTO factories 暴露為 fixtures ----------------------------------------

@pytest.fixture
def make_text_dto():
    from src.data.models import ItemCreateDTO
    from src.data.hasher import sha256_text
    from src.core.constants import ITEM_TYPE_TEXT, SOURCE_MODE_CLIPBOARD_TEXT

    def _factory(text="hello world", source_mode=SOURCE_MODE_CLIPBOARD_TEXT):
        return ItemCreateDTO(
            item_type=ITEM_TYPE_TEXT,
            source_mode=source_mode,
            text_content=text,
            content_hash=sha256_text(text),
            ocr_status='none',
        )
    return _factory


@pytest.fixture
def make_image_dto():
    from src.data.models import ItemCreateDTO
    from src.core.constants import ITEM_TYPE_IMAGE, SOURCE_MODE_REGION_OCR

    def _factory(
        rel_path="captures/test.png",
        thumb_path="thumbnails/test_thumb.png",
        source_mode=SOURCE_MODE_REGION_OCR,
    ):
        return ItemCreateDTO(
            item_type=ITEM_TYPE_IMAGE,
            source_mode=source_mode,
            raw_image_path=rel_path,
            thumbnail_path=thumb_path,
            image_width=100,
            image_height=100,
            image_hash="deadbeef",
            ocr_status='none',
        )
    return _factory


@pytest.fixture
def make_ocr_result():
    from src.data.models import OcrResultDTO

    def _factory(text="識別文字", confidence=0.95, status="done"):
        return OcrResultDTO(
            text=text,
            confidence=confidence,
            status=status,
            elapsed_ms=120,
        )
    return _factory
