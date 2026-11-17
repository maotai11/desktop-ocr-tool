# -*- coding: utf-8 -*-
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from weakref import WeakSet


@dataclass
class ItemDTO:
    id: int
    item_type: str
    source_mode: str
    text_content: Optional[str] = None
    text_content_length: int = 0
    edited_text: Optional[str] = None
    note_richtext: Optional[str] = None
    note_plaintext: Optional[str] = None
    raw_image_path: Optional[str] = None
    thumbnail_path: Optional[str] = None
    annotation_path: Optional[str] = None
    image_width: int = 0
    image_height: int = 0
    content_hash: Optional[str] = None
    image_hash: Optional[str] = None
    ocr_status: str = "none"
    edit_revision: int = 0
    ocr_job_id: Optional[str] = None
    ocr_engine: Optional[str] = None
    ocr_model_version: Optional[str] = None
    ocr_confidence: float = 0.0
    ocr_detail_json: Optional[str] = None
    ocr_elapsed_ms: int = 0
    ocr_error_message: Optional[str] = None
    category: Optional[str] = None
    is_pinned: bool = False
    is_archived: bool = False
    is_deleted: bool = False
    source_app_name: Optional[str] = None
    source_window_title: Optional[str] = None
    capture_region: Optional[str] = None
    capture_monitor: int = 0
    tags: list = field(default_factory=list)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    def get_effective_text(self) -> Optional[str]:
        return self.edited_text if self.edited_text is not None else self.text_content


@dataclass
class ItemCreateDTO:
    item_type: str
    source_mode: str
    text_content: Optional[str] = None
    raw_image_path: Optional[str] = None
    thumbnail_path: Optional[str] = None
    image_width: int = 0
    image_height: int = 0
    content_hash: Optional[str] = None
    image_hash: Optional[str] = None
    ocr_status: str = "none"
    source_app_name: Optional[str] = None
    source_window_title: Optional[str] = None
    capture_region: Optional[str] = None
    capture_monitor: int = 0
    # Transient only, never persisted as user content or a schema change.
    clipboard_epoch: Optional[int] = None
    transient_capture_files: tuple = field(default_factory=tuple, repr=False, compare=False)


@dataclass
class OcrResultDTO:
    text: str
    confidence: float
    status: str
    detail_json: Optional[str] = None
    elapsed_ms: int = 0
    error_message: Optional[str] = None
    engine: str = "unknown"
    model_version: str = "unknown"
    job_id: Optional[str] = None
    base_edit_revision: Optional[int] = None
    provenance_json: Optional[str] = None
    clipboard_epoch: Optional[int] = None


@dataclass
class TagDTO:
    id: int
    name: str
    color: str = "#4A90D9"


@dataclass
class StatsDTO:
    total: int = 0
    text_count: int = 0
    image_count: int = 0
    mixed_count: int = 0
    ocr_done: int = 0
    ocr_needs_review: int = 0
    ocr_failed: int = 0
    pinned_count: int = 0
    archived_count: int = 0


# These capabilities are process-local, never JSON/deserialized user metadata.
# A dict or a lookalike dataclass is not authority to remove a file.
_issued_capture_files = WeakSet()


@dataclass(frozen=True, eq=False)
class _CaptureFileOwnership:
    relative_path: str
    absolute_path: str
    identity: tuple
    digest: str


def _file_identity(path):
    stat = path.lstat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


def issue_capture_file_ownership(file_manager, relative_path):
    """Only call immediately after the exclusive fresh capture write succeeds."""
    path = Path(file_manager.get_abs_path(relative_path))
    if path.is_symlink() or not path.is_file():
        raise ValueError('Capture ownership requires a regular file')
    identity = _file_identity(path)
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if identity != _file_identity(path):
        raise ValueError('Capture changed while recording ownership')
    proof = _CaptureFileOwnership(relative_path, str(path), identity, digest)
    _issued_capture_files.add(proof)
    return proof


def remove_owned_capture_file(file_manager, proof):
    """False means preserve: changed/replaced/forged paths never get removed."""
    if not isinstance(proof, _CaptureFileOwnership) or proof not in _issued_capture_files:
        return False
    path = Path(file_manager.get_abs_path(proof.relative_path))
    if str(path) != proof.absolute_path or path.is_symlink():
        return False
    if not path.exists():
        return True
    if _file_identity(path) != proof.identity:
        return False
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if digest != proof.digest or _file_identity(path) != proof.identity:
        return False
    path.unlink()
    _issued_capture_files.discard(proof)
    return True
