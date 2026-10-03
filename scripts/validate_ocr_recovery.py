"""Reproducible synthetic pixel comparison against a pinned source baseline.

Runs the real bundled v4 models; this is neither a Taiwan field corpus nor proof
of aggregate OCR improvement. No model downloads or updates are performed.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import types

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('ORT_DISABLE_TELEMETRY', '1')

import cv2
import numpy as np
import psutil
from PIL import Image, ImageDraw, ImageFont

from src.ocr.engine import OcrEngine
from scripts.replay_spacing import edits


def baseline_engine(revision):
    """Load exact old engine/fusion/postprocessor source under isolated names."""
    for name in ('postprocessor', 'fusion', 'engine'):
        source = subprocess.check_output(
            ['git', 'show', f'{revision}:src/ocr/{name}.py'], cwd=ROOT, text=True)
        source = source.replace('from .postprocessor import', 'from ._probe_postprocessor import')
        source = source.replace('from .fusion import', 'from ._probe_fusion import')
        module = types.ModuleType(f'src.ocr._probe_{name}')
        module.__package__ = 'src.ocr'
        sys.modules[module.__name__] = module
        exec(compile(source, f'{revision}:src/ocr/{name}.py', 'exec'), module.__dict__)
    return module.OcrEngine


def make_cases(font_path, font_index):
    specs = [
        ('ordinary_tw', (640, 200), 24, '臺灣稅務申報 勞健保補充保險費', (12, 60)),
        ('ordinary_mixed', (640, 200), 18, 'API_key AB-12345678 10 kg (1,234) -5.00%', (12, 60)),
        ('rare_tw', (960, 120), 24, '己已巳 鬱 龘 爨 纛 𠮷 尞', (12, 40)),
        ('long_strip', (2000, 30), 16, '臺灣稅務申報 API_key AB-12345678 10 kg', (10, 3)),
        ('long_seam', (2000, 50), 20, '臺灣稅務申報期限 已繳稅款 尚未繳納金額', (420, 12)),
        ('small_large', (2300, 600), 10, '臺灣稅務申報 API_key AB-12345678 10 kg', (500, 210)),
        ('small_4k', (3840, 2160), 10, '臺灣稅務申報 API_key AB-12345678 10 kg', (1000, 500)),
    ]
    cases = []
    for ident, size, px, text, xy in specs:
        image = Image.new('RGB', size, 'white')
        font = ImageFont.truetype(str(font_path), px, index=font_index)
        ImageDraw.Draw(image).text(xy, text, font=font, fill='black')
        cases.append((ident, image, text, px, xy))
    return cases


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-ref', default='bba210ec')
    parser.add_argument('--font', type=Path, required=True)
    parser.add_argument('--font-index', type=int, default=3)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--case', action='append', help='Optional fixed case IDs to run')
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    candidates = make_cases(args.font, args.font_index)
    if args.case:
        candidates = [case for case in candidates if case[0] in args.case]
        if {case[0] for case in candidates} != set(args.case):
            parser.error('Unknown case ID')
    # Freeze generated pixels and GT before any recognition occurs.
    inputs = []
    for ident, image, truth, px, xy in candidates:
        path = args.output_dir / f'{ident}.png'
        image.save(path)
        inputs.append(dict(id=ident, ground_truth=truth, font_px=px, text_xy=xy,
                           source_hw=[image.height, image.width], file=path.name,
                           image_sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    manifest = dict(font_file=args.font.name,
                    font_sha256=hashlib.sha256(args.font.read_bytes()).hexdigest(),
                    font_index=args.font_index,
                    font_name=ImageFont.truetype(str(args.font), 20, index=args.font_index).getname(),
                    inputs=inputs)
    (args.output_dir / 'inputs.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')

    source_hashes = {f: hashlib.sha256((ROOT / 'src/ocr' / f).read_bytes()).hexdigest()
                     for f in ('engine.py', 'preprocessor.py', 'fusion.py', 'postprocessor.py')}
    current = OcrEngine()
    current.load()
    old = baseline_engine(args.baseline_ref)()
    old._engine, old._ready, old._model_version = current._engine, True, current._model_version
    process, rows = psutil.Process(), []
    for index, (ident, image, truth, _, _) in enumerate(candidates):
        array = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)
        row = dict(id=ident, ground_truth=truth)
        # Alternate order; timings remain single observations, not p95/p99.
        variants = [('baseline', old), ('repaired', current)]
        if index % 2:
            variants.reverse()
        for label, engine in variants:
            samples, stop = [], threading.Event()

            def sample_rss(stop=stop, samples=samples):
                while not stop.wait(.01):
                    samples.append(process.memory_info().rss)

            monitor = threading.Thread(target=sample_rss, daemon=True)
            monitor.start()
            start = time.perf_counter()
            try:
                result = engine.run_ocr(array)
            finally:
                duration = (time.perf_counter() - start) * 1000
                stop.set()
                monitor.join()
            row[label] = dict(result=result, metrics=edits(truth, result['text']),
                              measured_ms=duration, sampled_peak_rss_bytes=max(samples or [process.memory_info().rss]))
            print(ident, label, result['status'], repr(result['text']), round(duration), flush=True)
        rows.append(row)
        report = dict(baseline_ref=args.baseline_ref, model_version=current._model_version,
                      source_sha256=source_hashes,
                      rows=rows, limitations=[
                          'Synthetic pixels from one TC font; not a held-out field corpus',
                          'Rare-glyph font coverage is not independently verified; inspect input pixels',
                          'No normalization beyond the existing zh-hant conversion and NFC edit metric',
                          'One timed observation per variant; same process/model session, not isolated RSS or latency percentiles',
                          'No Windows, packaged-app or real offline-machine acceptance in this report'])
        (args.output_dir / 'results.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
