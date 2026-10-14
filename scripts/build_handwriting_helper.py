"""Build the pinned native helper locally; never invoked by the delivered app."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def source_identity(root):
    directory = Path(root) / 'native/handwriting'
    manifest = json.loads((directory / 'UPSTREAM_MANIFEST.json').read_text())
    files = {}
    for name, info in manifest['files'].items():
        path = directory / 'vendor' / name
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        expected = manifest['patched_feature_sha256'] if name == 'zinnia/feature.cpp' else info['sha256']
        if digest != expected:
            raise ValueError('Pinned native source mismatch: ' + name)
        files['vendor/' + name] = digest
    for name in ('helper.cpp', 'model_and_strokes.h', 'CMakeLists.txt'):
        files[name] = hashlib.sha256((directory / name).read_bytes()).hexdigest()
    return {'upstream_commit': manifest['commit'], 'files': files,
            'sha256': hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()}


def compiler_environment():
    env = os.environ.copy()
    if sys.platform != 'win32' or shutil.which('cl.exe'):
        return env
    vswhere = Path(os.environ['ProgramFiles(x86)']) / 'Microsoft Visual Studio/Installer/vswhere.exe'
    installation = subprocess.check_output([str(vswhere), '-latest', '-products', '*',
        '-requires', 'Microsoft.VisualStudio.Component.VC.Tools.x86.x64', '-property', 'installationPath'],
        timeout=20, text=True).strip()
    if not installation:
        raise RuntimeError('Installed Microsoft x64 compiler not found')
    setup = Path(installation) / 'Common7/Tools/VsDevCmd.bat'
    output = subprocess.check_output(['cmd.exe', '/d', '/s', '/c',
        f'call "{setup}" -no_logo -arch=x64 -host_arch=x64 >nul && set'],
        timeout=30, text=True)
    env.update(line.split('=', 1) for line in output.splitlines() if '=' in line and not line.startswith('='))
    return env


def build(root=ROOT):
    root = Path(root).resolve()
    identity = source_identity(root)
    directory = root / 'native/handwriting'
    output = directory / 'runtime'
    output.mkdir(exist_ok=True)
    name = 'inkpick-handwriting.exe' if sys.platform == 'win32' else 'inkpick-handwriting'
    executable = output / name
    sources = [directory / 'helper.cpp', *(directory / 'vendor/zinnia' / name
        for name in ('character.cpp', 'feature.cpp', 'recognizer.cpp', 'sexp.cpp'))]
    env = compiler_environment()
    if sys.platform == 'win32':
        compiler = shutil.which('cl.exe', path=env.get('PATH'))
        if not compiler:
            raise RuntimeError('Microsoft compiler unavailable')
        command = [compiler, '/nologo', '/O2', '/MT', '/std:c++17', '/EHsc',
            '/DWORDS_LITENDIAN', '/DZINNIA_STATIC_LIBRARY', '/DHAVE_WINDOWS_H',
            '/DHAVE_SYS_TYPES_H', '/DHAVE_SYS_STAT_H', '/DHAVE_FCNTL_H', '/DHAVE_STRING_H',
            '/D_CRT_SECURE_NO_WARNINGS', '/I' + str(directory / 'vendor/zinnia'),
            *map(str, sources), '/Fe:' + str(executable), '/link', 'bcrypt.lib']
    else:
        compiler = shutil.which('g++')
        if not compiler:
            raise RuntimeError('Installed C++17 compiler unavailable')
        command = [compiler, '-std=c++17', '-O2', '-DWORDS_LITENDIAN', '-DZINNIA_STATIC_LIBRARY',
            '-DHAVE_SYS_TYPES_H', '-DHAVE_SYS_STAT_H', '-DHAVE_SYS_MMAN_H', '-DHAVE_FCNTL_H',
            '-DHAVE_UNISTD_H', '-DHAVE_MMAP', '-I' + str(directory / 'vendor/zinnia'),
            *map(str, sources), '-lcrypto', '-o', str(executable)]
    process = subprocess.Popen(command, cwd=output, env=env,
                               start_new_session=sys.platform != 'win32')
    try:
        code = process.wait(timeout=90)
    except subprocess.TimeoutExpired:
        if sys.platform == 'win32':
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                           check=False, capture_output=True, timeout=15)
        else:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=15)
        raise RuntimeError('Native compilation deadline exceeded')
    if code:
        raise RuntimeError('Native compilation failed')
    lock = {'schema': 1, 'protocol': 1, 'platform': sys.platform, 'name': name,
            'size_bytes': executable.stat().st_size,
            'sha256': hashlib.sha256(executable.read_bytes()).hexdigest(), 'source': identity}
    if sys.platform == 'win32':
        import pefile
        pe = pefile.PE(str(executable))
        if pe.FILE_HEADER.Machine != 0x8664:
            raise ValueError('Handwriting helper is not x64')
        imports = sorted(entry.dll.decode('ascii').lower() for entry in pe.DIRECTORY_ENTRY_IMPORT)
        allowed = {'kernel32.dll', 'bcrypt.dll', 'advapi32.dll', 'user32.dll', 'ntdll.dll'}
        if not set(imports) <= allowed:
            raise ValueError('Unverified helper DLL imports: ' + repr(imports))
        lock['native_imports'] = imports
        lock['machine'] = 'AMD64'
        lock['crt'] = 'MSVC /MT Release'
        pe.close()
    (output / 'helper.lock.json').write_text(json.dumps(lock, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(lock))
    return lock


if __name__ == '__main__':
    build()
