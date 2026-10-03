# -*- coding: utf-8 -*-
import logging
import time
import cv2
import numpy as np
from .postprocessor import sort_boxes_and_merge
from .fusion import fuse_passes, fuse_tiles, has_text_conflicts
from .preprocess import enhance_for_ocr, upscale_if_small
from .preprocessor import (plan_ocr_tiles, prepare_ocr_tile, restore_tile_results,
                           validate_image_shape, MAX_ENCODED_BYTES)
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
        self._model_version = "unknown"
        # Patch H1: pluggable second engine slot（預設 Null，不影響現有 pipeline）
        self._secondary: SecondaryEngineBase = NullSecondaryEngine()
        self._enable_secondary_engine: bool = False
        # Patch H3: 細粒度觸發控制，預設 True 保持與 H1 行為一致
        self._secondary_for_handwriting: bool = True
        self._secondary_for_low_confidence: bool = True
        self._secondary_confidence_threshold: float = 0.85
        # 進度條回呼
        self._progress_callback = None

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
        # Opt out before importing the wrapper or creating any ORT session.
        import onnxruntime as ort
        ort.disable_telemetry_events()
        from .model_validator import verified_model_manifest
        manifest = verified_model_manifest()
        from rapidocr_onnxruntime import RapidOCR
        self._engine = RapidOCR(**{f'{key}_model_path': info['absolute_path']
                                  for key, info in manifest.items()},
                                intra_op_num_threads=2, inter_op_num_threads=1)
        self._model_version = ';'.join(f'{key}:{info["sha256"]}' for key,info in sorted(manifest.items()))
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

    def set_progress_callback(self, callback) -> None:
        """Expose OCR progress updates through the wrapper, not the vendor SDK."""
        self._progress_callback = callback

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
        started = time.monotonic()
        try:
            if not isinstance(image, np.ndarray):
                raise ValueError('OCR input must be a NumPy image')
            validate_image_shape(image.shape)
            if image.dtype != np.uint8 or image.ndim != 3 or image.shape[2] != 3:
                raise ValueError('OCR input must be uint8 BGR with three channels')
            if not self._ready:
                raise ValueError('OCR 引擎未就緒')
            tiles = plan_ocr_tiles(image.shape, self._max_short_side)
            if not tiles:
                result = self._run_single_ocr(image, mode)
                result['preprocessing'] = {'strategy': 'whole_image', 'source_hw': list(image.shape[:2])}
                return result
            merged, tile_evidence, warnings, tile_hypotheses = [], [], [], []
            for index, tile in enumerate(tiles):
                if self._progress_callback:
                    self._progress_callback(10 + int(75 * index / len(tiles)),
                                            f'切片辨識 {index + 1}/{len(tiles)}')
                # A perfectly uniform crop contains no image information. This
                # exact test does not discard faint/low-contrast text.
                x, y, w, h = tile['source_xywh']
                crop = image[y:y + h, x:x + w]
                uniform = bool(np.all(crop == crop[0, 0]))
                if uniform:
                    result = {'text': '', 'detail': [], 'status': 'done',
                              'engine': 'rapidocr_onnxruntime', 'model_version': self._model_version,
                              'hypotheses': {'skipped': 'exactly_uniform_source_tile'}}
                else:
                    # At most one inference tile and its enhancement are alive.
                    prepared = prepare_ocr_tile(image, tile)
                    result = self._run_single_ocr(prepared, mode, preprocessed=True)
                    del prepared
                if result['status'] == 'failed':
                    raise RuntimeError(f'切片 {index + 1}/{len(tiles)} 失敗: {result.get("error", "unknown")}')
                if result['text'] and not result.get('detail'):
                    raise ValueError('切片結果沒有座標，無法安全合併；原圖保留供重試')
                tile_hypotheses.append({'tile_index': index, 'hypotheses': result.get('hypotheses', {}),
                                        'detail': result['detail']})
                raw = [[r['box'], r['text'], r['confidence']] for r in result['detail']]
                mapped = restore_tile_results(raw, tile)
                merged, conflicts = fuse_tiles(merged, mapped)
                if conflicts:
                    warnings.append(f'tile {index + 1}: overlapping seam hypotheses disagree')
                warnings.extend(result.get('warnings', []))
                tile_evidence.append(dict(tile, inference_skipped=uniform, engine=result.get('engine', 'unknown'),
                                          model_version=result.get('model_version', 'unknown')))
            final = self._process_results(merged, int((time.monotonic() - started) * 1000))
            # Tiling is a bounded recovery path, not a completeness certificate.
            final['status'] = 'needs_review'
            for row in final['detail']:
                row.pop('raw_text', None)  # Raw model text lives in per-tile hypotheses.
                row['text_source'] = 'tile_fusion'
            engines = sorted({r['engine'] for r in tile_evidence})
            versions = sorted({r['model_version'] for r in tile_evidence})
            final.update(engine=engines[0] if len(engines) == 1 else 'mixed:' + ','.join(engines),
                         model_version='|'.join(versions),
                         warnings=['Tiled OCR: review text at tile boundaries'] + warnings,
                         hypotheses={'tiles': tile_hypotheses},
                         preprocessing={'strategy': 'overlapping_tiles',
                                        'source_hw': list(image.shape[:2]), 'tiles': tile_evidence})
            return final
        except Exception as exc:
            logger.error('OCR input/tiling failed: %s', exc, exc_info=True)
            return dict(text='', confidence=0., status='failed', detail=[],
                        elapsed_ms=int((time.monotonic() - started) * 1000), error=str(exc))

    def _run_single_ocr(self, image: np.ndarray, mode: str = 'screen',
                        preprocessed: bool = False) -> dict:
        if not self._ready:
            return {'text': '', 'confidence': 0, 'status': 'failed',
                    'detail': [], 'elapsed_ms': 0, 'error': 'OCR 引擎未就緒'}
        t0 = time.time()
        original_hw = image.shape[:2]
        try:
            # Step 1: upscale 小圖 (10%)
            if self._progress_callback and not preprocessed:
                self._progress_callback(10, "圖片處理中...")
            if not preprocessed:
                image = upscale_if_small(image, self._max_short_side)
            warnings = []
            hypotheses = {}

            # Step 2: 第一次推論（raw BGR） (50%)
            if self._progress_callback and not preprocessed:
                self._progress_callback(30, "文字辨識中...")
            try:
                results = self._do_ocr_array(image)
            except Exception as primary_error:
                failed = dict(text='',confidence=0.,status='failed',error=str(primary_error))
                if self._should_use_secondary(failed, mode):
                    candidate = self._secondary.recognize(image,mode)
                    if self._is_better(candidate,failed):
                        candidate.update(engine=self._secondary.name,
                                         elapsed_ms=int((time.time()-t0)*1000))
                        candidate.setdefault('model_version','unknown')
                        return self._original_coordinates(candidate, original_hw, image.shape[:2])
                raise

            hypotheses['first_pass'] = self._snapshot_results(results)

            # Step 3: 判斷是否需要 second pass (70%)
            needs_second = self._enable_second_pass and self._should_retry(results)
            if needs_second:
                if self._progress_callback and not preprocessed:
                    self._progress_callback(60, "二次辨識中...")
                binarize = self._enable_handwriting or (mode == 'handwriting')
                try:
                    enhanced = enhance_for_ocr(image, binarize=binarize)
                    results2 = self._do_ocr_array(enhanced)
                    hypotheses['second_pass'] = self._snapshot_results(results2)
                    if has_text_conflicts(results, results2):
                        warnings.append('Overlapping first/second-pass text disagrees; review raw hypotheses')
                    results = self._merge_results(results, results2)
                    logger.debug("OCR second-pass 觸發，合併後 %d 筆", len(results))
                except Exception as retry_error:
                    logger.warning('二次辨識失敗，保留第一次結果: %s', retry_error)
                    if not results:
                        raise
                    warnings.append(f'Second pass failed; first-pass text retained: {retry_error}')

            # Step 4: 合併結果 (90%)
            if self._progress_callback and not preprocessed:
                self._progress_callback(80, "合併結果...")

            elapsed_ms = int((time.time() - t0) * 1000)
            primary_result = self._process_results(results, elapsed_ms)
            primary_result['hypotheses'] = hypotheses
            if warnings:
                primary_result.update(status='needs_review', warnings=warnings)

            # Step 4 (Patch H1): 第二引擎 fallback routing
            # 僅在開關開啟、第二引擎可用、且主引擎結果不佳時觸發
            if self._should_use_secondary(primary_result, mode):
                logger.debug("觸發第二引擎 fallback [%s], mode=%s", self._secondary.name, mode)
                try:
                    secondary_result = self._secondary.recognize(image, mode)
                except Exception:
                    logger.exception('第二引擎失敗，保留第一引擎結果')
                    secondary_result = {'text':'', 'status':'failed'}
                # 若第二引擎有更好的結果，採用之；否則保留主引擎結果
                if self._is_better(secondary_result, primary_result):
                    secondary_result['elapsed_ms'] = int((time.time() - t0) * 1000)
                    secondary_result['engine'] = self._secondary.name
                    secondary_result.setdefault('model_version', 'unknown')
                    secondary_result['hypotheses'] = dict(hypotheses, secondary=secondary_result.get('detail', []))
                    logger.debug("第二引擎結果較優 (conf=%.3f > %.3f)，採用",
                                 secondary_result['confidence'], primary_result['confidence'])
                    return self._original_coordinates(secondary_result, original_hw, image.shape[:2])
                logger.debug("第二引擎結果未優於主引擎，保留主引擎結果")

            primary_result["elapsed_ms"] = int((time.time() - t0) * 1000)
            primary_result.update(engine="rapidocr_onnxruntime", model_version=self._model_version)
            return self._original_coordinates(primary_result, original_hw, image.shape[:2])
        except Exception as e:
            elapsed_ms = int((time.time() - t0) * 1000)
            logger.error(f"OCR 執行失敗: {e}", exc_info=True)
            return {'text': '', 'confidence': 0, 'status': 'failed',
                    'detail': [], 'elapsed_ms': elapsed_ms, 'error': str(e)}

    @staticmethod
    def _snapshot_results(results):
        snapshot = []
        for item in results or []:
            if item is None:
                continue
            try:
                box, text, confidence = (item[:3] if len(item) >= 3
                                         else (item[0], item[1][0], item[1][1]))
                if not np.isfinite(float(confidence)):
                    continue
                snapshot.append({'box': np.asarray(box, dtype=float).tolist(),
                                 'raw_text': text, 'confidence': float(confidence)})
            except (TypeError, ValueError, IndexError):
                logger.warning('Ignoring malformed OCR hypothesis snapshot')
        return snapshot

    @staticmethod
    def _original_coordinates(result, original_hw, processed_hw):
        # Do not mutate a provider-owned response reused across calls.
        import copy
        result = copy.deepcopy(result)
        sy, sx = original_hw[0] / processed_hw[0], original_hw[1] / processed_hw[1]
        for detail in result.get('detail', []):
            box = detail.get('box')
            if box is not None:
                points = np.asarray(box, dtype=float)
                if points.shape == (4,):
                    x1, y1, x2, y2 = points
                    points = np.array([[x1, y1], [x2, y1], [x2, y2], [x1, y2]])
                if points.shape != (4, 2) or not np.isfinite(points).all():
                    raise ValueError('Invalid OCR quadrilateral')
                detail['box'] = (points * [sx, sy]).tolist()
        result['coordinate_transform'] = {'source_hw': list(original_hw),
                                           'processed_hw': list(processed_hw),
                                           'scale_to_source_xy': [sx, sy],
                                           'hypotheses_coordinate_space': 'processed_image'}
        return result

    def _do_ocr_array(self, image: np.ndarray):
        result, _ = self._engine(image)
        return result or []

    def _process_results(self, results, elapsed_ms: int) -> dict:
        if not results:
            return {'text': '', 'confidence': 0.0, 'status': 'done',
                    'detail': [], 'elapsed_ms': elapsed_ms}

        detail = []
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
                if text and np.isfinite(conf) and 0.1 < conf <= 1.0:
                    raw_text = text
                    text = _s2t(text)  # 簡→繁轉換；保留模型原始文字
                    if hasattr(box, 'tolist'):
                        box = box.tolist()
                    detail.append({'box': box, 'text': text, 'raw_text': raw_text, 'confidence': conf})
                    confs.append(conf)
            except Exception:
                continue

        # 用 sort_boxes_and_merge 依位置排列：同行合為一列（空格），不同行才換行
        full_text = sort_boxes_and_merge(detail)
        avg_conf = sum(confs) / len(confs) if confs else 0.0

        if avg_conf >= self._confidence_accept and all(c >= self._confidence_review for c in confs):
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
        """Retry empty output or any weak region; a page average can hide rare glyphs."""
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
        return any(not np.isfinite(c) or c < self._confidence_review for c in confs)

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
        if candidate.get("status") == "failed":
            return False
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
        return fuse_passes(r1, r2)

    def run_ocr_from_path(self, image_path: str, mode: str = 'screen') -> dict:
        # cv2.imread 不支援 UNC 路徑（\\server\...）或含中文路徑，
        # 改用 np.fromfile + cv2.imdecode 繞過此限制
        try:
            # Inspect the same open file before allocating compressed bytes or
            # decoded pixels. This also avoids a path-replacement gap.
            import os
            from PIL import Image
            with open(image_path, 'rb') as source:
                if os.fstat(source.fileno()).st_size > MAX_ENCODED_BYTES:
                    raise ValueError('圖片檔案超過 32 MB')
                with Image.open(source) as header:
                    validate_image_shape((header.height, header.width))
                    source.seek(0)
                    encoded = source.read(MAX_ENCODED_BYTES + 1)
                if len(encoded) > MAX_ENCODED_BYTES:
                    raise ValueError('圖片檔案超過 32 MB')
            img = cv2.imdecode(np.frombuffer(encoded, dtype=np.uint8), cv2.IMREAD_COLOR)
        except Exception as e:
            logger.error(f"讀取圖片失敗: {image_path!r} -> {e}")
            return {'text': '', 'confidence': 0, 'status': 'failed',
                    'detail': [], 'elapsed_ms': 0, 'error': f'無法讀取圖片: {e}'}
        if img is None:
            logger.error(f"cv2.imdecode 回傳 None，路徑可能無效: {image_path!r}")
            return {'text': '', 'confidence': 0, 'status': 'failed',
                    'detail': [], 'elapsed_ms': 0, 'error': '無法讀取圖片'}
        return self.run_ocr(img, mode)
