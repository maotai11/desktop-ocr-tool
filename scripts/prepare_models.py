"""Build/development-only preparation of hash-pinned official model assets.

Never imported by the application. The delivered EXE must contain every asset.
"""
import hashlib
import json
import os
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OFFICIAL_PREFIX = 'https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.9.2/onnx/'


def prepare(root=ROOT):
    root = Path(root).resolve()
    seen = set()
    for lock in sorted((root / 'models').glob('models*.lock.json')):
        for info in json.loads(lock.read_text(encoding='utf-8')).values():
            path = (root / info['path']).resolve()
            if not path.is_relative_to(root / 'models'):
                raise ValueError('Model path escapes project')
            if path in seen:
                continue
            seen.add(path)
            if path.exists():
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                if digest != info['sha256'] or path.stat().st_size != info['size_bytes']:
                    raise ValueError(f'Existing model differs from pinned asset: {path.name}')
                continue
            source = info.get('source', '')
            if not source.startswith(OFFICIAL_PREFIX) or '..' in source:
                raise ValueError(f'Missing verified official source for {path.name}')
            expected_size = info['size_bytes']
            if type(expected_size) is not int or not 0 < expected_size <= 250_000_000:
                raise ValueError('Invalid model size limit')
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.model-', delete=False) as output:
                    temporary = Path(output.name)
                    digest = hashlib.sha256(); size = 0
                    with urllib.request.urlopen(source, timeout=120) as response:
                        while block := response.read(1024 * 1024):
                            size += len(block)
                            if size > expected_size:
                                raise ValueError('Model download exceeds pinned size')
                            digest.update(block); output.write(block)
                if size != expected_size or digest.hexdigest() != info['sha256']:
                    raise ValueError(f'Downloaded model hash/size mismatch: {path.name}')
                os.replace(temporary, path)
                print(f'Prepared {path.relative_to(root)} ({size} bytes, SHA256 verified)')
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)


if __name__ == '__main__':
    prepare()
