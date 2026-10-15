"""Synthetic archive bytes, not a Windows release or license certification."""
import hashlib
import json
import stat
import zipfile
import pytest
from scripts import public_release_bundle as public


def sha(raw): return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def bundle(tmp_path):
    candidate = tmp_path / 'private.zip'
    members = {'release/InkPick.exe': b'synthetic-exe',
               'release/LICENSE.txt': b'synthetic-license',
               'release/handwriting-source/source.cpp': b'synthetic-source',
               'release/BUILD_MANIFEST.json': b'PRIVATE_MACHINE_PATH',
               'release/candidate-validation/probe.json': b'PRIVATE_ERROR'}
    def write():
        with zipfile.ZipFile(candidate, 'w') as z:
            for k, v in members.items(): z.writestr(k, v)
    write()
    approved = {k.removeprefix('release/'):sha(v) for k,v in members.items()
                if 'PRIVATE' not in v.decode()}
    def run(output=None, **overrides):
        args = dict(prefix='release/', approved=approved, executable='InkPick.exe',
                    candidate_sha256=public.file_hash(candidate),
                    executable_sha256=sha(b'synthetic-exe'), source_commit='a'*40)
        args.update(overrides)
        return public.prepare_public_bundle(candidate, output or tmp_path/'public.zip', **args)
    return candidate, members, write, approved, run


def test_public_zip_keeps_exact_approved_bytes_only(bundle, tmp_path):
    candidate, members, _, approved, run = bundle
    before = candidate.read_bytes()
    result = run()
    assert candidate.read_bytes() == before
    with zipfile.ZipFile(tmp_path/'public.zip') as z:
        assert set(z.namelist()) == {'release/'+n for n in approved} | {'release/PUBLIC_MANIFEST.json'}
        for n in approved: assert z.read('release/'+n) == members['release/'+n]
        assert all(b'PRIVATE' not in z.read(n) for n in z.namelist())
        manifest = json.loads(z.read('release/PUBLIC_MANIFEST.json'))
        assert manifest['clean_machine_verified'] is False
    assert result['original_candidate_sha256'] == sha(before)
    assert result['public_bundle_sha256'] == public.file_hash(tmp_path/'public.zip')
    assert result['original_candidate_sha256'] != result['public_bundle_sha256']


def test_deterministic_output(bundle,tmp_path):
    *_,run=bundle
    a=run(tmp_path/'a.zip'); b=run(tmp_path/'b.zip')
    assert a==b


@pytest.mark.parametrize('path',['../evil','/root','release/../evil','release/C:evil',
                               'release/CON.txt','release/x\\y','release/trailing.',
                               'release/InkPick.EXE'])
def test_unsafe_or_case_colliding_member_fails_without_output(bundle,tmp_path,path):
    candidate,members,write,_,run=bundle
    members[path]=b'unsafe'; write()
    with pytest.raises(public.BundleError): run()
    assert not (tmp_path/'public.zip').exists()


def test_symlink_member_refused(bundle,tmp_path):
    candidate,_,_,_,run=bundle
    with zipfile.ZipFile(candidate,'a') as z:
        i=zipfile.ZipInfo('release/link'); i.create_system=3
        i.external_attr=(stat.S_IFLNK|0o777)<<16; z.writestr(i,b'/private')
    with pytest.raises(public.BundleError):run()


def test_wrong_approved_hash_cleans_staging(bundle,tmp_path):
    _,_,_,approved,run=bundle
    approved['LICENSE.txt']='0'*64
    with pytest.raises(public.BundleError):run()
    assert not list(tmp_path.glob('.inkpick-public-*'))
    assert not (tmp_path/'public.zip').exists()


def test_output_never_overwrites(bundle,tmp_path):
    *_,run=bundle
    out=tmp_path/'public.zip';out.write_bytes(b'owner data')
    with pytest.raises(public.BundleError):run()
    assert out.read_bytes()==b'owner data'


@pytest.mark.parametrize('name',['BUILD_MANIFEST.json','candidate-validation/probe.json',
                                 'docs/private.md','PUBLIC_MANIFEST.json'])
def test_approval_cannot_include_private_report_slots(bundle,name):
    _,_,_,approved,run=bundle;approved[name]='a'*64
    with pytest.raises(public.BundleError):run()


def test_missing_approved_member_refused(bundle):
    _,_,_,approved,run=bundle;approved['missing.txt']='a'*64
    with pytest.raises(public.BundleError):run()


def test_resource_bounds_enforced(bundle,monkeypatch):
    *_,run=bundle;monkeypatch.setattr(public,'MAX_TOTAL',1)
    with pytest.raises(public.BundleError):run()


def test_candidate_binding_refused(bundle):
    *_,run=bundle
    with pytest.raises(public.BundleError):run(candidate_sha256='0'*64)


def test_duplicate_exact_member_refused(bundle):
    candidate,_,_,_,run=bundle
    with pytest.warns(UserWarning), zipfile.ZipFile(candidate,'a') as z:
        z.writestr('release/InkPick.exe',b'other')
    with pytest.raises(public.BundleError):run()


def test_racing_output_is_not_clobbered(bundle,tmp_path,monkeypatch):
    *_,run=bundle
    link=public.os.link
    def create_then_link(src,dst):
        dst.write_bytes(b'new owner data')
        return link(src,dst)
    monkeypatch.setattr(public.os,'link',create_then_link)
    with pytest.raises(FileExistsError):run()
    assert (tmp_path/'public.zip').read_bytes()==b'new owner data'
    assert not list(tmp_path.glob('.inkpick-public-*'))


def test_interrupted_copy_never_leaves_partial_public_output(bundle,tmp_path,monkeypatch):
    *_,run=bundle
    original=zipfile.ZipFile.open
    def fail_write(self,name,mode='r',*args,**kwargs):
        if mode=='w':raise OSError('synthetic disk failure')
        return original(self,name,mode,*args,**kwargs)
    monkeypatch.setattr(zipfile.ZipFile,'open',fail_write)
    with pytest.raises(OSError):run()
    assert not (tmp_path/'public.zip').exists()
    assert not list(tmp_path.glob('.inkpick-public-*'))


@pytest.mark.parametrize('name', ['build_manifest.JSON', 'Candidate-Validation/probe.json',
                                 'DOCS/private.md', 'public_manifest.JSON',
                                 'nested/Artifacts/raw.json'])
def test_private_slots_are_case_insensitive(bundle, name):
    _, _, _, approved, run = bundle
    approved[name] = 'a' * 64
    with pytest.raises(public.BundleError, match='Private report'):
        run()


def source_inventory():
    files = {name: sha(name.encode()) for name in
             ('helper.cpp', 'model_and_strokes.h', 'CMakeLists.txt', 'vendor/zinnia/COPYING')}
    identity = {'helper': {'source': {'files': files}}}
    source = {'native/handwriting/' + n: {'sha256': h} for n, h in files.items()}
    source.update({name: {'sha256': sha(name.encode())} for name in
                   ('THIRD_PARTY_NOTICES.md', 'LICENSE-APACHE-2.0.txt',
                    'models/handwriting/manifest.json', 'native/handwriting/UPSTREAM_MANIFEST.json',
                    'native/handwriting/patches/0001-bound-vertex-tree.patch')})
    return identity, source


def test_public_selection_uses_committed_inventory_not_archive_names():
    identity, source = source_inventory()
    source['docs/private.md'] = {'sha256': sha(b'private')}
    selected = public.approved_release_files(executable='InkPick.exe', executable_sha256='a'*64,
                                            identity=identity, source_files=source)
    assert 'docs/private.md' not in selected
    assert selected['THIRD_PARTY_NOTICES.md'] == source['THIRD_PARTY_NOTICES.md']['sha256']
    assert 'handwriting-source/zinnia/vendor/zinnia/COPYING' in selected
    from scripts.handwriting_bundle import SOURCE_SHA256
    assert selected['handwriting-source/tegaki-zh_TW/handwriting-zh_TW.xml'] == SOURCE_SHA256


@pytest.mark.parametrize('defect', ['missing_license', 'changed_native', 'unknown_native', 'missing_helper'])
def test_public_selection_rejects_incomplete_or_unbound_sources(defect):
    identity, source = source_inventory()
    files = identity['helper']['source']['files']
    if defect == 'missing_license': del source['LICENSE-APACHE-2.0.txt']
    if defect == 'changed_native': source['native/handwriting/helper.cpp']['sha256'] = 'b'*64
    if defect == 'unknown_native':
        files['private.json'] = 'c'*64
        source['native/handwriting/private.json'] = {'sha256': 'c'*64}
    if defect == 'missing_helper': del files['helper.cpp']
    with pytest.raises(public.BundleError):
        public.approved_release_files(executable='InkPick.exe', executable_sha256='a'*64,
                                      identity=identity, source_files=source)
