"""Explicit model/helper/icon bundle identity and exact embedded-byte checks."""
import hashlib
import json
from pathlib import Path

from src.core.branding import ICON_SIZES
from src.handwriting.assets import ALPHABET_SHA256, MODEL_BYTES, MODEL_SHA256

VALIDATION_SHA256 = '29e7b94bbb26b413b729110357de0d81a720418e736e06b7fac561fae923af58'
SOURCE_SHA256 = '85ab37c56bcb14a54a7ae106f2504b6505cf6c9917ca8c636312153fd9d00e53'
LICENSE_SHA256 = 'a190dc9c8043755d90f8b0a75fa66b9e42d4af4c980bf5ddc633f0124db3cee7'
MODEL_FILES = ('models/handwriting/handwriting-zh_TW.model', 'models/handwriting/alphabet.txt',
               'models/handwriting/manifest.json', 'models/handwriting/validation.json')
HELPER_LOCK = 'native/handwriting/runtime/helper.lock.json'
HELPER_PATH = 'native/handwriting/runtime/inkpick-handwriting.exe'
BRAND_FILES = ('assets/branding/manifest.json', 'assets/branding/inkpick.ico',
               *(f'assets/branding/inkpick-{size}.png' for size in ICON_SIZES))


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''):
            digest.update(block)
    return digest.hexdigest()


def identity(root):
    root = Path(root)
    manifest = json.loads((root / 'models/handwriting/manifest.json').read_text(encoding='utf-8'))
    expected = {'model_sha256': MODEL_SHA256, 'model_size_bytes': MODEL_BYTES,
                'alphabet_sha256': ALPHABET_SHA256, 'validation_sha256': VALIDATION_SHA256,
                'source_sha256': SOURCE_SHA256, 'source_size_bytes': 24523156,
                'license_sha256': LICENSE_SHA256, 'label_count': 11853, 'license': 'LGPL-2.1'}
    if any(manifest.get(key) != value for key, value in expected.items()):
        raise ValueError('Handwriting model manifest identity mismatch')
    for name, digest in zip(MODEL_FILES, (MODEL_SHA256, ALPHABET_SHA256, None, VALIDATION_SHA256)):
        if digest and sha256(root / name) != digest:
            raise ValueError('Handwriting bundle asset hash mismatch: ' + name)
    if (root / MODEL_FILES[0]).stat().st_size != MODEL_BYTES:
        raise ValueError('Handwriting model size mismatch')
    for name, digest in (('handwriting-zh_TW.xml', SOURCE_SHA256), ('COPYING-LGPL-2.1.txt', LICENSE_SHA256)):
        if sha256(root / 'models/handwriting/source' / name) != digest:
            raise ValueError('Corresponding handwriting source/license hash mismatch')
    helper = json.loads((root / HELPER_LOCK).read_text(encoding='utf-8'))
    helper_name = HELPER_PATH if helper.get('platform') == 'win32' else HELPER_PATH.removesuffix('.exe')
    if (helper.get('schema') != 1 or helper.get('protocol') != 1
            or helper.get('sha256') != sha256(root / helper_name)
            or helper.get('size_bytes') != (root / helper_name).stat().st_size):
        raise ValueError('Handwriting helper hash/size mismatch')
    from scripts.build_handwriting_helper import source_identity
    if helper.get('source') != source_identity(root):
        raise ValueError('Handwriting helper does not match current source')
    branding = json.loads((root / 'assets/branding/manifest.json').read_text(encoding='utf-8'))
    for name, info in branding['files'].items():
        path = root / 'assets/branding' / name
        if sha256(path) != info['sha256'] or path.stat().st_size != info['size_bytes']:
            raise ValueError('Brand asset identity mismatch')
    return dict(manifest, helper=helper, branding=branding)


def data_files(root):
    root = Path(root)
    return [(root / name, str(Path(name).parent)) for name in (*MODEL_FILES, HELPER_LOCK, *BRAND_FILES)]


def inspect_payload(reader, names, expected, source_files=None):
    def extract(name):
        if name not in names:
            raise ValueError('Frozen handwriting/brand asset absent: ' + name)
        return reader.extract(names[name])
    if (expected.get('model_sha256') != MODEL_SHA256 or expected.get('model_size_bytes') != MODEL_BYTES
            or expected.get('alphabet_sha256') != ALPHABET_SHA256
            or expected.get('validation_sha256') != VALIDATION_SHA256):
        raise ValueError('Frozen handwriting manifest contract mismatch')
    model = extract(MODEL_FILES[0])
    if len(model) != MODEL_BYTES or hashlib.sha256(model).hexdigest() != MODEL_SHA256:
        raise ValueError('Frozen handwriting model hash/size mismatch')
    for path, digest in ((MODEL_FILES[1], ALPHABET_SHA256), (MODEL_FILES[3], VALIDATION_SHA256)):
        if hashlib.sha256(extract(path)).hexdigest() != digest:
            raise ValueError('Frozen handwriting asset hash mismatch')
    manifest_bytes = extract(MODEL_FILES[2])
    manifest = json.loads(manifest_bytes.decode('utf-8'))
    if manifest != {key: value for key, value in expected.items() if key not in ('helper', 'branding')}:
        raise ValueError('Frozen handwriting manifest mismatch')
    helper_bytes = extract(HELPER_PATH)
    helper = expected['helper']
    if (helper.get('platform') != 'win32' or helper.get('machine') != 'AMD64'
            or helper.get('crt') != 'MSVC /MT Release' or helper.get('protocol') != 1
            or helper.get('name') != 'inkpick-handwriting.exe'
            or len(helper_bytes) != helper['size_bytes']
            or hashlib.sha256(helper_bytes).hexdigest() != helper['sha256']
            or json.loads(extract(HELPER_LOCK).decode('utf-8')) != helper):
        raise ValueError('Frozen handwriting helper identity mismatch')
    branding = json.loads(extract('assets/branding/manifest.json').decode('utf-8'))
    if branding != expected['branding']:
        raise ValueError('Frozen branding manifest mismatch')
    for path in BRAND_FILES[1:]:
        data = extract(path)
        info = branding['files'][Path(path).name]
        if len(data) != info['size_bytes'] or hashlib.sha256(data).hexdigest() != info['sha256']:
            raise ValueError('Frozen branding asset hash/size mismatch')
    for path in (MODEL_FILES[2], MODEL_FILES[3], *BRAND_FILES):
        if source_files is not None and source_files.get(path) != {'sha256': hashlib.sha256(extract(path)).hexdigest()}:
            raise ValueError('Frozen handwriting/brand source hash mismatch')
    return {'verified': True, 'model_sha256': MODEL_SHA256, 'helper_sha256': helper['sha256'],
            'helper_source_sha256': helper['source']['sha256'], 'branding_verified': True,
            'native_imports': helper['native_imports']}
