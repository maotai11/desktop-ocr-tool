"""Prepare the reviewed, hash-locked Windows CPython runtime outside the repo."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCK = ROOT / 'scripts/windows_runtime.lock.json'
MAX_MEMBERS = 10_000
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024
IDENTITY = {
    'schema': 1, 'provider': 'astral-sh/python-build-standalone', 'release_tag': '20261026',
    'source_commit': 'fa506e0858ac9f2cc5b289c137b888a2aa24d1ee',
    'python_version': '3.12.16', 'openssl_version': '3.5.9',
    'archive_filename': 'cpython-3.12.16+20261026-x86_64-pc-windows-msvc-install_only_stripped.tar.gz',
    'archive_url': 'https://github.com/astral-sh/python-build-standalone/releases/download/20261026/'
                   'cpython-3.12.16%2B20261026-x86_64-pc-windows-msvc-install_only_stripped.tar.gz',
    'archive_size': 22014416,
    'archive_sha256': '1f2ab53880fc67ea323398c6388e05af28addc99a8e389209f08a51fdff4b149',
    'archive_prefix': 'python/',
}


def require(value, message):
    if not value:
        raise ValueError(message)


def file_digest(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def canonical_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')


def safe_relative(name):
    require(type(name) is str and 0 < len(name) <= 512 and '\\' not in name
            and not name.startswith('/') and PurePosixPath(name).as_posix() == name,
            'Invalid relative runtime path')
    reserved = {'CON', 'PRN', 'AUX', 'NUL', 'CONIN$', 'CONOUT$',
                *(f'{prefix}{index}' for prefix in ('COM', 'LPT') for index in range(10)),
                *(prefix + digit for prefix in ('COM', 'LPT') for digit in '¹²³')}
    require(all(part not in ('', '.', '..') and not part.endswith((' ', '.'))
                and part.split('.')[0].upper() not in reserved
                and not any(ord(char) < 32 or char in ':<>"|?*' for char in part)
                for part in name.split('/')), 'Unsafe Windows runtime path')
    return name


def load_runtime_lock(path=DEFAULT_LOCK):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'Duplicate runtime lock field')
            result[key] = value
        return result
    lock = json.loads(Path(path).read_text(encoding='utf-8'), object_pairs_hook=pairs)
    require(type(lock) is dict and set(lock) == {*IDENTITY, 'files'}, 'Invalid runtime lock schema')
    require(all(type(lock[key]) is type(value) and lock[key] == value for key, value in IDENTITY.items()),
            'Unapproved runtime provider or artifact')
    files = lock['files']
    require(type(files) is dict and 8 <= len(files) <= 128, 'Incomplete runtime PE lock')
    seen = set()
    for name, item in files.items():
        safe_relative(name)
        require(name.casefold() not in seen, 'Duplicate runtime PE path')
        seen.add(name.casefold())
        require(type(item) is dict and set(item) == {'sha256', 'size_bytes'}
                and type(item['sha256']) is str and re.fullmatch('[0-9a-f]{64}', item['sha256'])
                and type(item['size_bytes']) is int and 64 <= item['size_bytes'] <= MAX_FILE_BYTES,
                'Invalid runtime PE identity')
    require({'python.exe', 'pythonw.exe', 'python312.dll', 'python3.dll', 'DLLs/_ssl.pyd',
             'DLLs/_hashlib.pyd', 'DLLs/libcrypto-3-x64.dll', 'DLLs/libssl-3-x64.dll'} <= set(files),
            'Required runtime PE files are missing')
    return lock


def download_archive(path, lock):
    request = urllib.request.Request(lock['archive_url'], headers={'User-Agent': 'InkPick-runtime-bootstrap'})
    count = 0
    with urllib.request.urlopen(request, timeout=120) as response, Path(path).open('xb') as output:
        while block := response.read(1024 * 1024):
            count += len(block)
            require(count <= lock['archive_size'], 'Runtime download exceeded locked size')
            output.write(block)
    require(count == lock['archive_size'] and file_digest(path) == lock['archive_sha256'],
            'Runtime archive hash or size mismatch')


def extract_archive(path, target, lock):
    """Validate the complete tar before creating any extracted file."""
    require(Path(path).stat().st_size == lock['archive_size'] and file_digest(path) == lock['archive_sha256'],
            'Runtime archive hash or size mismatch')
    target = Path(target)
    require(not target.exists(), 'Extraction target must be new')
    with tarfile.open(path, 'r:gz') as archive:
        members = archive.getmembers()
        require(0 < len(members) <= MAX_MEMBERS, 'Runtime archive entry limit exceeded')
        planned, seen, aliases, types, total = [], set(), {}, {}, 0
        for member in members:
            require(member.isdir() or member.isfile(), 'Runtime links and special files are forbidden')
            require(not member.issparse() and not member.linkname, 'Sparse or linked runtime member')
            name = member.name.rstrip('/') if member.isdir() else member.name
            require(name == 'python' or name.startswith('python/'), 'Unexpected runtime archive prefix')
            require(name != 'python' or member.isdir(), 'Runtime root is not a directory')
            relative = name.removeprefix('python/') if name != 'python' else ''
            if not relative:
                require(name.casefold() not in seen, 'Duplicate archive root')
                seen.add(name.casefold())
                continue
            safe_relative(relative)
            require(relative.casefold() not in seen, 'Duplicate Windows runtime member')
            seen.add(relative.casefold())
            parts = relative.split('/')
            for index in range(1, len(parts) + 1):
                prefix = '/'.join(parts[:index])
                folded = prefix.casefold()
                require(folded not in aliases or aliases[folded] == prefix, 'Case-aliased runtime path')
                aliases[folded] = prefix
                if index < len(parts):
                    require(types.get(folded) != 'file', 'Runtime file/directory collision')
            kind = 'directory' if member.isdir() else 'file'
            folded = relative.casefold()
            if kind == 'file':
                require(not any(key.startswith(folded + '/') for key in aliases), 'Runtime parent is a file')
            types[folded] = kind
            require(0 <= member.size <= MAX_FILE_BYTES and (not member.isdir() or member.size == 0),
                    'Oversized runtime member')
            total += member.size
            require(total <= MAX_TOTAL_BYTES, 'Runtime expanded size limit exceeded')
            planned.append((member, relative))
        target.mkdir()
        for member, relative in planned:
            destination = target.joinpath(*relative.split('/'))
            if member.isdir():
                destination.mkdir(parents=True, exist_ok=True)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as source, destination.open('xb') as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
                require(destination.stat().st_size == member.size, 'Incomplete runtime extraction')


def verify_native_files(root, lock, *, immutable_only=False):
    root = Path(root)
    def redirected(path):
        return path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction())
    require(not redirected(root), 'Runtime root is redirected')
    expected = {name: item for name, item in lock['files'].items()
                if not immutable_only or not name.casefold().startswith('lib/site-packages/')}
    actual = {}
    for path in root.rglob('*'):
        require(not redirected(path), 'Runtime filesystem contains a link or junction')
        if not path.is_file():
            continue
        name = path.relative_to(root).as_posix()
        if immutable_only and name.casefold().startswith('lib/site-packages/'):
            continue
        with path.open('rb') as stream:
            header = stream.read(2)
        if header == b'MZ' or path.suffix.casefold() in ('.dll', '.pyd', '.exe'):
            require(header == b'MZ', 'Native runtime suffix contains no PE data')
            actual[name] = {'sha256': file_digest(path), 'size_bytes': path.stat().st_size}
    require(actual == expected, 'Runtime PE files differ from the committed lock')
    return actual


SMOKE = '''import hashlib,json,ssl,sys,_ssl,_hashlib
print(json.dumps({'python_version':'.'.join(map(str,sys.version_info[:3])),
 'openssl_version':'.'.join(map(str,ssl.OPENSSL_VERSION_INFO[:3])),
 'openssl_library':ssl.OPENSSL_VERSION,'executable':sys.executable,
 'prefix':sys.prefix,'base_prefix':sys.base_prefix,'ssl_module':_ssl.__file__,
 'hashlib_module':_hashlib.__file__,
 'hashlib_ok':hashlib.sha256(b'abc').hexdigest()=='ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad'})
'''  # All absolute paths stay private inside this bootstrap process.


def smoke_runtime(root, lock):
    root = Path(root).resolve()
    completed = subprocess.run([str(root / 'python.exe'), '-I', '-c', SMOKE],
                               capture_output=True, timeout=60, check=True)
    result = json.loads(completed.stdout)
    require(result['python_version'] == lock['python_version']
            and result['openssl_version'] == lock['openssl_version']
            and result['openssl_library'].startswith('OpenSSL ' + lock['openssl_version'] + ' ')
            and result['hashlib_ok'] is True, 'Prepared runtime or OpenSSL version mismatch')
    require(Path(result['executable']).resolve() == root / 'python.exe'
            and Path(result['prefix']).resolve() == root and Path(result['base_prefix']).resolve() == root
            and Path(result['ssl_module']).resolve() == root / 'DLLs/_ssl.pyd'
            and Path(result['hashlib_module']).resolve() == root / 'DLLs/_hashlib.pyd',
            'Prepared runtime used another Python installation')


def export_environment(root, environment):
    path_file, env_file = environment.get('GITHUB_PATH'), environment.get('GITHUB_ENV')
    require(bool(path_file) == bool(env_file), 'Incomplete GitHub environment files')
    if not path_file:
        require(environment.get('GITHUB_ACTIONS') != 'true', 'GitHub environment export is required')
        return False
    root = Path(root).resolve()
    require(not any(char in str(root) for char in '\r\n'), 'Invalid runtime export path')
    with Path(path_file).open('a', encoding='utf-8') as stream:
        stream.write(str(root) + '\n' + str(root / 'Scripts') + '\n')
    with Path(env_file).open('a', encoding='utf-8') as stream:
        for name in ('pythonLocation', 'Python_ROOT_DIR', 'Python3_ROOT_DIR'):
            stream.write(name + '=' + str(root) + '\n')
    return True


def prepare(destination, report_path, *, environment=None):
    environment = dict(os.environ if environment is None else environment)
    lock = load_runtime_lock()
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from src.core.version import APP_VERSION
    report = dict(schema=1, probe='windows_runtime_preparation', version=APP_VERSION,
        platform='win32', provider=lock['provider'], python_version=lock['python_version'],
        openssl_version=lock['openssl_version'], archive_sha256=lock['archive_sha256'],
        files_inventory_sha256=hashlib.sha256(canonical_bytes(lock['files'])).hexdigest(),
        native_files_count=len(lock['files']), passed=False,
        download_verified=False, extraction_verified=False, runtime_version_verified=False,
        openssl_verified=False, hashlib_verified=False, ensurepip_completed=False, environment_exported=False)
    if re.fullmatch('[0-9a-f]{40}', environment.get('GITHUB_SHA', '')):
        report['source_commit'] = environment['GITHUB_SHA']
    destination = Path(destination).resolve()
    temporary = Path(environment.get('RUNNER_TEMP') or tempfile.gettempdir()).resolve()
    installed = False
    try:
        require(temporary.is_dir() and destination != temporary and destination.is_relative_to(temporary)
                and not destination.is_relative_to(ROOT.resolve()) and not ROOT.resolve().is_relative_to(destination)
                and not destination.exists(), 'Runtime destination must be new and outside the workspace in runner temp')
        with tempfile.TemporaryDirectory(prefix='inkpick-runtime-', dir=temporary) as staging:
            staging = Path(staging)
            archive, extracted = staging / 'runtime.tar.gz', staging / 'runtime'
            download_archive(archive, lock)
            report['download_verified'] = True
            extract_archive(archive, extracted, lock)
            verify_native_files(extracted, lock)
            report['extraction_verified'] = True
            destination.parent.mkdir(parents=True, exist_ok=True)
            extracted.rename(destination)
            installed = True
            smoke_runtime(destination, lock)
            report.update(runtime_version_verified=True, openssl_verified=True, hashlib_verified=True)
            pip_environment = dict(environment, PIP_NO_INDEX='1', PIP_DISABLE_PIP_VERSION_CHECK='1', PIP_CONFIG_FILE=os.devnull)
            subprocess.run([str(destination / 'python.exe'), '-I', '-m', 'ensurepip', '--upgrade', '--default-pip'],
                           env=pip_environment, capture_output=True, timeout=120, check=True)
            subprocess.run([str(destination / 'python.exe'), '-I', '-m', 'pip', '--version'],
                           env=pip_environment, capture_output=True, timeout=60, check=True)
            verify_native_files(destination, lock, immutable_only=True)
            report['ensurepip_completed'] = True
            (destination / 'Scripts').mkdir(exist_ok=True)
            report['environment_exported'] = export_environment(destination, environment)
            report['files'] = lock['files']
            report['passed'] = True
    except Exception:
        if installed:
            shutil.rmtree(destination)
        report['error_code'] = 'WINDOWS_RUNTIME_PREPARATION_FAILED'
        raise
    finally:
        output = Path(report_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(canonical_bytes(report) + b'\n')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', required=True, type=Path)
    parser.add_argument('--report', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        require(sys.platform == 'win32', 'Windows runtime bootstrap requires Windows')
        prepare(args.destination, args.report)
        print(str(args.destination.resolve() / 'python.exe'))
        return 0
    except Exception:  # noqa: BLE001 - raw URLs, directories and child output stay private
        # Even a preflight/lock failure must replace any stale previous PASS.
        try:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_bytes(canonical_bytes({'schema': 1, 'probe': 'windows_runtime_preparation',
                'platform': 'win32', 'passed': False, 'error_code': 'WINDOWS_RUNTIME_PREPARATION_FAILED'}) + b'\n')
        except OSError:
            pass
        print('WINDOWS_RUNTIME_PREPARATION_FAILED', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
