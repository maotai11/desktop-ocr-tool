"""Retry orchestration contracts + real SQLite/Qt review integration.

Model stubs test policy, not OCR accuracy; the native same-fixture benchmark is
scripts/benchmark_retry_policy.py. No test claims cancellation of a hung native call.
"""
import json
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor

from src.data.exporter import Exporter
from src.data.models import OcrResultDTO
from src.ocr.bounded_recognizer import BoundedRecognizer
from src.ocr.engine import OcrEngine
from src.ui.editor_window import EditorWindow
from src.workers.db_worker import DbWorker
from src.workers.ocr_worker import OcrWorker


def raw(text='NT$ 100.00', confidence=.7):
    return [[[20, 20], [180, 20], [180, 50], [20, 50]], text, confidence]


def image():
    # Nonbinary gradient makes all three variants distinguishable.
    return np.broadcast_to(np.arange(160, dtype=np.uint8)[None, :, None], (160, 160, 3)).copy()


def ready(monkeypatch, outputs, **kwargs):
    engine = OcrEngine(max_image_short_side=160, **kwargs)
    engine._ready = True
    engine._model_version = 'fixture-model'
    calls = []
    outputs = iter(outputs)

    def recognize(im):
        calls.append(im.copy())
        value = next(outputs)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(engine, '_do_ocr_array', recognize)
    return engine, calls


def persist(item_repo, item_id, result):
    job, revision = item_repo.begin_ocr_attempt(item_id)
    item_repo.update_ocr_result(item_id, OcrResultDTO(
        text=result['text'], confidence=result['confidence'], status=result['status'],
        detail_json=json.dumps(result.get('detail', []), ensure_ascii=False),
        provenance_json=json.dumps(result, ensure_ascii=False),
        job_id=job, base_edit_revision=revision,
        engine=result.get('engine', 'fixture'), model_version=result.get('model_version', 'fixture')))
    return job


@pytest.mark.parametrize('limit', [1, 2, 3])
def test_configured_pass_limit_and_nonidentical_variants(monkeypatch, limit):
    engine, calls = ready(monkeypatch, [[raw()]] * 3, max_ocr_passes=limit)
    result = engine.run_ocr(image())
    assert len(calls) == limit
    assert len({engine._image_fingerprint(im) for im in calls}) == limit
    assert all(im.shape == calls[0].shape for im in calls)
    assert result['status'] == 'needs_review'
    metadata = result['hypotheses']['pass_metadata']
    assert len(metadata) == limit
    assert 'low_average_confidence' in metadata.get('second_pass', {}).get('reason', ['low_average_confidence'])


@pytest.mark.parametrize('invalid', [0, 4, -1, True, '3', 2.5])
def test_invalid_limits_rejected(invalid):
    with pytest.raises(ValueError, match='1, 2 or 3'):
        OcrEngine(max_ocr_passes=invalid)


def test_high_confidence_stops_after_first(monkeypatch):
    engine, calls = ready(monkeypatch, [[raw(confidence=.99)]], max_ocr_passes=3)
    assert engine.run_ocr(image())['status'] == 'done'
    assert len(calls) == 1


def test_agreeing_recovery_stops_after_second_but_requires_review(monkeypatch):
    engine, calls = ready(monkeypatch, [[raw()], [raw(confidence=.98)]], max_ocr_passes=3)
    result = engine.run_ocr(image())
    assert len(calls) == 2 and result['status'] == 'needs_review'
    assert result['confidence'] == .98


def test_conflicting_amount_never_wins_on_score_or_repeated_vote(monkeypatch):
    engine, calls = ready(monkeypatch, [[raw('NT$ 100.00')],
                                        [raw('NT$ 700.00', .99)], [raw('NT$ 700.00', .999)]], max_ocr_passes=3)
    result = engine.run_ocr(image())
    assert len(calls) == 3
    assert result['text'] == 'NT$ 100.00' and result['status'] == 'needs_review'
    h = result['hypotheses']
    assert h['second_pass'][0]['raw_text'] == 'NT$ 700.00'
    assert h['third_pass'][0]['raw_text'] == 'NT$ 700.00'
    assert 'conflicting_text' in h['pass_metadata']['third_pass']['reason']
    assert h['pass_metadata']['third_pass']['selection'] == 'prior_retained'


def test_third_identical_variant_is_skipped(monkeypatch):
    engine, calls = ready(monkeypatch, [[raw()], [raw()]], max_ocr_passes=3)
    monkeypatch.setattr(engine, '_third_pass_image', lambda im, previous_binarized: im.copy())
    result = engine.run_ocr(image())
    assert len(calls) == 2
    assert result['hypotheses']['pass_metadata']['third_pass']['outcome'] == 'skipped_identical_image'


def test_retry_error_keeps_prior_evidence_and_stops_further_work(monkeypatch):
    engine, calls = ready(monkeypatch, [[raw()], RuntimeError('allocation rejected')], max_ocr_passes=3)
    result = engine.run_ocr(image())
    assert len(calls) == 2 and result['text'] == 'NT$ 100.00'
    assert result['status'] == 'needs_review'
    assert result['hypotheses']['pass_metadata']['second_pass']['outcome'] == 'failed'


def test_empty_retry_error_preserves_provenance(monkeypatch):
    engine, calls = ready(monkeypatch, [[], RuntimeError('inference failed')], max_ocr_passes=3)
    result = engine.run_ocr(image())
    assert result['status'] == 'failed' and len(calls) == 2
    assert result['hypotheses']['first_pass'] == []
    assert result['hypotheses']['pass_metadata']['second_pass']['error'] == 'inference failed'


def test_recognition_budget_is_shared_across_passes(monkeypatch):
    class Recognizer:
        def __call__(self, crops):
            return [], 0

    bounded = BoundedRecognizer(Recognizer())
    bounded.MAX_TOTAL_WIDTH = 640  # Exactly two 320-wide crops, never three.
    engine = OcrEngine(max_ocr_passes=3, max_image_short_side=160)
    engine._ready = True
    engine._engine = SimpleNamespace(text_rec=bounded)
    calls = []

    def infer(im):
        calls.append(1)
        bounded([np.zeros((48, 100, 3), np.uint8)])
        return [raw()]

    monkeypatch.setattr(engine, '_do_ocr_array', infer)
    result = engine.run_ocr(image())
    assert len(calls) == 3 and bounded.total_width == 640 and bounded.total_crops == 2
    assert result['text'] == 'NT$ 100.00' and result['status'] == 'needs_review'
    assert result['hypotheses']['pass_metadata']['third_pass']['outcome'] == 'failed'


def test_live_config_respects_legacy_retry_off(monkeypatch):
    engine, calls = ready(monkeypatch, [[raw()]], max_ocr_passes=3)
    engine.configure(enable_second_pass=False)
    engine.run_ocr(image())
    assert len(calls) == 1
    engine.configure(max_ocr_passes=2)
    assert engine._max_ocr_passes == 2


def make_candidate_editor(qtbot, item_repo, file_mgr, make_image_dto, monkeypatch, *, tiled=False):
    engine, _ = ready(monkeypatch, [[raw('first', .7)], [raw('candidate', .99)], [raw('third', .98)]], max_ocr_passes=3)
    result = engine.run_ocr(image())
    if tiled:
        result['hypotheses'] = {'tiles': [{'tile_index': 0, 'hypotheses': result['hypotheses']}]}
    item_id = item_repo.insert(make_image_dto())
    job = persist(item_repo, item_id, result)
    item_repo.update_edited_text(item_id, 'saved correction')
    editor = EditorWindow(item_repo.get_by_id(item_id), item_repo, file_mgr)
    qtbot.addWidget(editor)
    return editor, item_id, job


def test_review_candidate_apply_undo_cancel_and_explicit_save(
        qtbot, item_repo, file_mgr, make_image_dto, monkeypatch, tmp_path):
    editor, item_id, job = make_candidate_editor(qtbot, item_repo, file_mgr, make_image_dto, monkeypatch)
    assert editor._text_edit.toPlainText() == 'saved correction'
    editor._candidate_combo.setCurrentIndex(2)  # raw second pass, not processed transcript
    assert editor._candidate_preview.toPlainText() == 'candidate'
    assert 'clahe_denoise' in editor._candidate_info.text()
    before = item_repo.get_ocr_attempts(item_id)
    editor._apply_review_candidate()
    assert editor._text_edit.toPlainText() == 'candidate'
    assert item_repo.get_by_id(item_id).get_effective_text() == 'saved correction'
    assert not editor._btn_confirm.isEnabled()
    editor._text_edit.undo()
    assert editor._text_edit.toPlainText() == 'saved correction'
    editor._text_edit.redo()
    editor.reject()  # Cancelling the dialog never saves candidate draft.
    assert item_repo.get_by_id(item_id).get_effective_text() == 'saved correction'
    editor._save()
    item = item_repo.get_by_id(item_id)
    assert item.get_effective_text() == 'candidate' and item.ocr_status == 'confirmed'
    assert item_repo.get_ocr_attempts(item_id) == before
    exported = Exporter(str(tmp_path), file_mgr._data_dir).export_json([item])
    assert json.loads(open(exported).read())[0]['text'] == 'candidate'
    assert item_repo.search_fulltext('candidate')[0].id == item_id


def test_partial_tile_candidate_requires_user_selection_and_undo(
        qtbot, item_repo, file_mgr, make_image_dto, monkeypatch):
    editor, item_id, _ = make_candidate_editor(qtbot, item_repo, file_mgr, make_image_dto, monkeypatch, tiled=True)
    editor._candidate_combo.setCurrentIndex(2)
    assert not editor._apply_candidate.isEnabled()
    cursor = editor._text_edit.textCursor()
    cursor.setPosition(0)
    cursor.setPosition(5, QTextCursor.MoveMode.KeepAnchor)
    editor._text_edit.setTextCursor(cursor)
    assert editor._apply_candidate.isEnabled()
    editor._apply_review_candidate()
    assert editor._text_edit.toPlainText() == 'candidate correction'
    editor._text_edit.undo()
    assert editor._text_edit.toPlainText() == 'saved correction'


@pytest.mark.parametrize('action', ['save', 'confirm'])
def test_stale_editor_cannot_confirm_new_unseen_ocr(
        qtbot, item_repo, file_mgr, make_image_dto, monkeypatch, action):
    editor, item_id, _ = make_candidate_editor(qtbot, item_repo, file_mgr, make_image_dto, monkeypatch)
    persist(item_repo, item_id, dict(text='new unseen text', confidence=.7, status='needs_review'))
    warnings = []
    monkeypatch.setattr('src.ui.editor_window.QMessageBox.warning', lambda *args: warnings.append(args[-1]))
    editor._save() if action == 'save' else editor._confirm_ocr()
    assert warnings and 'OCR 已更新' in warnings[0]
    assert item_repo.get_by_id(item_id).ocr_status == 'needs_review'
    assert item_repo.get_by_id(item_id).edited_text == 'saved correction'


def test_old_and_malformed_attempts_are_readable_without_destroying_text(
        qtbot, item_repo, file_mgr, make_image_dto):
    item_id = item_repo.insert(make_image_dto())
    item_repo.update_ocr_result(item_id, OcrResultDTO(text='old text', confidence=.7,
                                                    status='needs_review', provenance_json='invalid JSON'))
    editor = EditorWindow(item_repo.get_by_id(item_id), item_repo, file_mgr)
    qtbot.addWidget(editor)
    assert editor._candidate_preview.toPlainText() == 'old text'
    assert editor._text_edit.toPlainText() == 'old text'


def test_worker_qt_persists_all_passes_and_gracefully_drains_on_close(
        qtbot, item_repo, file_mgr, make_image_dto, monkeypatch):
    path, relative = file_mgr.save_capture(Image.fromarray(image()))
    item_id = item_repo.insert(make_image_dto(rel_path=relative))
    engine, calls = ready(monkeypatch, [[raw()], [raw('NT$ 700.00', .99)], [raw('NT$ 700.00', .99)]], max_ocr_passes=3)
    worker = OcrWorker(engine)
    worker.set_repository(item_repo, file_mgr)
    db_worker = DbWorker(item_repo, file_mgr)
    worker.ocr_done.connect(db_worker.update_ocr, Qt.ConnectionType.QueuedConnection)
    published = []
    db_worker.ocr_persisted.connect(lambda *args: published.append(args))
    assert worker.queue_ocr(item_id, path)
    worker.stop()  # Graceful drain, not native-inference interruption.
    assert not worker.queue_ocr(item_id, path)
    qtbot.waitUntil(lambda: bool(published), timeout=5000)
    assert worker.wait(5000) and len(calls) == 3
    attempt = item_repo.get_ocr_attempts(item_id)[-1]
    assert json.loads(attempt['provenance_json'])['hypotheses']['third_pass'][0]['raw_text'] == 'NT$ 700.00'
    assert item_repo.get_by_id(item_id).ocr_status == 'needs_review'


@pytest.mark.parametrize('change', ['none', 'edit', 'new_job', 'review_status'])
def test_uncertain_persisted_ack_copies_usable_current_candidate_without_confirming(
        qtbot, item_repo, make_image_dto, monkeypatch, change):
    from src.workers.pipeline import Pipeline
    item_id = item_repo.insert(make_image_dto())
    job, revision = item_repo.begin_ocr_attempt(item_id)
    dto = OcrResultDTO(text='uncertain', confidence=.99, status='needs_review', job_id=job, base_edit_revision=revision)
    item_repo.update_ocr_result(item_id, dto)
    copied, status = [], []
    monkeypatch.setattr('src.clipboard.writer.write_text_to_clipboard', copied.append)
    if change == 'edit':
        item_repo.update_edited_text(item_id, 'manual')
    elif change == 'new_job':
        item_repo.begin_ocr_attempt(item_id)
    elif change == 'review_status':
        dto.status = 'done'  # Re-read DB uncertainty, do not trust a stale DTO.
    pipeline = SimpleNamespace(repo=item_repo, closing=False, cfg=None,
                               widget=SimpleNamespace(set_ocr_status=status.append, set_ocr_progress=lambda _: None))
    Pipeline.persisted(pipeline, item_id, dto)
    assert copied == (['uncertain'] if change in ('none', 'review_status') else [])
    assert not status if change == 'new_job' else '待確認' in status[-1]
    if change in ('none', 'review_status'):
        assert item_repo.get_by_id(item_id).ocr_status == 'needs_review'


def test_settings_persist_pass_limit_and_restart_contract(qtbot, monkeypatch):
    from src.core.config import DEFAULT_SETTINGS
    from src.ui.settings_dialog import SettingsDialog
    import copy
    values = copy.deepcopy(DEFAULT_SETTINGS)

    class Config:
        def get(self, *keys, default=None):
            data = values
            for key in keys:
                data = data.get(key, {}) if isinstance(data, dict) else {}
            return data if data != {} else default

        def set(self, section, key, value):
            values[section][key] = value

    dialog = SettingsDialog(Config())
    qtbot.addWidget(dialog)
    assert dialog._sp_max_passes.value() == 2
    dialog._sp_max_passes.setValue(3)
    monkeypatch.setattr('src.core.autostart.set_autostart', lambda _: None)
    dialog._save()
    assert values['ocr']['max_ocr_passes'] == 3
    assert OcrEngine(max_ocr_passes=values['ocr']['max_ocr_passes'])._max_ocr_passes == 3


@pytest.mark.parametrize('covers_full', [False, True])
def test_small_region_full_source_candidate_can_fill_empty_draft(
        qtbot, item_repo, file_mgr, make_image_dto, covers_full):
    item_id = item_repo.insert(make_image_dto())
    snapshot = {'box': raw()[0], 'raw_text': '復原文字', 'confidence': .7}
    payload = {'hypotheses': {'tiles': [{'tile_index': 0, 'hypotheses': {'first_pass': [snapshot]}}]},
               'preprocessing': {'strategy': 'bounded_small_region', 'source_hw': [50, 100],
                                 'tiles': [{'source_xywh': [0, 0, 100 if covers_full else 50, 50]}]}}
    persist(item_repo, item_id, dict(payload, text='', confidence=0., status='failed'))
    editor = EditorWindow(item_repo.get_by_id(item_id), item_repo, file_mgr)
    qtbot.addWidget(editor)
    assert not editor._text_edit.toPlainText()
    assert editor._apply_candidate.isEnabled() == covers_full
    editor._apply_review_candidate()
    assert editor._text_edit.toPlainText() == ('復原文字' if covers_full else '')
    assert not item_repo.get_by_id(item_id).get_effective_text()
    if covers_full:
        editor._text_edit.undo()
        assert not editor._text_edit.toPlainText()


def test_shared_budget_does_not_restart_at_next_tile(monkeypatch):
    from src.ocr.preprocessor import plan_ocr_tiles

    class Recognizer:
        def __call__(self, crops):
            return [], 0

    bounded = BoundedRecognizer(Recognizer())
    bounded.MAX_TOTAL_WIDTH = 640
    engine = OcrEngine(max_ocr_passes=3)
    engine._ready = True
    engine._engine = SimpleNamespace(text_rec=bounded)
    source = np.broadcast_to(np.arange(2000, dtype=np.uint8)[None, :, None], (30, 2000, 3)).copy()
    tiles = plan_ocr_tiles(source.shape)
    assert len(tiles) > 1
    calls = []

    def infer(im):
        calls.append(1)
        bounded([np.zeros((48, 100, 3), np.uint8)])
        px, py = tiles[0]['padding_xy']
        return [[[[px + 3, py + 3], [px + 40, py + 3], [px + 40, py + 15], [px + 3, py + 15]], 'first tile', .7]]

    monkeypatch.setattr(engine, '_do_ocr_array', infer)
    result = engine.run_ocr(source)
    assert result['status'] == 'failed' and not result['text']
    assert len(calls) == 4 and bounded.total_crops == 2 and bounded.total_width == 640


def test_editor_same_job_completion_is_not_silently_confirmed(
        qtbot, item_repo, file_mgr, make_image_dto, monkeypatch):
    item_id = item_repo.insert(make_image_dto())
    job, revision = item_repo.begin_ocr_attempt(item_id)
    editor = EditorWindow(item_repo.get_by_id(item_id), item_repo, file_mgr)
    qtbot.addWidget(editor)
    item_repo.update_ocr_result(item_id, OcrResultDTO(text='unseen result', confidence=.7,
                                                    status='needs_review', job_id=job, base_edit_revision=revision))
    warnings = []
    monkeypatch.setattr('src.ui.editor_window.QMessageBox.warning', lambda *args: warnings.append(args[-1]))
    editor._save()
    assert warnings and 'OCR 已更新' in warnings[0]
    assert item_repo.get_by_id(item_id).ocr_status == 'needs_review'
    assert item_repo.get_by_id(item_id).edited_text is None


def test_identical_second_pass_does_not_repeat_inference(monkeypatch):
    engine, calls = ready(monkeypatch, [[raw()]], max_ocr_passes=2)
    monkeypatch.setattr('src.ocr.engine.enhance_for_ocr', lambda im, binarize: im.copy())
    result = engine.run_ocr(image())
    assert len(calls) == 1
    assert result['hypotheses']['pass_metadata']['second_pass']['outcome'] == 'skipped_identical_image'
    assert result['status'] == 'needs_review'
