# -*- coding: utf-8 -*-
import sqlite3
import os
import logging
import threading
from contextlib import contextmanager
from functools import wraps
from typing import Optional
from ..core.constants import SCHEMA_VERSION

logger = logging.getLogger(__name__)


def write_transaction(method):
    """Rollback failures before the next operation can commit them."""
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._db.transaction():
            return method(self, *args, **kwargs)
    return wrapped

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
    """CREATE TRIGGER IF NOT EXISTS items_fts_update
       AFTER UPDATE ON items BEGIN
           INSERT INTO items_fts(items_fts, rowid, text_content, edited_text, note_plaintext)
           VALUES ('delete', old.id, old.text_content, old.edited_text, old.note_plaintext);
           INSERT INTO items_fts(rowid, text_content, edited_text, note_plaintext)
           VALUES (new.id, new.text_content, new.edited_text, new.note_plaintext);
       END""",
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
        self._lock = threading.RLock()
        self._write_lock = threading.RLock()
        self._closed = False
        self._initialize()

    def _initialize(self):
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        self._conn = self.get_connection()
        self._run_migrations()
        logger.info(f"資料庫初始化完成: {self._db_path}")

    def _run_migrations(self):
        for stmt in _CREATE_TABLES:
            self._conn.execute(stmt)
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
                conn = sqlite3.connect(self._db_path, check_same_thread=False)
                conn.row_factory = sqlite3.Row
                conn.execute('PRAGMA foreign_keys = ON')
                conn.execute('PRAGMA journal_mode = WAL')
                conn.execute('PRAGMA synchronous = NORMAL')
                conn.execute('PRAGMA busy_timeout = 5000')
                self._local.connection = conn
                self._connections.append(conn)
            return conn

    @contextmanager
    def transaction(self):
        with self._write_lock:
            with self.get_connection() as conn:
                yield conn

    def close(self):
        with self._write_lock, self._lock:
            self._closed = True
            for conn in self._connections:
                conn.close()
            self._connections.clear()
            self._conn = None
