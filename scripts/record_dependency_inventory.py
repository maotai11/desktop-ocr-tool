"""Inventory resolved wheels; recorded hashes are not an independently trusted lock."""
import argparse
import hashlib
import importlib.metadata
import json
import re
import sys
import zipfile
from email.parser import BytesParser
from pathlib import Path, PurePosixPath


def inventory_wheels(directory):
    wheels = []
    identities = set()
    for path in sorted(Path(directory).glob('*.whl')):
        digest = hashlib.sha256()
        with path.open('rb') as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                digest.update(chunk)
        with zipfile.ZipFile(path) as archive:
            # Vendored libraries may legitimately contain their own nested
            # .dist-info/METADATA. Only the wheel's top-level distribution is
            # its identity; multiple top-level identities are still rejected.
            names = [name for name in archive.namelist()
                     if len(PurePosixPath(name).parts) == 2
                     and PurePosixPath(name).parts[0].endswith('.dist-info')
                     and PurePosixPath(name).parts[1] == 'METADATA']
            if len(names) != 1 or archive.getinfo(names[0]).file_size > 2 * 1024 * 1024:
                raise ValueError(f'Invalid wheel metadata: {path.name}')
            metadata = BytesParser().parsebytes(archive.read(names[0]))
        name, version = metadata.get('Name'), metadata.get('Version')
        if not name or not version:
            raise ValueError(f'Missing wheel identity: {path.name}')
        identity = re.sub(r'[-_.]+', '-', name).lower()
        if identity in identities:
            raise ValueError(f'Duplicate wheel distribution: {name}')
        identities.add(identity)
        wheels.append({'filename': path.name, 'name': name, 'version': version,
                       'size_bytes': path.stat().st_size, 'sha256': digest.hexdigest(),
                       'license_expression': metadata.get('License-Expression'),
                       'license_review': 'NOT_RUN'})
    if not wheels:
        raise ValueError('Wheelhouse is empty')
    return wheels


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheelhouse', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args(argv)
    report = {'schema': 1, 'status': 'RECORDED_NOT_A_TRUSTED_LOCK',
              'python': sys.version, 'platform': sys.platform,
              'wheels': inventory_wheels(args.wheelhouse),
              'installed': {d.metadata['Name']: d.version for d in importlib.metadata.distributions()},
              'license_review': 'NOT_RUN'}
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
