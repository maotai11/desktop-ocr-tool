# -*- coding: utf-8 -*-
import json
import os
import shutil
import sys
from typing import Any
import logging

logger = logging.getLogger(__name__)

# These were written into old profiles but never consumed by the core app.
# They are not advertised in fresh settings. ConfigManager deliberately keeps
# unknown loaded keys when saving, so retiring a setting never erases a user's
# old profile or enables an experimental engine as a side effect.
RETIRED_CORE_SETTINGS = {
    'general.language': 'The bundled interface has no locale-switching implementation.',
    'capture.backend': 'Core capture uses the single verified MSS backend.',
    'capture.screenshot_format': 'Capture storage is PNG; export has its own explicit format.',
    'capture.jpg_quality': 'The PNG capture path has no JPEG quality control.',
    'capture.include_cursor': 'The core capture API has no cursor-inclusion setting.',
    'capture.capture_sound': 'No capture-sound player is connected.',
    'ocr.engine': 'Core uses the verified RapidOCR/ONNX profile.',
    'ocr.model_det': 'Model paths are fixed by the verified model-profile lock.',
    'ocr.model_rec': 'Model paths are fixed by the verified model-profile lock.',
    'ocr.model_cls': 'Model paths are fixed by the verified model-profile lock.',
    'ocr.language': 'Recognition language is part of the verified model profile.',
    'ocr.primary_engine': 'Experimental provider selection is not connected to core startup.',
    'ocr.secondary_engine': 'Experimental provider selection is not connected to core startup.',
    'ocr.auto_switch_secondary': 'Experimental provider routing is not connected to core startup.',
    'ocr.auto_switch_threshold': 'Experimental provider routing is not connected to core startup.',
    'ocr.enable_secondary_engine': 'Optional engines are not bundled or enabled by core settings.',
    'ocr.secondary_engine_provider': 'Optional engines are not bundled or enabled by core settings.',
    'ocr.secondary_engine_for_handwriting': 'Optional-engine routing remains an extension API only.',
    'ocr.secondary_engine_for_low_confidence': 'Optional-engine routing remains an extension API only.',
    'ocr.secondary_engine_confidence_threshold': 'Optional-engine routing remains an extension API only.',
    'preprocessing.enable_deskew': 'The current engine does not consume this legacy preprocessing map.',
    'preprocessing.enable_denoise': 'The current engine uses its explicit bounded retry policy.',
    'preprocessing.enable_contrast_enhance': 'The current engine uses its explicit bounded retry policy.',
    'preprocessing.enable_shadow_removal': 'No shadow-removal implementation is connected.',
    'preprocessing.binarize_method': 'The current engine uses its explicit bounded retry policy.',
    'history.max_items': 'No automatic count-based removal is implemented.',
    'history.auto_delete_archived_days': 'Automatic permanent deletion is not implemented.',
}

DEFAULT_SETTINGS = {
    "general": {
        "start_with_windows": False,
        "start_minimized": True,
        "single_instance": True,
        "data_directory": "./data"
    },
    "capture": {
        "auto_ocr_on_capture": True,
        "save_raw_image": True
    },
    "ocr": {
        "model_profile": "v6-small",
        "confidence_accept": 0.85,
        "confidence_review": 0.60,
        "enable_second_pass": True,
        "max_ocr_passes": 2,
        "enable_handwriting_mode": False,
        "max_image_short_side": 960
    },
    "clipboard": {
        "monitor_clipboard": False,
        "auto_save_text": True,
        "auto_save_image": False,
        "auto_ocr_on_clipboard_image": False,
        "ignore_self": True,
        "max_text_length": 50000,
        "deduplicate": True,
        "ocr_copy_mode": "all",
        "copy_remove_commas": False,
        "copy_remove_apostrophes": False,
        "copy_remove_newlines": False,
        "copy_remove_punctuation": False
    },
    "documents": {
        "max_file_size_mb": 100
    },
    "history": {
        "auto_archive_days": 90
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
    if os.environ.get("DESKTOP_OCR_HOME"):
        return os.path.abspath(os.environ["DESKTOP_OCR_HOME"])
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
            # The previous UI allowed 2048 although the bounded pipeline stops at 1920.
            # Keep the user's other settings and data intact.
            short_side = self._data['ocr'].get('max_image_short_side')
            if isinstance(short_side, (int, float)) and 1920 < short_side <= 2048:
                self._data['ocr']['max_image_short_side'] = 1920
                self.save()
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
        """Legacy extension helper; core loading uses the verified profile lock."""
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
