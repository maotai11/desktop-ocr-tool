"""Synthetic release-evidence contracts only; never native Windows proof."""
import hashlib
import json

from src.handwriting.assets import ALPHABET_SHA256, MODEL_BYTES, MODEL_SHA256
from scripts.verify_candidate_bundle import expected_model_identity


def identity():
    return {'model_sha256': MODEL_SHA256, 'model_size_bytes': MODEL_BYTES,
            'alphabet_sha256': ALPHABET_SHA256, 'label_count': 11853,
            'validation_sha256': '29e7b94bbb26b413b729110357de0d81a720418e736e06b7fac561fae923af58',
            'helper': {'schema': 1, 'protocol': 2, 'platform': 'win32', 'machine': 'AMD64',
                       'name': 'inkpick-handwriting.exe', 'crt': 'MSVC /MT Release',
                       'size_bytes': 100, 'sha256': 'e' * 64,
                       'native_imports': ['bcrypt.dll', 'kernel32.dll'],
                       'source': {'upstream_commit': '581faa8f6f15e4a7b21964be3a5ec36265c80e5b',
                                  'sha256': 'd' * 64}}}


def probe(version, executable_hash, frozen=True):
    data = identity()
    return {'schema': 1, 'probe': 'handwriting_lifecycle', 'passed': True, 'frozen': frozen,
            'platform': 'win32', 'qt_platform': 'windows', 'version': version,
            'executable_sha256': executable_hash if frozen else None,
            'clean_machine_verified': False, 'phase': 'completed',
            'helper_sha256': data['helper']['sha256'], 'model_sha256': MODEL_SHA256,
            'alphabet_sha256': ALPHABET_SHA256, 'validation_sha256': data['validation_sha256'],
            'helper_source_sha256': data['helper']['source']['sha256'], 'helper_architecture': 'AMD64', 'helper_protocol': 2,
            'queue_latest_only': True, 'clipboard_roundtrip': True, 'self_mime_preserved': True,
            'helper_stopped': True, 'job_assigned': True, 'firewall_gate_acknowledged': False,
            'accuracy_evidence': 'UPSTREAM_TRAINING_REPLAY_ONLY',
            'cases': [{'label': label, 'candidates': [label], 'rank': 1, 'passed': True}
                      for label in '一二十己已巳臺灣龜鬱鑑靈壹貳參龘']}


def image_probe(version, executable_hash, models, frozen=True):
    rec=models['v6-small']['rec']
    return dict(schema=1,probe='handwriting_image_lifecycle',version=version,frozen=frozen,
        platform='win32',qt_platform='windows',executable_sha256=executable_hash if frozen else None,
        helper_protocol=2,passed=True,inference_completed=True,worker_pid_verified=True,
        job_assigned=True,cancelled_and_reaped=True,children_reaped=True,clean_machine_verified=False,
        accuracy_evidence='UPSTREAM_TRAINING_RASTER_FUNCTIONAL_ONLY',candidates=['一'],
        model_sha256=rec['sha256'],character_sha256=rec['character_sha256'])


def extra_evidence(root, manifest):
    firewall = root / 'artifacts/firewall-validation'
    report = probe(manifest['version'], manifest['executable_sha256'])
    source = probe(manifest['version'], manifest['executable_sha256'], frozen=False)
    (root / 'source-handwriting.json').write_text(json.dumps(source), encoding='utf-8')
    report['firewall_gate_acknowledged'] = True
    raw = json.dumps(report).encode()
    (firewall / 'frozen-handwriting.json').write_bytes(raw)
    gate = {'schema': 1, 'passed': True, 'helper_outbound_blocked': True,
            'executable_outbound_blocked': True, 'clean_machine_verified': False,
            'firewall_service_running': True, 'firewall_profiles_enabled': True,
            'helper_sha256': identity()['helper']['sha256'], 'executable_sha256': manifest['executable_sha256'],
            'version': manifest['version'], 'report_sha256': hashlib.sha256(raw).hexdigest()}
    (firewall / 'handwriting-firewall-gate.json').write_text(json.dumps(gate))
    stress = {'schema': 1, 'probe': 'model_runtime_stress', 'version': manifest['version'],
              'platform': 'win32', 'frozen': True, 'passed': True,
              'executable_sha256': manifest['executable_sha256'], 'iterations_per_profile': 500,
              'telemetry_env': '1', 'clean_machine_verified': False,
              'profiles': {name: {'passed': True, 'iterations_completed': 500, 'failures': [],
                                  'identity': expected_model_identity(name, models)}
                           for name, models in manifest['model_profiles'].items()}}
    for entry in stress['profiles'].values():
        entry['identity']['recognition']['input_shape'] = [1, 3, 48, 320]
        entry['identity']['recognition']['output_shape'] = [1, 40, entry['identity']['recognition']['output_classes']]
    (firewall / 'frozen-runtime-stress.json').write_text(json.dumps(stress))
