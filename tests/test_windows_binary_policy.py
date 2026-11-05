"""Actual PE fixtures prove policy decisions; no fixture DLL is executed."""
from copy import deepcopy
from pathlib import Path
import struct
from types import SimpleNamespace

import pytest

from scripts import audit_native_payload as native
from scripts import windows_binary_policy as policy
from test_native_payload_audit import api_set_fixture, pe_with_exports, tiny_pe, tiny_pe_with_import

CONTRACT = 'api-ms-win-core-console-l1-1-0.dll'
OTHER = 'api-ms-win-core-file-l1-1-0.dll'


def write(root, name, data):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return str(path)


def exporting(*, forwarder=None):
    data = bytearray(pe_with_exports(1, ordinal_only=False))
    offset = data.index(b'Export000000\0')
    data[offset:offset + 13] = b'OpenPrinterW\0'
    if forwarder is not None:
        raw = forwarder.encode('ascii') + b'\0'
        position = 0x328
        if len(raw) > 24:
            assert len(raw) <= 512
            position = len(data)
            data.extend(bytes(512))
            struct.pack_into('<I', data, 0x98 + 240 + 8, len(data) - 512)
            struct.pack_into('<I', data, 0x98 + 240 + 16, len(data) - 512)
            struct.pack_into('<I', data, 0x98 + 116, len(data) - 0x300)
        struct.pack_into('<I', data, 0x340, 0x1000 + position - 512)
        data[position:position + len(raw)] = raw
    return bytes(data)


def conditional_namespace(default, importer, alternate, *, name=CONTRACT[:-4]):
    data = bytearray(api_set_fixture(default, alternate, name=name,
                                    hashed_prefix=name.rsplit('-', 1)[0]))
    alias = importer.encode('utf-16-le')
    offset = len(data)
    data.extend(alias)
    struct.pack_into('<II', data, 52 + 20 + 4, offset, len(alias))
    struct.pack_into('<I', data, 4, len(data))
    return native.parse_api_set(bytes(data))


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(policy, '_windows10_or_later', lambda: True)
    system = tmp_path / 'System32'
    system.mkdir()
    write(system, 'kernelbase.dll', exporting())
    write(system, 'ucrtbase.dll', exporting())
    namespace = native._ApiSetNamespace()
    namespace.add_record(CONTRACT, CONTRACT.rsplit('-', 1)[0], ('kernelbase.dll',))
    namespace.add_record(OTHER, OTHER.rsplit('-', 1)[0], ('kernelbase.dll',))
    loader = lambda: (system, namespace, set())
    binaries = [
        (CONTRACT, write(tmp_path, 'sources/stub.dll', tiny_pe()), 'BINARY'),
        ('consumer.pyd', write(tmp_path, 'sources/consumer.pyd', tiny_pe_with_import(CONTRACT)), 'EXTENSION'),
    ]
    return SimpleNamespace(root=tmp_path, system=system, namespace=namespace, loader=loader, binaries=binaries)


@pytest.mark.parametrize('as_data', [False, True])
def test_real_pe_os_replacement_removes_only_proven_exact_root_compatibility_file(fixture, as_data):
    f = fixture
    controls = ['vcruntime140.dll', 'msvcp140.dll', 'libssl-3-x64.dll', 'libcrypto-3-x64.dll',
                'api-ms-win-core-unknown-l1-1-0.dll', 'package/' + CONTRACT]
    entries = [(name, write(f.root, 'controls/' + name, tiny_pe()), 'BINARY') for name in controls]
    text = ('readme.txt', write(f.root, 'readme.txt', b'keep data'), 'DATA')
    binaries = f.binaries[1:] + entries if as_data else f.binaries + entries
    datas = [f.binaries[0], text] if as_data else [text]
    result, result_data = policy.filter_tocs(binaries, datas, loader=f.loader)
    assert [item[0] for item in result] == ['consumer.pyd'] + controls
    assert result_data == [text]
    assert (f.root / 'sources/stub.dll').is_file()  # collection only, no source deletion


@pytest.mark.parametrize('defect', ['unknown_contract', 'missing_export', 'kernel_host', 'ambiguous_alias'])
def test_unproven_os_contract_is_retained(fixture, defect):
    f = fixture
    if defect == 'unknown_contract':
        f.namespace = native._ApiSetNamespace()
        write(f.system, CONTRACT, exporting())  # no name-only fallback allowed
    elif defect == 'missing_export':
        write(f.system, 'kernelbase.dll', tiny_pe())
    elif defect == 'kernel_host':
        # Parse real schema bytes so canonical metadata and the measured
        # default-host index describe the same kernel-only contract.
        f.namespace = native.parse_api_set(api_set_fixture('driver.sys', name=CONTRACT[:-4],
            hashed_prefix=CONTRACT.rsplit('-', 1)[0]))
        assert f.namespace.hosts_for_name(CONTRACT) == ('driver.sys',)
        f.loader = lambda: (f.system, f.namespace, set())
        write(f.system, 'driver.sys', exporting())
    else:
        prefix = CONTRACT.rsplit('-', 1)[0]
        f.namespace = native._ApiSetNamespace()
        f.namespace.add_record(prefix + '-1.dll', prefix, ('first.dll',))
        f.namespace.add_record(prefix + '-2.dll', prefix, ('second.dll',))
        assert f.namespace.hosts_for_name(CONTRACT, importer_name='consumer.pyd') == ()
    f.loader = lambda: (f.system, f.namespace, set())
    assert policy.filter_tocs(f.binaries, [], loader=f.loader)[0] == f.binaries


@pytest.mark.parametrize('defect', ['candidate_non_pe', 'candidate_x86', 'host_x86', 'host_redirected'])
def test_invalid_pe_or_redirected_replacement_never_silently_drops_candidate(fixture, defect):
    f = fixture
    if defect == 'candidate_non_pe':
        Path(f.binaries[0][1]).write_bytes(b'not a PE')
    elif defect == 'candidate_x86':
        Path(f.binaries[0][1]).write_bytes(tiny_pe(0x14c))
    elif defect == 'host_x86':
        write(f.system, 'kernelbase.dll', tiny_pe(0x14c))
    else:
        (f.system / 'kernelbase.dll').unlink()
        external = Path(write(f.root, 'outside.dll', exporting()))
        (f.system / 'kernelbase.dll').symlink_to(external)
        assert policy.filter_tocs(f.binaries, [], loader=f.loader)[0] == f.binaries
        return
    with pytest.raises(ValueError):
        policy.filter_tocs(f.binaries, [], loader=f.loader)


def test_ucrt_replacement_requires_all_inbound_symbols_and_preserves_real_crt(fixture):
    f = fixture
    binaries = [('ucrtbase.dll', f.binaries[0][1], 'BINARY'),
                ('consumer.pyd', write(f.root, 'consumer.pyd', tiny_pe_with_import('ucrtbase.dll')), 'EXTENSION')]
    assert [item[0] for item in policy.filter_tocs(binaries, [], loader=f.loader)[0]] == ['consumer.pyd']
    write(f.system, 'ucrtbase.dll', tiny_pe())
    assert policy.filter_tocs(binaries, [], loader=f.loader)[0] == binaries


@pytest.mark.parametrize('forwarder,valid', [
    ('ntdll.Export000000', True), ('ntdll.#7', True), ('ntdll.#9999', False),
    ('absent.Entry', False), ('driver.sys.Entry', False), ('ntdll.#bad', False),
    ('kernelbase.OpenPrinterW', False), ('api-ms-unknown.Entry', False),
])
def test_system_forwarder_chains_must_resolve_without_cycles_or_unknown_hosts(fixture, forwarder, valid):
    f = fixture
    write(f.system, 'kernelbase.dll', exporting(forwarder=forwarder))
    write(f.system, 'ntdll.dll', pe_with_exports(1, ordinal_only=False))
    result, _ = policy.filter_tocs(f.binaries, [], loader=f.loader)
    assert (CONTRACT not in {item[0] for item in result}) is valid


def test_kept_candidate_imports_are_rechecked_until_removal_set_is_stable(fixture):
    f = fixture
    write(f.system, 'kernelbase.dll', tiny_pe())
    Path(f.binaries[0][1]).write_bytes(tiny_pe_with_import(OTHER))
    binaries = f.binaries + [(OTHER, write(f.root, 'second.dll', tiny_pe()), 'BINARY')]
    assert policy.filter_tocs(binaries, [], loader=f.loader)[0] == binaries


@pytest.mark.parametrize('forwarder,removes', [
    ('ucrtbase.Absent', False), ('ucrtbase.#9999', False),
    ('ucrtbase.OpenPrinterW', True), ('ucrtbase.#7', True),
])
def test_retained_bundled_forwarder_requires_os_replacement_symbol(fixture, forwarder, removes):
    f = fixture
    binaries = [('ucrtbase.dll', f.binaries[0][1], 'BINARY'),
                ('kept.dll', write(f.root, 'kept.dll', exporting(forwarder=forwarder)), 'BINARY'),
                ('consumer.pyd', write(f.root, 'consumer.pyd', tiny_pe_with_import('kept.dll')), 'EXTENSION')]
    result, _ = policy.filter_tocs(binaries, [], loader=f.loader)
    assert ('ucrtbase.dll' not in {entry[0] for entry in result}) is removes


def test_malformed_retained_forwarder_conservatively_preserves_candidates(fixture):
    f = fixture
    binaries = [('ucrtbase.dll', f.binaries[0][1], 'BINARY'),
                ('kept.dll', write(f.root, 'kept.dll', exporting(forwarder='ucrtbase.#bad')), 'BINARY')]
    assert policy.filter_tocs(binaries, [], loader=f.loader)[0] == binaries


def test_named_and_ordinal_aliases_share_one_bundled_forwarder_check(fixture, monkeypatch):
    f = fixture
    binaries = [('ucrtbase.dll', f.binaries[0][1], 'BINARY'),
                ('kept.dll', write(f.root, 'kept.dll', exporting(forwarder='ucrtbase.OpenPrinterW')), 'BINARY')]
    original = policy._SystemProof.satisfies
    calls = []
    def observed(self, name, symbols, **kwargs):
        calls.append((name, list(symbols)))
        return original(self, name, symbols, **kwargs)
    monkeypatch.setattr(policy._SystemProof, 'satisfies', observed)
    policy.filter_tocs(binaries, [], loader=f.loader)
    assert calls.count(('ucrtbase.dll', ['OpenPrinterW'])) == 1


def test_newly_retained_candidate_forwarder_is_checked_on_next_fixed_point_pass(fixture):
    f = fixture
    write(f.system, 'kernelbase.dll', tiny_pe())  # consumer prevents removing CONTRACT
    Path(f.binaries[0][1]).write_bytes(exporting(forwarder='ucrtbase.Absent'))
    binaries = f.binaries + [('ucrtbase.dll', write(f.root, 'ucrt-stub.dll', tiny_pe()), 'BINARY')]
    assert policy.filter_tocs(binaries, [], loader=f.loader)[0] == binaries


def test_policy_is_inactive_outside_supported_windows(fixture, monkeypatch):
    monkeypatch.setattr(policy, '_windows10_or_later', lambda: False)
    missing = [('ucrtbase.dll', 'not-read-on-other-platforms', 'BINARY')]
    assert policy.filter_tocs(missing, []) == (missing, [])


def test_real_importer_selects_conditional_host_without_borrowing_another_context(fixture):
    f = fixture
    namespace = conditional_namespace('default.dll', 'special.pyd', 'alternate.dll')
    write(f.system, 'default.dll', tiny_pe())
    write(f.system, 'alternate.dll', exporting())
    loader = lambda: (f.system, namespace, set())
    proof = policy._SystemProof(loader)
    assert proof.satisfies(CONTRACT, ['OpenPrinterW'], requester_basename='special.pyd')
    assert not proof.satisfies(CONTRACT, ['OpenPrinterW'], requester_basename='ordinary.pyd')
    assert not proof.satisfies(CONTRACT, ['OpenPrinterW'])  # genuinely unknown parent
    binaries = [f.binaries[0], ('nested/special.pyd', f.binaries[1][1], 'EXTENSION')]
    assert [entry[0] for entry in policy.filter_tocs(binaries, [], loader=loader)[0]] == ['nested/special.pyd']
    binaries[1] = ('ordinary.pyd', binaries[1][1], 'EXTENSION')
    assert policy.filter_tocs(binaries, [], loader=loader)[0] == binaries


def test_bundled_forwarder_supplies_its_real_dll_basename_as_requester(fixture):
    f = fixture
    namespace = conditional_namespace('default.dll', 'special.dll', 'alternate.dll')
    write(f.system, 'default.dll', tiny_pe())
    write(f.system, 'alternate.dll', exporting())
    binaries = [f.binaries[0], ('nested/special.dll', write(f.root, 'special.dll',
        exporting(forwarder=CONTRACT[:-4] + '.OpenPrinterW')), 'BINARY')]
    loader = lambda: (f.system, namespace, set())
    assert [entry[0] for entry in policy.filter_tocs(binaries, [], loader=loader)[0]] == ['nested/special.dll']
    binaries[1] = ('ordinary.dll', binaries[1][1], 'BINARY')
    assert policy.filter_tocs(binaries, [], loader=loader)[0] == binaries


def test_os_forwarder_supplies_actual_forwarding_os_dll_as_requester(fixture):
    f = fixture
    name = 'api-ms-x-l1-1-0'
    namespace = conditional_namespace('ucrtbase.dll', 'ucrtbase.dll', 'kernelbase.dll', name=name)
    write(f.system, 'ucrtbase.dll', exporting(forwarder=name + '.#7'))
    binaries = [('ucrtbase.dll', f.binaries[0][1], 'BINARY'),
                ('consumer.pyd', write(f.root, 'consumer.pyd', tiny_pe_with_import('ucrtbase.dll')), 'EXTENSION')]
    loader = lambda: (f.system, namespace, set())
    assert [entry[0] for entry in policy.filter_tocs(binaries, [], loader=loader)[0]] == ['consumer.pyd']


@pytest.mark.parametrize('alternate', ['alternate.dll', 'missing.dll', 'driver.sys'])
def test_unreferenced_conditional_candidate_requires_every_measured_host_identity(fixture, alternate):
    f = fixture
    namespace = conditional_namespace('default.dll', 'special.dll', alternate)
    write(f.system, 'default.dll', tiny_pe())
    write(f.system, 'alternate.dll', tiny_pe())
    write(f.system, 'driver.sys', tiny_pe())
    loader = lambda: (f.system, namespace, set())
    result, _ = policy.filter_tocs([f.binaries[0]], [], loader=loader)
    assert (result == []) is (alternate == 'alternate.dll')


@pytest.mark.parametrize('defect', ['ambiguous_default', 'empty_default', 'empty_conditional'])
def test_unreferenced_identity_never_flattens_ambiguous_or_empty_selector_choices(fixture, defect):
    f = fixture
    if defect == 'ambiguous_default':
        namespace = native.parse_api_set(api_set_fixture('first.dll', 'second.dll',
            name=CONTRACT[:-4], hashed_prefix=CONTRACT.rsplit('-', 1)[0]))
    elif defect == 'empty_default':
        namespace = conditional_namespace('', 'special.dll', 'alternate.dll')
    else:
        namespace = conditional_namespace('default.dll', 'special.dll', '')
    for name in ('first.dll', 'second.dll', 'default.dll', 'alternate.dll'):
        write(f.system, name, tiny_pe())
    loader = lambda: (f.system, namespace, set())
    assert policy.filter_tocs([f.binaries[0]], [], loader=loader)[0] == [f.binaries[0]]


def test_generated_wrapper_preserves_entire_current_cli_and_filters_before_exe(tmp_path, monkeypatch):
    from scripts import build, handwriting_bundle
    import PyInstaller.utils.hooks as hooks
    monkeypatch.setattr(build, 'bundled_data_files', lambda root: [(root / 'locked.onnx', 'models')])
    monkeypatch.setattr(handwriting_bundle, 'data_files', lambda root: [(root / 'handwriting.onnx', 'handwriting')])
    source = tmp_path / 'src/main.py'
    source.parent.mkdir()
    source.write_text('print("fixture")')
    paths = build.resolve_release_paths(tmp_path)
    arguments = build.pyinstaller_command(tmp_path, paths)[3:]
    wrapper, generated, parsed = policy.generate_wrapped_spec(arguments)
    untouched = generated.read_bytes()
    assert wrapper.name == 'InkPick.spec' and generated.parent == wrapper.parent
    assert parsed.onefile and not parsed.console and parsed.noupx
    assert parsed.clean_build and parsed.noconfirm
    assert parsed.collect_binaries == ['onnxruntime', 'pypdfium2_raw']
    assert parsed.collect_all == ['zhconv']
    monkeypatch.setattr(hooks, 'collect_data_files', lambda name: [(name + '/data', name)])
    monkeypatch.setattr(hooks, 'copy_metadata', lambda name: [(name + '/METADATA', name)])
    monkeypatch.setattr(hooks, 'collect_dynamic_libs', lambda name: [(name + '/native.dll', name)])
    monkeypatch.setattr(hooks, 'collect_all', lambda name: ([(name + '/all', name)], [], [name + '.hidden']))
    removed = (CONTRACT, 'controlled-stub.dll', 'BINARY')
    kept = ('libssl-3-x64.dll', 'locked-ssl.dll', 'BINARY')
    data = ('model.onnx', 'model.onnx', 'DATA')
    traces = []
    def execute(path):
        calls = {}
        def analysis(*args, **kwargs):
            calls['analysis'] = deepcopy((args, kwargs))
            return SimpleNamespace(pure=[], scripts=['main.py'], binaries=[removed, kept], datas=[data])
        def exe(*args, **kwargs):
            calls['exe'] = deepcopy((args, kwargs))
        namespace = {'SPECPATH': str(path.parent), 'Analysis': analysis,
                     'PYZ': lambda value: 'PYZ', 'EXE': exe}
        exec(compile(path.read_bytes(), str(path), 'exec'), namespace)
        traces.append(calls)
    execute(generated)
    def filter_completed_analysis(value):
        assert value.binaries == [removed, kept]  # late or cached Analysis output
        value.binaries.remove(removed)
        return value
    monkeypatch.setattr(policy, 'filter_analysis', filter_completed_analysis)
    execute(wrapper)
    assert generated.read_bytes() == untouched
    direct, wrapped = traces
    assert direct['analysis'] == wrapped['analysis']
    assert direct['exe'][1] == wrapped['exe'][1]
    assert direct['exe'][0] == ('PYZ', ['main.py'], [removed, kept], [data], [])
    assert wrapped['exe'][0] == ('PYZ', ['main.py'], [kept], [data], [])


def test_wrapper_build_invocation_keeps_only_build_flags(tmp_path, monkeypatch):
    import PyInstaller.__main__ as pyi
    wrapper = tmp_path / 'InkPick.spec'
    parsed = SimpleNamespace(distpath='dist', workpath='build', clean_build=True,
                             noconfirm=True, upx_dir=None, loglevel='INFO')
    monkeypatch.setattr(policy, 'generate_wrapped_spec', lambda arguments: (wrapper, None, parsed))
    calls = []
    monkeypatch.setattr(pyi, 'run', calls.append)
    assert policy.main(['--onefile', '--windowed', 'main.py']) == 0
    assert calls == [[str(wrapper), '--distpath', 'dist', '--workpath', 'build',
                      '--clean', '--noconfirm', '--log-level', 'INFO']]
