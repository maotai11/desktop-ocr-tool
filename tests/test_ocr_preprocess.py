# -*- coding: utf-8 -*-
"""
Patch 9A — unit tests for src/ocr/preprocess.py and OcrEngine new logic.
No RapidOCR loaded; engine._do_ocr_array is mocked.
"""
import numpy as np
import pytest
from src.ocr.preprocess import enhance_for_ocr, upscale_if_small
from src.ocr.engine import OcrEngine


# ──────────────────────────────────────────
# preprocess.py
# ──────────────────────────────────────────

class TestUpscaleIfSmall:
    def _make(self, h, w):
        return np.zeros((h, w, 3), dtype=np.uint8)

    def test_no_resize_when_in_range(self):
        # 短邊 1080，在 [960, 1920] 區間內，不應縮放
        img = self._make(1080, 1920)
        out = upscale_if_small(img, min_short_side=960)
        assert out.shape == (1080, 1920, 3)

    def test_downscale_4k(self):
        # 短邊 2160 > 1920，應縮小到短邊 1920
        img = self._make(2160, 3840)
        out = upscale_if_small(img, min_short_side=960)
        assert min(out.shape[:2]) <= 1920

    def test_upscale_portrait(self):
        # short side = 100, target 960 → scale ×9.6
        img = self._make(100, 200)
        out = upscale_if_small(img, min_short_side=960)
        assert min(out.shape[:2]) >= 960

    def test_upscale_landscape(self):
        img = self._make(200, 100)  # short side = 100
        out = upscale_if_small(img, min_short_side=960)
        assert min(out.shape[:2]) >= 960

    def test_upscale_square(self):
        img = self._make(64, 64)
        out = upscale_if_small(img, min_short_side=960)
        assert out.shape[0] == 960
        assert out.shape[1] == 960

    def test_aspect_ratio_preserved(self):
        img = self._make(100, 200)  # 1:2 ratio
        out = upscale_if_small(img, min_short_side=960)
        ratio_before = 200 / 100
        ratio_after = out.shape[1] / out.shape[0]
        assert abs(ratio_after - ratio_before) < 0.02


class TestEnhanceForOcr:
    def _make_bgr(self, h=64, w=128):
        img = np.random.randint(0, 256, (h, w, 3), dtype=np.uint8)
        return img

    def test_output_shape_unchanged(self):
        img = self._make_bgr()
        out = enhance_for_ocr(img)
        assert out.shape == img.shape

    def test_output_is_3channel(self):
        img = self._make_bgr()
        out = enhance_for_ocr(img)
        assert out.ndim == 3
        assert out.shape[2] == 3

    def test_binarize_produces_only_0_and_255(self):
        img = self._make_bgr()
        out = enhance_for_ocr(img, binarize=True)
        unique_vals = set(out.flatten().tolist())
        assert unique_vals.issubset({0, 255})


# ──────────────────────────────────────────
# OcrEngine._should_retry
# ──────────────────────────────────────────

class TestShouldRetry:
    def _engine(self):
        e = OcrEngine(confidence_accept=0.85, confidence_review=0.60)
        return e

    def test_empty_results_triggers_retry(self):
        e = self._engine()
        assert e._should_retry([]) is True

    def test_none_results_triggers_retry(self):
        e = self._engine()
        assert e._should_retry(None) is True

    def test_low_confidence_triggers_retry(self):
        e = self._engine()
        # conf=0.50 < 0.60 threshold
        results = [([[0, 0], [10, 0], [10, 10], [0, 10]], "Hello", 0.50)]
        assert e._should_retry(results) is True

    def test_high_confidence_no_retry(self):
        e = self._engine()
        results = [([[0, 0], [10, 0], [10, 10], [0, 10]], "Hello", 0.80)]
        assert e._should_retry(results) is False


# ──────────────────────────────────────────
# OcrEngine._merge_results
# ──────────────────────────────────────────

class TestMergeResults:
    def test_merge_combines_lists(self):
        r1 = [("a",), ("b",)]
        r2 = [("c",)]
        merged = OcrEngine._merge_results(r1, r2)
        assert len(merged) == 3

    def test_merge_with_empty_first(self):
        r2 = [("x",)]
        merged = OcrEngine._merge_results([], r2)
        assert len(merged) == 1

    def test_merge_with_none(self):
        r1 = [("a",)]
        merged = OcrEngine._merge_results(r1, None)
        assert len(merged) == 1
