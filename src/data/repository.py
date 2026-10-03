# -*- coding: utf-8 -*-
import logging
import sqlite3
import uuid
from datetime import datetime
from dataclasses import replace
from contextlib import contextmanager
from typing import Optional, List
from .database import Database, write_transaction
from .models import ItemDTO, ItemCreateDTO, OcrResultDTO, TagDTO, StatsDTO
from .hasher import sha256_text
from ..core.constants import DEDUP_SECONDS

logger = logging.getLogger(__name__)


def _row_to_item(row: sqlite3.Row) -> ItemDTO:
    return ItemDTO(
        id=row['id'],
        item_type=row['item_type'],
        source_mode=row['source_mode'],
        text_content=row['text_content'],
        text_content_length=row['text_content_length'] or 0,
        edited_text=row['edited_text'],
        note_richtext=row['note_richtext'],
        note_plaintext=row['note_plaintext'],
        raw_image_path=row['raw_image_path'],
        thumbnail_path=row['thumbnail_path'],
        annotation_path=row['annotation_path'],
        image_width=row['image_width'] or 0,
        image_height=row['image_height'] or 0,
        content_hash=row['content_hash'],
        image_hash=row['image_hash'],
        ocr_status=row['ocr_status'] or 'none',
        edit_revision=row['edit_revision'] or 0,
        ocr_job_id=row['ocr_job_id'],
        ocr_engine=row['ocr_engine'],
        ocr_model_version=row['ocr_model_version'],
        ocr_confidence=row['ocr_confidence'] or 0.0,
        ocr_detail_json=row['ocr_detail_json'],
        ocr_elapsed_ms=row['ocr_elapsed_ms'] or 0,
        ocr_error_message=row['ocr_error_message'],
        category=row['category'],
        is_pinned=bool(row['is_pinned']),
        is_archived=bool(row['is_archived']),
        is_deleted=bool(row['is_deleted']),
        source_app_name=row['source_app_name'],
        source_window_title=row['source_window_title'],
        capture_region=row['capture_region'],
        capture_monitor=row['capture_monitor'] or 0,
        created_at=row['created_at'],
        updated_at=row['updated_at'],
    )


class ItemRepository:
    def __init__(self, db: Database):
        self._db = db

    @property
    def _conn(self) -> sqlite3.Connection:
        return self._db.get_connection()

    def _now(self) -> str:
        return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    @write_transaction
    def insert(self, dto: ItemCreateDTO) -> Optional[int]:
        now = self._now()
        try:
            cur = self._conn.execute("""
                INSERT INTO items (
                    item_type, source_mode, text_content, text_content_length,
                    raw_image_path, thumbnail_path, image_width, image_height,
                    content_hash, image_hash, ocr_status,
                    source_app_name, source_window_title,
                    capture_region, capture_monitor,
                    created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                dto.item_type, dto.source_mode,
                dto.text_content,
                len(dto.text_content) if dto.text_content else 0,
                dto.raw_image_path, dto.thumbnail_path,
                dto.image_width, dto.image_height,
                dto.content_hash, dto.image_hash,
                dto.ocr_status,
                dto.source_app_name, dto.source_window_title,
                dto.capture_region, dto.capture_monitor,
                now, now
            ))
            self._conn.commit()
            return cur.lastrowid
        except Exception as e:
            logger.error(f"insert 失敗: {e}")
            try:
                self._conn.rollback()
            except Exception:
                pass  # nosec B110 — connection may be broken; rollback failure is unrecoverable
            return None

    def get_by_id(self, item_id: int) -> Optional[ItemDTO]:
        row = self._conn.execute(
            "SELECT * FROM items WHERE id=?", (item_id,)
        ).fetchone()
        return _row_to_item(row) if row else None

    @write_transaction
    def begin_ocr_attempt(self, item_id: int, source_check=None) -> tuple:
        """Reserve a durable latest job before it can run; never clear user text."""
        item = self.get_by_id(item_id)
        if item is None or item.is_deleted:
            raise ValueError(f'Item {item_id} is deleted or no longer exists')
        if source_check is not None and not source_check(item.raw_image_path):
            raise ValueError('原圖已移除或變更；未加入 OCR 佇列')
        job_id = uuid.uuid4().hex
        self._conn.execute(
            'INSERT INTO ocr_attempts(job_id,item_id,base_edit_revision) VALUES(?,?,?)',
            (job_id, item_id, item.edit_revision))
        self._conn.execute(
            "UPDATE items SET ocr_job_id=?,ocr_status='pending',ocr_error_message=NULL,updated_at=? WHERE id=?",
            (job_id, self._now(), item_id))
        return job_id, item.edit_revision

    def get_ocr_attempts(self, item_id: int) -> list:
        return [dict(row) for row in self._conn.execute(
            'SELECT * FROM ocr_attempts WHERE item_id=? ORDER BY rowid', (item_id,))]

    def update_ocr_result(self, item_id: int, result: OcrResultDTO) -> bool:
        working_result = replace(result)
        with self._db.transaction():
            applied = self._record_ocr_result(item_id, working_result)
        # Only expose the generated legacy job ID after its transaction commits.
        result.job_id = working_result.job_id
        result.base_edit_revision = working_result.base_edit_revision
        return applied

    def _record_ocr_result(self, item_id: int, result: OcrResultDTO) -> bool:
        """Record every attempt, applying only the latest live item's result.

        A failed or empty rerun preserves the previous successful text/provenance.
        Manual edits (including an intentional empty edit) always take precedence.
        """
        item = self.get_by_id(item_id)
        if item is None:
            return False  # A hard-deleted item must never be recreated by late OCR.
        job_id = result.job_id
        if job_id:
            attempt = self._conn.execute(
                'SELECT * FROM ocr_attempts WHERE job_id=? AND item_id=?',
                (job_id, item_id)).fetchone()
            if attempt is None:
                raise ValueError(f'Unknown OCR attempt for item {item_id}')
            if attempt['completed_at'] is not None:
                return False  # Idempotent delivery: do not publish a result twice.
            base_revision = attempt['base_edit_revision']
        else:
            # Compatibility for direct repository consumers and self-test; an
            # unversioned result cannot supersede a reserved asynchronous job.
            job_id = uuid.uuid4().hex
            base_revision = item.edit_revision
            self._conn.execute(
                'INSERT INTO ocr_attempts(job_id,item_id,base_edit_revision) VALUES(?,?,?)',
                (job_id, item_id, base_revision))
            if item.ocr_job_id is None:
                self._conn.execute('UPDATE items SET ocr_job_id=? WHERE id=?', (job_id, item_id))
                item.ocr_job_id = job_id
            else:
                previous = self._conn.execute(
                    'SELECT completed_at FROM ocr_attempts WHERE job_id=?',
                    (item.ocr_job_id,)).fetchone()
                if previous and previous['completed_at'] is not None:
                    self._conn.execute('UPDATE items SET ocr_job_id=? WHERE id=?', (job_id, item_id))
                    item.ocr_job_id = job_id
        result.job_id, result.base_edit_revision = job_id, base_revision
        disposition = ('deleted' if item.is_deleted else
                       'obsolete' if item.ocr_job_id != job_id else 'applied')
        self._conn.execute("""
            UPDATE ocr_attempts SET status=?,text_content=?,confidence=?,detail_json=?,provenance_json=?,
                elapsed_ms=?,error_message=?,engine=?,model_version=?,disposition=?,completed_at=?
            WHERE job_id=?
        """, (result.status, result.text, result.confidence, result.detail_json, result.provenance_json,
              result.elapsed_ms, result.error_message, result.engine,
              result.model_version, disposition, self._now(), job_id))
        if disposition != 'applied':
            return False
        has_text = bool(result.text) and result.status in ('done', 'needs_review', 'confirmed')
        text = result.text if has_text else item.text_content
        effective = item.edited_text if item.edited_text is not None else text
        new_type = ('mixed' if effective else 'image') if item.raw_image_path else ('text' if effective else 'image')
        status = result.status if has_text else 'failed'
        error = result.error_message if has_text or result.error_message else 'OCR 未產生文字；已保留先前文字'
        self._conn.execute("""
            UPDATE items SET text_content=?,text_content_length=?,item_type=?,
                ocr_status=?,ocr_engine=?,ocr_model_version=?,ocr_confidence=?,
                ocr_detail_json=?,ocr_elapsed_ms=?,ocr_error_message=?,content_hash=?,updated_at=?
            WHERE id=?
        """, (text, len(text) if text else 0, new_type, status,
              result.engine if has_text else item.ocr_engine,
              result.model_version if has_text else item.ocr_model_version,
              result.confidence if has_text else item.ocr_confidence,
              result.detail_json if has_text else item.ocr_detail_json,
              result.elapsed_ms if has_text else item.ocr_elapsed_ms,
              error, sha256_text(effective) if effective is not None else None,
              self._now(), item_id))
        return True

    @write_transaction
    def update_edited_text(self, item_id: int, text: str, expected_revision=None):
        item = self.get_by_id(item_id)
        if item is None or item.is_deleted:
            raise ValueError(f'Item {item_id} is deleted or no longer exists')
        if expected_revision is not None and item.edit_revision != expected_revision:
            raise ValueError('文字已在另一個視窗更新；請重新開啟後再編輯')
        self._conn.execute(
            'UPDATE items SET edited_text=?,content_hash=?,edit_revision=edit_revision+1,updated_at=? WHERE id=?',
            (text, sha256_text(text), self._now(), item_id))

    @write_transaction
    def save_editor_content(self, item_id: int, text: str, richtext: str,
                            plaintext: str, expected_revision: int,
                            update_text: bool = True, confirm_review: bool = True):
        """One transaction saves the draft, note, and review decision together."""
        item = self.get_by_id(item_id)
        if item is None or item.is_deleted:
            raise ValueError(f'Item {item_id} is deleted or no longer exists')
        if item.edit_revision != expected_revision:
            raise ValueError('文字已在另一個視窗更新；請重新開啟後再編輯')
        self._conn.execute(
            'UPDATE items SET edited_text=?,content_hash=?,edit_revision=edit_revision+1,updated_at=? WHERE id=?',
            (text if update_text else item.edited_text,
             sha256_text(text) if update_text else item.content_hash, self._now(), item_id))
        self._conn.execute(
            "UPDATE items SET note_richtext=?,note_plaintext=?,"
            "ocr_status=CASE WHEN ? AND ocr_status='needs_review' THEN 'confirmed' ELSE ocr_status END WHERE id=?",
            (richtext, plaintext, confirm_review, item_id))

    @contextmanager
    def image_cleanup_guard(self):
        # Keep the lock across independently committed journal/metadata writes
        # and the filesystem operation. GUI reservations/deletions cannot enter
        # the unlink gap, but a pending journal still survives metadata failure.
        with self._db._write_lock:
            yield

    def is_current_ocr_job(self, item_id: int, job_id: str, raw_path=None) -> bool:
        item = self.get_by_id(item_id)
        return bool(item and not item.is_deleted and item.ocr_job_id == job_id and
                    (raw_path is None or item.raw_image_path == raw_path))

    @write_transaction
    def plan_image_cleanup(self, item_id: int, job_id: str) -> Optional[str]:
        item = self.get_by_id(item_id)
        if not item or item.is_deleted or item.ocr_job_id != job_id or not item.raw_image_path:
            return None
        self._conn.execute(
            "INSERT INTO image_cleanup(item_id,raw_image_path,status,error_message,updated_at) VALUES(?,?,'pending',NULL,?) "
            "ON CONFLICT(item_id) DO UPDATE SET raw_image_path=excluded.raw_image_path,status='pending',error_message=NULL,updated_at=excluded.updated_at",
            (item_id, item.raw_image_path, self._now()))
        return item.raw_image_path

    def pending_image_cleanups(self) -> list:
        return [dict(row) for row in self._conn.execute(
            "SELECT * FROM image_cleanup WHERE status='pending'")]

    @write_transaction
    def finish_image_cleanup(self, item_id: int, path: str):
        # The journal survives a failed metadata commit after unlink, allowing
        # reconciliation to distinguish missing data from a retained original.
        self._conn.execute(
            "UPDATE items SET raw_image_path=NULL,item_type=CASE "
            "WHEN COALESCE(edited_text,text_content,'')!='' THEN 'text' ELSE 'image' END,updated_at=? "
            "WHERE id=? AND raw_image_path=?", (self._now(), item_id, path))
        self._conn.execute(
            "UPDATE image_cleanup SET status='completed',error_message=NULL,updated_at=? WHERE item_id=?",
            (self._now(), item_id))

    @write_transaction
    def image_cleanup_failed(self, item_id: int, error: str):
        self._conn.execute(
            'UPDATE image_cleanup SET error_message=?,updated_at=? WHERE item_id=?',
            (error, self._now(), item_id))

    @write_transaction
    def update_note(self, item_id: int, richtext: str, plaintext: str):
        now = self._now()
        self._conn.execute(
            "UPDATE items SET note_richtext=?, note_plaintext=?, edit_revision=edit_revision+1, updated_at=? WHERE id=?",
            (richtext, plaintext, now, item_id)
        )
        self._conn.commit()

    @write_transaction
    def update_annotation(self, item_id: int, path: str):
        now = self._now()
        self._conn.execute(
            "UPDATE items SET annotation_path=?, updated_at=? WHERE id=?",
            (path, now, item_id)
        )
        self._conn.commit()

    @write_transaction
    def clear_image_paths(self, item_id: int):
        """清空 raw_image_path（暫存圖已刪除後的 DB 一致性補強）。
        thumbnail_path 保留，縮圖檔仍存在可供 detail panel 顯示。
        """
        now = self._now()
        self._conn.execute(
            "UPDATE items SET raw_image_path=NULL, "
            "item_type=CASE WHEN COALESCE(edited_text,text_content,'')!='' "
            "THEN 'text' ELSE 'image' END, updated_at=? WHERE id=?",
            (now, item_id)
        )
        self._conn.commit()

    @write_transaction
    def update_ocr_status(self, item_id: int, status: str):
        now = self._now()
        self._conn.execute(
            "UPDATE items SET ocr_status=?, updated_at=? WHERE id=?",
            (status, now, item_id)
        )
        self._conn.commit()

    @write_transaction
    def confirm_review(self, item_id: int):
        now = self._now()
        self._conn.execute(
            "UPDATE items SET ocr_status='confirmed', updated_at=? WHERE id=? AND ocr_status='needs_review'",
            (now, item_id)
        )
        self._conn.commit()

    @write_transaction
    def set_pinned(self, item_id: int, pinned: bool):
        now = self._now()
        self._conn.execute(
            "UPDATE items SET is_pinned=?, updated_at=? WHERE id=?",
            (1 if pinned else 0, now, item_id)
        )
        self._conn.commit()

    @write_transaction
    def set_archived(self, item_id: int, archived: bool):
        now = self._now()
        self._conn.execute(
            "UPDATE items SET is_archived=?, updated_at=? WHERE id=?",
            (1 if archived else 0, now, item_id)
        )
        self._conn.commit()

    @write_transaction
    def soft_delete(self, item_id: int):
        now = self._now()
        self._conn.execute(
            "UPDATE items SET is_deleted=1,ocr_job_id=NULL,"
            "ocr_status=CASE WHEN ocr_status IN ('pending','processing') THEN 'failed' ELSE ocr_status END,"
            "updated_at=? WHERE id=?",
            (now, item_id)
        )
        self._conn.commit()

    @write_transaction
    def restore(self, item_id: int):
        now = self._now()
        self._conn.execute(
            "UPDATE items SET is_deleted=0, updated_at=? WHERE id=?",
            (now, item_id)
        )
        self._conn.commit()

    @write_transaction
    def hard_delete(self, item_id: int) -> Optional[ItemDTO]:
        item = self.get_by_id(item_id)
        if item:
            self._conn.execute("DELETE FROM items WHERE id=?", (item_id,))
            self._conn.commit()
        return item

    @write_transaction
    def hard_delete_all_soft_deleted(self) -> tuple:
        rows = self._conn.execute(
            "SELECT * FROM items WHERE is_deleted=1"
        ).fetchall()
        items = [_row_to_item(r) for r in rows]
        self._conn.execute("DELETE FROM items WHERE is_deleted=1")
        self._conn.commit()
        return len(items), items

    def get_last_by_source_mode(self, source_mode: str) -> Optional[ItemDTO]:
        row = self._conn.execute("""
            SELECT * FROM items
            WHERE source_mode=? AND is_deleted=0
            ORDER BY created_at DESC, id DESC LIMIT 1
        """, (source_mode,)).fetchone()
        return _row_to_item(row) if row else None

    def should_deduplicate(self, source_mode: str,
                           content_hash: Optional[str] = None,
                           image_hash: Optional[str] = None) -> bool:
        last = self.get_last_by_source_mode(source_mode)
        if not last:
            return False
        if content_hash and last.content_hash == content_hash:
            if last.created_at:
                try:
                    last_time = datetime.strptime(last.created_at, '%Y-%m-%d %H:%M:%S')
                    diff = (datetime.now() - last_time).total_seconds()
                    if 0 <= diff <= DEDUP_SECONDS:
                        return True
                except ValueError:
                    pass
        if image_hash and image_hash.startswith('sha256:') and last.image_hash == image_hash:
            if last.created_at:
                try:
                    last_time = datetime.strptime(last.created_at, '%Y-%m-%d %H:%M:%S')
                    diff = (datetime.now() - last_time).total_seconds()
                    if 0 <= diff <= DEDUP_SECONDS:
                        return True
                except ValueError:
                    pass
        return False

    def list_recent(self, limit: int = 50, offset: int = 0,
                    show_deleted: bool = False,
                    show_archived: bool = False,
                    item_type: Optional[str] = None,
                    pinned_only: bool = False,
                    needs_review_only: bool = False) -> List[ItemDTO]:
        conditions = []
        params = []
        if not show_deleted:
            conditions.append("is_deleted=0")
        if not show_archived:
            conditions.append("is_archived=0")
        if item_type:
            conditions.append("item_type=?")
            params.append(item_type)
        if pinned_only:
            conditions.append("is_pinned=1")
        if needs_review_only:
            conditions.append("ocr_status='needs_review'")
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        params += [limit, offset]
        rows = self._conn.execute(f"""
            SELECT * FROM items {where}
            ORDER BY is_pinned DESC, created_at DESC
            LIMIT ? OFFSET ?
        """, params).fetchall()  # nosec B608 — {where} is built from hardcoded literals only; item_type is parameterized
        return [_row_to_item(r) for r in rows]

    def search_fulltext(self, query: str, limit: int = 50) -> List[ItemDTO]:
        results = []
        ids_seen = set()
        # Escape FTS5 special characters to prevent syntax errors
        fts_query = '"' + query.replace('"', '""') + '"'
        try:
            rows = self._conn.execute("""
                SELECT items.* FROM items
                JOIN items_fts ON items.id = items_fts.rowid
                WHERE items_fts MATCH ? AND items.is_deleted=0
                ORDER BY rank LIMIT ?
            """, (fts_query, limit)).fetchall()
            for r in rows:
                item = _row_to_item(r)
                results.append(item)
                ids_seen.add(item.id)
        except Exception as e:
            logger.warning(f"FTS 搜尋失敗，改用 LIKE: {e}")

        if len(results) < 5:
            like_q = f"%{query}%"
            rows2 = self._conn.execute("""
                SELECT * FROM items
                WHERE is_deleted=0 AND (
                    text_content LIKE ? OR edited_text LIKE ? OR note_plaintext LIKE ?
                )
                ORDER BY created_at DESC LIMIT ?
            """, (like_q, like_q, like_q, limit)).fetchall()
            for r in rows2:
                item = _row_to_item(r)
                if item.id not in ids_seen:
                    results.append(item)
                    ids_seen.add(item.id)
        return results[:limit]

    def get_statistics(self) -> StatsDTO:
        r = self._conn.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN item_type='text' THEN 1 ELSE 0 END) as text_count,
                SUM(CASE WHEN item_type='image' THEN 1 ELSE 0 END) as image_count,
                SUM(CASE WHEN item_type='mixed' THEN 1 ELSE 0 END) as mixed_count,
                SUM(CASE WHEN ocr_status IN ('done','confirmed') THEN 1 ELSE 0 END) as ocr_done,
                SUM(CASE WHEN ocr_status='needs_review' THEN 1 ELSE 0 END) as ocr_needs_review,
                SUM(CASE WHEN ocr_status='failed' THEN 1 ELSE 0 END) as ocr_failed,
                SUM(CASE WHEN is_pinned=1 THEN 1 ELSE 0 END) as pinned_count,
                SUM(CASE WHEN is_archived=1 THEN 1 ELSE 0 END) as archived_count
            FROM items WHERE is_deleted=0
        """).fetchone()
        if r:
            return StatsDTO(
                total=r['total'] or 0,
                text_count=r['text_count'] or 0,
                image_count=r['image_count'] or 0,
                mixed_count=r['mixed_count'] or 0,
                ocr_done=r['ocr_done'] or 0,
                ocr_needs_review=r['ocr_needs_review'] or 0,
                ocr_failed=r['ocr_failed'] or 0,
                pinned_count=r['pinned_count'] or 0,
                archived_count=r['archived_count'] or 0,
            )
        return StatsDTO()

    def count(self, show_deleted: bool = False, needs_review_only: bool = False) -> int:
        conds = []
        if not show_deleted:
            conds.append("is_deleted=0")
        if needs_review_only:
            conds.append("ocr_status='needs_review'")
        where = ("WHERE " + " AND ".join(conds)) if conds else ""
        r = self._conn.execute(f"SELECT COUNT(*) FROM items {where}").fetchone()  # nosec B608 — conds contains only hardcoded literals
        return r[0] if r else 0


class TagRepository:
    def __init__(self, db: Database):
        self._db = db

    @property
    def _conn(self):
        return self._db.get_connection()

    @write_transaction
    def create(self, name: str, color: str = "#4A90D9") -> Optional[TagDTO]:
        try:
            cur = self._conn.execute(
                "INSERT INTO tags(name, color) VALUES(?,?)", (name, color)
            )
            self._conn.commit()
            return TagDTO(id=cur.lastrowid, name=name, color=color)
        except sqlite3.IntegrityError:
            logger.warning(f"標籤 '{name}' 已存在")
            return None

    @write_transaction
    def update(self, tag_id: int, name: str, color: str):
        self._conn.execute('UPDATE tags SET name=?,color=? WHERE id=?', (name, color, tag_id))
        self._conn.commit()

    def usage_count(self, tag_id: int) -> int:
        return self._conn.execute('SELECT COUNT(*) FROM item_tags WHERE tag_id=?', (tag_id,)).fetchone()[0]

    def list_all(self) -> List[TagDTO]:
        rows = self._conn.execute("SELECT * FROM tags ORDER BY name").fetchall()
        return [TagDTO(id=r['id'], name=r['name'], color=r['color']) for r in rows]

    @write_transaction
    def delete(self, tag_id: int):
        self._conn.execute("DELETE FROM tags WHERE id=?", (tag_id,))
        self._conn.commit()

    @write_transaction
    def add_to_item(self, item_id: int, tag_id: int):
        self._conn.execute(
            "INSERT OR IGNORE INTO item_tags(item_id, tag_id) VALUES(?,?)",
            (item_id, tag_id)
        )
        self._conn.commit()

    @write_transaction
    def remove_from_item(self, item_id: int, tag_id: int):
        self._conn.execute(
            "DELETE FROM item_tags WHERE item_id=? AND tag_id=?",
            (item_id, tag_id)
        )
        self._conn.commit()

    def get_tags_for_item(self, item_id: int) -> List[TagDTO]:
        rows = self._conn.execute("""
            SELECT t.* FROM tags t
            JOIN item_tags it ON t.id = it.tag_id
            WHERE it.item_id=?
            ORDER BY t.name
        """, (item_id,)).fetchall()
        return [TagDTO(id=r['id'], name=r['name'], color=r['color']) for r in rows]
