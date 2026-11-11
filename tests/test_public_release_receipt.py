import json
from dataclasses import replace
import pytest
from scripts import public_release_receipt as r
from scripts import publish_prerelease as p

CTX=p.RunnerContext('a'*40,'123','1')

@pytest.fixture
def public_input(tmp_path):
    root=tmp_path/'public'; root.mkdir()
    for name in (p.EXE_NAME,p.ZIP_NAME,p.METADATA_NAME,p.README_NAME):
        (root/name).write_bytes(('synthetic fixture '+name).encode())
    sums=''.join(f'{p.sha256_file(root/name)}  {name}\n' for name in
                 (p.EXE_NAME,p.ZIP_NAME,p.METADATA_NAME,p.README_NAME))
    (root/p.CHECKSUM_NAME).write_bytes(sums.encode('ascii'))
    assets=tuple(p.Asset.from_file(root/name,kind) for name,kind in r.expected_assets().items())
    prepared=p.PreparedRelease(CTX,assets,p.release_notes(CTX,assets))
    r.write_receipt(prepared)
    return root

def mutate(root,key,value):
    path=root/r.RECEIPT
    data=json.loads(path.read_bytes());data[key]=value
    path.write_bytes(p.canonical_json(data))

def test_same_run_receipt_roundtrip(public_input):
    prepared=r.load_prepared(public_input,CTX)
    assert len(prepared.assets)==5
    assert prepared.context==CTX
    assert prepared.body==p.release_notes(CTX,prepared.assets)

@pytest.mark.parametrize('field,value',[('source_commit','b'*40),('run_id','124')])
def test_cross_build_rejected(public_input,field,value):
    with pytest.raises(p.PublicationError):r.load_prepared(public_input,replace(CTX,**{field:value}))


def test_later_same_run_publisher_rechecks_original_asset_bytes(public_input):
    original = r.load_prepared(public_input, CTX)
    retried = r.load_prepared(public_input, replace(CTX, run_attempt='2'))
    assert retried.context.run_attempt == '2'
    assert retried.assets == original.assets
    assert retried.body == original.body
    (public_input / p.EXE_NAME).write_bytes(b'changed after original validation')
    with pytest.raises(p.PublicationError):
        r.load_prepared(public_input, replace(CTX, run_attempt='2'))


@pytest.mark.parametrize('attempt', ['2', '0', '01', '-1', '', '1.0', '１', '9' * 10, 1, True])
def test_newer_or_invalid_receipt_attempt_rejected(public_input, attempt):
    mutate(public_input, 'run_attempt', attempt)
    with pytest.raises(p.PublicationError, match='receipt attempt'):
        r.load_prepared(public_input, CTX)


@pytest.mark.parametrize('field,value', [('source_commit', 'b' * 40), ('run_id', '124')])
def test_earlier_receipt_from_other_build_remains_rejected(public_input, field, value):
    mutate(public_input, field, value)
    with pytest.raises(p.PublicationError, match='another build'):
        r.load_prepared(public_input, replace(CTX, run_attempt='2'))

@pytest.mark.parametrize('field,value',[('schema',True),('schema',2),('repository','else/repo'),('version','invalid'),('assets',{}),('extra','unexpected')])
def test_receipt_schema_and_identity_rejected(public_input,field,value):
    mutate(public_input,field,value)
    with pytest.raises(p.PublicationError):r.load_prepared(public_input,CTX)

@pytest.mark.parametrize('name',[p.EXE_NAME,p.ZIP_NAME,p.CHECKSUM_NAME,p.METADATA_NAME,p.README_NAME])
def test_modified_asset_rejected(public_input,name):
    path=public_input/name;raw=path.read_bytes();path.write_bytes(b'X'+raw[1:])
    with pytest.raises(p.PublicationError):r.load_prepared(public_input,CTX)

@pytest.mark.parametrize('kind',['missing','extra','directory','duplicate_json','oversize'])
def test_bad_input_rejected(public_input,kind):
    if kind=='missing':(public_input/p.EXE_NAME).rename(public_input/'wrong.exe')
    if kind=='extra':(public_input/'private.log').write_text('private')
    if kind=='directory':(public_input/'extra').mkdir()
    if kind=='duplicate_json':(public_input/r.RECEIPT).write_text('{"schema":1,"schema":1}')
    if kind=='oversize':(public_input/r.RECEIPT).write_bytes(b' '*16385)
    with pytest.raises(p.PublicationError):r.load_prepared(public_input,CTX)

def test_rehashed_wrong_checksum_still_rejected(public_input):
    path=public_input/p.CHECKSUM_NAME;path.write_text('wrong checksum')
    receipt=json.loads((public_input/r.RECEIPT).read_bytes())
    receipt['assets'][p.CHECKSUM_NAME]={'bytes':path.stat().st_size,'sha256':p.sha256_file(path)}
    (public_input/r.RECEIPT).write_bytes(p.canonical_json(receipt))
    with pytest.raises(p.PublicationError):r.load_prepared(public_input,CTX)


def test_manual_is_required_even_when_other_four_assets_are_unchanged(public_input):
    (public_input/p.README_NAME).unlink()
    with pytest.raises(p.PublicationError, match='Unexpected public input files'):
        r.load_prepared(public_input,CTX)


def test_rehashed_checksum_cannot_omit_the_manual(public_input):
    checksum=public_input/p.CHECKSUM_NAME
    checksum.write_bytes(''.join(f'{p.sha256_file(public_input/name)}  {name}\n' for name in
                               (p.EXE_NAME,p.ZIP_NAME,p.METADATA_NAME)).encode('ascii'))
    receipt=json.loads((public_input/r.RECEIPT).read_bytes())
    receipt['assets'][p.CHECKSUM_NAME]={'bytes':checksum.stat().st_size,'sha256':p.sha256_file(checksum)}
    (public_input/r.RECEIPT).write_bytes(p.canonical_json(receipt))
    with pytest.raises(p.PublicationError, match='Public checksum mismatch'):
        r.load_prepared(public_input,CTX)


def test_rehashed_oversized_manual_is_refused(public_input):
    manual=public_input/p.README_NAME
    manual.write_bytes(b'x'*(p.MAX_GUIDE_BYTES+1))
    receipt=json.loads((public_input/r.RECEIPT).read_bytes())
    receipt['assets'][p.README_NAME]={'bytes':manual.stat().st_size,'sha256':p.sha256_file(manual)}
    (public_input/r.RECEIPT).write_bytes(p.canonical_json(receipt))
    with pytest.raises(p.PublicationError, match='Invalid asset record'):
        r.load_prepared(public_input,CTX)

def test_redirected_asset_rejected(public_input,tmp_path):
    path=public_input/p.EXE_NAME;outside=tmp_path/'outside';path.rename(outside)
    try:path.symlink_to(outside)
    except OSError:pytest.skip('host does not permit symlinks')
    with pytest.raises(p.PublicationError):r.load_prepared(public_input,CTX)


def test_public_prepare_cli_is_credential_free(public_input,tmp_path,monkeypatch):
    monkeypatch.setattr(p,'validate_runner_context',lambda _:CTX)
    monkeypatch.setattr(p,'GitHubClient',lambda *_:pytest.fail('Network/token client must not be constructed'))
    assert p.main(['--public-input',str(public_input),'--output-dir',str(tmp_path/'evidence'),'--prepare-only'])==0


def test_invalid_public_input_never_constructs_client(public_input,tmp_path,monkeypatch):
    monkeypatch.setattr(p,'validate_runner_context',lambda _:CTX)
    monkeypatch.setattr(p,'GitHubClient',lambda *_:pytest.fail('Invalid input reached credential boundary'))
    (public_input/p.EXE_NAME).write_bytes(b'bad')
    assert p.main(['--public-input',str(public_input),'--output-dir',str(tmp_path/'evidence')])==1
    assert not (tmp_path/'evidence').exists()


@pytest.mark.parametrize('invalid_receipt', [False, True])
def test_script_entrypoint_works_without_installed_packages(public_input,tmp_path,invalid_receipt):
    import os
    import subprocess
    import sys
    from pathlib import Path
    event=tmp_path/'event.json'
    event.write_bytes(p.canonical_json({'repository':{'full_name':p.REPOSITORY},'ref':p.BRANCH_REF,
        'after':CTX.source_commit,'deleted':False,'head_commit':{'id':CTX.source_commit,'message':p.PUBLISH_MARKER}}))
    env={**os.environ,'GITHUB_ACTIONS':'true','GITHUB_EVENT_NAME':'push','GITHUB_REPOSITORY':p.REPOSITORY,
         'GITHUB_REF':p.BRANCH_REF,'GITHUB_SHA':CTX.source_commit,'GITHUB_RUN_ID':CTX.run_id,
         'GITHUB_RUN_ATTEMPT':CTX.run_attempt,'GITHUB_EVENT_PATH':str(event)}
    env.pop('GH_TOKEN',None)
    if invalid_receipt:
        mutate(public_input, 'run_id', '124')
    result=subprocess.run([sys.executable,'-S',str(Path(p.__file__).resolve()),'--public-input',str(public_input),
                          '--output-dir',str(tmp_path/'script-evidence'),'--prepare-only'],env=env,
                          capture_output=True,text=True,timeout=15,cwd=tmp_path)
    if invalid_receipt:
        assert result.returncode == 1
        assert 'Public receipt belongs to another build' in result.stderr
        assert 'unexpected validation failure' not in result.stderr
        assert not (tmp_path / 'script-evidence').exists()
    else:
        assert result.returncode==0,result.stdout+result.stderr
        assert 'no network or token access' in result.stdout
