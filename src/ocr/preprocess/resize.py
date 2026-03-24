# -*- coding: utf-8 -*-
import cv2
import numpy as np


def resize_image(img: np.ndarray, max_short_side: int = 960,
                 min_short_side: int = 128) -> np.ndarray:
    """
    雙向 resize：
    - 短邊 < min_short_side → 放大（INTER_CUBIC），避免小文字偵測失敗
    - 短邊 > max_short_side → 縮小（INTER_AREA），加快推論
    """
    h, w = img.shape[:2]
    short = min(h, w)
    if short < min_short_side:
        scale = min_short_side / short
        new_w = int(w * scale)
        new_h = int(h * scale)
        return cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_CUBIC)
    if short > max_short_side:
        scale = max_short_side / short
        new_w = int(w * scale)
        new_h = int(h * scale)
        return cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)
    return img
