"""Exercise the real import/canvas/controller/serialized OCR/output wiring."""
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw
from test_widget_interactions import _make_widget

from src.clipboard.writer import copy_text
from src.core.config import ConfigManager
from src.documents.template import Region
from src.workers.ocr_worker import OcrWorker


class ColorEngine:
    """Deterministic crop oracle; this tests coordinates, not model accuracy."""
    def set_progress_callback(self, callback):
        self.progress = callback

    def is_ready(self):
        return getattr(self, 'ready', False)

    def load(self, progress_cb):
        self.ready = True

    def run_ocr(self, image, mode):
        blue, green, red = np.asarray(image).mean(axis=(0, 1))
        text = '王小明' if red > max(blue, green) else '李小華' if green > blue else '1,234'
        return {'text': text, 'status': 'done', 'confidence': .99}


@pytest.fixture
def document_shell(qtbot, tmp_path, monkeypatch):
    monkeypatch.setenv('DESKTOP_OCR_HOME', str(tmp_path / 'app'))
    shell = _make_widget(tmp_path)
    shell._cfg = ConfigManager()
    shell._cfg.set('clipboard', 'copy_remove_commas', True)
    ocr = OcrWorker(ColorEngine())
    shell._ocr_worker = ocr
    shell._btn_documents.click()
    yield shell, shell._documents, shell._document_controller
    shell.shutdown_documents()
    ocr.stop()
    qtbot.waitUntil(lambda: shell.documents_is_stopped() and not ocr.isRunning(), timeout=10000)
    ocr.wait(1000)
    from PySide6.QtCore import QCoreApplication, QEvent
    shell.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def _form(path, first_color):
    with Image.new('RGB', (400, 300), 'white') as image:
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 199, 149), fill=first_color)
        draw.rectangle((200, 0, 399, 149), fill='blue')
        draw.rectangle((200, 150, 399, 299), fill='red')
        image.save(path)


@pytest.mark.parametrize('override', [False, True])
def test_import_inherited_regions_override_template_copy_and_txt(
        qapp, qtbot, tmp_path, document_shell, override):
    shell, window, controller = document_shell
    paths = [tmp_path / '第一張.png', tmp_path / '第二張.png']
    _form(paths[0], 'red')
    _form(paths[1], 'green')
    originals = [path.read_bytes() for path in paths]
    copy_text('先前的剪貼簿')
    window.import_paths([str(path) for path in paths])
    qtbot.waitUntil(lambda: len(controller.pages) == 2 and window._canvas.image_size[0] > 0,
                    timeout=10000)
    assert window._layout.field_names == ('辨識1',)
    window._set_region(Region(0, 0, .5, .5))
    window._add_field()
    window._set_region(Region(.5, 0, .5, .5))
    window._template.setPlainText('文字｛辨識1}文字測試{辨識2}')
    if override:
        window._page_combo.setCurrentIndex(1)
        qtbot.waitUntil(lambda: window._canvas.image_size[0] > 0, timeout=10000)
        window._field_list.setCurrentRow(0)
        window._set_region(Region(.5, .5, .5, .5))
        assert window._layout.region_for('辨識1', 0) != window._layout.region_for('辨識1', 1)
    assert window._recognize_button.isEnabled()
    window._start_recognition()
    qtbot.waitUntil(lambda: len(window._results) == 2 and not window._busy, timeout=10000)
    second = '王小明' if override else '李小華'
    expected = f'文字王小明文字測試1,234\n\n文字{second}文字測試1,234'
    assert window._output.toPlainText() == expected
    assert qapp.clipboard().text() == '先前的剪貼簿'
    window._copy_results()
    assert qapp.clipboard().text() == expected  # General OCR cleanup cannot alter the template.
    destination = Path(window.export_directory) / '套版結果.txt'
    assert window.export_to_path(destination)
    assert destination.read_bytes().startswith(b'\xef\xbb\xbf')
    assert destination.read_text(encoding='utf-8-sig') == expected
    assert [path.read_bytes() for path in paths] == originals
    assert not list(Path(shell._data_dir).glob('captures/**/*.png'))


def test_entry_reuses_window_and_refuses_new_work_during_shutdown(document_shell):
    shell, window, controller = document_shell
    shell.open_document_template()
    assert shell._documents is window and shell._document_controller is controller
    shell.shutdown_documents()
    shell._documents = None
    shell._document_controller = None
    shell.open_document_template()
    assert shell._documents is None and shell._document_controller is None


def test_real_pdf_pages_use_the_bundled_ocr_and_merge_template(
        qapp, qtbot, tmp_path, document_shell):
    import json

    from src.ocr.engine import OcrEngine
    from src.ocr.model_validator import model_root
    shell, window, controller = document_shell
    engine = OcrEngine(enable_second_pass=False)
    engine.set_progress_callback(shell._ocr_worker.ocr_progress.emit)
    shell._ocr_worker._engine = engine
    fixture_root = model_root() / 'models/validation'
    cases = json.loads((fixture_root / 'fixtures.json').read_text())['cases'][:2]
    images = []
    try:
        for case in cases:
            with Image.open(fixture_root / case['file']) as image:
                images.append(image.convert('RGB'))
        pdf = tmp_path / '合成辨識範例.pdf'
        images[0].save(pdf, format='PDF', save_all=True, append_images=images[1:],
                       resolution=150, quality=100, subsampling=0)
    finally:
        for image in images:
            image.close()
    copy_text('辨識前保留')
    window.import_paths([str(pdf)])
    qtbot.waitUntil(lambda: len(controller.pages) == 2 and window._canvas.image_size[0] > 0,
                    timeout=10000)
    window._set_region(Region(0, 0, 1, 1))
    window._template.setPlainText('欄位：｛辨識1}')
    window._start_recognition()
    qtbot.waitUntil(lambda: len(window._results) == 2 and not window._busy, timeout=30000)
    expected = '\n\n'.join('欄位：' + case['ground_truth'] for case in cases)
    assert window._output.toPlainText() == expected
    assert window._copy_button.isEnabled()
    assert qapp.clipboard().text() == '辨識前保留'


def test_pipeline_waits_for_document_renderer_before_final_quit(qtbot, tmp_db, item_repo, file_mgr):
    from test_data_integrity_regressions import make_pipeline
    pipeline, _worker, _thread, _ocr, _capture, widget = make_pipeline(tmp_db, item_repo, file_mgr)
    ready = [False]
    widget.documents_is_stopped = lambda: ready[0]
    pipeline._database_closed = True
    pipeline._finish_shutdown()
    assert not pipeline.closed and pipeline._tools_poll.isActive()
    ready[0] = True
    qtbot.waitUntil(lambda: pipeline.closed, timeout=2000)
    assert not pipeline._tools_poll.isActive()


def test_document_limit_defaults_merge_with_existing_settings(tmp_path, monkeypatch):
    import json
    monkeypatch.setenv('DESKTOP_OCR_HOME', str(tmp_path))
    (tmp_path / 'config').mkdir()
    (tmp_path / 'config/settings.json').write_text(
        json.dumps({'clipboard': {'ocr_copy_mode': 'select'}}), encoding='utf-8')
    cfg = ConfigManager()
    assert cfg.get('documents', 'max_file_size_mb') == 100
    assert cfg.get('clipboard', 'ocr_copy_mode') == 'select'
    cfg.set('documents', 'max_file_size_mb', 250)
    assert ConfigManager().get('documents', 'max_file_size_mb') == 250
