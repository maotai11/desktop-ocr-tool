"""Prepare a public portable ZIP from explicit, independently approved hashes.

No network, credentials, GitHub writes, extraction, wildcard copying or release
approval. This staging primitive must be supplied a reviewed manifest by the
release coordinator; an archive's own filenames/hashes are not that approval.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import zipfile

MAX_ARCHIVE = 800 * 1024 * 1024
MAX_TOTAL = 600 * 1024 * 1024
MAX_EXE = 512 * 1024 * 1024
MAX_SOURCE = 32 * 1024 * 1024
MAX_MEMBERS = 4096
HASH = re.compile(r'[0-9a-f]{64}')
NAME = re.compile(r'[A-Za-z0-9_-][A-Za-z0-9_.-]*')
PUBLIC_MANIFEST = 'PUBLIC_MANIFEST.json'
BLOCKED = {'BUILD_MANIFEST.json', 'SOURCE_MANIFEST.json', 'dependency-inventory.json',
           'candidate-validation', 'docs', 'artifacts', 'Validate-Candidate.ps1'}


class BundleError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise BundleError(message)


def safe_name(name):
    require(type(name) is str and 0 < len(name) <= 240, 'Invalid member name')
    parts = name.split('/')
    require(all(NAME.fullmatch(p) is not None and p not in ('.', '..')
                and not p.endswith('.') for p in parts), 'Unsafe member path')
    # Windows device names are invalid even with an extension.
    devices = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)),
               *(f'LPT{i}' for i in range(1, 10))}
    require(all(p.split('.')[0].upper() not in devices for p in parts), 'Windows device path')
    return name


def file_hash(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def no_links(path):
    path = Path(path).absolute()
    for part in (path, *path.parents):
        require(not part.is_symlink() and not (hasattr(part, 'is_junction') and part.is_junction()),
                'Redirected filesystem path')
    return path


def prepare_public_bundle(candidate, output, *, prefix, approved, executable,
                          candidate_sha256, executable_sha256, source_commit):
    """Copy only approved members, preserving exact EXE/source bytes.

    `approved` maps relative public filenames to independently established SHA256.
    All private evidence remains in the original ZIP. The returned manifest binds
    the old candidate and new public ZIP separately; no identical-ZIP claim.
    """
    candidate, output = no_links(candidate), no_links(output)
    require(candidate.is_file() and candidate.stat().st_size <= MAX_ARCHIVE, 'Invalid candidate')
    require(output.parent.is_dir() and not output.exists(), 'Output must be fresh')
    require(type(prefix) is str and prefix.endswith('/'), 'Invalid archive prefix')
    safe_name(prefix[:-1])
    safe_name(executable)
    require(executable.endswith('.exe') and '/' not in executable, 'Invalid executable name')
    require(type(approved) is dict and 0 < len(approved) <= MAX_MEMBERS, 'Invalid approved manifest')
    approved = dict(approved)
    for name, digest in approved.items():
        safe_name(name)
        require(type(digest) is str and HASH.fullmatch(digest), 'Invalid approved digest')
        require(name.casefold() != PUBLIC_MANIFEST.casefold()
                and not any(p.casefold() in {v.casefold() for v in BLOCKED} for p in name.split('/')),
                'Private report cannot be in a public manifest')
    require(type(source_commit) is str and re.fullmatch('[0-9a-f]{40}', source_commit), 'Invalid commit')
    require(type(candidate_sha256) is str and HASH.fullmatch(candidate_sha256), 'Invalid candidate hash')
    require(type(executable_sha256) is str and HASH.fullmatch(executable_sha256), 'Invalid EXE hash')
    require(approved.get(executable) == executable_sha256, 'EXE missing from approved manifest')
    require(file_hash(candidate) == candidate_sha256, 'Candidate bytes changed')
    temporary = None
    try:
        with zipfile.ZipFile(candidate) as incoming:
            infos = incoming.infolist()
            require(len(infos) <= MAX_MEMBERS, 'Too many archive members')
            found, folded = {}, set()
            for info in infos:
                safe_name(info.filename.rstrip('/'))
                require(info.filename.startswith(prefix), 'Unexpected archive root')
                require(not info.flag_bits & 1, 'Encrypted archive member')
                kind = stat.S_IFMT(info.external_attr >> 16)
                require(kind in (0, stat.S_IFREG, stat.S_IFDIR), 'Special archive member')
                key = info.filename.rstrip('/').casefold()
                require(key not in folded, 'Duplicate or case-colliding archive member')
                folded.add(key)
                if not info.is_dir():
                    relative = info.filename[len(prefix):]
                    found[relative] = info
            require(all(name in found for name in approved), 'Missing approved member')
            total = 0
            for name in approved:
                size = found[name].file_size
                require(0 <= size <= (MAX_EXE if name == executable else MAX_SOURCE), 'Oversized member')
                total += size
            require(total <= MAX_TOTAL, 'Public bundle exceeds size bound')
            fd, temporary = tempfile.mkstemp(prefix='.inkpick-public-', suffix='.zip', dir=output.parent)
            os.close(fd)
            with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED) as outgoing:
                for name in sorted(approved):
                    entry = zipfile.ZipInfo(prefix + name, date_time=(1980, 1, 1, 0, 0, 0))
                    entry.compress_type = zipfile.ZIP_DEFLATED
                    entry.create_system = 3
                    entry.external_attr = 0o100644 << 16
                    digest, count = hashlib.sha256(), 0
                    with incoming.open(found[name]) as src, outgoing.open(entry, 'w') as dst:
                        for block in iter(lambda: src.read(1024 * 1024), b''):
                            count += len(block)
                            require(count <= found[name].file_size, 'Member grew while copying')
                            digest.update(block)
                            dst.write(block)
                    require(count == found[name].file_size and digest.hexdigest() == approved[name],
                            'Approved member content mismatch')
                manifest = {'schema': 1, 'source_commit': source_commit,
                            'original_candidate_sha256': candidate_sha256,
                            'executable_sha256': executable_sha256,
                            'files': approved, 'clean_machine_verified': False}
                entry = zipfile.ZipInfo(prefix + PUBLIC_MANIFEST, date_time=(1980, 1, 1, 0, 0, 0))
                entry.compress_type = zipfile.ZIP_DEFLATED
                entry.create_system = 3
                entry.external_attr = 0o100644 << 16
                outgoing.writestr(entry, (json.dumps(manifest, sort_keys=True, indent=2) + '\n').encode())
        require(file_hash(candidate) == candidate_sha256, 'Candidate changed during preparation')
        result = {'source_commit': source_commit, 'original_candidate_sha256': candidate_sha256,
                  'public_bundle_sha256': file_hash(Path(temporary)),
                  'public_bundle_bytes': Path(temporary).stat().st_size,
                  'executable_sha256': executable_sha256, 'copied_file_count': len(approved)}
        # Same-filesystem hard link is exclusive: never overwrite an existing
        # output that appeared after initial checks. No unsafe replace fallback.
        os.link(temporary, output)
        return result
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)


def approved_release_files(*, executable, executable_sha256, identity, source_files):
    """Select public bytes from already verified build/source evidence.

    This does not authorize arbitrary source paths or trust ZIP listings. Callers
    must first verify the candidate, and recheck corresponding sources in the
    prepared public ZIP. New documentation requires an explicit code review.
    """
    from scripts.handwriting_bundle import SOURCE_SHA256, LICENSE_SHA256
    safe_name(executable)
    require(executable.endswith('.exe') and '/' not in executable, 'Invalid executable name')
    require(type(executable_sha256) is str and HASH.fullmatch(executable_sha256), 'Invalid EXE hash')
    require(type(source_files) is dict and type(identity) is dict, 'Invalid verified source evidence')
    files = identity.get('helper', {}).get('source', {}).get('files')
    require(type(files) is dict and 0 < len(files) <= MAX_MEMBERS - 10, 'Missing native source inventory')
    approved = {executable: executable_sha256,
                'handwriting-source/tegaki-zh_TW/handwriting-zh_TW.xml': SOURCE_SHA256,
                'handwriting-source/tegaki-zh_TW/COPYING-LGPL-2.1.txt': LICENSE_SHA256}
    mapping = {
        'THIRD_PARTY_NOTICES.md': 'THIRD_PARTY_NOTICES.md',
        'LICENSE-APACHE-2.0.txt': 'LICENSE-APACHE-2.0.txt',
        'handwriting-source/MODEL_MANIFEST.json': 'models/handwriting/manifest.json',
        'handwriting-source/zinnia/UPSTREAM_MANIFEST.json': 'native/handwriting/UPSTREAM_MANIFEST.json',
        'handwriting-source/zinnia/patches/0001-bound-vertex-tree.patch':
            'native/handwriting/patches/0001-bound-vertex-tree.patch',
    }
    # Keep this closed: no newly introduced native source path is automatically
    # promoted into a public release just because it appears in build metadata.
    native = {'helper.cpp', 'model_and_strokes.h', 'CMakeLists.txt'}
    for name, digest in files.items():
        safe_name(name)
        require(name in native or name.startswith('vendor/zinnia/'), 'Unapproved native source location')
        require(type(digest) is str and HASH.fullmatch(digest), 'Invalid native source hash')
        destination = 'handwriting-source/zinnia/' + name
        require(destination not in mapping, 'Duplicate approved source destination')
        mapping[destination] = 'native/handwriting/' + name
        require(source_files.get(mapping[destination]) == {'sha256': digest}, 'Native source differs from commit')
    for destination, origin in mapping.items():
        record = source_files.get(origin)
        require(type(record) is dict and set(record) == {'sha256'}
                and type(record['sha256']) is str and HASH.fullmatch(record['sha256']),
                'Missing committed public source')
        approved[destination] = record['sha256']
    require(native <= set(files) and 'vendor/zinnia/COPYING' in files, 'Incomplete native source distribution')
    return approved
