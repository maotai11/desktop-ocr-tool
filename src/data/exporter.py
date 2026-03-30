# -*- coding: utf-8 -*-
import csv
import json
import logging
import os
import zipfile
from datetime import datetime
from typing import List
from .models import ItemDTO

logger = logging.getLogger(__name__)


class Exporter:
    def __init__(self, export_dir: str, data_dir: str):
        self._export_dir = export_dir
        self._data_dir = data_dir

    def _ts(self) -> str:
        return datetime.now().strftime('%Y%m%d_%H%M%S')

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
                    item.id, item.item_type, item.source_mode,
                    item.get_effective_text() or '',
                    f"{item.ocr_confidence:.3f}" if item.ocr_confidence else '',
                    item.ocr_status,
                    item.created_at or '',
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
        path = os.path.join(self._export_dir, f"export_{self._ts()}.zip")
        with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as zf:
            txt_content = ""
            for item in items:
                txt_content += f"[{item.created_at}]\n{item.get_effective_text() or ''}\n\n"
            zf.writestr("texts.txt", txt_content.encode('utf-8'))
            for item in items:
                for rel in [item.raw_image_path, item.annotation_path]:
                    if rel:
                        abs_p = os.path.join(self._data_dir, rel) if not os.path.isabs(rel) else rel
                        if os.path.exists(abs_p):
                            zf.write(abs_p, os.path.basename(abs_p))
        logger.info(f"已匯出 ZIP: {path}")
        return path
