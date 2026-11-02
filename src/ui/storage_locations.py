"""Read-only locations for files produced by this running app."""
from dataclasses import dataclass
import os
from pathlib import Path
import sys
import tempfile

from ..core.config import get_project_root


@dataclass(frozen=True)
class StorageLocation:
    key: str
    label: str
    path: Path | None
    detail: str = ''

    @property
    def available(self) -> bool:
        return self.path is not None and self.path.is_dir()

    @property
    def tooltip(self) -> str:
        return f'{self.detail}\n{self.path}' if self.path else self.detail


def _path(value, fallback) -> Path:
    if not isinstance(value, (str, os.PathLike)):
        value = fallback
    return Path(value).absolute()


def _active_lock_directory() -> Path | None:
    """Expose only the POSIX lock actually held by this process; never create one."""
    if sys.platform == 'win32' or not hasattr(os, 'getuid'):
        return None
    from ..core import single_instance
    descriptor = single_instance._posix_lock
    if descriptor is None:
        return None
    candidate = Path(tempfile.gettempdir()) / f'desktop-ocr-{os.getuid()}' / 'instance.lock'
    try:
        held = os.fstat(descriptor)
        existing = candidate.stat()
    except OSError:
        return None
    if (held.st_dev, held.st_ino) != (existing.st_dev, existing.st_ino):
        return None
    return candidate.parent


def get_storage_locations(data_dir, cfg) -> list[StorageLocation]:
    """Use active data/config paths, the logger's root, and live bundle extraction.

    Opening this list creates no folders. The shared OS temp directory is never
    presented as the application's own cache. Source probes use DESKTOP_OCR_HOME,
    already handled by the same root resolver as config and logger.
    """
    runtime_root = Path(get_project_root()).absolute()
    config_root = _path(getattr(cfg, '_root', None), runtime_root)
    config_dir = _path(getattr(cfg, '_config_dir', None), config_root / 'config')
    data = _path(data_dir, config_root / 'data')
    locations = [
        StorageLocation('captures', '截圖', data / 'captures', '擷取的原始圖片'),
        StorageLocation('annotations', '標註附件', data / 'annotations', '圖片編輯與標註附件'),
        StorageLocation('thumbnails', '縮圖', data / 'thumbnails', '歷史紀錄的圖片預覽'),
        StorageLocation('exports', '匯出檔案', data / 'exports', '從歷史紀錄匯出的檔案'),
        StorageLocation('history', '歷史資料', data, '本機歷史、資料庫與資料庫暫存檔'),
        StorageLocation('config', '設定檔案', config_dir, '設定、備份與寫入中的暫存檔'),
        StorageLocation('logs', '執行記錄 (Log)', runtime_root / 'logs', '本次執行與保留的過往記錄'),
    ]
    bundle = getattr(sys, '_MEIPASS', None)
    if getattr(sys, 'frozen', False) and isinstance(bundle, (str, os.PathLike)):
        locations.append(StorageLocation(
            'runtime_temp', '程式暫存', Path(bundle).absolute(),
            '這次執行解開的程式資源；正常結束後由系統清理',
        ))
    else:
        locations.append(StorageLocation(
            'runtime_temp', '程式暫存（目前無）', None,
            '目前沒有獨立的程式解壓暫存；資料與附件請看上方位置',
        ))
    lock_dir = _active_lock_directory()
    if lock_dir is not None:
        locations.append(StorageLocation(
            'instance_temp', '執行鎖定暫存', lock_dir, '這個程式使用的單一執行鎖定檔',
        ))
    locations.append(StorageLocation(
        'program', '程式資料夾', runtime_root, '程式使用的主資料夾',
    ))
    return locations
