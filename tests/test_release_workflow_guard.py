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
