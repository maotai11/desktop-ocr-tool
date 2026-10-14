"""Build/development-only official model preparation; never used at runtime."""
import hashlib
import io
import json
from pathlib import Path
import struct
import sys
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SOURCE = 'https://github.com/tegaki/tegaki/releases/download/v0.3/tegaki-zinnia-traditional-chinese-0.3.zip'
ARCHIVE_BYTES = 36773128
ARCHIVE_SHA256 = 'f41032e67a4eff056813d243eabc32ea07eea404c714bdb5882a5b0fcda51690'
MODEL_BYTES = 52210768
MODEL_SHA256 = 'cd47f16b64e7ecaa4c2813d0f98231dfea7b411f48be1ead5c9b2eeb19330b11'


def model_alphabet(model):
    if len(model) != MODEL_BYTES or hashlib.sha256(model).hexdigest() != MODEL_SHA256:
        raise ValueError('Official model hash/size mismatch')
    if struct.unpack_from('<III', model) != (MODEL_BYTES ^ 0x0ef71821, 1, 11853):
        raise ValueError('Official model header mismatch')
    offset = 12
    labels = set()
    for _ in range(11853):
        if offset + 20 > len(model):
            raise ValueError('Model label bounds')
        slot = model[offset:offset + 16]
        if b'\0' not in slot:
            raise ValueError('Model label termination')
        label = slot.split(b'\0', 1)[0].decode('utf-8', errors='strict')
        if len(label) != 1 or label in labels or not label.isprintable():
            raise ValueError('Model alphabet mismatch')
        labels.add(label)
        offset += 20
        while True:
            if offset + 8 > len(model):
                raise ValueError('Model feature bounds')
            index, = struct.unpack_from('<i', model, offset)
            offset += 8
            if index == -1:
                break
    if offset != len(model):
        raise ValueError('Model trailing bytes')
    return ''.join(sorted(labels))


def prepare(root=ROOT):
    directory = Path(root) / 'models/handwriting'
    directory.mkdir(parents=True, exist_ok=True)
    existing = directory / 'handwriting-zh_TW.model'
    if existing.exists():
        model_alphabet(existing.read_bytes())
        prepare_fixtures(root)
        return
    with urllib.request.urlopen(SOURCE, timeout=120) as response:
        archive_bytes = response.read(ARCHIVE_BYTES + 1)
    if (len(archive_bytes) != ARCHIVE_BYTES or
            hashlib.sha256(archive_bytes).hexdigest() != ARCHIVE_SHA256):
        raise ValueError('Official model archive hash/size mismatch')
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        prefix = 'tegaki-zinnia-traditional-chinese-0.3/'
        names = set(archive.namelist())
        def member(suffix, limit):
            name = prefix + suffix
            if name not in names or archive.getinfo(name).file_size > limit:
                raise ValueError('Missing/oversized official archive member')
            return archive.read(name)
        model = member('handwriting-zh_TW.model', MODEL_BYTES)
        alphabet = model_alphabet(model).encode('utf-8')
        # Source and license accompany the portable distribution outside the EXE.
        source = member('handwriting-zh_TW.xml', 25000000)
        license_text = member('COPYING', 50000)
        source_dir = directory / 'source'
        source_dir.mkdir(exist_ok=True)
        (source_dir / 'handwriting-zh_TW.xml').write_bytes(source)
        (source_dir / 'COPYING-LGPL-2.1.txt').write_bytes(license_text)
    existing.write_bytes(model)
    (directory / 'alphabet.txt').write_bytes(alphabet)
    manifest = {'schema': 1, 'source': SOURCE, 'archive_sha256': ARCHIVE_SHA256,
                'model_sha256': MODEL_SHA256, 'model_size_bytes': MODEL_BYTES,
                'label_count': 11853, 'license': 'LGPL-2.1',
                'alphabet_sha256': hashlib.sha256(alphabet).hexdigest(),
                'source_sha256': hashlib.sha256(source).hexdigest(),
                'source_size_bytes': len(source),
                'license_sha256': hashlib.sha256(license_text).hexdigest()}
    (directory / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    prepare_fixtures(root)
    print('Prepared exact official Traditional Chinese model and corresponding source')


def prepare_fixtures(root):
    # Pin a small subset of model-source traces for frozen smoke, not accuracy.
    from scripts.probe_handwriting_helper import replays
    directory = Path(root) / 'models/handwriting'
    cases = [{'label': label, 'strokes': strokes} for label, strokes in replays(Path(root))]
    data = json.dumps({'schema': 1, 'evidence': 'UPSTREAM_TRAINING_REPLAY_ONLY', 'cases': cases},
                      ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    (directory / 'validation.json').write_bytes(data)
    manifest = json.loads((directory / 'manifest.json').read_text())
    manifest['validation_sha256'] = hashlib.sha256(data).hexdigest()
    (directory / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    prepare()
