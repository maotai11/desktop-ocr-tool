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
    active_record = active_started = active_durations = None
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
            profile_started = time.perf_counter()
            record = {'passed': False, 'engine_ready': False, 'iterations_completed': 0,
                      'elapsed_seconds': 0., 'checkpoints': {}, 'failures': []}
            report['profiles'][profile] = record
            durations = []
            active_record, active_started, active_durations = record, profile_started, durations

            def checkpoint():
                # Wall time includes model loading and preceding atomic writes.
                # Latency samples use only the already completed native calls.
                record['elapsed_seconds'] = time.perf_counter() - profile_started
                if durations:
                    record['latency_ms'] = {f'p{p}': float(np.percentile(durations, p)) for p in (50,95,99)}
                write_validation_report(report_path, report)

            checkpoint()
            record['before_load'] = resources()
            engine = OcrEngine(model_profile=profile)
            started = time.perf_counter(); engine.load()
            record['load_seconds'] = time.perf_counter() - started
            record['engine_ready'] = bool(engine.is_ready())
            record['identity'] = engine.model_identity
            record['models'] = engine._model_version
            checkpoint()
            for index in range(iterations):
                case, array = images[index % len(images)]
                started = time.perf_counter(); result = engine.run_ocr(array)
                durations.append((time.perf_counter() - started) * 1000)
                record['iterations_completed'] = index + 1
                if result['text'] != case['ground_truth'] or result['status'] == 'failed':
                    record['failures'].append({'iteration': index+1, 'fixture': case['id'], 'result': result})
                if index + 1 == 1 or (index + 1) % 10 == 0 or index + 1 == iterations:
                    record['checkpoints'][str(index+1)] = resources()
                    checkpoint()
            record['latency_ms'] = {f'p{p}': float(np.percentile(durations, p)) for p in (50,95,99)}
            record['passed'] = not record['failures']
            del engine
            gc.collect()
            record['after_unload'] = resources()
            checkpoint()
            active_record = active_started = active_durations = None
        report['passed'] = all(r['passed'] for r in report['profiles'].values())
    except Exception as exc:  # noqa: BLE001 - report any failed native/runtime stage
        import traceback
        report['error'] = str(exc); report['traceback'] = traceback.format_exc()
    finally:
        # Preserve the latest completed count even for cancellation. A native
        # call that has not returned never increments the progress counter.
        if active_record is not None:
            active_record['elapsed_seconds'] = time.perf_counter() - active_started
            if active_durations:
                active_record['latency_ms'] = {f'p{p}': float(np.percentile(active_durations, p)) for p in (50,95,99)}
        write_validation_report(report_path, report)
    return 0 if report['passed'] else 1
