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


def verified_model_manifest(root=None):
    root = Path(root or model_root()).resolve()
    lock = json.loads((root / 'models/models.lock.json').read_text(encoding='utf-8'))
    if set(lock) != {'det', 'rec', 'cls'}:
        raise ValueError('Model manifest must contain det, rec and cls')
    for key, info in lock.items():
        path = (root / info['path']).resolve()
        if not path.is_relative_to(root / 'models'):
            raise ValueError('Model path escapes bundle')
        if not info.get('sha256') or sha256_file(path) != info['sha256']:
            raise ValueError(f'Model SHA256 mismatch: {key}')
        info['absolute_path'] = str(path)
    return lock


def validate_models(project_root):
    try:
        verified_model_manifest(project_root)
        return True, []
    except (OSError, ValueError, KeyError) as exc:
        return False, [str(exc)]
