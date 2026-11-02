"""New document smoke evidence is routed early and exported without document data."""
import json

from scripts.prepare_public_validation import (
    observed_result,
    safe_fields,
    source_step,
    sources,
)


def test_document_cli_probe_precedes_normal_application(tmp_path, monkeypatch):
    from src import document_probe, main
    calls = []
    monkeypatch.setattr(document_probe, 'run_document_probe', lambda path: calls.append(path) or 7)
    report = tmp_path / 'document.json'
    assert main.run(['--self-test-documents', str(report)]) == 7
    assert calls == [str(report.resolve())]


def test_document_metadata_only_keeps_fixed_probe_flags():
    raw = {
        'passed': True, 'probe': 'document_template_lifecycle',
        'fixture_kind': 'SYNTHETIC_SOLID_RECTANGLES',
        'ocr_accuracy_tested': False, 'model_inference_tested': False,
        'checks': {'pdf_rendering': True, 'image_rendering': True,
                   'region_inheritance': True, 'page_override': True,
                   'template_merge': True, 'source_unchanged': True,
                   'temporary_files_cleaned': True},
        'input_path': 'PRIVATE_INPUT_SENTINEL', 'text': 'PRIVATE_TEXT_SENTINEL',
        'template': 'PRIVATE_TEMPLATE_SENTINEL',
    }
    clean = safe_fields(raw)
    assert clean['probe'] == raw['probe']
    assert clean['checks'] == {**raw['checks'], 'nonpublic_error_present': False}
    assert not clean['ocr_accuracy_tested'] and not clean['model_inference_tested']
    assert 'PRIVATE_' not in json.dumps(clean)
    assert observed_result(clean) == 'PASS_AS_RECORDED_NOT_OVERALL_APPROVAL'
    with_error = safe_fields({**raw, 'traceback': 'PRIVATE_ERROR_SENTINEL'})
    assert 'PRIVATE_' not in json.dumps(with_error)
    assert with_error['nonpublic_error_present']
    assert observed_result(with_error) == 'INCONSISTENT_AS_RECORDED'
    clean['checks']['pdf_rendering'] = False
    assert observed_result(clean) == 'INCONSISTENT_AS_RECORDED'


def test_source_and_frozen_document_reports_have_distinct_steps():
    records = {path: (kind, windows) for path, kind, windows in sources()}
    assert records['artifacts/source-documents.json'] == ('json', False)
    assert records['artifacts/document-validation/frozen-documents.json'] == ('json', True)
    assert source_step('artifacts/source-documents.json') == 'source_documents'
    assert source_step('artifacts/document-validation/frozen-documents.json') == 'firewall'
