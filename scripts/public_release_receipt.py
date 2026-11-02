"""Same-run transfer of four already verified public assets between CI jobs.

Trust boundary: only Actions' same-run artifact download is accepted by the
workflow. This receipt binds bytes and provenance; it is not a signature and
must never authorize publication of arbitrary user-uploaded archives.
"""
from pathlib import Path

from scripts import publish_prerelease as p
from scripts.prepare_public_validation import strict_json

RECEIPT = 'public-release-receipt.json'
TYPES = {'exe': 'application/octet-stream', 'zip': 'application/zip', 'txt': 'text/plain'}


def expected_assets():
    return {p.EXE_NAME: TYPES['exe'], p.ZIP_NAME: TYPES['zip'],
            p.CHECKSUM_NAME: TYPES['txt'], p.METADATA_NAME: TYPES['zip']}


def write_receipt(prepared):
    root = prepared.assets[0].path.parent
    expected = expected_assets()
    p.require({a.name for a in prepared.assets} == set(expected), 'Unexpected public asset set')
    for a in prepared.assets:
        a.recheck()
        p.require(a.path.parent == root and a.content_type == expected[a.name], 'Unexpected asset location/type')
    value = {'schema': 1, 'repository': p.REPOSITORY, 'version': p.VERSION,
             'source_commit': prepared.context.source_commit, 'run_id': prepared.context.run_id,
             'run_attempt': prepared.context.run_attempt,
             'assets': {a.name: {'bytes': a.size, 'sha256': a.sha256} for a in prepared.assets}}
    with (root / RECEIPT).open('xb') as f:
        f.write(p.canonical_json(value))


def load_prepared(root, context):
    root = Path(root)
    p.require(root.is_dir() and not root.is_symlink()
              and not any(x.is_symlink() for x in root.parents), 'Redirected public input')
    expected = expected_assets()
    p.require({x.name for x in root.iterdir()} == {*expected, RECEIPT}, 'Unexpected public input files')
    receipt = root / RECEIPT
    p.require(receipt.is_file() and not receipt.is_symlink() and receipt.stat().st_size <= 16384,
              'Missing or oversized public receipt')
    try:
        value = strict_json(receipt.read_bytes())
    except (ValueError, TypeError, RecursionError):
        raise p.PublicationError('Invalid public receipt') from None
    p.require(isinstance(value, dict) and set(value) == {'schema','repository','version','source_commit','run_id','run_attempt','assets'}, 'Invalid receipt fields')
    p.require(type(value['schema']) is int and value['schema'] == 1
              and value['repository'] == p.REPOSITORY and value['version'] == p.VERSION
              and value['source_commit'] == context.source_commit and value['run_id'] == context.run_id
              and value['run_attempt'] == context.run_attempt, 'Public receipt belongs to another build')
    records = value['assets']
    p.require(isinstance(records, dict) and set(records) == set(expected), 'Incomplete asset receipt')
    assets = []
    for name, kind in expected.items():
        record = records[name]
        maximum = 4096 if name == p.CHECKSUM_NAME else (p.MAX_JSON_BYTES if name == p.METADATA_NAME else 800*1024*1024)
        p.require(isinstance(record, dict) and set(record) == {'bytes','sha256'}
                  and type(record['bytes']) is int and 0 < record['bytes'] <= maximum
                  and isinstance(record['sha256'], str) and p.SHA256_RE.fullmatch(record['sha256']) is not None,
                  'Invalid asset record')
        path = root / name
        p.require(path.is_file() and not path.is_symlink() and path.stat().st_size == record['bytes'], 'Missing/changed public asset')
        asset = p.Asset.from_file(path, kind)
        p.require(asset.sha256 == record['sha256'], 'Changed public asset hash')
        assets.append(asset)
    by_name = {a.name:a for a in assets}
    expected_sums = ''.join(f'{by_name[name].sha256}  {name}\n' for name in (p.EXE_NAME,p.ZIP_NAME,p.METADATA_NAME))
    p.require((root / p.CHECKSUM_NAME).read_bytes() == expected_sums.encode('ascii'), 'Public checksum mismatch')
    return p.PreparedRelease(context, tuple(assets), p.release_notes(context, tuple(assets)))
