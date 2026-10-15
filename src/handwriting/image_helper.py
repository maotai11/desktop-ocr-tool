"""One-job offline raster helper. No heavyweight import occurs before START.

The parent owns its Job and deadline. It sends the same bounded integer stroke
payload as the native helper; no paths, images or native pointers cross IPC.
"""
import math
import os
from pathlib import Path
import struct
import sys
from .protocol import (HEADER, MAGIC, VERSION, START, READY, RECOGNIZE, RESULT,
                       STOP, MAX_REQUEST_BYTES, MAX_POINTS, MAX_POINTS_PER_STROKE,
                       MAX_STROKES, CANVAS_SIZE, encode_strokes, frame)


def _stdio():
    if sys.stdin is not None and sys.stdout is not None:
        return sys.stdin.buffer, sys.stdout.buffer
    if sys.platform != 'win32':
        raise RuntimeError('No helper pipes')
    # PyInstaller --windowed sets sys.std* to None. QProcess still supplies
    # redirected Windows handles; wrap them only inside this child route.
    import ctypes
    import msvcrt
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetStdHandle.argtypes = [ctypes.c_uint32]
    kernel.GetStdHandle.restype = ctypes.c_void_p
    handles = [kernel.GetStdHandle(value & 0xffffffff) for value in (-10, -11)]
    if any(value in (None, 0, ctypes.c_void_p(-1).value) for value in handles):
        raise RuntimeError('No redirected helper pipes')
    return (os.fdopen(msvcrt.open_osfhandle(handles[0], os.O_RDONLY|os.O_BINARY), 'rb'),
            os.fdopen(msvcrt.open_osfhandle(handles[1], os.O_WRONLY|os.O_BINARY), 'wb'))


def _read(stream):
    header = stream.read(HEADER.size)
    if not header:
        return None
    if len(header) != HEADER.size:
        raise ValueError('Truncated image header')
    magic, version, kind, reserved, generation, size = HEADER.unpack(header)
    if magic != MAGIC or version != VERSION or reserved or size > MAX_REQUEST_BYTES:
        raise ValueError('Invalid image header')
    payload = stream.read(size)
    if len(payload) != size:
        raise ValueError('Truncated image payload')
    return kind, generation, payload


def _strokes(payload):
    if len(payload) < 2:
        raise ValueError('Invalid strokes')
    count, = struct.unpack_from('<H', payload)
    if not 1 <= count <= MAX_STROKES:
        raise ValueError('Invalid stroke count')
    offset, total, strokes = 2, 0, []
    for _ in range(count):
        if offset+2 > len(payload):
            raise ValueError('Truncated stroke')
        points, = struct.unpack_from('<H', payload, offset)
        offset += 2
        total += points
        if not 1 <= points <= MAX_POINTS_PER_STROKE or total > MAX_POINTS or offset+points*4 > len(payload):
            raise ValueError('Invalid points')
        stroke = tuple(struct.unpack_from('<HH', payload, offset+index*4) for index in range(points))
        if any(x > CANVAS_SIZE or y > CANVAS_SIZE for x,y in stroke):
            raise ValueError('Invalid coordinate')
        offset += points*4
        strokes.append(stroke)
    if offset != len(payload):
        raise ValueError('Trailing stroke data')
    return tuple(strokes)


def _normalize(strokes):
    points = [point for stroke in strokes for point in stroke]
    left, right = min(x for x,y in points), max(x for x,y in points)
    top, bottom = min(y for x,y in points), max(y for x,y in points)
    span = max(right-left, bottom-top)
    if not span:
        return tuple(tuple((500, 500) for _ in stroke) for stroke in strokes)
    scale = 800/span
    return tuple(tuple((round(500+(x-(left+right)/2)*scale),
                        round(500+(y-(top+bottom)/2)*scale)) for x,y in stroke)
                 for stroke in strokes)


def run():
    try:
        source, sink = _stdio()
        initial = _read(source)
        if initial is None:
            return 0
        if initial != (START, 0, b''):
            raise ValueError('START required')
        # Job assignment/START is complete before NumPy/OpenCV/ORT imports.
        os.environ['ORT_DISABLE_TELEMETRY'] = '1'
        import cv2
        import numpy as np
        import onnxruntime as ort
        ort.disable_telemetry_events()
        from rapidocr_onnxruntime.ch_ppocr_rec.text_recognize import TextRecognizer
        from rapidocr_onnxruntime.utils import read_yaml
        import rapidocr_onnxruntime
        from src.ocr.model_validator import verified_model_manifest, verify_recognizer_identity
        from .candidates import is_cjk_scalar
        manifest = verified_model_manifest(profile='v6-small')
        cfg = read_yaml(Path(rapidocr_onnxruntime.__file__).parent/'config.yaml')['Rec']
        cfg.update(model_path=manifest['rec']['absolute_path'], intra_op_num_threads=1,
                   inter_op_num_threads=1, rec_batch_num=1, rec_img_shape=[3,48,320])
        rec = TextRecognizer(cfg)
        verify_recognizer_identity(rec, manifest['rec'])
        sink.write(frame(READY, 0, struct.pack('<HHI', 0, 0, os.getpid()))); sink.flush()
        request = _read(source)
        if request is None or request == (STOP, 0, b''):
            return 0
        kind, generation, payload = request
        if kind != RECOGNIZE or not generation:
            raise ValueError('Invalid image request')
        strokes = _strokes(payload)
        values = []
        for ink in (strokes, _normalize(strokes)):
            image = np.full((256,256,3), 255, dtype=np.uint8)
            for stroke in ink:
                points = np.array([[round(x*255/1000),round(y*255/1000)] for x,y in stroke], dtype=np.int32)
                if len(points) == 1:
                    cv2.circle(image, tuple(points[0]), 1, (0,0,0), -1, lineType=cv2.LINE_AA)
                else:
                    cv2.polylines(image, [points], False, (0,0,0), 3, lineType=cv2.LINE_AA)
            result, _ = rec(image)
            text, score = result[0]
            # A single-character tool must not silently split a word or convert
            # scripts. A multi-character/blank output produces no candidate.
            if is_cjk_scalar(text) and math.isfinite(float(score)) and text not in [v[0] for v in values]:
                values.append((text, float(score)))
        output = bytearray(struct.pack('<HBB', 0, len(values), 0))
        for text, score in values:
            label = text.encode('utf-8')
            output.extend(bytes([len(label)])+label+struct.pack('<f', score))
        sink.write(frame(RESULT, generation, output)); sink.flush()
        return 0  # One request: release this ORT session/process immediately.
    except Exception:
        # Protocol-only stdout, no user/model paths or text in diagnostics.
        return 2
