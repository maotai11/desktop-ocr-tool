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


# Per-image work budgets. They bound input/resize work, not total native ORT RSS.
MAX_SOURCE_PIXELS = 32_000_000
MAX_ENCODED_BYTES = 32_000_000
MAX_TILES = 64
MAX_TILE_PIXELS = 1536 * 1536
MAX_TOTAL_TILE_PIXELS = 80_000_000
TILE_SIDE = 1536
DETECTOR_MIN_SIDE = 736


def validate_image_shape(shape):
    if len(shape) < 2 or min(shape[:2]) <= 0:
        raise ValueError('Empty image')
    if int(shape[0]) * int(shape[1]) > MAX_SOURCE_PIXELS:
        raise ValueError('原圖超過 3200 萬像素，請縮小範圍；原圖保留供重試')


def plan_ocr_tiles(shape, min_short_side=960):
    """Plan before allocating. Ordinary images retain the established resize path.

    Tiling avoids the vendor's global 2000px reduction on large/long inputs.
    It uses overlap for seam reconciliation, at most 3x scaling, and letterboxes
    each short axis to 736px to prevent the detector's hidden min-side upscale.
    Only descriptors are stored; the engine materializes one tile at a time.
    """
    validate_image_shape(shape)
    if not 32 <= min_short_side <= 1920:
        raise ValueError('max_image_short_side must be between 32 and 1920')
    h, w = map(int, shape[:2])
    short = min(h, w)
    old_scale = min_short_side / short if short < min_short_side else (
        1920 / short if short > 1920 else 1.0)
    if (max(h, w) <= 2000 and h * w * old_scale ** 2 <= 16_000_000
            and max(h, w) / short <= 8):
        return []

    scale = min(3.0, max(2.0, min_short_side / short))
    side = int(TILE_SIDE / scale)
    overlap = max(32, side // 4)

    def starts(length):
        positions = [0]
        while positions[-1] + side < length:
            positions.append(min(positions[-1] + side - overlap, length - side))
            if len(positions) > MAX_TILES:
                raise ValueError('OCR 切片數超過上限，請縮小範圍；原圖保留供重試')
        return positions

    xs, ys = starts(w), starts(h)
    if len(xs) * len(ys) > MAX_TILES:
        raise ValueError('OCR 切片數超過上限，請縮小範圍；原圖保留供重試')
    tiles, pixels = [], 0
    for y in ys:
        for x in xs:
            tw, th = min(side, w - x), min(side, h - y)
            rw, rh = max(1, round(tw * scale)), max(1, round(th * scale))
            pw, ph = max(DETECTOR_MIN_SIDE, rw), max(DETECTOR_MIN_SIDE, rh)
            pixels += pw * ph
            if pw * ph > MAX_TILE_PIXELS or pixels > MAX_TOTAL_TILE_PIXELS:
                raise ValueError('OCR 切片工作量超過上限，請縮小範圍；原圖保留供重試')
            tiles.append(dict(source_xywh=[x, y, tw, th], resized_hw=[rh, rw],
                              inference_hw=[ph, pw], scale_xy=[rw / tw, rh / th],
                              padding_xy=[(pw - rw) // 2, (ph - rh) // 2]))
    return tiles


def prepare_ocr_tile(image, tile):
    """Materialize one bounded BGR tile with edge-replicated padding."""
    import cv2
    x, y, w, h = tile['source_xywh']
    rh, rw = tile['resized_hw']
    ph, pw = tile['inference_hw']
    px, py = tile['padding_xy']
    crop = cv2.resize(image[y:y + h, x:x + w], (rw, rh), interpolation=cv2.INTER_CUBIC)
    return cv2.copyMakeBorder(crop, py, ph - rh - py, px, pw - rw - px,
                              cv2.BORDER_REPLICATE)


def restore_tile_results(results, tile):
    """Map quadrilaterals from padded inference pixels back to source pixels."""
    x, y, w, h = tile['source_xywh']
    sx, sy = tile['scale_xy']
    px, py = tile['padding_xy']
    mapped = []
    for item in results:
        box = np.asarray(item[0], dtype=float)
        if box.shape != (4, 2) or not np.isfinite(box).all():
            raise ValueError('Invalid OCR quadrilateral')
        # Replicated border should not create text wholly outside the crop.
        cx, cy = box.mean(axis=0)
        if not px <= cx <= px + w * sx or not py <= cy <= py + h * sy:
            continue
        box[:, 0] = np.clip((box[:, 0] - px) / sx, 0, w) + x
        box[:, 1] = np.clip((box[:, 1] - py) / sy, 0, h) + y
        mapped.append([box.tolist(), item[1], item[2]])
    return mapped
