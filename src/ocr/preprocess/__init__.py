# -*- coding: utf-8 -*-
"""
OCR 影像前處理套件

對外暴露兩個高階函式供 engine.py 使用：
  - enhance_for_ocr(image, binarize) — CLAHE + denoise + optional Otsu binarize
  - upscale_if_small(image, min_short_side) — bicubic upscale when short side too small
"""
import cv2
import numpy as np
from .contrast import enhance_contrast
from .denoise import denoise_image
from .resize import resize_image


def enhance_for_ocr(image: np.ndarray, binarize: bool = False) -> np.ndarray:
    """
    對 BGR 影像套用對比增強前處理，回傳 3-channel BGR。

    步驟：
        1. CLAHE 對比度均衡化（已包含灰階轉換）
        2. fastNlMeans 去雜訊
        3. 若 binarize=True：Otsu 二值化（手寫模式）
        4. 確保回傳格式為 3-channel BGR
    """
    img = enhance_contrast(image)  # → 3-channel BGR
    img = denoise_image(img)       # → 3-channel BGR

    if binarize:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        img = cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR)

    return img


def upscale_if_small(image: np.ndarray, min_short_side: int = 960) -> np.ndarray:
    """
    雙向 resize：
    - 短邊 < min_short_side → bicubic 放大（提升小截圖辨識率）
    - 短邊 > 1920 → INTER_AREA 縮小（避免 4K 截圖推論過慢）
    - 其餘尺寸直接回傳原圖
    """
    _MAX_SHORT_SIDE = 1920
    return resize_image(image, max_short_side=_MAX_SHORT_SIDE, min_short_side=min_short_side)
