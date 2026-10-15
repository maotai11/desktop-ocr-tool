"""Public review metadata must never copy raw report content or hide failures."""
import hashlib
import json
from pathlib import Path

import pytest
import yaml

from scripts import prepare_public_validation as public

COMMIT = 'a' * 40


def write(root, relative, data):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def export(tmp_path, **kwargs):
    return public.export(tmp_path, COMMIT, kwargs.get('platform','Windows'),
                         kwargs.get('steps',{}), kwargs.get('status','failure'))


def record(report, source):
    return next(r for r in report['evidence'] if r['source'] == source)


def test_export_never_copies_paths_errors_names_or_raw_bytes(tmp_path):
    secret = 'PRIVATE_MACHINE_SENTINEL'
    raw = json.dumps({'schema':2, 'passed':False, 'version':public.VERSION,
        'absolute_path':'/home/'+secret, 'error':secret, 'traceback':secret,
        'model_profiles':{'v6-small':{'passed':False, 'error':secret}},
        'source_commit':COMMIT}).encode()
    source = write(tmp_path,'source-selftest.json',raw)
    write(tmp_path,'test-results.xml', ('<testsuites><testsuite name="'+secret+'" hostname="'+secret+'" tests="2" failures="1" errors="0" skipped="1"><testcase name="'+secret+'"><failure>'+secret+'</failure></testcase><testcase><skipped>'+secret+'</skipped></testcase></testsuite></testsuites>').encode())
    report=export(tmp_path)
    item=record(report,'source-selftest.json')
    assert item['sha256']==hashlib.sha256(raw).hexdigest()
    assert item['size_bytes']==len(raw)
    assert item['allowlisted']['passed'] is False
    assert item['observed_result']=='FAIL_AS_RECORDED'
    assert record(report,'test-results.xml')['aggregate']==dict(tests=2,failures=1,errors=0,skipped=1,testcase_count=2,outcome='FAIL')
    output=tmp_path/public.OUTPUT
    assert {p.name for p in output.iterdir()}=={'summary.json','summary.sha256'}
    assert secret not in (output/'summary.json').read_text()
    assert source.read_bytes()==raw
    assert (output/'summary.sha256').read_text().split()[0]==hashlib.sha256((output/'summary.json').read_bytes()).hexdigest()


@pytest.mark.parametrize('data', [b'{',b'{"passed":true,"passed":false}',b'{"passed":NaN}',b'[]'])
def test_invalid_json_never_becomes_pass(tmp_path,data):
    write(tmp_path,'source-selftest.json',data)
    item=record(export(tmp_path),'source-selftest.json')
    assert item['state']=='INVALID_REPORT'
    assert item['error_code']=='REPORT_PARSE_OR_SCHEMA'
    assert item['sha256']==hashlib.sha256(data).hexdigest()


@pytest.mark.parametrize('field,value', [('passed','true'),('version','1.2.3-TOKEN_PRIVATE'),
    ('platform','/home/PRIVATE'),('model_sha256','/home/PRIVATE'),('source_commit','TOKEN_PRIVATE'),
    ('iterations_completed',float('inf')),('exit_code',True),('exit_code',10**400)])
def test_allowlisted_fields_are_strict_typed_and_formatted(field,value):
    result=public.safe_fields({field:value})
    assert field not in result
    assert result['invalid_allowlisted_fields']==[field]
    assert public.observed_result(result)=='INVALID_FIELDS'


def test_steps_keep_failure_conclusion_difference_missing_and_platform_skips(tmp_path):
    report=export(tmp_path,steps={'tests':{'outcome':'failure','conclusion':'success'},
        'build':{'outcome':'skipped','conclusion':'skipped'},
        'source_selftest':{'outcome':'PRIVATE','conclusion':'failure'}},platform='Linux')
    assert report['steps']['tests']['outcome']=='failure'
    assert report['steps']['tests']['conclusion']=='success'
    assert report['steps']['build']==dict(expected_on_platform=False,outcome='skipped',conclusion='skipped')
    assert report['steps']['source_selftest']['outcome']=='INVALID'
    assert report['steps']['source_image']['outcome']=='MISSING'
    assert record(report,'source-selftest.json')['state']=='MISSING'
    assert report['preceding_job_status']=='failure'
    assert report['release_approved'] is False


@pytest.mark.parametrize('xml', [
 b'<testsuites><testsuite tests="1" failures="0" errors="0" skipped="0"><testcase><failure>PRIVATE</failure></testcase></testsuite></testsuites>',
 b'<testsuites><testsuite tests="1" failures="1" errors="0" skipped="0"><testcase/></testsuite></testsuites>',
 b'<!DOCTYPE x [<!ENTITY e "PRIVATE">]><testsuite tests="0"/>',
 b'<testsuite tests="NaN"/>', b'<testsuite tests="0"><testsuite tests="0"/></testsuite>',
])
def test_inconsistent_or_entity_junit_rejected(xml):
    with pytest.raises(ValueError):public.junit_counts(xml)


def test_symlink_source_and_output_parent_rejected(tmp_path):
    outside=tmp_path/'outside';outside.write_text('PRIVATE')
    try: (tmp_path/'source-selftest.json').symlink_to(outside)
    except OSError: pytest.skip('Symlink creation unavailable on this test host')
    assert record(export(tmp_path),'source-selftest.json')['state']=='UNSAFE_OR_UNREADABLE'
    other=tmp_path/'another';other.mkdir();(other/'artifacts').symlink_to(tmp_path/'artifacts',target_is_directory=True)
    with pytest.raises(ValueError):export(other)


def test_size_limit_and_nonempty_output_fail_closed(tmp_path,monkeypatch):
    monkeypatch.setattr(public,'MAX_REPORT',5)
    write(tmp_path,'source-selftest.json',b'PRIVATE_TOO_LARGE')
    assert record(export(tmp_path),'source-selftest.json')['state']=='SIZE_LIMIT'
    write(tmp_path,public.OUTPUT+'/unexpected.txt',b'PRIVATE')
    with pytest.raises(ValueError):export(tmp_path)


def test_binary_hash_is_actual_and_bytes_never_uploaded(tmp_path):
    relative=f'artifacts/InkPick-v{public.VERSION}.zip'
    path=write(tmp_path,relative,b'PRIVATE_FAKE_ZIP')
    item=record(export(tmp_path),relative)
    assert item['sha256']==hashlib.sha256(path.read_bytes()).hexdigest()
    assert item['size_bytes']==path.stat().st_size
    assert b'PRIVATE_FAKE_ZIP' not in (tmp_path/public.OUTPUT/'summary.json').read_bytes()


def test_true_top_level_with_failed_nested_record_is_not_public_pass():
    assert public.observed_result(public.safe_fields({'passed':True,'phases':{'execution':{'passed':False}}}))=='INCONSISTENT_AS_RECORDED'


def test_workflow_uploads_only_new_safe_directory_and_keeps_raw_gates():
    root=Path(__file__).resolve().parents[1]
    workflow=yaml.load((root/'.github/workflows/validate.yml').read_text(),Loader=yaml.BaseLoader)
    steps=workflow['jobs']['validate']['steps']
    uploads=[s for s in steps if s.get('uses','').startswith('actions/upload-artifact@')]
    assert len(uploads)==3
    metadata = [s for s in uploads if s['with']['path']=='artifacts/public-validation/']
    assert len(metadata)==2
    assert all('always()' in s['if'] and "steps.public_metadata.outcome == 'success'" in s['if'] for s in metadata)
    binaries = [s for s in uploads if s not in metadata]
    assert len(binaries)==1
    assert binaries[0]['with']['path']=='${{ runner.temp }}/inkpick-public-release/'
    assert "success()" in binaries[0]['if'] and "steps.public_release.outcome == 'success'" in binaries[0]['if']
    assert "always()" not in binaries[0]['if']
    stage=next(s for s in steps if s.get('id')=='public_release')
    assert stage['if']=="runner.os == 'Windows'"
    assert stage['env']['INKPICK_PUBLIC_DIR']=='${{ runner.temp }}/inkpick-public-release'
    assert stage['run']=='python scripts/prepare_public_release.py --artifact-root . --output-dir "$env:INKPICK_PUBLIC_DIR"'
    assert steps.index(stage)>next(i for i,s in enumerate(steps) if s.get('id')=='bundle_verify')
    prepare=next(s for s in steps if s.get('id')=='public_metadata')
    assert prepare['if']=='always()'
    assert '${{ toJSON(steps) }}'==prepare['env']['VALIDATION_STEP_RESULTS']
    assert {s.get('id') for s in steps}>={'tests','source_selftest','source_app','source_handwriting','source_image','build','firewall','bundle_verify'}
    assert next(s for s in steps if s.get('id')=='bundle_verify')['run'].startswith('python scripts/verify_candidate_bundle.py')


def test_fixed_model_identity_preserves_hashes_but_checks_models_stays_bool():
    digest='b'*64
    fields=public.safe_fields({'models':{'rec':{'sha256':digest,'absolute_path':'PRIVATE'}},
        'model_profiles':{'v6-small':{'passed':True,'models':{'det':{'sha256':digest}},
            'model_identity':{'recognition':{'character_sha256':digest,'absolute_path':'PRIVATE'}}}},
        'checks':{'models':True}})
    assert fields['models']['rec']['sha256']==digest
    assert fields['model_profiles']['v6-small']['model_identity']['recognition']['character_sha256']==digest
    assert fields['checks']['models'] is True
    assert 'PRIVATE' not in json.dumps(fields)
    invalid=public.safe_fields({'checks':{'models':{'rec':{'sha256':digest}}}})
    assert invalid['checks']['invalid_allowlisted_fields']==['models']
    assert public.observed_result(invalid)=='INVALID_FIELDS'


@pytest.mark.parametrize('field', ['failures','cases'])
def test_positive_pass_flag_with_failed_array_is_inconsistent(field):
    item={'passed':True,field:[{'passed':False,'error':'PRIVATE'}]}
    assert public.observed_result(public.safe_fields(item))=='INCONSISTENT_AS_RECORDED'


@pytest.mark.parametrize('field,value', [('platform',None),('platform',False),('probe',[]),('phase',{})])
def test_enum_wrong_types_are_invalid(field,value):
    assert public.observed_result(public.safe_fields({'passed':True,field:value}))=='INVALID_FIELDS'


def test_actual_model_lock_shape_is_allowlisted_without_marking_upstream_urls_invalid():
    root=Path(__file__).resolve().parents[1]
    lock=json.loads((root/'models/models.lock.json').read_text())
    fields=public.safe_fields({'passed':True,'models':lock,'checks':{'models':True}})
    assert public.observed_result(fields)=='PASS_AS_RECORDED_NOT_OVERALL_APPROVAL'
    assert fields['models']['rec']['version']=='pp-ocrv6-small'
    assert fields['models']['rec']['sha256']==lock['rec']['sha256']
    assert fields['models']['rec']['decoder_sha256']==lock['rec']['decoder_sha256']
    assert 'https:' not in json.dumps(fields)


def test_execution_nonzero_exit_overrides_recorded_pass():
    fields=public.safe_fields({'passed':True,'phases':{'execution':{'passed':True,'exit_code':1}}})
    assert public.observed_result(fields)=='INCONSISTENT_AS_RECORDED'


def test_json_recorded_pass_does_not_override_failed_or_missing_step(tmp_path):
    write(tmp_path,'source-selftest.json',b'{"passed":true}')
    row=record(export(tmp_path,steps={'source_selftest':{'outcome':'failure','conclusion':'failure'}}),'source-selftest.json')
    assert row['observed_result']=='INCONSISTENT_STEP_OUTCOME'
    assert row['step_outcome']=='failure'


@pytest.mark.parametrize('key', ['schema','rank','size_bytes','iterations_completed','exit_code'])
def test_discrete_fields_cannot_use_float_to_bypass_result_checks(key):
    value=public.safe_fields({'passed':True,key:1.0})
    assert public.observed_result(value)=='INVALID_FIELDS'


@pytest.mark.parametrize('key', ['timed_out','output_limit_exceeded'])
def test_explicit_timeout_output_limit_contradicts_recorded_pass(key):
    assert public.observed_result(public.safe_fields({'passed':True,key:True}))=='INCONSISTENT_AS_RECORDED'


def test_failure_fingerprints_preserve_failures_without_identifiers(tmp_path):
    private = 'PRIVATE_FAILURE_DETAIL'
    xml = ('<testsuites><testsuite tests="3" failures="1" errors="1" skipped="0">'
           '<testcase classname="tests.example" name="test_ok"/>'
           '<testcase classname="tests.example" name="test_bad[' + private + ']"><failure>' + private + '</failure></testcase>'
           '<testcase classname="tests.example" name="test_error"><error message="' + private + '"/></testcase>'
           '</testsuite></testsuites>').encode()
    write(tmp_path, 'test-results.xml', xml)
    report = export(tmp_path)
    item = record(report, 'test-results.xml')
    assert item['aggregate']['failures'] == item['aggregate']['errors'] == 1
    assert item['failed_case_fingerprints'] == [
        {'state': 'failure', 'case_sha256': hashlib.sha256(('tests.example\0test_bad[' + private + ']').encode()).hexdigest()},
        {'state': 'error', 'case_sha256': hashlib.sha256(b'tests.example\0test_error').hexdigest()}]
    encoded = json.dumps(report)
    assert private not in encoded and 'test_bad' not in encoded and 'tests.example' not in encoded


def test_failure_fingerprints_do_not_bypass_xml_validation():
    with pytest.raises(ValueError):
        public.junit_failure_fingerprints(b'<!DOCTYPE x><testsuites/>')
    with pytest.raises(ValueError):
        public.junit_failure_fingerprints(b'<testsuites><testsuite tests="0" failures="0" errors="0" skipped="0"><testcase><failure/></testcase></testsuite></testsuites>')


def test_runtime_stress_fixed_identity_and_firewall_enum_are_supported():
    identity = 'v6-small;' + ';'.join(role+':ppocr-mobile-v2.0:'+('a'*64) for role in ('cls','det','rec'))
    result = public.safe_fields({'passed': True, 'network_observation': 'OUTBOUND_BLOCKED_NOT_PACKET_TRACE', 'profiles': {'v6-small': {'passed': True, 'models': identity}}})
    assert public.observed_result(result) == 'PASS_AS_RECORDED_NOT_OVERALL_APPROVAL'
    assert result['profiles']['v6-small']['models']['rec']['sha256'] == 'a'*64
    for invalid in (identity.replace('v6-small;', 'v6-medium;'), identity+';PRIVATE', identity.replace('cls:', 'other:'), identity.replace('a'*64, '/home/PRIVATE')):
        result = public.safe_fields({'passed': True, 'profiles': {'v6-small': {'models': invalid}}})
        assert public.observed_result(result) == 'INVALID_FIELDS'
        assert 'PRIVATE' not in json.dumps(result)
    assert public.observed_result(public.safe_fields({'network_observation':'PACKET_TRACE_VERIFIED'})) == 'INVALID_FIELDS'


def test_linux_development_helper_architecture_remains_explicit():
    result = public.safe_fields({'passed': True, 'platform': 'linux', 'helper_architecture': 'LINUX_X64_ASSESSMENT'})
    assert result['helper_architecture'] == 'LINUX_X64_ASSESSMENT'
    assert public.observed_result(result) == 'PASS_AS_RECORDED_NOT_OVERALL_APPROVAL'
    assert public.observed_result(public.safe_fields({'helper_architecture': '/home/PRIVATE'})) == 'INVALID_FIELDS'
