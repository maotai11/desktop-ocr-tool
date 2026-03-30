# -*- coding: utf-8 -*-
import logging
import os
import random
import string
from datetime import datetime
from PIL import Image

logger = logging.getLogger(__name__)


class FileManager:
    def __init__(self, data_dir: str, thumbnail_size: tuple = (80, 80)):
        self._data_dir = data_dir
        self._thumb_size = thumbnail_size
        for sub in ('captures', 'thumbnails', 'annotations', 'exports'):
            os.makedirs(os.path.join(data_dir, sub), exist_ok=True)

    def _dated_subdir(self, base: str) -> str:
        now = datetime.now()
        sub = os.path.join(base, now.strftime('%Y'), now.strftime('%m'))
        os.makedirs(sub, exist_ok=True)
        return sub

    def _make_filename(self, ext: str = 'png') -> str:
        now = datetime.now().strftime('%Y%m%d_%H%M%S')
        rand = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
        return f"{now}_{rand}.{ext}"

    def save_capture(self, image_data, ext: str = 'png') -> tuple:
        sub = self._dated_subdir(os.path.join(self._data_dir, 'captures'))
        filename = self._make_filename(ext)
        abs_path = os.path.join(sub, filename)

        if isinstance(image_data, Image.Image):
            image_data.save(abs_path)
        elif hasattr(image_data, 'shape'):  # numpy array
            import numpy as np
            Image.fromarray(image_data).save(abs_path)
        elif isinstance(image_data, bytes):
            with open(abs_path, 'wb') as f:
                f.write(image_data)
        else:
            raise ValueError(f"不支援的圖片格式: {type(image_data)}")

        rel_path = os.path.relpath(abs_path, self._data_dir)
        logger.debug(f"已儲存擷取圖片: {abs_path}")
        return abs_path, rel_path

    def create_thumbnail(self, image_path: str) -> tuple:
        sub = self._dated_subdir(os.path.join(self._data_dir, 'thumbnails'))
        base = os.path.basename(image_path)
        name, _ = os.path.splitext(base)
        thumb_filename = f"{name}_thumb.png"
        abs_path = os.path.join(sub, thumb_filename)
        try:
            img = Image.open(image_path)
            img.thumbnail(self._thumb_size, Image.LANCZOS)
            img.save(abs_path, 'PNG')
            rel_path = os.path.relpath(abs_path, self._data_dir)
            return abs_path, rel_path
        except Exception as e:
            logger.error(f"建立縮圖失敗: {e}")
            return '', ''

    def delete_item_files(self, item) -> list:
        failed = []
        for rel_path in [item.raw_image_path, item.thumbnail_path, item.annotation_path]:
            if rel_path:
                abs_path = self.get_abs_path(rel_path)
                if os.path.exists(abs_path):
                    try:
                        os.remove(abs_path)
                    except Exception as e:
                        logger.warning(f"刪除檔案失敗 {abs_path}: {e}")
                        failed.append(abs_path)
        return failed

    def get_abs_path(self, rel_path: str) -> str:
        if not rel_path:
            return ''
        if os.path.isabs(rel_path):
            return rel_path
        return os.path.join(self._data_dir, rel_path)

    def get_export_dir(self) -> str:
        d = os.path.join(self._data_dir, 'exports')
        os.makedirs(d, exist_ok=True)
        return d
