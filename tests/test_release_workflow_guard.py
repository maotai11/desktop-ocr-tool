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
    for clause in ("github.event_name == 'push'", "github.ref == 'refs/heads/fix/pending-paste-numeric-copy'",
                   "github.repository == 'maotai11/desktop-ocr-tool'",
                   "github.event.head_commit.message == 'publish-prerelease: v1.7.0-rc.2'"):
        assert clause in job['if']
    assert job['steps'][0]['with']['ref'] == '${{ github.sha }}'
    assert job['steps'][0]['with']['persist-credentials'] == 'false'
    assert any(step.get('with', {}).get('name') == 'validation-Windows' for step in job['steps'])
