# -*- coding: utf-8 -*-
"""
Patch H2 — 第二引擎 provider 工廠

用法：
    from src.ocr.providers import create_provider
    provider = create_provider('paddleocr_v5_mobile')
    ocr_engine.set_secondary_engine(provider)
    ocr_engine.configure(enable_secondary_engine=True)

設計原則：
- create_provider() 使用 lazy import，只有實際呼叫時才 import 具體 adapter
- 未知 name 或依賴未安裝時，一律回傳 NullSecondaryEngine（不拋例外）
- 新增 provider：在 providers/ 新增一個檔案 + 在 _PROVIDER_MAP 加一行
"""
from __future__ import annotations

from ..secondary_engine import SecondaryEngineBase, NullSecondaryEngine

# 已知 provider 名稱 → (模組路徑, 類別名稱)
# 使用字串延遲引用，避免模組頂層 import 副作用
_PROVIDER_MAP: dict[str, tuple[str, str]] = {
    'paddleocr_v5': (
        '.paddleocr_v5_provider',
        'PaddleOCRv5Provider',
    ),
    'cnocr_traditional_chinese': (
        '.cnocr_provider',
        'CnOcrProvider',
    ),
}


def create_provider(name: str, **kwargs) -> SecondaryEngineBase:
    """依名稱建立 provider 實例。

    Parameters
    ----------
    name : str
        Provider 識別名稱，目前支援：'paddleocr_v5_mobile'
    **kwargs
        傳遞給具體 provider 建構子的額外參數

    Returns
    -------
    SecondaryEngineBase
        若名稱已知，回傳對應 provider 實例；
        若名稱未知或 import 失敗，回傳 NullSecondaryEngine。
    """
    entry = _PROVIDER_MAP.get(name)
    if entry is None:
        return NullSecondaryEngine()

    module_rel, class_name = entry
    try:
        import importlib
        module = importlib.import_module(module_rel, package=__package__)
        cls = getattr(module, class_name)
        return cls(**kwargs)
    except Exception:
        # import 失敗（e.g., paddleocr 未安裝，但不應到這裡因為 adapter 已有防護）
        return NullSecondaryEngine()


def list_known_providers() -> tuple[str, ...]:
    """回傳所有已登記的 provider 名稱（不代表全部都已安裝）。"""
    return tuple(_PROVIDER_MAP.keys())
