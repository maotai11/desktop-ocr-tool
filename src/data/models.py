# -*- coding: utf-8 -*-
from dataclasses import dataclass, field
from typing import Optional


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
        return self.edited_text if self.edited_text else self.text_content


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


@dataclass
class OcrResultDTO:
    text: str
    confidence: float
    status: str
    detail_json: Optional[str] = None
    elapsed_ms: int = 0
    error_message: Optional[str] = None
    engine: str = "onnxruntime"
    model_version: str = "pp-ocrv4"


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
