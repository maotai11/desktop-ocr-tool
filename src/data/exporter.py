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

logger = logging.getLogger(__name__)


class Exporter:
    def __init__(self, export_dir: str, data_dir: str):
        self._export_dir = export_dir
        self._data_dir = data_dir

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
        with open(path, 'w', encoding='utf-8') as f:
            for item in items:
                text = item.get_effective_text() or ''
                f.write(f"[{item.created_at}] [{item.source_mode}]\n{text}\n")
                f.write("-" * 40 + "\n")
        logger.info(f"已匯出 TXT: {path}")
        return path

    def export_csv(self, items: List[ItemDTO]) -> str:
        path = os.path.join(self._export_dir, f"export_{self._ts()}.csv")
        with open(path, 'w', newline='', encoding='utf-8-sig') as f:
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
        with open(path, 'w', encoding='utf-8') as f:
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
        with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as zf:
            txt_content = ""
            for item in items:
                txt_content += f"[{item.created_at}]\n{item.get_effective_text() or ''}\n\n"
            zf.writestr("texts.txt", txt_content.encode('utf-8'))
            for absolute, name in files:
                zf.write(absolute, name)
        logger.info(f"已匯出 ZIP: {path}")
        return path
