# -*- coding: utf-8 -*-
import hashlib
import logging
from typing import Optional
import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)


def sha256_text(text: str) -> Optional[str]:
    if not text:
        return None
    cleaned = text.strip()
    if not cleaned:
        return None
    return hashlib.sha256(cleaned.encode('utf-8')).hexdigest()


def phash_image(image_path: str) -> Optional[str]:
    try:
        img = Image.open(image_path).convert('L')
        img = img.resize((32, 32), Image.LANCZOS)
        arr = np.array(img, dtype=np.float32)
        try:
            from scipy.fft import dct
            dct_arr = dct(dct(arr, axis=0, norm='ortho'), axis=1, norm='ortho')
            dct_low = dct_arr[:8, :8]
            median = float(np.median(dct_low))
            bits = (dct_low > median).flatten()
        except ImportError:
            return _simple_image_hash_from_arr(arr)

        hex_str = ''
        for i in range(0, 64, 4):
            nibble = (int(bits[i]) << 3 | int(bits[i + 1]) << 2 |
                      int(bits[i + 2]) << 1 | int(bits[i + 3]))
            hex_str += format(nibble, 'x')
        return hex_str
    except Exception as e:
        logger.warning(f"pHash 計算失敗 {image_path}: {e}")
        try:
            return _simple_image_hash(image_path)
        except Exception:
            return None


def _simple_image_hash_from_arr(arr: np.ndarray) -> str:
    small = Image.fromarray(arr.astype(np.uint8)).resize((8, 8), Image.LANCZOS)
    a = np.array(small)
    avg = float(a.mean())
    bits = (a > avg).flatten()
    hex_str = ''
    for i in range(0, 64, 4):
        nibble = (int(bits[i]) << 3 | int(bits[i + 1]) << 2 |
                  int(bits[i + 2]) << 1 | int(bits[i + 3]))
        hex_str += format(nibble, 'x')
    return hex_str


def _simple_image_hash(image_path: str) -> str:
    img = Image.open(image_path).convert('L').resize((8, 8), Image.LANCZOS)
    arr = np.array(img)
    avg = float(arr.mean())
    bits = (arr > avg).flatten()
    hex_str = ''
    for i in range(0, 64, 4):
        nibble = (int(bits[i]) << 3 | int(bits[i + 1]) << 2 |
                  int(bits[i + 2]) << 1 | int(bits[i + 3]))
        hex_str += format(nibble, 'x')
    return hex_str
