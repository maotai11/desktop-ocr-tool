"""Bounded real-controller search proof, without private screens or model claims."""
import json
import os
from types import SimpleNamespace

from src import shared_search_probe as probe


def test_cli_shared_search_probe_keeps_private_state_and_proves_logical_lifecycle(qapp, tmp_path):
    from src.comparison import shared_capture
    provider = shared_capture.mss.mss
    before_clipboard = qapp.clipboard().text()
    before_home = os.environ.get('DESKTOP_OCR_HOME')
    report_path = tmp_path / 'shared.json'
    assert probe.run_shared_search_probe(report_path) == 0
    report = json.loads(report_path.read_text(encoding='utf-8'))
    assert report['passed'] is True and report['checks'] == dict.fromkeys(probe.CHECK_NAMES, True)
    assert report['probe'] == 'shared_search_lifecycle'
    assert report['model_inference_tested'] is False and report['ocr_accuracy_tested'] is False
    assert report['clean_machine_verified'] is False
    if report['platform'] == 'win32':
        assert report['overlay_capture_verified'] is True
    else:
        assert report['overlay_capture_verified'] is False
    assert shared_capture.mss.mss is provider
    assert qapp.clipboard().text() == before_clipboard
    assert os.environ.get('DESKTOP_OCR_HOME') == before_home
    assert not any(word in report_path.read_text(encoding='utf-8') for word in ('訂單AB', '新捲動', '過期列'))


def test_capture_failure_replaces_old_success_without_private_paths(qapp, tmp_path, monkeypatch):
    from src.comparison import shared_capture
    factory = shared_capture.mss.mss
    def unavailable(*args):
        raise OSError('C:/Users/PRIVATE_SEARCH/SECRET_SCREEN.png')
    monkeypatch.setattr(shared_capture, 'capture_shared', unavailable)
    path = tmp_path / 'shared.json'
    path.write_text('{"passed": true, "text": "old PRIVATE_WORDS"}')
    assert probe.run_shared_search_probe(path) == 1
    report = json.loads(path.read_text(encoding='utf-8'))
    assert report['passed'] is False and report['error_code'] == 'SHARED_SEARCH_PROBE_CHECK_FAILED'
    assert not any(word in path.read_text(encoding='utf-8') for word in ('PRIVATE_SEARCH', 'SECRET_SCREEN', 'PRIVATE_WORDS'))
    assert shared_capture.mss.mss is factory


def test_cli_dispatches_shared_search_before_normal_application(tmp_path, monkeypatch):
    from src import main
    called = []
    monkeypatch.setattr(probe, 'run_shared_search_probe', lambda path: called.append(path) or 0)
    path = tmp_path / 'shared.json'
    assert main.run(['--self-test-shared-search', str(path)]) == 0
    assert called == [str(path)]


def test_windows_run_requires_actual_native_pixel_proof(qapp, tmp_path, monkeypatch):
    from src import shared_search_windows_probe as native
    monkeypatch.setattr(probe, 'sys', SimpleNamespace(platform='win32'))
    monkeypatch.setattr(probe, '_logical_checks', lambda app: dict.fromkeys(probe.CHECK_NAMES, True))
    monkeypatch.setattr(native, 'verify_overlay_capture', lambda app: False)
    path = tmp_path / 'native-refused.json'
    assert probe.run_shared_search_probe(path) == 1
    report = json.loads(path.read_text(encoding='utf-8'))
    assert report['overlay_capture_verified'] is False and report['passed'] is False
    assert report['error_code'] == 'SHARED_SEARCH_PROBE_CHECK_FAILED'


def test_invoice_annotation_probe_rejects_source_region_as_result_anchor(qapp, tmp_path, monkeypatch):
    """An old A-position implementation must not satisfy the new B proof."""
    from src.ui.search_highlight_overlay import SearchHighlightOverlay
    real = SearchHighlightOverlay.callouts.fget

    def wrong_anchor(overlay):
        return tuple({**card, 'anchor': (20., 40., 50., 12.)} for card in real(overlay))

    monkeypatch.setattr(SearchHighlightOverlay, 'callouts', property(wrong_anchor))
    path = tmp_path / 'wrong-anchor.json'
    assert probe.run_shared_search_probe(path) == 1
    report = json.loads(path.read_text(encoding='utf-8'))
    assert report['passed'] is False
    assert report['checks']['invoice_match_annotation'] is False


def test_near_number_cannot_be_reported_as_verified_by_probe(qapp, tmp_path, monkeypatch):
    """Inject false-green handling after actual numeric matching has run."""
    from src.comparison import row_values
    real = row_values.match_field

    def false_green(*args, **kwargs):
        result = real(*args, **kwargs)
        if result['text'] == '12345' and result['candidates']:
            result['status'] = 'matched'
        return result

    monkeypatch.setattr(row_values, 'match_field', false_green)
    path = tmp_path / 'false-green.json'
    assert probe.run_shared_search_probe(path) == 1
    report = json.loads(path.read_text(encoding='utf-8'))
    assert report['passed'] is False
    assert report['checks']['numeric_similarity_review'] is False


def test_whole_source_probe_runs_actual_default_rows_and_releases_workers(qapp):
    from src.comparison import shared_capture
    provider = shared_capture.mss.mss
    clipboard = qapp.clipboard().text()
    checks = probe._row_region_checks(qapp)
    assert len(checks) == 7 and all(checks.values())
    assert shared_capture.mss.mss is provider and qapp.clipboard().text() == clipboard


def test_whole_source_probe_rejects_old_invoice_result_after_changed_pixels(qapp, monkeypatch):
    """A stale OCR cache must fail even when the worker did run again."""
    from copy import deepcopy
    from src.comparison.shared_controller import SharedSearchController
    recognized = SharedSearchController._recognized
    reused = []

    def stale_invoice(self, token, page, name, result):
        if name == 'B' and self._b_cache is not None:
            reused.append(name)
            result = deepcopy(self._b_cache[1])
        return recognized(self, token, page, name, result)

    monkeypatch.setattr(SharedSearchController, '_recognized', stale_invoice)
    checks = probe._row_region_checks(qapp)
    assert reused and checks['changed_invoice_refreshes_matches'] is False
    assert checks['workers_stopped'] and checks['clipboard_history_untouched']


def test_whole_source_probe_rejects_arrow_left_on_previous_row(qapp, monkeypatch):
    from src.ui.search_highlight_overlay import SearchHighlightOverlay
    original = SearchHighlightOverlay.source_row_arrow.fget
    observed = []

    def stationary_arrow(self):
        arrow = original(self)
        if arrow is None:
            return None
        observed.append(arrow)
        return observed[0]

    monkeypatch.setattr(SearchHighlightOverlay, 'source_row_arrow', property(stationary_arrow))
    checks = probe._row_region_checks(qapp)
    assert len(set(observed)) >= 2
    assert checks['source_region_rows_extracted'] is True
    assert checks['source_row_arrow_navigation'] is False and checks['workers_stopped']


def test_whole_source_probe_rejects_numeric_near_value_marked_as_verified(qapp, monkeypatch):
    from src.comparison import row_values
    match = row_values.match_field
    altered = []

    def false_green(*args, **kwargs):
        result = match(*args, **kwargs)
        if result['text'] == '67890' and result['candidates']:
            altered.append(result['score'])
            result['status'] = 'matched'
        return result

    monkeypatch.setattr(row_values, 'match_field', false_green)
    checks = probe._row_region_checks(qapp)
    assert altered and checks['near_candidates_visible_for_review'] is False
    assert checks['workers_stopped'] and checks['clipboard_history_untouched']
