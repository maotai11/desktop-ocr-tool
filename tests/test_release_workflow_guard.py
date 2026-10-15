"""Publication privilege and opt-in are confined to the approved candidate."""
from pathlib import Path

import yaml


def test_publish_workflow_is_fixed_push_only_and_job_scoped():
    root = Path(__file__).resolve().parents[1]
    workflow = yaml.load((root / '.github/workflows/validate.yml').read_text(encoding='utf-8'), Loader=yaml.BaseLoader)
    assert workflow['permissions'] == {'contents': 'read'}
    job = workflow['jobs']['publish_prerelease']
    assert job['needs'] == 'validate'
    assert job['permissions'] == {'contents': 'write', 'actions': 'read'}
    for clause in ("github.event_name == 'push'", "github.ref == 'refs/heads/fix/inkpick-handwriting'",
                   "github.repository == 'maotai11/desktop-ocr-tool'",
                   "github.event.head_commit.message == 'publish-prerelease: v1.8.0-rc.1'"):
        assert clause in job['if']
    assert job['steps'][0]['with']['ref'] == '${{ github.sha }}'
    assert job['steps'][0]['with']['persist-credentials'] == 'false'
    assert any(step.get('with', {}).get('name') == 'validation-Windows' for step in job['steps'])


def test_handwriting_build_and_both_process_firewall_gate_are_mandatory():
    root = Path(__file__).resolve().parents[1]
    workflow = yaml.load((root / '.github/workflows/validate.yml').read_text(encoding='utf-8'), Loader=yaml.BaseLoader)
    commands = '\n'.join(step.get('run', '') for step in workflow['jobs']['validate']['steps'])
    for required in ('prepare_handwriting_model.py', 'build_handwriting_helper.py',
                     'probe_handwriting_helper.py', '--self-test-handwriting source-handwriting.json',
                     'validate_handwriting_firewall.ps1', 'iterations_per_profile -ne 500'):
        assert required in commands
    script = (root / 'scripts/validate_handwriting_firewall.ps1').read_text(encoding='utf-8')
    assert 'Add-ExactBlock $exe' in script and 'Add-ExactBlock $helperPath' in script
    assert 'Get-NetFirewallApplicationFilter' in script
    assert script.index('Add-ExactBlock $helperPath') < script.index('Set-Content -LiteralPath $startPath')
    assert 'Remove-NetFirewallRule -Name $name' in script
    assert 'clean_machine_verified = $false' in script


def test_dependency_and_native_preparation_propagate_every_exit_code():
    root = Path(__file__).resolve().parents[1]
    workflow = yaml.load((root / '.github/workflows/validate.yml').read_text(encoding='utf-8'), Loader=yaml.BaseLoader)
    steps = workflow['jobs']['validate']['steps']
    commands = [step.get('run', '') for step in steps]
    expected = [
        'python -m pip wheel --wheel-dir artifacts/wheelhouse -r requirements-dev.txt',
        'python scripts/record_dependency_inventory.py --wheelhouse artifacts/wheelhouse --report artifacts/dependency-inventory.json',
        'python -m pip install --no-index --find-links artifacts/wheelhouse -r requirements-dev.txt',
        'python -m pip check',
        'python scripts/record_dependency_inventory.py --wheelhouse artifacts/wheelhouse --report artifacts/dependency-inventory.json',
        'python scripts/prepare_models.py',
        'python scripts/prepare_handwriting_model.py',
        'python scripts/build_handwriting_helper.py',
        'python scripts/probe_handwriting_helper.py --report artifacts/handwriting-native-probe.json',
    ]
    start = commands.index(expected[0])
    indexes = list(range(start, start + len(expected)))
    assert commands[start:start + len(expected)] == expected
    for index in indexes:
        # Default hosted shell propagates the only native command's exit code;
        # later prep steps run only if all preceding steps succeeded.
        assert '\n' not in commands[index]
        assert not steps[index].get('continue-on-error')
        assert 'if' not in steps[index] and 'shell' not in steps[index]


def test_public_upload_uses_same_absolute_runner_temp_directory():
    root = Path(__file__).resolve().parents[1]
    workflow = yaml.load((root / '.github/workflows/validate.yml').read_text(encoding='utf-8'), Loader=yaml.BaseLoader)
    steps = workflow['jobs']['validate']['steps']
    prepare = next(step for step in steps if step.get('id') == 'public_release')
    upload = next(step for step in steps if step.get('with', {}).get('name') == 'inkpick-public-candidate-Windows')
    directory = prepare['env']['INKPICK_PUBLIC_DIR']
    assert directory == '${{ runner.temp }}/inkpick-public-release'
    assert '--output-dir "$env:INKPICK_PUBLIC_DIR"' in prepare['run']
    assert upload['with']['path'] == directory + '/'
    assert '..' not in upload['with']['path'].split('/')
    assert upload['with']['if-no-files-found'] == 'error'
    assert "steps.public_release.outcome == 'success'" in upload['if']
