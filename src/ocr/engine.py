# -*- coding: utf-8 -*-
import logging
import time
import cv2
import numpy as np
from .postprocessor import sort_boxes_and_merge
from .preprocess import enhance_for_ocr, upscale_if_small
from .secondary_engine import SecondaryEngineBase, NullSecondaryEngine

logger = logging.getLogger(__name__)

try:
    import zhconv as _zhconv
    def _s2t(text: str) -> str:
        return _zhconv.convert(text, 'zh-hant')
except ImportError:
    logger.warning("zhconv 未安裝，簡→繁轉換停用")
    def _s2t(text: str) -> str:
        return text


class OcrEngine:
    """OCR 引擎，使用 RapidOCR PP-OCRv4（ONNX Runtime，離線輕量）。"""

    # 預設值；Patch 9D 會改由 config 傳入
    _DEFAULT_MAX_SHORT_SIDE = 960
    _DEFAULT_SECOND_PASS = True
    _DEFAULT_HANDWRITING = False

    def __init__(self, confidence_accept: float = 0.85,
                 confidence_review: float = 0.60,
                 max_image_short_side: int = _DEFAULT_MAX_SHORT_SIDE,
                 enable_second_pass: bool = _DEFAULT_SECOND_PASS,
                 enable_handwriting_mode: bool = _DEFAULT_HANDWRITING):
        self._confidence_accept = confidence_accept
        self._confidence_review = confidence_review
        self._max_short_side = max_image_short_side
        self._enable_second_pass = enable_second_pass
        self._enable_handwriting = enable_handwriting_mode
        self._engine = None
        self._ready = False
        # Patch H1: pluggable second engine slot（預設 Null，不影響現有 pipeline）
        self._secondary: SecondaryEngineBase = NullSecondaryEngine()
        self._enable_secondary_engine: bool = False
        # Patch H3: 細粒度觸發控制，預設 True 保持與 H1 行為一致
        self._secondary_for_handwriting: bool = True
        self._secondary_for_low_confidence: bool = True
        self._secondary_confidence_threshold: float = 0.85

    def load(self, progress_cb=None):
        """載入 RapidOCR 引擎。progress_cb(pct: int, msg: str) 可選。"""
        def _progress(pct, msg):
            logger.debug(f"OCR 載入進度 {pct}%: {msg}")
            if progress_cb:
                progress_cb(pct, msg)

        logger.info("開始載入 OCR 引擎 [RapidOCR PP-OCRv4]...")
        _progress(0, "初始化...")
        t0 = time.time()

        _progress(20, "載入 RapidOCR PP-OCRv4 (ONNX)...")
        from rapidocr_onnxruntime import RapidOCR
        self._engine = RapidOCR()
        logger.info("RapidOCR PP-OCRv4 引擎已建立")

        _progress(90, "暖機推論...")
        dummy = np.zeros((64, 256, 3), dtype=np.uint8)
        self._do_ocr_array(dummy)  # raises if fails → engine_failed

        elapsed = time.time() - t0
        logger.info(f"OCR 引擎載入完成，耗時 {elapsed:.2f}s")
        self._ready = True
        _progress(100, "就緒")

    def is_ready(self) -> bool:
        return self._ready

    def set_secondary_engine(self, engine: SecondaryEngineBase) -> None:
        """注入第二引擎實作（Patch H1）。傳入 NullSecondaryEngine() 可停用。"""
        self._secondary = engine
        logger.info(f"第二 OCR 引擎已設定: {engine.name} (available={engine.is_available()})")

    def get_secondary_info(self) -> dict:
        """Patch H3: 回傳第二引擎目前狀態，供 UI diagnostics 使用。

        Returns
        -------
        dict
            {
              'name': str,       引擎識別名稱
              'available': bool, is_available() 結果
              'enabled': bool,   enable_secondary_engine 開關狀態
            }
        """
        return {
            'name': self._secondary.name,
            'available': self._secondary.is_available(),
            'enabled': self._enable_secondary_engine,
        }

    def configure(self, **kwargs):
        """即時更新可調參數，無需重啟引擎。"""
        if 'max_image_short_side' in kwargs:
            self._max_short_side = int(kwargs['max_image_short_side'])
        if 'enable_second_pass' in kwargs:
            self._enable_second_pass = bool(kwargs['enable_second_pass'])
        if 'enable_handwriting_mode' in kwargs:
            self._enable_handwriting = bool(kwargs['enable_handwriting_mode'])
        if 'confidence_accept' in kwargs:
            self._confidence_accept = float(kwargs['confidence_accept'])
        if 'confidence_review' in kwargs:
            self._confidence_review = float(kwargs['confidence_review'])
        # Patch H1: 第二引擎開關
        if 'enable_secondary_engine' in kwargs:
            self._enable_secondary_engine = bool(kwargs['enable_secondary_engine'])
        # Patch H3: 細粒度觸發控制
        if 'secondary_for_handwriting' in kwargs:
            self._secondary_for_handwriting = bool(kwargs['secondary_for_handwriting'])
        if 'secondary_for_low_confidence' in kwargs:
            self._secondary_for_low_confidence = bool(kwargs['secondary_for_low_confidence'])
        if 'secondary_confidence_threshold' in kwargs:
            self._secondary_confidence_threshold = float(kwargs['secondary_confidence_threshold'])

    def run_ocr(self, image: np.ndarray, mode: str = 'screen') -> dict:
        if not self._ready:
            return {'text': '', 'confidence': 0, 'status': 'failed',
                    'detail': [], 'elapsed_ms': 0, 'error': 'OCR 引擎未就緒'}
        t0 = time.time()
        try:
            # Step 1: upscale 小圖
            image = upscale_if_small(image, self._max_short_side)

            # Step 2: 第一次推論（raw BGR）
            results = self._do_ocr_array(image)

            # Step 3: 判斷是否需要 second pass
            needs_second = self._enable_second_pass and self._should_retry(results)
            if needs_second:
                binarize = self._enable_handwriting or (mode == 'handwriting')
                enhanced = enhance_for_ocr(image, binarize=binarize)
                results2 = self._do_ocr_array(enhanced)
                results = self._merge_results(results, results2)
                logger.debug("OCR second-pass 觸發，合併後 %d 筆", len(results))

            elapsed_ms = int((time.time() - t0) * 1000)
            primary_result = self._process_results(results, elapsed_ms)

            # Step 4 (Patch H1): 第二引擎 fallback routing
            # 僅在開關開啟、第二引擎可用、且主引擎結果不佳時觸發
            if self._should_use_secondary(primary_result, mode):
                logger.debug("觸發第二引擎 fallback [%s], mode=%s", self._secondary.name, mode)
                secondary_result = self._secondary.recognize(image, mode)
                # 若第二引擎有更好的結果，採用之；否則保留主引擎結果
                if self._is_better(secondary_result, primary_result):
                    secondary_result['elapsed_ms'] += primary_result['elapsed_ms']
                    logger.debug("第二引擎結果較優 (conf=%.3f > %.3f)，採用",
                                 secondary_result['confidence'], primary_result['confidence'])
                    return secondary_result
                logger.debug("第二引擎結果未優於主引擎，保留主引擎結果")

            return primary_result
        except Exception as e:
            elapsed_ms = int((time.time() - t0) * 1000)
            logger.error(f"OCR 執行失敗: {e}", exc_info=True)
            return {'text': '', 'confidence': 0, 'status': 'failed',
                    'detail': [], 'elapsed_ms': elapsed_ms, 'error': str(e)}

    def _do_ocr_array(self, image: np.ndarray):
        result, _ = self._engine(image)
        return result or []

    def _process_results(self, results, elapsed_ms: int) -> dict:
        if not results:
            return {'text': '', 'confidence': 0.0, 'status': 'done',
                    'detail': [], 'elapsed_ms': elapsed_ms}

        detail = []
        texts = []
        confs = []
        for item in results:
            if item is None:
                continue
            try:
                if len(item) >= 3:
                    box, text, conf = item[0], item[1], float(item[2])
                elif len(item) == 2:
                    box = item[0]
                    text, conf = item[1][0], float(item[1][1])
                else:
                    continue
                if text and conf > 0.1:
                    text = _s2t(text)  # 簡→繁轉換
                    detail.append({'box': box, 'text': text, 'confidence': conf})
                    texts.append(text)
                    confs.append(conf)
            except Exception:
                continue

        # 用 sort_boxes_and_merge 依位置排列：同行合為一列（空格），不同行才換行
        full_text = sort_boxes_and_merge(detail)
        avg_conf = sum(confs) / len(confs) if confs else 0.0

        if avg_conf >= self._confidence_accept:
            status = 'done'
        else:
            status = 'needs_review'

        return {
            'text': full_text,
            'confidence': avg_conf,
            'status': status,
            'detail': detail,
            'elapsed_ms': elapsed_ms
        }

    def _should_retry(self, results) -> bool:
        """第一次推論結果為空，或平均信心低於 confidence_review 時觸發二次推論。"""
        if not results:
            return True
        confs = []
        for item in results:
            if item is None:
                continue
            try:
                conf = float(item[2]) if len(item) >= 3 else float(item[1][1])
                confs.append(conf)
            except Exception:
                continue
        if not confs:
            return True
        return (sum(confs) / len(confs)) < self._confidence_review

    def _should_use_secondary(self, primary_result: dict, mode: str) -> bool:
        """Patch H1/H3: 決定是否啟動第二引擎 fallback。

        觸發條件（全部需成立）：
        1. `_enable_secondary_engine` 開關為 True
        2. 第二引擎 is_available()
        3. 符合下列觸發路徑之一：
           - handwriting 路徑：`_secondary_for_handwriting=True` 且
             （handwriting_mode 開啟 或 mode=='handwriting'）
           - 低信心路徑：`_secondary_for_low_confidence=True` 且
             （status=='failed' 或 text 為空 或 conf < _secondary_confidence_threshold）

        Patch H3 新增細粒度控制，預設值（兩個 for_* 皆 True，threshold=0.85）
        與 H1 原始行為等價。
        """
        if not self._enable_secondary_engine:
            return False
        if not self._secondary.is_available():
            return False

        status = primary_result.get('status', '')
        text = primary_result.get('text', '')
        conf = primary_result.get('confidence', 0.0)

        use_handwriting_path = self._enable_handwriting or (mode == 'handwriting')
        weak_primary = (
            status == 'failed' or
            not text or
            conf < self._secondary_confidence_threshold
        )

        trigger_handwriting = use_handwriting_path and self._secondary_for_handwriting
        trigger_low_conf = weak_primary and self._secondary_for_low_confidence

        return trigger_handwriting or trigger_low_conf

    @staticmethod
    def _is_better(candidate: dict, baseline: dict) -> bool:
        """回傳 True 若 candidate 的結果優於 baseline。

        優先比較 text 是否有內容，再比較 confidence。
        baseline status='failed' 時，任何有 text 的 candidate 都算優。
        """
        cand_text = candidate.get('text', '')
        base_text = baseline.get('text', '')
        base_status = baseline.get('status', '')

        if not cand_text:
            return False  # candidate 空結果不算優
        if base_status == 'failed' or not base_text:
            return True   # baseline 失敗，有結果就好
        return candidate.get('confidence', 0.0) > baseline.get('confidence', 0.0)

    @staticmethod
    def _merge_results(r1, r2):
        """合併兩次推論結果：以簡單聯集為主，不做 NMS。
        若 r1 和 r2 有重複的文字框，兩者均保留（_process_results 的 box 排序會自然去重疊）。
        """
        merged = list(r1 or [])
        merged.extend(r2 or [])
        return merged

    def run_ocr_from_path(self, image_path: str, mode: str = 'screen') -> dict:
        # cv2.imread 不支援 UNC 路徑（\\server\...）或含中文路徑，
        # 改用 np.fromfile + cv2.imdecode 繞過此限制
        try:
            img_array = np.fromfile(image_path, dtype=np.uint8)
            img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
        except Exception as e:
            logger.error(f"讀取圖片失敗: {image_path!r} -> {e}")
            img = None
        if img is None:
            logger.error(f"cv2.imdecode 回傳 None，路徑可能無效: {image_path!r}")
            return {'text': '', 'confidence': 0, 'status': 'failed',
                    'detail': [], 'elapsed_ms': 0, 'error': '無法讀取圖片'}
        return self.run_ocr(img, mode)
