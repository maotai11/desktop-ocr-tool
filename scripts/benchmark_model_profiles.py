"""Compare fixed raster/GT inputs with every bundled profile, preserving spaces.

Run one process per profile for meaningful peak-RSS comparisons. Inputs are public
synthetic fixtures only; this is not a held-out real-world accuracy certificate.
"""
import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('ORT_DISABLE_TELEMETRY', '1')

import cv2
import numpy as np
import psutil

from scripts.replay_spacing import edits
from src.ocr.engine import OcrEngine
from src.ocr.model_validator import MODEL_PROFILES


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--profile', choices=MODEL_PROFILES, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    inputs = json.loads(args.inputs.read_text(encoding='utf-8'))['inputs']
    engine = OcrEngine(model_profile=args.profile)
    start = time.perf_counter(); engine.load()
    report = {'profile': args.profile, 'model_identity': engine.model_identity,
              'load_ms': (time.perf_counter() - start) * 1000,
              'input_manifest_sha256': hashlib.sha256(args.inputs.read_bytes()).hexdigest(),
              'rows': [], 'limitations': ['Synthetic images, not user holdout',
                  'Single timing observations, not p95/p99', 'Literal spacing retained']}
    process = psutil.Process()
    for item in inputs:
        path = args.inputs.parent / item['file']
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != item['image_sha256']:
            raise ValueError(f'Changed input: {item["id"]}')
        image = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
        start = time.perf_counter(); result = engine.run_ocr(image)
        row = {'id': item['id'], 'image_sha256': digest, 'ground_truth': item['ground_truth'],
               'result': result, 'metrics': edits(item['ground_truth'], result['text']),
               'literal_exact': result['text'] == item['ground_truth'],
               'milliseconds': (time.perf_counter() - start) * 1000,
               'rss_after_bytes': process.memory_info().rss,
               'recognition_crops': engine._engine.text_rec.total_crops,
               'recognition_normalized_width': engine._engine.text_rec.total_width}
        report['rows'].append(row)
        print(item['id'], result['status'], repr(result['text']), row['metrics'], flush=True)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    report['summary'] = {'literal_exact': sum(r['literal_exact'] for r in report['rows']),
        'cases': len(inputs), 'errors': sum(r['metrics']['errors'] for r in report['rows']),
        'characters': sum(r['metrics']['N'] for r in report['rows']),
        'whitespace_edits': sum(r['metrics']['whitespace_edits'] for r in report['rows'])}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
