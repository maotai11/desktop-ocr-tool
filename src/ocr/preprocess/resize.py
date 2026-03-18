# -*- coding: utf-8 -*-
import cv2
import numpy as np


def resize_image(img: np.ndarray, max_short_side: int = 960) -> np.ndarray:
    h, w = img.shape[:2]
    short = min(h, w)
    if short <= max_short_side:
        return img
    scale = max_short_side / short
    new_w = int(w * scale)
    new_h = int(h * scale)
    return cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)
