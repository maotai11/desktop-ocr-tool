# -*- coding: utf-8 -*-
import json
import os
import shutil
import sys
from typing import Any
import logging

logger = logging.getLogger(__name__)

DEFAULT_SETTINGS = {
    "general": {
        "language": "zh-TW",
        "start_with_windows": False,
        "start_minimized": True,
        "single_instance": True,
        "data_directory": "./data"
    },
    "capture": {
        "backend": "mss",
        "screenshot_format": "png",
        "jpg_quality": 95,
        "include_cursor": False,
        "auto_ocr_on_capture": True,
        "capture_sound": False,
        "save_raw_image": True
    },
    "ocr": {
        "engine": "onnxruntime",
        "model_det": "models/det/pp-ocrv4_det.onnx",
        "model_rec": "models/rec/pp-ocrv4_rec.onnx",
        "model_cls": "models/cls/pp-ocrv4_cls.onnx",
        "language": "chinese_cht",
        "confidence_accept": 0.85,
        "confidence_review": 0.60,
        "enable_second_pass": False,
        "enable_handwriting_mode": False,
        "max_image_short_side": 960
    },
    "preprocessing": {
        "enable_deskew": True,
        "enable_denoise": True,
        "enable_contrast_enhance": True,
        "enable_shadow_removal": False,
        "binarize_method": "sauvola"
    },
    "clipboard": {
        "monitor_clipboard": True,
        "auto_save_text": True,
        "auto_save_image": False,
        "auto_ocr_on_clipboard_image": False,
        "ignore_self": True,
        "max_text_length": 50000,
        "deduplicate": True
    },
    "history": {
        "max_items": 10000,
        "auto_archive_days": 90,
        "auto_delete_archived_days": 0
    },
    "hotkeys": {
        "capture_region_ocr": "Ctrl+Shift+O",
        "capture_region_image": "Ctrl+Shift+S",
        "capture_fullscreen": "Ctrl+Shift+F",
        "toggle_widget": "Ctrl+Shift+Space",
        "paste_last": "Ctrl+Shift+V",
        "open_console": "Ctrl+Shift+M",
        "quick_search": "Ctrl+Shift+Q"
    },
    "ui": {
        "theme": "system",
        "widget_opacity": 0.95,
        "widget_position": "remember",
        "widget_max_items": 20,
        "thumbnail_size": [80, 80],
        "font_size": 13,
        "widget_click_action": "select",
        "font_family_priority": ["Microsoft JhengHei UI", "Microsoft JhengHei", ""]
    }
}


def get_project_root() -> str:
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))


class ConfigManager:
    def __init__(self):
        self._root = get_project_root()
        self._config_dir = os.path.join(self._root, 'config')
        self._settings_path = os.path.join(self._config_dir, 'settings.json')
        self._data: dict = {}
        self.load()

    def load(self):
        os.makedirs(self._config_dir, exist_ok=True)
        if not os.path.exists(self._settings_path):
            self._data = self._deep_copy(DEFAULT_SETTINGS)
            self.save()
            logger.info("建立預設設定檔")
            return
        try:
            with open(self._settings_path, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
            self._data = self._merge(DEFAULT_SETTINGS, loaded)
            logger.info("設定檔載入成功")
        except Exception as e:
            backup = self._settings_path + '.bak'
            shutil.copy2(self._settings_path, backup)
            logger.warning(f"設定檔格式錯誤，已備份至 {backup}，使用預設值: {e}")
            self._data = self._deep_copy(DEFAULT_SETTINGS)
            self.save()

    def save(self):
        os.makedirs(self._config_dir, exist_ok=True)
        tmp_path = self._settings_path + '.tmp'
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, self._settings_path)

    def get(self, *keys, default=None) -> Any:
        d = self._data
        for k in keys:
            if isinstance(d, dict) and k in d:
                d = d[k]
            else:
                return default
        return d

    def set(self, *keys_and_value):
        keys = keys_and_value[:-1]
        value = keys_and_value[-1]
        d = self._data
        for k in keys[:-1]:
            d = d.setdefault(k, {})
        d[keys[-1]] = value
        self.save()

    def get_data_directory(self) -> str:
        rel = self.get('general', 'data_directory', default='./data')
        if os.path.isabs(rel):
            return rel
        return os.path.abspath(os.path.join(self._root, rel))

    def get_model_path(self, key: str) -> str:
        rel = self.get('ocr', key, default='')
        if not rel:
            return ''
        if os.path.isabs(rel):
            return rel
        # Frozen EXE: 模型在 sys._MEIPASS（PyInstaller 解壓暫存目錄）
        if getattr(sys, 'frozen', False):
            return os.path.abspath(os.path.join(sys._MEIPASS, rel))
        return os.path.abspath(os.path.join(self._root, rel))

    def _deep_copy(self, d: dict) -> dict:
        return json.loads(json.dumps(d))

    def _merge(self, default: dict, loaded: dict) -> dict:
        result = self._deep_copy(default)
        for k, v in loaded.items():
            if k in result and isinstance(result[k], dict) and isinstance(v, dict):
                result[k] = self._merge(result[k], v)
            else:
                result[k] = v
        return result


_config: "ConfigManager | None" = None


def get_config() -> ConfigManager:
    global _config
    if _config is None:
        _config = ConfigManager()
    return _config
