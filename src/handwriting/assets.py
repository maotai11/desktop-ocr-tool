"""Resolve only bundled, locked resources. There is no runtime download path."""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sys

from ..core.branding import resource_root

MODEL_SHA256 = 'cd47f16b64e7ecaa4c2813d0f98231dfea7b411f48be1ead5c9b2eeb19330b11'
MODEL_BYTES = 52210768
ALPHABET_SHA256 = 'f2d9d1e2a05ed4df86cceb32d4a45ab4043e9ca6f74aed4e4cf8d291ce084105'


@dataclass(frozen=True)
class HelperAssets:
    program: Path
    arguments: tuple
    alphabet: frozenset


def _file(path, maximum):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > maximum:
        raise ValueError('Bundled handwriting resource missing or redirected')
    return path.read_bytes()


def verified_assets(root=None):
    root = Path(root or resource_root()).resolve()
    directory = root / 'models/handwriting'
    alphabet_bytes = _file(directory / 'alphabet.txt', 100000)
    if hashlib.sha256(alphabet_bytes).hexdigest() != ALPHABET_SHA256:
        raise ValueError('Handwriting alphabet hash mismatch')
    alphabet = frozenset(alphabet_bytes.decode('utf-8', errors='strict'))
    if len(alphabet) != 11853:
        raise ValueError('Handwriting alphabet size mismatch')
    model = directory / 'handwriting-zh_TW.model'
    if model.is_symlink() or not model.is_file() or model.stat().st_size != MODEL_BYTES:
        raise ValueError('Handwriting model missing or redirected')
    # The helper hashes and validates these exact owned bytes before native open.
    name = 'inkpick-handwriting.exe' if sys.platform == 'win32' else 'inkpick-handwriting'
    runtime = root / 'native/handwriting/runtime'
    lock = json.loads(_file(runtime / 'helper.lock.json', 16384).decode('utf-8'))
    program = runtime / name
    data = _file(program, 10000000)
    if (lock.get('schema') != 1 or lock.get('platform') != sys.platform
            or lock.get('protocol') != 1 or lock.get('name') != name
            or lock.get('size_bytes') != len(data)
            or lock.get('sha256') != hashlib.sha256(data).hexdigest()):
        raise ValueError('Handwriting helper identity mismatch')
    return HelperAssets(program.resolve(), (str(model.resolve()),), alphabet)
