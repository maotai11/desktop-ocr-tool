"""Actual PDFium packaging oracle and fixed, non-private failure reports."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src import document_probe


def test_real_pdfium_images_regions_and_templates_pass_without_a_qt_application(tmp_path):
    report_path = tmp_path / 'documents.json'
    assert document_probe.run_document_probe(report_path) == 0
    report = json.loads(report_path.read_text())
    assert report['probe'] == 'document_template_lifecycle'
    assert report['fixture_kind'] == 'SYNTHETIC_SOLID_RECTANGLES'
    assert report['passed'] is True
    assert report['checks'] == dict.fromkeys(document_probe.CHECK_NAMES, True)
    assert report['clean_machine_verified'] is False
    assert report['ocr_accuracy_tested'] is False
    assert report['model_inference_tested'] is False
    assert report['frozen'] is False and report['executable_sha256'] is None
    assert not any(key in report for key in ('path', 'text', 'result', 'traceback', 'error'))


@pytest.mark.parametrize('failing_stage', ['load_sources', 'render_page'])
def test_loader_or_renderer_failure_replaces_previous_pass_with_fixed_error(
        tmp_path, monkeypatch, failing_stage):
    report_path = tmp_path / 'documents.json'
    report_path.write_text('{"passed": true, "private": "old report"}')
    directories = []
    real_pdf = document_probe._write_fixture_pdf

    def create_fixture(path):
        directories.append(path.parent)
        real_pdf(path)

    def failure(*args, **kwargs):
        raise ValueError('C:/Users/PrivatePerson/real-invoice.pdf sk-private-fixture')

    monkeypatch.setattr(document_probe, '_write_fixture_pdf', create_fixture)
    monkeypatch.setattr(document_probe, failing_stage, failure)
    assert document_probe.run_document_probe(report_path) == 1
    report = json.loads(report_path.read_text())
    assert report['passed'] is False
    assert report['error_code'] == 'DOCUMENT_PROBE_CHECK_FAILED'
    assert not report['checks']['pdf_rendering']
    assert report['checks']['temporary_files_cleaned'] is True
    assert directories and all(not path.exists() for path in directories)
    raw = report_path.read_text()
    for private in ('PrivatePerson', 'invoice', 'sk-private', 'old report', 'traceback'):
        assert private not in raw


def test_modified_synthetic_source_is_rejected_and_cleaned(tmp_path, monkeypatch):
    original = document_probe.load_sources

    def mutate_after_import(paths):
        sources = original(paths)
        Path(paths[0]).write_bytes(b'Changed after import')
        return sources

    monkeypatch.setattr(document_probe, 'load_sources', mutate_after_import)
    report_path = tmp_path / 'documents.json'
    assert document_probe.run_document_probe(report_path) == 1
    report = json.loads(report_path.read_text())
    assert not report['passed'] and not report['checks']['source_unchanged']
    assert report['checks']['temporary_files_cleaned'] is True


def test_frozen_report_uses_executable_identity_without_changing_loader_sys(tmp_path, monkeypatch):
    monkeypatch.setattr(document_probe, 'sys', SimpleNamespace(platform='win32', frozen=True))
    monkeypatch.setattr(document_probe, 'executable_sha256', lambda: 'a' * 64)
    report_path = tmp_path / 'documents.json'
    assert document_probe.run_document_probe(report_path) == 0
    report = json.loads(report_path.read_text())
    assert report['frozen'] is True and report['platform'] == 'win32'
    assert report['executable_sha256'] == 'a' * 64
    assert report['passed'] is True


def test_unwritable_report_returns_failure_without_printing_private_exception(tmp_path, monkeypatch, capsys):
    def failure(*args):
        raise OSError('/private/user/report/location')

    monkeypatch.setattr(document_probe, 'write_validation_report', failure)
    assert document_probe.run_document_probe(tmp_path / 'documents.json') == 1
    assert capsys.readouterr() == ('', '')
