"""Validate the exact three bundled ONNX assets before creating sessions."""
import hashlib
import json
import sys
from pathlib import Path


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def model_root():
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[2]))


MODEL_PROFILES = {'v6-small': 'models.lock.json', 'v6-medium': 'models-v6-medium.lock.json'}
DEFAULT_MODEL_PROFILE = 'v6-small'


def verified_model_manifest(root=None, profile=DEFAULT_MODEL_PROFILE):
    root = Path(root or model_root()).resolve()
    if profile not in MODEL_PROFILES:
        raise ValueError(f'Unknown bundled OCR profile: {profile}')
    lock = json.loads((root / 'models' / MODEL_PROFILES[profile]).read_text(encoding='utf-8'))
    if set(lock) != {'det', 'rec', 'cls'}:
        raise ValueError('Model manifest must contain det, rec and cls')
    for key, info in lock.items():
        path = (root / info['path']).resolve()
        if not path.is_relative_to(root / 'models'):
            raise ValueError('Model path escapes bundle')
        if not info.get('sha256') or sha256_file(path) != info['sha256']:
            raise ValueError(f'Model SHA256 mismatch: {key}')
        if path.stat().st_size != info.get('size_bytes'):
            raise ValueError(f'Model size mismatch: {key}')
        info['absolute_path'] = str(path)
    return lock


def verify_recognizer_identity(recognizer, info):
    """Bind the actual loaded session, ordered CTC alphabet and CPU provider."""
    session = recognizer.session.session
    metadata = session.get_modelmeta().custom_metadata_map
    character = metadata.get('character', '')
    if (not character or hashlib.sha256(character.encode('utf-8')).hexdigest()
            != info['character_sha256']):
        raise ValueError('Recognition character metadata mismatch')
    symbols = character.splitlines()
    expected = ['blank', *symbols, ' ']
    decoder_hash = hashlib.sha256(json.dumps(expected, ensure_ascii=False,
        separators=(',', ':')).encode('utf-8')).hexdigest()
    if (len(symbols) != info['character_count']
            or list(recognizer.postprocess_op.character) != expected
            or session.get_outputs()[0].shape[-1] != info['output_classes']
            or len(expected) != info['output_classes']
            or decoder_hash != info['decoder_sha256']):
        raise ValueError('Recognition CTC dictionary/output mismatch')
    providers = session.get_providers()
    if providers != ['CPUExecutionProvider']:
        raise ValueError(f'Unexpected recognition provider: {providers}')
    inputs, outputs = session.get_inputs(), session.get_outputs()
    if (len(inputs) != 1 or inputs[0].type != 'tensor(float)'
            or len(inputs[0].shape) != 4 or inputs[0].shape[1] != 3
            or inputs[0].shape[2] != 48
            or len(outputs) != 1 or len(outputs[0].shape) != 3
            or outputs[0].type != 'tensor(float)'):
        raise ValueError('Recognition model tensor contract mismatch')
    return {'character_sha256': info['character_sha256'],
            'character_count': len(symbols), 'output_classes': len(expected),
            'providers': providers, 'recognition_shape': [3, 48, 320],
            'input_type': inputs[0].type, 'input_shape': inputs[0].shape,
            'output_shape': outputs[0].shape, 'blank_index': 0,
            'space_index': len(expected) - 1,
            'decoder_sha256': decoder_hash}


def validate_models(project_root):
    try:
        verified_model_manifest(project_root)
        return True, []
    except (OSError, ValueError, KeyError) as exc:
        return False, [str(exc)]
