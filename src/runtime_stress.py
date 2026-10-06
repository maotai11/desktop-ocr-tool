"""Bounded real-model stress for the actual frozen payload, never a clean-host claim."""
import gc
import hashlib
import json
import os
import sys
import time

from .core.validation_report import executable_sha256, write_validation_report
from .core.version import APP_VERSION


def run_runtime_stress(report_path, iterations=500):
    report = {'schema': 1, 'probe': 'model_runtime_stress', 'version': APP_VERSION,
              'platform': sys.platform, 'frozen': bool(getattr(sys, 'frozen', False)),
              'executable_sha256': executable_sha256(), 'iterations_per_profile': iterations,
              'telemetry_env': os.environ.get('ORT_DISABLE_TELEMETRY'),
              'passed': False, 'clean_machine_verified': False, 'profiles': {},
              'limitations': ['Three repeated fixed fixtures; not general accuracy',
                  'RSS checkpoints do not establish absence of all leaks',
                  'Normal native completion only; no hung-inference termination proof']}
    try:
        if type(iterations) is not int or not 1 <= iterations <= 5000:
            raise ValueError('Stress iterations must be 1..5000')
        import cv2
        import numpy as np
        import psutil

        from .ocr.engine import OcrEngine
        from .ocr.model_validator import MODEL_PROFILES, model_root
        fixture_root = model_root() / 'models/validation'
        cases = json.loads((fixture_root / 'fixtures.json').read_text(encoding='utf-8'))['cases']
        images = []
        for case in cases:
            data = (fixture_root / case['file']).read_bytes()
            if hashlib.sha256(data).hexdigest() != case['image_sha256']:
                raise ValueError('Stress fixture hash mismatch')
            images.append((case, cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)))
        process = psutil.Process()

        def resources():
            memory = process.memory_info()
            return {'rss_bytes': memory.rss, 'private_bytes': getattr(memory, 'private', None),
                    'threads': process.num_threads(),
                    'handles_or_fds': process.num_handles() if sys.platform == 'win32' else process.num_fds()}

        for profile in MODEL_PROFILES:
            record = {'passed': False, 'before_load': resources(), 'checkpoints': {}, 'failures': []}
            report['profiles'][profile] = record
            engine = OcrEngine(model_profile=profile)
            started = time.perf_counter(); engine.load()
            record['load_seconds'] = time.perf_counter() - started
            record['identity'] = engine.model_identity
            record['models'] = engine._model_version
            durations = []
            for index in range(iterations):
                case, array = images[index % len(images)]
                started = time.perf_counter(); result = engine.run_ocr(array)
                durations.append((time.perf_counter() - started) * 1000)
                record['iterations_completed'] = index + 1
                if result['text'] != case['ground_truth'] or result['status'] == 'failed':
                    record['failures'].append({'iteration': index+1, 'fixture': case['id'], 'result': result})
                if index + 1 in (1, 10, 100, iterations):
                    record['checkpoints'][str(index+1)] = resources()
                    write_validation_report(report_path, report)
            record['latency_ms'] = {f'p{p}': float(np.percentile(durations, p)) for p in (50,95,99)}
            record['passed'] = not record['failures']
            del engine
            gc.collect()
            record['after_unload'] = resources()
            write_validation_report(report_path, report)
        report['passed'] = all(r['passed'] for r in report['profiles'].values())
    except Exception as exc:  # noqa: BLE001 - report any failed native/runtime stage
        import traceback
        report['error'] = str(exc); report['traceback'] = traceback.format_exc()
    write_validation_report(report_path, report)
    return 0 if report['passed'] else 1
