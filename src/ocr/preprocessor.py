# -*- coding: utf-8 -*-
import logging
import numpy as np
from .preprocess.resize import resize_image
from .preprocess.contrast import enhance_contrast
from .preprocess.deskew import deskew_image
from .preprocess.denoise import denoise_image

logger = logging.getLogger(__name__)


def preprocess_screen(img: np.ndarray, max_short_side: int = 960) -> np.ndarray:
    return resize_image(img, max_short_side)


def preprocess_document(img: np.ndarray, cfg: dict, max_short_side: int = 960) -> np.ndarray:
    if cfg.get('enable_contrast_enhance', True):
        img = enhance_contrast(img)
    if cfg.get('enable_deskew', True):
        img = deskew_image(img)
    if cfg.get('enable_denoise', True):
        img = denoise_image(img)
    return resize_image(img, max_short_side)


def select_pipeline(mode: str, cfg: dict, img: np.ndarray,
                    max_short_side: int = 960) -> np.ndarray:
    if mode == 'screen':
        return preprocess_screen(img, max_short_side)
    return preprocess_document(img, cfg, max_short_side)
