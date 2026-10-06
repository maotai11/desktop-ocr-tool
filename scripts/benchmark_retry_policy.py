"""Actual same-raster 1/2/3-pass OCR comparison, preserving literal errors.

This reports conditional retry policies, not forced repeated inference. Confidence
scores are not correctness. Synthetic fixtures are not customer accuracy evidence.
Run each limit in a fresh process for interpretable peak RSS.
"""
import argparse
import hashlib
import json
import os
import platform
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('ORT_DISABLE_TELEMETRY', '1')

import cv2
import numpy as np
import psutil

from scripts.replay_spacing import edits
from src.ocr.engine import OcrEngine
from src.ocr.model_validator import MODEL_PROFILES


def count_passes(hypotheses):
    if 'tiles' in hypotheses:
        return sum(count_passes(tile.get('hypotheses', {})) for tile in hypotheses['tiles'])
    return sum(meta.get('outcome') == 'completed'
               for meta in hypotheses.get('pass_metadata', {}).values())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--profile', choices=MODEL_PROFILES, required=True)
    parser.add_argument('--passes', type=int, choices=(1, 2, 3), required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    manifest_bytes = args.inputs.read_bytes()
    manifest = json.loads(manifest_bytes)
    inputs = manifest.get('cases', manifest.get('inputs', []))
    if not inputs:
        raise ValueError('No frozen ground-truth inputs')
    engine = OcrEngine(model_profile=args.profile, max_ocr_passes=args.passes)
    start = time.perf_counter()
    engine.load()
    report = {'profile': args.profile, 'max_ocr_passes': args.passes,
              'model_identity': engine.model_identity, 'load_ms': (time.perf_counter()-start)*1000,
              'input_manifest_sha256': hashlib.sha256(manifest_bytes).hexdigest(),
              'engine_source_sha256': hashlib.sha256((ROOT/'src/ocr/engine.py').read_bytes()).hexdigest(),
              'source_sha256': {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in (
                  'src/ocr/engine.py', 'src/ocr/preprocessor.py', 'src/ocr/fusion.py',
                  'src/ocr/postprocessor.py', 'src/ocr/bounded_recognizer.py')},
              'environment': {'platform': platform.platform(), 'python': platform.python_version(),
                              'opencv': cv2.__version__, 'numpy': np.__version__},
              'rows': [], 'limitations': ['Synthetic inputs, not customer holdout',
                  'One timing observation per input; not p95/p99',
                  'Conditional retries; high-confidence wrong text may not trigger retries',
                  'Same-model preprocessing candidates are correlated, not independent votes',
                  'Literal spacing retained; no ground truth used in selection']}
    process = psutil.Process()
    for case in inputs:
        image_bytes = (args.inputs.parent / case['file']).read_bytes()
        digest = hashlib.sha256(image_bytes).hexdigest()
        if digest != case.get('sha256', case.get('image_sha256')):
            raise ValueError(f'Changed image: {case["id"]}')
        image = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
        started = time.perf_counter()
        result = engine.run_ocr(image)
        row = {'id': case['id'], 'image_sha256': digest, 'ground_truth': case['ground_truth'],
               'result': result, 'metrics': edits(case['ground_truth'], result['text']),
               'milliseconds': (time.perf_counter()-started)*1000,
               'inference_passes': count_passes(result.get('hypotheses', {})),
               'recognition_crops': engine._engine.text_rec.total_crops,
               'recognition_normalized_width': engine._engine.text_rec.total_width,
               'rss_after_bytes': process.memory_info().rss}
        report['rows'].append(row)
        print(args.passes, case['id'], repr(result['text']), row['metrics']['errors'], row['inference_passes'], flush=True)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    report['summary'] = {
        'cases': len(inputs), 'literal_exact': sum(r['metrics']['errors'] == 0 for r in report['rows']),
        'errors': sum(r['metrics']['errors'] for r in report['rows']),
        'characters': sum(r['metrics']['N'] for r in report['rows']),
        'whitespace_edits': sum(r['metrics']['whitespace_edits'] for r in report['rows']),
        'total_ms': sum(r['milliseconds'] for r in report['rows']),
        'inference_passes': sum(r['inference_passes'] for r in report['rows']),
        'failed': sum(r['result']['status'] == 'failed' for r in report['rows'])}
    try:
        import resource
        report['peak_rss_bytes'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == 'darwin' else 1024)
    except ImportError:
        report['peak_rss_bytes'] = process.memory_info().peak_wset
    if hashlib.sha256(args.inputs.read_bytes()).hexdigest() != report['input_manifest_sha256']:
        raise ValueError('Input manifest changed during benchmark')
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print('SUMMARY', json.dumps(report['summary']), flush=True)


if __name__ == '__main__':
    main()
