"""Synthetic offline profile bytes and schema-2 probes; never runtime evidence."""
import copy
import hashlib
import json

from src.ocr.model_validator import DEFAULT_MODEL_PROFILE, MODEL_PROFILES

PROFILE_LOCKS = {profile: 'models/' + name for profile, name in MODEL_PROFILES.items()}


def fixture_validation():
    cases, assets = [], {}
    for name, text in [('traditional', '臺灣稅務申報'), ('amount', 'NT$ 1,234.00'), ('date_api', '2026/10/06 API')]:
        data = (name + ' fake image bytes').encode()
        cases.append({'id': name, 'file': name + '.png', 'ground_truth': text,
                      'image_sha256': hashlib.sha256(data).hexdigest(),
                      'expected': {profile: text for profile in PROFILE_LOCKS}})
        assets['models/validation/' + name + '.png'] = data
    fixtures = {'cases': cases}
    assets['models/validation/fixtures.json'] = json.dumps(fixtures).encode()
    return fixtures, assets


def fixture_profiles():
    assets = {'models/det.onnx': b'det', 'models/cls.onnx': b'cls',
              'models/rec-v6-small.onnx': b'rec-v6-small', 'models/rec-v6-medium.onnx': b'rec-v6-medium'}
    profiles = {}
    for profile in MODEL_PROFILES:
        version = profile
        models = {}
        for role in ('det', 'rec', 'cls'):
            path = f'models/rec-{version}.onnx' if role == 'rec' else f'models/{role}.onnx'
            models[role] = {'path': path, 'sha256': hashlib.sha256(assets[path]).hexdigest(),
                            'size_bytes': len(assets[path]),
                            'version': 'pp-ocr' + version if role == 'rec' else 'pp-ocrv6'}
        models['rec'].update(character_count=3, output_classes=5,
                             character_sha256=hashlib.sha256(version.encode()).hexdigest(),
                             decoder_sha256=hashlib.sha256((version + '-decoder').encode()).hexdigest())
        profiles[profile] = models
        assets[PROFILE_LOCKS[profile]] = json.dumps(models).encode()
    assets.update(fixture_validation()[1])
    assets['rapidocr_onnxruntime/config.yaml'] = b'fake-runtime-config'
    assets['rapidocr_onnxruntime-1.4.4.dist-info/METADATA'] = b'Name: rapidocr-onnxruntime\nVersion: 1.4.4\n'
    return profiles, assets


def profile_probes(profiles, fixtures=None):
    fixtures = fixtures or fixture_validation()[0]
    probes = {}
    for profile, models in profiles.items():
        rec = models['rec']
        identity = {
            'profile': profile, 'runtime': 'rapidocr_onnxruntime', 'runtime_version': '1.4.4',
            'recognition_batch': 1, 'max_recognition_width': 4096,
            'recognition': {
                'character_sha256': rec['character_sha256'], 'character_count': rec['character_count'],
                'output_classes': rec['output_classes'], 'decoder_sha256': rec['decoder_sha256'],
                'providers': ['CPUExecutionProvider'], 'recognition_shape': [3, 48, 320],
                'input_type': 'tensor(float)', 'input_shape': ['batch', 3, 48, 'width'],
                'output_shape': ['batch', 'sequence', rec['output_classes']],
                'blank_index': 0, 'space_index': rec['output_classes'] - 1,
            },
        }
        ocr = {'text': 'Desktop OCR 12345', 'status': 'done', 'engine': 'rapidocr_onnxruntime',
               'model_version': profile + ';' + ';'.join(
                   f'{role}:{info["version"]}:{info["sha256"]}' for role, info in sorted(models.items()))}
        probes[profile] = {'models': copy.deepcopy(models), 'passed': True,
                           'model_identity': identity, 'ocr_result': ocr,
                           'fixtures': [{'id': case['id'], 'image_sha256': case['image_sha256'],
                                         'ground_truth': case['ground_truth'],
                                         'result': dict(ocr, text=case['ground_truth'])}
                                        for case in fixtures['cases']]}
    return probes


def profile_report(profiles, fixtures=None):
    probes = profile_probes(profiles, fixtures)
    return {'models': copy.deepcopy(profiles[DEFAULT_MODEL_PROFILE]), 'model_profiles': probes,
            'ocr_result': copy.deepcopy(probes[DEFAULT_MODEL_PROFILE]['ocr_result'])}
