"""Filter redundant Windows 10+ compatibility binaries after complete Analysis.

This does not trust the collected stubs. Removal requires the current measured
Windows namespace and System32 exports (including bounded forwarder chains) to
satisfy every import from the final kept PE files. The frozen payload and native
audit gates still inspect the resulting executable independently.
"""
from __future__ import annotations

from pathlib import Path
import re
import sys

from scripts import audit_native_payload as native

ROOT = Path(__file__).resolve().parents[1]
PYINSTALLER_VERSION = '6.22.3'
# Exact compatibility files observed in the Windows candidate, not a wildcard
# deletion policy for DLLs or the Visual C++/OpenSSL runtime.
OS_COMPATIBILITY_FILES = frozenset({
    'api-ms-win-core-console-l1-1-0.dll', 'api-ms-win-core-datetime-l1-1-0.dll',
    'api-ms-win-core-debug-l1-1-0.dll', 'api-ms-win-core-errorhandling-l1-1-0.dll',
    'api-ms-win-core-fibers-l1-1-0.dll', 'api-ms-win-core-fibers-l1-1-1.dll',
    'api-ms-win-core-file-l1-1-0.dll', 'api-ms-win-core-file-l1-2-0.dll',
    'api-ms-win-core-file-l2-1-0.dll', 'api-ms-win-core-handle-l1-1-0.dll',
    'api-ms-win-core-heap-l1-1-0.dll', 'api-ms-win-core-interlocked-l1-1-0.dll',
    'api-ms-win-core-kernel32-legacy-l1-1-1.dll', 'api-ms-win-core-libraryloader-l1-1-0.dll',
    'api-ms-win-core-localization-l1-2-0.dll', 'api-ms-win-core-memory-l1-1-0.dll',
    'api-ms-win-core-namedpipe-l1-1-0.dll', 'api-ms-win-core-processenvironment-l1-1-0.dll',
    'api-ms-win-core-processthreads-l1-1-0.dll', 'api-ms-win-core-processthreads-l1-1-1.dll',
    'api-ms-win-core-profile-l1-1-0.dll', 'api-ms-win-core-rtlsupport-l1-1-0.dll',
    'api-ms-win-core-string-l1-1-0.dll', 'api-ms-win-core-synch-l1-1-0.dll',
    'api-ms-win-core-synch-l1-2-0.dll', 'api-ms-win-core-sysinfo-l1-1-0.dll',
    'api-ms-win-core-sysinfo-l1-2-0.dll', 'api-ms-win-core-timezone-l1-1-0.dll',
    'api-ms-win-core-util-l1-1-0.dll', 'api-ms-win-crt-conio-l1-1-0.dll',
    'api-ms-win-crt-convert-l1-1-0.dll', 'api-ms-win-crt-environment-l1-1-0.dll',
    'api-ms-win-crt-filesystem-l1-1-0.dll', 'api-ms-win-crt-heap-l1-1-0.dll',
    'api-ms-win-crt-locale-l1-1-0.dll', 'api-ms-win-crt-math-l1-1-0.dll',
    'api-ms-win-crt-private-l1-1-0.dll', 'api-ms-win-crt-process-l1-1-0.dll',
    'api-ms-win-crt-runtime-l1-1-0.dll', 'api-ms-win-crt-stdio-l1-1-0.dll',
    'api-ms-win-crt-string-l1-1-0.dll', 'api-ms-win-crt-time-l1-1-0.dll',
    'api-ms-win-crt-utility-l1-1-0.dll', 'ucrtbase.dll',
})
MAX_FORWARD_DEPTH = 32
MAX_PROOF_SYMBOLS = 65_536


def _windows10_or_later():
    return sys.platform == 'win32' and sys.getwindowsversion().major >= 10


def _forwarder_target(forwarder):
    if not isinstance(forwarder, str) or not 0 < len(forwarder) <= 4096 or '.' not in forwarder:
        return None
    library, symbol = forwarder.rsplit('.', 1)
    library = library.lower()
    if not library.endswith(('.dll', '.drv')):
        library += '.dll'
    if not native._user_library_name(library) or not symbol:
        return None
    if re.fullmatch(r'#[0-9]{1,10}', symbol):
        symbol = int(symbol[1:])
        if not 0 < symbol <= 0xffffffff:
            return None
    elif symbol.startswith('#'):
        return None
    return library, symbol


class _SystemProof:
    def __init__(self, loader=None):
        system, self.api_sets, self.known = (loader or native._windows_loader)()
        self.system = Path(system).resolve()
        native.require(self.system.is_dir() and self.system.name.casefold() == 'system32',
                       'System32 replacement directory is unavailable')
        self.cache, self.proven = {}, {}
        self.steps = 0

    def metadata(self, name):
        if not native._user_library_name(name):
            return None
        if name not in self.cache:
            path = self.system / name
            if (not path.is_file() or native._redirected(path)
                    or path.resolve().parent != self.system or path.stat().st_size > native.MAX_FILE_BYTES):
                self.cache[name] = None
            else:
                self.cache[name] = native._pe_metadata(path.read_bytes())
        return self.cache[name]

    def _hosts(self, name):
        if isinstance(self.api_sets, native._ApiSetNamespace):
            return self.api_sets.hosts_for_name(name)
        return self.api_sets.get(name)

    def satisfies(self, name, symbols, *, depth=0, visiting=frozenset()):
        if depth >= MAX_FORWARD_DEPTH:
            return False
        # An unknown API contract must never fall back to a same-named DLL.
        if name.startswith(('api-ms-', 'ext-ms-')) and not self._hosts(name):
            return False
        dependency = {'name': name, 'kind': 'regular', '_symbols': list(symbols)}
        resolution = native.resolve_import({'path': '_policy_importer.pyd', 'provenance': None},
            dependency, [], system_metadata=self.metadata, api_sets=self.api_sets,
            known_dlls=self.known, package_dependencies={})
        if resolution['resolution'] not in ('system32', 'api_set') or resolution['missing_symbols_count']:
            return False
        target = resolution['target']
        metadata = self.metadata(target)
        if not isinstance(metadata.get('_forwarders'), dict):
            return False
        for symbol in symbols:
            key = (target, symbol)
            if key in self.proven:
                if not self.proven[key]:
                    return False
                continue
            if key in visiting or self.steps >= MAX_PROOF_SYMBOLS:
                return False
            self.steps += 1
            forwarder = metadata['_forwarders'].get(symbol)
            valid = True
            if forwarder is not None:
                parsed = _forwarder_target(forwarder)
                valid = parsed is not None and self.satisfies(parsed[0], [parsed[1]], depth=depth + 1,
                                                               visiting=visiting | {key})
            self.proven[key] = valid
            if not valid:
                return False
        return True


def filter_tocs(binaries, datas, *, loader=None):
    """Return filtered TOCs, retaining candidates whenever OS proof is absent."""
    binaries, datas = list(binaries), list(datas)
    if not _windows10_or_later():
        return binaries, datas
    entries = binaries + datas
    native.require(len(entries) <= native.MAX_ENTRIES, 'Collected file count exceeds policy bounds')
    metadata, candidates = {}, set()
    total = 0
    for entry in entries:
        native.require(isinstance(entry, (tuple, list)) and len(entry) == 3
                       and all(isinstance(value, str) for value in entry), 'Invalid collected file tuple')
        destination, source, kind = entry
        destination = native.safe_path(destination.replace('\\', '/'))
        if kind == 'SYMLINK':
            continue
        path = Path(source)
        native.require(path.is_file() and not native._redirected(path), 'Collected source is absent or redirected')
        with path.open('rb') as stream:
            header = stream.read(2)
        if header != b'MZ' and not destination.lower().endswith(native.NATIVE_SUFFIXES):
            continue
        native.require(header == b'MZ' and path.stat().st_size <= native.MAX_FILE_BYTES,
                       'Collected native source is not a bounded PE')
        total += path.stat().st_size
        native.require(total <= native.MAX_TOTAL_BYTES and len(metadata) < native.MAX_NATIVE_FILES,
                       'Collected native sources exceed policy bounds')
        key = destination.casefold()
        native.require(key not in metadata, 'Duplicate native destination in collected files')
        item = native._pe_metadata(path.read_bytes())
        metadata[key] = item
        if key in OS_COMPATIBILITY_FILES and item['is_dll'] is True:
            candidates.add(key)
    if not candidates:
        return binaries, datas
    proof = _SystemProof(loader)
    # Simultaneous removal is allowed only while every final kept importer is
    # satisfied. A retained candidate becomes an importer in the next pass.
    removable = {name for name in candidates if proof.satisfies(name, [])}
    forwarder_checks = 0
    while True:
        rejected = set()
        for name, item in metadata.items():
            if name in removable:
                continue
            for dependency in item['imports']:
                target = dependency['name']
                if target in removable and not proof.satisfies(target, dependency['_symbols']):
                    rejected.add(target)
            # Export forwarders are loader edges too, even though there is no
            # import descriptor for them. Named/ordinal aliases share a value.
            for forwarder in set(item['_forwarders'].values()):
                forwarder_checks += 1
                parsed = _forwarder_target(forwarder)
                if parsed is None or forwarder_checks > MAX_PROOF_SYMBOLS:
                    rejected.update(removable)  # cannot rule out a candidate edge
                    break
                target, symbol = parsed
                if target in removable and not proof.satisfies(target, [symbol]):
                    rejected.add(target)
        if not rejected:
            break
        removable.difference_update(rejected)
    def kept(entry):
        return entry[0].replace('\\', '/').casefold() not in removable
    return [entry for entry in binaries if kept(entry)], [entry for entry in datas if kept(entry)]


def filter_analysis(analysis):
    analysis.binaries, analysis.datas = filter_tocs(analysis.binaries, analysis.datas)
    return analysis


def generate_wrapped_spec(arguments):
    """Preserve the pinned CLI's makespec behavior without rewriting its source."""
    import PyInstaller
    from PyInstaller.__main__ import generate_parser, run_makespec
    native.require(PyInstaller.__version__ == PYINSTALLER_VERSION, 'Unreviewed PyInstaller version')
    args = generate_parser().parse_args(arguments)
    native.require(len(args.filenames) == 1 and not args.filenames[0].endswith('.spec'),
                   'The release wrapper requires one source script')
    original = Path(run_makespec(**vars(args)))
    generated = original.with_name(original.stem + '.generated.spec')
    native.require(not original.is_symlink() and not generated.is_symlink(), 'Redirected generated spec')
    original.replace(generated)
    # Keep the original spec name/work directory, and keep the untouched output
    # of makespec next to it so SPECPATH-relative resources retain their meaning.
    wrapper = (
        'from pathlib import Path as _InkPickPath\n'
        'import sys as _InkPickSys\n'
        f'_InkPickSys.path.insert(0, {str(ROOT)!r})\n'
        'from scripts.windows_binary_policy import filter_analysis as _inkpick_filter\n'
        '_inkpick_original_analysis = Analysis\n'
        'def Analysis(*args, **kwargs):\n'
        '    return _inkpick_filter(_inkpick_original_analysis(*args, **kwargs))\n'
        f'_inkpick_generated = _InkPickPath(SPECPATH) / {generated.name!r}\n'
        'exec(compile(_inkpick_generated.read_bytes(), str(_inkpick_generated), "exec"), globals())\n'
    )
    original.write_text(wrapper, encoding='utf-8')
    return original, generated, args


def main(arguments=None):
    from PyInstaller.__main__ import run
    wrapper, _generated, args = generate_wrapped_spec(sys.argv[1:] if arguments is None else arguments)
    build_arguments = [str(wrapper), '--distpath', args.distpath, '--workpath', args.workpath]
    if args.clean_build:
        build_arguments.append('--clean')
    if args.noconfirm:
        build_arguments.append('--noconfirm')
    if args.upx_dir:
        build_arguments += ['--upx-dir', args.upx_dir]
    if args.loglevel:
        build_arguments += ['--log-level', args.loglevel]
    run(build_arguments)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
