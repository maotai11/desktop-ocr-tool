"""Bound native recognition work; optionally reuse exact rasters within one tile job."""
import hashlib
import math
import sys
from contextlib import contextmanager

import numpy as np


class BoundedRecognizer:
    MAX_WIDTH = 4096
    MAX_CROPS = 512
    MAX_TOTAL_WIDTH = 65536
    MAX_CACHE_ENTRIES = 128
    MAX_CACHE_OUTPUT_BYTES = 65536
    MAX_CACHE_RASTER_BYTES = 256 * 1024

    def __init__(self, recognizer, *, model_context=None):
        self.recognizer = recognizer
        self.recognizer.rec_batch_num = 1
        self._model_context = model_context
        self._bound_signature = self._signature() if self._valid_context(model_context) else None
        self._operation = None
        self.reset_budget()

    @staticmethod
    def _valid_context(context):
        return (type(context) is tuple and len(context) == 3 and type(context[0]) is str
                and bool(context[0]) and all(type(value) is str and len(value) == 64
                    and all(char in '0123456789abcdef' for char in value) for value in context[1:]))

    def _signature(self):
        try:
            rec = self.recognizer
            if rec.rec_batch_num != 1 or tuple(rec.rec_image_shape) != (3, 48, 320):
                return None
            session = rec.session.session
            decoder = rec.postprocess_op
            characters = decoder.character
            if session is None or not isinstance(characters, (list, tuple)) or not characters:
                return None
            if not all(type(character) is str for character in characters):
                return None
            return id(rec), id(session), id(decoder), tuple(rec.rec_image_shape), tuple(characters)
        except (AttributeError, TypeError):
            return None

    def reset_budget(self):
        if getattr(self, '_operation', None) is not None:
            self._operation['entries'].clear()
            self._operation['output_bytes'] = 0
            self._operation['enabled'] = False
        self._operation = None
        self.last_exact_operation = None
        self.total_width = 0
        self.total_crops = 0

    @contextmanager
    def exact_raster_operation(self, model_context):
        """No cross-operation reuse; model and default batch-one decoding must match."""
        if self._operation is not None:
            self._operation['entries'].clear()
            self._operation['enabled'] = False
            yield self
            return
        enabled = (self._bound_signature is not None and model_context == self._model_context
                   and self._signature() == self._bound_signature)
        state = {'enabled': enabled, 'entries': {}, 'output_bytes': 0, 'logical_crops': 0,
                 'native_miss_crops': 0, 'native_width': 0, 'cache_hits': 0, 'cache_misses': 0,
                 'max_entries': 0, 'max_output_bytes': 0}
        self._operation = state
        try:
            yield self
        finally:
            state['entries'].clear()
            state['output_bytes'] = 0
            if self._operation is state:
                self._operation = None
            self.last_exact_operation = {key: value for key, value in state.items()
                if key not in ('entries', 'output_bytes')}
            self.last_exact_operation['cleared'] = True

    @staticmethod
    def _default_options(args, kwargs):
        return ((not args and not kwargs) or
                (len(args) == 1 and args[0] is False and not kwargs) or
                (not args and len(kwargs) == 1 and kwargs.get('return_word_box') is False))

    def _key(self, crop):
        if not crop.flags.c_contiguous or crop.nbytes > self.MAX_CACHE_RASTER_BYTES:
            return None
        return crop.shape, hashlib.sha256(memoryview(crop).cast('B')).digest()

    @staticmethod
    def _valid_output(result, count):
        if (not isinstance(result, tuple) or len(result) != 2 or type(result[0]) is not list
                or len(result[0]) != count or type(result[1]) not in (int, float)
                or not math.isfinite(result[1]) or result[1] < 0):
            return False
        return all(type(row) in (list, tuple) and len(row) == 2 and type(row[0]) is str
                   and type(row[1]) is float and math.isfinite(row[1]) and 0 <= row[1] <= 1
                   for row in result[0])

    @staticmethod
    def _clone_row(row):
        return (row[0], row[1]) if type(row) is tuple else [row[0], row[1]]

    def __call__(self, images, *args, **kwargs):
        try:
            return self._recognize(images, *args, **kwargs)
        except BaseException:
            if self._operation is not None:
                self._operation['entries'].clear()
                self._operation['output_bytes'] = 0
                self._operation['enabled'] = False
            raise

    def _recognize(self, images, *args, **kwargs):
        crops = [images] if isinstance(images, np.ndarray) else images
        if not isinstance(crops, (list, tuple)) or len(crops) > self.MAX_CROPS:
            raise ValueError('文字區塊過多，請縮小框選範圍後重試')
        if not crops:
            return self.recognizer(crops, *args, **kwargs)
        widths = []
        for crop in crops:
            if (not isinstance(crop, np.ndarray) or crop.dtype != np.uint8
                    or crop.ndim != 3 or crop.shape[2] != 3
                    or crop.shape[0] < 1 or crop.shape[1] < 1):
                raise ValueError('無效的文字區塊，原圖保留供重試')
            width = max(320, math.ceil(48 * crop.shape[1] / crop.shape[0]))
            if width > self.MAX_WIDTH:
                raise ValueError('文字行過長或過細，請分段框選；原圖保留供重試')
            widths.append(width)
        state = self._operation
        reuse = (state is not None and state['enabled'] and self._default_options(args, kwargs)
                 and self._signature() == self._bound_signature)
        if state is not None and state['enabled'] and not reuse:
            state['entries'].clear()
            state['output_bytes'] = 0
            state['enabled'] = False
        keys = [self._key(crop) for crop in crops] if reuse else [None] * len(crops)
        hits = {index: state['entries'][key] for index, key in enumerate(keys)
                if reuse and key is not None and key in state['entries']}
        misses = [index for index in range(len(crops)) if index not in hits]
        native_width = sum(widths[index] for index in misses)
        if native_width + self.total_width > self.MAX_TOTAL_WIDTH or len(crops) + self.total_crops > self.MAX_CROPS:
            raise ValueError('文字辨識工作量超限，請分段框選；原圖保留供重試')
        self.total_width += native_width
        self.total_crops += len(crops)
        if state is not None:
            state['logical_crops'] += len(crops)
            state['native_miss_crops'] += len(misses)
            state['native_width'] += native_width
            state['cache_hits'] += len(hits)
            state['cache_misses'] += len(misses)
        try:
            result = (self.recognizer(crops, *args, **kwargs) if not reuse else
                      self.recognizer([crops[index] for index in misses], *args, **kwargs) if misses else ([], 0.))
        except BaseException:
            if state is not None:
                state['entries'].clear()
                state['output_bytes'] = 0
                state['enabled'] = False
            raise
        if not reuse:
            return result
        if not self._valid_output(result, len(misses)):
            state['entries'].clear()
            state['output_bytes'] = 0
            state['enabled'] = False
            if hits:
                raise ValueError('無法安全展開文字辨識結果；原圖保留供重試')
            return result
        assembled = [None] * len(crops)
        for index, value in hits.items():
            assembled[index] = self._clone_row(value)
        for index, row in zip(misses, result[0]):
            assembled[index] = self._clone_row(row)
            key = keys[index]
            if key is not None and key in state['entries'] and tuple(state['entries'][key]) != tuple(row):
                # A backend which contradicts itself for identical rasters is
                # not a pure batch-one recognizer; preserve this native batch.
                state['entries'].clear()
                state['output_bytes'] = 0
                state['enabled'] = False
            if key is None or key in state['entries'] or not state['enabled']:
                continue
            cost = sys.getsizeof(row) + sys.getsizeof(row[0]) + sys.getsizeof(row[1])
            if len(state['entries']) < self.MAX_CACHE_ENTRIES and state['output_bytes'] + cost <= self.MAX_CACHE_OUTPUT_BYTES:
                state['entries'][key] = self._clone_row(row)
                state['output_bytes'] += cost
        state['max_entries'] = max(state['max_entries'], len(state['entries']))
        state['max_output_bytes'] = max(state['max_output_bytes'], state['output_bytes'])
        return assembled, result[1]
