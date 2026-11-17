# -*- coding: utf-8 -*-
import sqlite3
import os
import logging
import threading
from contextlib import contextmanager
from functools import wraps
from ..core.constants import SCHEMA_VERSION
from ..core.resource_budget import disk_quota_guard, register_managed_root, ResourceBudgetExceeded, disk_write_reservation

logger = logging.getLogger(__name__)


def write_transaction(method):
    """Rollback failures before the next operation can commit them."""
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._db.transaction():
            return method(self, *args, **kwargs)
    return wrapped


_FTS_UPDATE_TRIGGER = """CREATE TRIGGER IF NOT EXISTS items_fts_update
       AFTER UPDATE OF text_content, edited_text, note_plaintext ON items
       WHEN old.text_content IS NOT new.text_content
         OR old.edited_text IS NOT new.edited_text
         OR old.note_plaintext IS NOT new.note_plaintext
       BEGIN
           INSERT INTO items_fts(items_fts, rowid, text_content, edited_text, note_plaintext)
           VALUES ('delete', old.id, old.text_content, old.edited_text, old.note_plaintext);
           INSERT INTO items_fts(rowid, text_content, edited_text, note_plaintext)
           VALUES (new.id, new.text_content, new.edited_text, new.note_plaintext);
       END"""

_CREATE_TABLES = [
    """CREATE TABLE IF NOT EXISTS app_meta (
        key   TEXT PRIMARY KEY,
        value TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS migrations (
        version     INTEGER PRIMARY KEY,
        applied_at  TEXT DEFAULT (datetime('now','localtime')),
        description TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS tags (
        id    INTEGER PRIMARY KEY AUTOINCREMENT,
        name  TEXT UNIQUE NOT NULL,
        color TEXT NOT NULL DEFAULT '#4A90D9'
    )""",
    """CREATE TABLE IF NOT EXISTS items (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        item_type           TEXT NOT NULL DEFAULT 'text',
        source_mode         TEXT NOT NULL DEFAULT 'region_ocr',
        text_content        TEXT,
        text_content_length INTEGER DEFAULT 0,
        edited_text         TEXT,
        note_richtext       TEXT,
        note_plaintext      TEXT,
        raw_image_path      TEXT,
        thumbnail_path      TEXT,
        annotation_path     TEXT,
        image_width         INTEGER DEFAULT 0,
        image_height        INTEGER DEFAULT 0,
        content_hash        TEXT,
        image_hash          TEXT,
        ocr_status          TEXT NOT NULL DEFAULT 'none',
        ocr_engine          TEXT,
        ocr_model_version   TEXT,
        ocr_confidence      REAL DEFAULT 0.0,
        ocr_detail_json     TEXT,
        ocr_elapsed_ms      INTEGER DEFAULT 0,
        ocr_error_message   TEXT,
        category            TEXT,
        is_pinned           INTEGER DEFAULT 0,
        is_archived         INTEGER DEFAULT 0,
        is_deleted          INTEGER DEFAULT 0,
        source_app_name     TEXT,
        source_window_title TEXT,
        capture_region      TEXT,
        capture_monitor     INTEGER DEFAULT 0,
        created_at          TEXT DEFAULT (datetime('now','localtime')),
        updated_at          TEXT DEFAULT (datetime('now','localtime'))
    )""",
    """CREATE TABLE IF NOT EXISTS ocr_attempts (
        job_id TEXT PRIMARY KEY,
        item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
        base_edit_revision INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'pending',
        text_content TEXT,
        confidence REAL,
        detail_json TEXT,
        provenance_json TEXT,
        elapsed_ms INTEGER,
        error_message TEXT,
        engine TEXT,
        model_version TEXT,
        disposition TEXT NOT NULL DEFAULT 'pending',
        created_at TEXT DEFAULT (datetime('now','localtime')),
        completed_at TEXT
    )""",
    """CREATE INDEX IF NOT EXISTS idx_ocr_attempts_item ON ocr_attempts(item_id)""",
    """CREATE TABLE IF NOT EXISTS image_cleanup (
        item_id INTEGER PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE,
        raw_image_path TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        error_message TEXT,
        updated_at TEXT DEFAULT (datetime('now','localtime'))
    )""",
    """CREATE TABLE IF NOT EXISTS item_tags (
        item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
        tag_id  INTEGER NOT NULL REFERENCES tags(id)  ON DELETE CASCADE,
        PRIMARY KEY (item_id, tag_id)
    )""",
    """CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
        text_content,
        edited_text,
        note_plaintext,
        content='items',
        content_rowid='id',
        tokenize='unicode61'
    )""",
    """CREATE TRIGGER IF NOT EXISTS items_fts_insert
       AFTER INSERT ON items BEGIN
           INSERT INTO items_fts(rowid, text_content, edited_text, note_plaintext)
           VALUES (new.id, new.text_content, new.edited_text, new.note_plaintext);
       END""",
    _FTS_UPDATE_TRIGGER,
    """CREATE TRIGGER IF NOT EXISTS items_fts_delete
       AFTER DELETE ON items BEGIN
           INSERT INTO items_fts(items_fts, rowid, text_content, edited_text, note_plaintext)
           VALUES ('delete', old.id, old.text_content, old.edited_text, old.note_plaintext);
       END""",
    # --- v4: 效能索引 ---
    """CREATE INDEX IF NOT EXISTS idx_items_deleted_pinned_created
       ON items(is_deleted, is_pinned, created_at)""",
    """CREATE INDEX IF NOT EXISTS idx_items_source_deleted_created
       ON items(source_mode, is_deleted, created_at)""",
    """CREATE INDEX IF NOT EXISTS idx_items_status_deleted
       ON items(ocr_status, is_deleted)""",
    """CREATE INDEX IF NOT EXISTS idx_items_archive_age
       ON items(updated_at, id)
       WHERE is_deleted=0 AND is_archived=0 AND is_pinned=0""",
    """CREATE INDEX IF NOT EXISTS idx_items_content_hash
       ON items(content_hash) WHERE content_hash IS NOT NULL""",
    """CREATE INDEX IF NOT EXISTS idx_items_image_hash
       ON items(image_hash) WHERE image_hash IS NOT NULL""",
    """CREATE INDEX IF NOT EXISTS idx_item_tags_tag_id
       ON item_tags(tag_id, item_id)""",
]


class Database:
    def __init__(self, db_path: str):
        self._db_path = os.path.abspath(db_path)
        self._local = threading.local()
        self._connections = []
        self._failed_connections = []
        self._lock = threading.RLock()
        self._write_lock = threading.RLock()
        self._closed = False
        self.read_only = False
        for suffix in ('', '-journal', '-wal', '-shm'):
            register_managed_root(self._db_path + suffix, owner=self)
        try:
            self._initialize()
        except BaseException:
            try:
                self.close()
            except BaseException:
                # Preserve the initialization failure; failed handles remain
                # owned by this instance so explicit cleanup can retry them.
                pass
            raise

    def _initialize(self):
        try:
            with disk_quota_guard(reserve_bytes=self._write_reservation()):
                directory = os.path.dirname(self._db_path)
                if not os.path.isdir(os.path.dirname(directory)):
                    raise FileNotFoundError('請先選擇已存在的資料上層資料夾')
                os.makedirs(directory, exist_ok=True)
                self._conn = self.get_connection()
                self._bound_growth(self._conn)
                self._run_migrations()
        except ResourceBudgetExceeded:
            if not os.path.isfile(self._db_path):
                raise
            self.read_only = True
            # Read-only WAL can create a missing shared-memory file. Do not let
            # opening an over-quota legacy profile write that unreserved file.
            if os.path.exists(self._db_path + '-wal') and not os.path.exists(self._db_path + '-shm'):
                raise
            self._conn = self.get_connection()
        logger.info('資料庫已開啟' if not self.read_only else '資料用量已達上限，歷史資料以唯讀開啟')

    _MAX_TRANSACTION_GROWTH = 8 * 1024 * 1024

    def _write_reservation(self):
        size = os.path.getsize(self._db_path) if os.path.isfile(self._db_path) else 0
        wal = self._db_path + '-wal'
        wal_size = os.path.getsize(wal) if os.path.isfile(wal) else 0
        # A rollback journal can contain every old page plus per-page headers.
        # Multiple commits within one repository operation retain this reserve;
        # allow a journal of the grown database too, and a legacy WAL checkpoint.
        return disk_write_reservation(self._db_path, size + size // 128 + 3 * self._MAX_TRANSACTION_GROWTH + wal_size,
                                      new_files=4, new_directories=1)

    def _bound_growth(self, conn):
        page_size = int(conn.execute('PRAGMA page_size').fetchone()[0])
        page_count = int(conn.execute('PRAGMA page_count').fetchone()[0])
        maximum = page_count + self._MAX_TRANSACTION_GROWTH // page_size
        conn.execute(f'PRAGMA max_page_count={maximum}')

    def _run_migrations(self):
        for stmt in _CREATE_TABLES:
            self._conn.execute(stmt)
        # Additive migrations preserve existing items, edits, and FTS content.
        columns = {row[1] for row in self._conn.execute('PRAGMA table_info(items)')}
        for name, definition in (
            ('edit_revision', 'INTEGER NOT NULL DEFAULT 0'),
            ('ocr_job_id', 'TEXT'),
        ):
            if name not in columns:
                self._conn.execute(f'ALTER TABLE items ADD COLUMN {name} {definition}')
        attempt_columns = {row[1] for row in self._conn.execute('PRAGMA table_info(ocr_attempts)')}
        if 'provenance_json' not in attempt_columns:
            self._conn.execute('ALTER TABLE ocr_attempts ADD COLUMN provenance_json TEXT')
        # Older installations rebuild FTS even for pin/archive/timestamp-only
        # writes. Replace just that trigger, preserving all indexed content.
        existing = self._conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' AND name='items_fts_update'").fetchone()
        normalize = lambda sql: ''.join(sql.replace('IF NOT EXISTS ', '').split()).casefold()
        if existing is None or normalize(existing['sql']) != normalize(_FTS_UPDATE_TRIGGER):
            self._conn.execute('DROP TRIGGER IF EXISTS items_fts_update')
            self._conn.execute(_FTS_UPDATE_TRIGGER)
        self._conn.commit()

        cur = self._conn.execute(
            "SELECT value FROM app_meta WHERE key='schema_version'"
        )
        row = cur.fetchone()
        current_version = int(row[0]) if row else 0

        if current_version < SCHEMA_VERSION:
            self._conn.execute(
                "INSERT OR REPLACE INTO app_meta(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),)
            )
            self._conn.execute(
                "INSERT OR IGNORE INTO migrations(version, description) VALUES(?, ?)",
                (SCHEMA_VERSION, f"初始化 Schema v{SCHEMA_VERSION}")
            )
            self._conn.commit()
            logger.info(f"資料庫 Schema 已更新至 v{SCHEMA_VERSION}")

    def get_connection(self) -> sqlite3.Connection:
        with self._lock:
            if self._closed:
                raise RuntimeError('Database is closed')
            conn = getattr(self._local, 'connection', None)
            if conn is None:
                # Each calling thread owns a distinct transaction. Cross-thread
                # close is allowed only after lifecycle shutdown has joined workers.
                if self.read_only:
                    from pathlib import Path
                    conn = sqlite3.connect(Path(self._db_path).as_uri() + '?mode=ro',
                                           uri=True, check_same_thread=False)
                else:
                    conn = sqlite3.connect(self._db_path, check_same_thread=False)
                    # DELETE journals have a calculable full old-page bound;
                    # journal_size_limit alone would not bound an active WAL.
                try:
                    conn.execute('PRAGMA query_only = ON' if self.read_only else 'PRAGMA journal_mode = DELETE')
                    conn.row_factory = sqlite3.Row
                    conn.execute('PRAGMA foreign_keys = ON')
                    conn.execute('PRAGMA temp_store = MEMORY')
                    conn.execute('PRAGMA cache_size = -4096')
                    conn.execute('PRAGMA synchronous = FULL')
                    conn.execute('PRAGMA busy_timeout = 5000')
                except BaseException:
                    try:
                        conn.close()
                    except BaseException:
                        # An unpublished handle must never become usable via
                        # thread-local state, even if its cleanup needs retry.
                        self._failed_connections.append(conn)
                    raise
                self._local.connection = conn
                self._connections.append(conn)
            return conn

    @contextmanager
    def transaction(self):
        with self._write_lock:
            if self.read_only:
                raise ResourceBudgetExceeded('disk')
            # Nested repository methods share one transaction admission, rather
            # than reserving the complete rollback journal multiple times.
            depth = getattr(self._local, 'write_depth', 0)
            if depth:
                with self.get_connection() as conn:
                    yield conn
                return
            with disk_quota_guard(reserve_bytes=self._write_reservation()):
                conn = self.get_connection()
                self._bound_growth(conn)
                self._local.write_depth = 1
                try:
                    with conn:
                        yield conn
                finally:
                    self._local.write_depth = 0

    def close(self):
        with self._write_lock, self._lock:
            self._closed = True
            failed = []
            first_error = None
            for conn in self._connections + self._failed_connections:
                try:
                    conn.close()
                except BaseException as exc:
                    failed.append(conn)
                    if first_error is None:
                        first_error = exc
            self._connections.clear()
            self._failed_connections = failed
            self._conn = None
            if first_error is not None:
                raise first_error
