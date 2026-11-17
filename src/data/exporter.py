# -*- coding: utf-8 -*-
import csv
import json
import logging
import os
import zipfile
import uuid
from datetime import datetime
from typing import List
from .models import ItemDTO
from .file_manager import FileManager
from ..core.resource_budget import bounded_output, register_managed_root

logger = logging.getLogger(__name__)


class Exporter:
    def __init__(self, export_dir: str, data_dir: str):
        self._export_dir = export_dir
        self._data_dir = data_dir
        register_managed_root(export_dir, owner=self)

    @staticmethod
    def _text_bound(items):
        # UTF-8, JSON escaping and CSV quote doubling all fit this conservative
        # bound; the stream enforces it even if a DTO changes during export.
        return 65536 + sum(4096 + 12 * sum(len(str(value or '')) for value in (
            item.get_effective_text(), item.created_at, item.source_mode,
            item.item_type, item.ocr_status)) for item in items)

    def _ts(self) -> str:
        return datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '_' + uuid.uuid4().hex[:8]

    @staticmethod
    def _csv_text(value):
        text = str(value or '')
        # CSV quoting alone does not stop spreadsheet formula execution.
        if text.lstrip().startswith(('=', '+', '-', '@')) or text.startswith(('\t', '\r', '\n')):
            return "'" + text
        return text

    def export_txt(self, items: List[ItemDTO]) -> str:
        path = os.path.join(self._export_dir, f"export_{self._ts()}.txt")
        with bounded_output(path, self._text_bound(items)) as f:
            for item in items:
                text = item.get_effective_text() or ''
                f.write(f"[{item.created_at}] [{item.source_mode}]\n{text}\n")
                f.write("-" * 40 + "\n")
        logger.info(f"已匯出 TXT: {path}")
        return path

    def export_csv(self, items: List[ItemDTO]) -> str:
        path = os.path.join(self._export_dir, f"export_{self._ts()}.csv")
        with bounded_output(path, self._text_bound(items), newline='', encoding='utf-8-sig') as f:
            w = csv.writer(f)
            w.writerow(['ID', '類型', '來源', '文字內容', '置信度', 'OCR狀態', '建立時間', '已釘選'])
            for item in items:
                w.writerow([
                    item.id, self._csv_text(item.item_type), self._csv_text(item.source_mode),
                    self._csv_text(item.get_effective_text()),
                    f"{item.ocr_confidence:.3f}" if item.ocr_confidence else '',
                    self._csv_text(item.ocr_status),
                    self._csv_text(item.created_at),
                    '是' if item.is_pinned else '否'
                ])
        logger.info(f"已匯出 CSV: {path}")
        return path

    def export_json(self, items: List[ItemDTO]) -> str:
        path = os.path.join(self._export_dir, f"export_{self._ts()}.json")
        data = [{
            'id': item.id,
            'type': item.item_type,
            'source': item.source_mode,
            'text': item.get_effective_text(),
            'ocr_status': item.ocr_status,
            'confidence': item.ocr_confidence,
            'pinned': item.is_pinned,
            'created_at': item.created_at,
        } for item in items]
        with bounded_output(path, self._text_bound(items)) as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"已匯出 JSON: {path}")
        return path

    def export_zip(self, items: List[ItemDTO]) -> str:
        file_mgr = FileManager(self._data_dir)
        path = os.path.join(self._export_dir, f"export_{self._ts()}.zip")
        files = []
        for item in items:
            for rel in (item.raw_image_path, item.annotation_path):
                if rel:
                    absolute = file_mgr.get_abs_path(rel)  # validate before creating archive
                    if os.path.isfile(absolute):
                        files.append((absolute, f'{item.id}/{os.path.relpath(absolute,self._data_dir)}'))
        maximum = self._text_bound(items) + sum(os.path.getsize(absolute) * 2 + 65536 for absolute, _ in files)
        with bounded_output(path, maximum, binary=True) as output:
            with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
                with archive.open('texts.txt', 'w') as stream:
                    for item in items:
                        stream.write(f"[{item.created_at}]\n{item.get_effective_text() or ''}\n\n".encode('utf-8'))
                for absolute, name in files:
                    archive.write(absolute, name)
        logger.info(f"已匯出 ZIP: {path}")
        return path
