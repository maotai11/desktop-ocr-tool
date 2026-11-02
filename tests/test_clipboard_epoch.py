"""Manual intent survives capture, DB, OCR and publication queue boundaries."""
from dataclasses import replace
from types import SimpleNamespace

from src.clipboard.writer import copy_text, get_clipboard_epoch, write_text_to_clipboard
from src.workers.pipeline import Pipeline
from test_data_integrity_regressions import make_pipeline, reserve


def test_late_ocr_keeps_handwriting_and_next_intent_can_select_then_copy(qapp, item_repo, make_image_dto):
    item_id = item_repo.insert(make_image_dto())
    epoch = get_clipboard_epoch()
    result = replace(reserve(item_repo, item_id), clipboard_epoch=epoch)
    assert item_repo.update_ocr_result(item_id, result)
    copy_text('臺')
    selections = []
    owner = SimpleNamespace(repo=item_repo, cfg=None, closing=False,
                            selection_requested=SimpleNamespace(emit=lambda *args: selections.append(args)),
                            widget=SimpleNamespace(set_ocr_status=lambda _: None))
    owner._selection_is_current = lambda item_id, dto: Pipeline._selection_is_current(owner, item_id, dto)
    Pipeline.persisted(owner, item_id, result)
    assert qapp.clipboard().text() == '臺'
    assert selections == []
    next_result = replace(reserve(item_repo, item_id), text='next intentional OCR',
                          clipboard_epoch=get_clipboard_epoch())
    assert item_repo.update_ocr_result(item_id, next_result)
    current_epoch = get_clipboard_epoch()
    Pipeline.persisted(owner, item_id, next_result)
    assert selections == [(item_id, next_result)]
    assert qapp.clipboard().text() == '臺'
    assert get_clipboard_epoch() == current_epoch
    Pipeline.copy_ocr_selection(owner, item_id, next_result, 'intentional')
    assert qapp.clipboard().text() == 'intentional'
    assert get_clipboard_epoch() == current_epoch + 1


def test_capture_saved_after_manual_copy_keeps_original_epoch(
        qapp, tmp_db, item_repo, file_mgr, make_image_dto, monkeypatch):
    pipeline, worker, thread, ocr, capture, widget = make_pipeline(tmp_db, item_repo, file_mgr)
    old_epoch = get_clipboard_epoch()
    dto = replace(make_image_dto(), clipboard_epoch=old_epoch)
    copy_text('龘')
    queued = []
    monkeypatch.setattr(ocr, 'queue_ocr', lambda *a, **kw: queued.append(kw['clipboard_epoch']))
    worker.save_item(dto)
    assert queued == [old_epoch] and get_clipboard_epoch() != old_epoch
    # Public acknowledgement still fires once, without an extra OCR submission.
    assert item_repo.get_by_id(1) is not None


def test_automatic_write_signal_reentrancy_does_not_hide_manual_copy(qapp, qtbot):
    clipboard = qapp.clipboard()
    qapp.processEvents()  # Drain older native notifications before this write.
    entered = False
    def manual_during_notification():
        nonlocal entered
        if not entered:
            entered = True
            write_text_to_clipboard('manual callback')
    before = get_clipboard_epoch()
    clipboard.dataChanged.connect(manual_during_notification)
    try:
        copy_text('automatic OCR', automatic=True)
        # Offscreen Qt emits synchronously; native Windows may queue the same
        # notification. Keep the handler connected until its bounded delivery.
        qtbot.waitUntil(lambda: entered, timeout=2000)
        qtbot.waitUntil(lambda: (get_clipboard_epoch() == before + 1 and
                                clipboard.text() == 'manual callback'), timeout=2000)
    finally:
        clipboard.dataChanged.disconnect(manual_during_notification)
    assert get_clipboard_epoch() == before + 1
    assert clipboard.text() == 'manual callback'


def test_synchronous_write_callback_keeps_manual_epoch(qapp, monkeypatch):
    from types import SimpleNamespace
    from src.clipboard import writer

    class SynchronousClipboard:
        entered = False
        mime = None

        def setMimeData(self, mime):
            self.mime = mime
            if not self.entered:
                self.entered = True
                writer.write_text_to_clipboard('synchronous manual callback')

    clipboard = SynchronousClipboard()
    monkeypatch.setattr(writer, 'QApplication', SimpleNamespace(clipboard=lambda: clipboard))
    before = get_clipboard_epoch()
    copy_text('automatic OCR', automatic=True)
    assert clipboard.entered
    assert get_clipboard_epoch() == before + 1
    assert clipboard.mime.text() == 'synchronous manual callback'


def test_empty_copy_and_concurrent_automatic_copies_preserve_epoch(qapp):
    before = get_clipboard_epoch()
    assert not copy_text('  ')
    copy_text('first automatic', automatic=True)
    copy_text('second automatic', automatic=True)
    assert get_clipboard_epoch() == before
