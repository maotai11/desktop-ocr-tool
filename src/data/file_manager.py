# -*- coding: utf-8 -*-
import logging
import os
import random
import string
from datetime import datetime
from pathlib import Path, PureWindowsPath
from PIL import Image
from ..core.resource_budget import (BoundedWriter, disk_quota_guard, register_managed_root,
                                    raster_preflight, disk_write_reservation, create_managed_directory)

logger = logging.getLogger(__name__)


class FileManager:
    def __init__(self, data_dir: str, thumbnail_size: tuple = (80, 80)):
        self._data_dir = str(Path(data_dir).resolve())
        self._thumb_size = thumbnail_size
        register_managed_root(self._data_dir, owner=self)
        # An existing over-budget profile remains inspectable; no eager writes.
        if not Path(self._data_dir).parent.is_dir():
            raise FileNotFoundError('請先選擇已存在的資料上層資料夾')


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
        if not (isinstance(image_data, (Image.Image, bytes)) or hasattr(image_data, 'shape')):
            raise ValueError(f"不支援的圖片格式: {type(image_data)}")
        image_format = Image.registered_extensions().get('.' + ext.lower())
        if image_format is None:
            raise ValueError(f'不支援的圖片副檔名: {ext}')
        if isinstance(image_data, bytes):
            maximum = len(image_data)
        else:
            width, height = image_data.size if isinstance(image_data, Image.Image) else image_data.shape[1::-1]
            raster_preflight(int(width), int(height))
            maximum = int(width) * int(height) * 8 + 65536
        with disk_quota_guard(self, reserve_bytes=disk_write_reservation(self._data_dir, maximum, new_directories=4)):
            sub = self._dated_subdir(os.path.join(self._data_dir, 'captures'))
            for _ in range(100):
                abs_path = os.path.join(sub, self._make_filename(ext))
                try:
                    handle = open(abs_path, 'xb')
                except FileExistsError:
                    continue
                try:
                    with handle:
                        writer = BoundedWriter(handle, maximum)
                        if isinstance(image_data, bytes):
                            writer.write(image_data)
                        else:
                            image = image_data if isinstance(image_data, Image.Image) else Image.fromarray(image_data)
                            image.save(writer, format=image_format)
                        handle.flush()
                        os.fsync(handle.fileno())
                except Exception:
                    Path(abs_path).unlink(missing_ok=True)
                    raise
                break
            else:
                raise FileExistsError('無法建立唯一截圖檔名，請重試')
        return abs_path, os.path.relpath(abs_path, self._data_dir)

    def create_thumbnail(self, image_path: str) -> tuple:
        temporary = None
        try:
            maximum = int(self._thumb_size[0]) * int(self._thumb_size[1]) * 8 + 65536
            with disk_quota_guard(self, reserve_bytes=disk_write_reservation(self._data_dir, maximum, new_directories=4)):
                sub = self._dated_subdir(os.path.join(self._data_dir, 'thumbnails'))
                name = Path(image_path).stem
                import tempfile
                descriptor, temporary = tempfile.mkstemp(prefix=f'{name}_', suffix='_thumb.png', dir=sub)
                destination = Path(temporary)
                with os.fdopen(descriptor, 'wb') as stream, Image.open(image_path) as image:
                    raster_preflight(*image.size)
                    image.thumbnail(self._thumb_size, Image.LANCZOS)
                    image.save(BoundedWriter(stream, maximum), 'PNG')
                    stream.flush()
                    os.fsync(stream.fileno())
                temporary = None
                return str(destination), os.path.relpath(destination, self._data_dir)
        except Exception:
            logger.warning('無法建立縮圖，原始圖片未變更')
            return '', ''
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)

    def delete_item_files(self, item) -> list:
        failed = []
        for rel_path in [getattr(item, name, None) for name in ('raw_image_path', 'thumbnail_path', 'annotation_path')]:
            if rel_path:
                try:
                    abs_path = self.get_abs_path(rel_path)
                    if os.path.exists(abs_path):
                        os.remove(abs_path)
                except (OSError, ValueError) as e:
                    logger.warning('刪除檔案失敗: %s', e)
                    failed.append(rel_path)
        return failed

    def get_abs_path(self, rel_path: str) -> str:
        if not rel_path:
            return ''
        if Path(rel_path).is_absolute() or PureWindowsPath(rel_path).drive:
            raise ValueError('Stored image path must be relative')
        root = Path(self._data_dir).resolve()
        target = (root / rel_path.replace('\\', '/')).resolve()
        if target == root or not target.is_relative_to(root):
            raise ValueError('Image path escapes the data directory')
        return str(target)

    def get_export_dir(self) -> str:
        d = os.path.join(self._data_dir, 'exports')
        create_managed_directory(self._data_dir, owner=self)
        create_managed_directory(d, owner=self)
        return d
