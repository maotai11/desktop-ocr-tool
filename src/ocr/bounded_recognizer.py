"""Reject pathological detector crops before native resize/logit allocation."""
import math

import numpy as np


class BoundedRecognizer:
    MAX_WIDTH = 4096
    MAX_CROPS = 512
    MAX_TOTAL_WIDTH = 65536

    def __init__(self, recognizer):
        self.recognizer = recognizer
        # Prevent one long crop from padding five unrelated short crops.
        self.recognizer.rec_batch_num = 1
        self.reset_budget()

    def reset_budget(self):
        self.total_width = 0
        self.total_crops = 0

    def __call__(self, images, *args, **kwargs):
        crops = [images] if isinstance(images, np.ndarray) else images
        if not isinstance(crops, (list, tuple)) or len(crops) > self.MAX_CROPS:
            raise ValueError('文字區塊過多，請縮小框選範圍後重試')
        total = 0
        # Validate the entire batch before the first native call: no partial success.
        for crop in crops:
            if (not isinstance(crop, np.ndarray) or crop.dtype != np.uint8
                    or crop.ndim != 3 or crop.shape[2] != 3
                    or crop.shape[0] < 1 or crop.shape[1] < 1):
                raise ValueError('無效的文字區塊，原圖保留供重試')
            width = max(320, math.ceil(48 * crop.shape[1] / crop.shape[0]))
            if width > self.MAX_WIDTH:
                raise ValueError('文字行過長或過細，請分段框選；原圖保留供重試')
            total += width
        if total + self.total_width > self.MAX_TOTAL_WIDTH or len(crops) + self.total_crops > self.MAX_CROPS:
            raise ValueError('文字辨識工作量超限，請分段框選；原圖保留供重試')
        self.total_width += total
        self.total_crops += len(crops)
        return self.recognizer(crops, *args, **kwargs)
