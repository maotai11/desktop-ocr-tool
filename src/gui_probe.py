"""Finite GUI control/lifecycle evidence with owned synthetic data only."""
from __future__ import annotations

import os
import re
import sys
import tempfile
from contextlib import ExitStack, contextmanager
from pathlib import Path

GUI_CHECK_NAMES = (
    'launcher_actions_and_capture_cancel', 'settings_cancel_and_apply',
    'history_search_tags_and_pin', 'editor_save_and_history_refresh',
    'ocr_selection_copy_explicit', 'document_import_template_zoom_clear',
    'handwriting_stroke_candidate_copy', 'paired_comparison_controls',
    'shared_search_controls', 'tray_pause_and_quit_route',
    'minimized_windows_restore', 'capture_cancel_restart_ignores_old_gesture',
    'shutdown_rejects_late_tool_entries',
    'windows_and_workers_closed', 'temporary_files_cleaned',
)


def _require(condition):
    if not condition:
        raise ValueError('GUI control evidence did not satisfy its invariant')


def _require_owned_fixture(path, owned_root):
    """Keep oracle reads inside the canonical temporary root owned by this run."""
    _require(Path(path).resolve().is_relative_to(owned_root))


def _until(predicate, timeout=8000):
    from PySide6.QtCore import QEventLoop, QTimer
    if predicate():
        return
    loop, poll, deadline = QEventLoop(), QTimer(), QTimer()
    poll.setInterval(5)
    deadline.setSingleShot(True)
    poll.timeout.connect(lambda: loop.quit() if predicate() else None)
    deadline.timeout.connect(loop.quit)
    poll.start()
    deadline.start(timeout)
    loop.exec()
    poll.stop()
    deadline.stop()
    _require(predicate())


@contextmanager
def _replace(owner, name, replacement):
    original = getattr(owner, name)
    setattr(owner, name, replacement)
    try:
        yield
    finally:
        setattr(owner, name, original)


def _button(parent, label):
    from PySide6.QtWidgets import QPushButton
    buttons = [button for button in parent.findChildren(QPushButton) if button.text() == label]
    _require(len(buttons) == 1)
    return buttons[0]


def _modal(action, handlers, *, popup=False):
    """Exercise real nested Qt dialog/menu loops with bounded queued input."""
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    remaining, errors = list(handlers), []
    poll, deadline = QTimer(), QTimer()
    poll.setInterval(5)
    deadline.setSingleShot(True)

    def current():
        return QApplication.activePopupWidget() if popup else QApplication.activeModalWidget()

    def interact():
        window = current()
        if window is None or not remaining:
            return
        handler = remaining.pop(0)
        # Nested tests may enter a second modal loop. Its own driver owns it.
        poll.stop()
        try:
            handler(window)
        except Exception as exc:  # noqa: BLE001 - propagate only after the modal loop closes
            errors.append(type(exc).__name__)
            window.close()
        if remaining:
            poll.start()

    def expired():
        errors.append('deadline')
        poll.stop()
        window = current()
        if window is not None:
            window.close()

    poll.timeout.connect(interact)
    deadline.timeout.connect(expired)
    poll.start()
    deadline.start(5000)
    try:
        action()
    finally:
        poll.stop()
        deadline.stop()
    _require(not remaining and not errors)


def _escape(widget):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication
    QApplication.sendEvent(widget, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape,
                                             Qt.KeyboardModifier.NoModifier))


def _pointer(widget, kind, point, buttons):
    """Send a real local/global pointer event without QtTest or desktop input."""
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication
    local = QPointF(point)
    event = QMouseEvent(kind, local, QPointF(widget.mapToGlobal(local.toPoint())),
                        Qt.MouseButton.LeftButton, buttons, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(widget, event)


def _stroke(widget, start, end):
    from PySide6.QtCore import QEvent, QPointF, Qt
    for kind, point, buttons in (
        (QEvent.Type.MouseButtonPress, start, Qt.MouseButton.LeftButton),
        (QEvent.Type.MouseButtonRelease, end, Qt.MouseButton.NoButton),
    ):
        _pointer(widget, kind, QPointF(point), buttons)


def _dispatch_for(milliseconds):
    """Let a known short debounce/delivery deadline expire without blocking Qt."""
    from PySide6.QtCore import QEventLoop, QTimer
    loop = QEventLoop()
    QTimer.singleShot(milliseconds, loop.quit)
    loop.exec()


def _context_action(console, label):
    def choose(menu):
        actions = [action for action in menu.actions() if action.text() == label]
        _require(len(actions) == 1 and actions[0].isEnabled())
        actions[0].trigger()
        menu.close()
    point = console._table.visualItemRect(console._table.item(0, 0)).center()
    _modal(lambda: console._table.customContextMenuRequested.emit(point), [choose], popup=True)


def _exercise_gui(app, directory, checks):
    """Create the actual GUI composition around owned DB/files and oracle OCR."""
    import mss
    from PIL import Image
    from PySide6.QtCore import (
        QCoreApplication,
        QEvent,
        QObject,
        QPointF,
        Qt,
        QTimer,
        Signal,
    )
    from PySide6.QtWidgets import QDialogButtonBox, QFileDialog, QMessageBox

    from .clipboard.monitoring import ClipboardMonitoringController
    from .clipboard.writer import copy_text
    from .core import autostart
    from .core.config import ConfigManager
    from .data.database import Database
    from .data.file_manager import FileManager
    from .data.models import ItemCreateDTO
    from .data.repository import ItemRepository, TagRepository
    from .ui import handwriting_window
    from .ui.capture_overlay import CaptureOverlay
    from .ui.tray_manager import TrayManager
    from .ui.widget import FloatingWidget
    from .workers import capture_worker as capture_module
    from .workers.capture_worker import CaptureWorker
    from .workers.db_worker import create_db_worker_in_thread
    from .workers.history_maintenance import HistoryMaintenanceController
    from .workers.ocr_worker import OcrWorker
    from .workers.pipeline import Pipeline

    class Oracle:
        def set_progress_callback(self, callback):
            self.progress = callback
        def is_ready(self):
            return True
        def run_ocr(self, image, mode):
            return {'text': 'GUI DOCUMENT', 'confidence': .99, 'status': 'done', 'detail': []}
        def run_ocr_from_path(self, path, mode):
            _require_owned_fixture(path, directory)
            return {'text': 'GUI ALPHA\nGUI BETA', 'confidence': .99, 'status': 'done',
                    'detail': [{'text': text, 'confidence': .99,
                                'box': [[1, y], [60, y], [60, y + 10], [1, y + 10]]}
                               for text, y in [('GUI ALPHA', 1), ('GUI BETA', 20)]]}

    class InkOracle(QObject):
        candidates = Signal(int, object)
        request_failed = Signal(int, str)
        stopped = Signal()
        busy_changed = Signal(bool)
        is_stopped = True
        generation = 0
        def __init__(self, parent=None):
            super().__init__(parent)
        def submit(self, strokes):
            _require(bool(strokes))
            self.generation += 1
            generation = self.generation
            QTimer.singleShot(0, self, lambda: self.candidates.emit(generation, ['甲', '乙']))
            return generation
        def resume(self):
            pass
        def cancel_current(self):
            pass
        def stop(self):
            self.stopped.emit()

    before_windows = {id(window) for window in app.topLevelWidgets()}
    database = worker = thread = ocr = capture = shell = pipeline = tray = overlay = None
    autostart_calls, save_ids, selection_events, forbidden_captures = [], [], [], []

    def no_desktop(*args, **kwargs):
        forbidden_captures.append(True)
        raise ValueError('Desktop capture is outside this synthetic probe')

    with ExitStack() as patches:
        patches.enter_context(_replace(autostart, 'set_autostart', lambda value: autostart_calls.append(value)))
        patches.enter_context(_replace(handwriting_window, 'HandwritingController', InkOracle))
        patches.enter_context(_replace(handwriting_window, 'ImageHandwritingController', InkOracle))
        patches.enter_context(_replace(capture_module, 'get_capture_backend', no_desktop))
        patches.enter_context(_replace(mss, 'mss', no_desktop))
        try:
            cfg = ConfigManager()
            data_dir = cfg.get_data_directory()
            database = Database(str(Path(data_dir) / 'gui-probe.sqlite3'))
            repo, tags, files = ItemRepository(database), TagRepository(database), FileManager(data_dir)
            worker, thread = create_db_worker_in_thread(repo, files)
            ocr, capture = OcrWorker(Oracle()), CaptureWorker(files)
            shell = FloatingWidget(repo, files, worker, ocr, cfg, data_dir, tag_repo=tags)
            pipeline = Pipeline(capture, ocr, worker, thread, database, repo, files, shell, cfg)
            watcher = ClipboardMonitoringController(cfg, lambda _: no_desktop(), parent=shell)
            shell._clip_watcher = pipeline.clipboard = watcher
            maintenance = HistoryMaintenanceController(worker, cfg, parent=pipeline)
            pipeline.set_history_maintenance(maintenance)
            shell.settings_saved.connect(watcher.refresh)
            shell.settings_saved.connect(maintenance.refresh)
            saved_settings = []
            shell.settings_saved.connect(lambda: saved_settings.append(True))
            worker.item_saved.connect(save_ids.append)
            pipeline.selection_requested.connect(pipeline.select_ocr_text)
            pipeline.selection_requested.connect(lambda *args: selection_events.append(True))
            ocr.engine_ready.connect(shell.on_ocr_engine_ready)
            ocr.start_loading()
            _until(lambda: shell._ocr_ready)
            tray = TrayManager(shell)
            tray.set_quit_callback(pipeline.shutdown)
            overlay = CaptureOverlay()
            cancelled, selected = [], []
            pipeline.set_capture_overlay(overlay)
            overlay.cancelled.connect(lambda: cancelled.append(True))
            # Cancellation probes must never acquire the desktop, including if
            # a regression accidentally delivers the retired selection.
            patches.enter_context(_replace(capture, 'capture_region', lambda *args: selected.append(args)))
            shell.set_capture_callbacks(lambda: pipeline.start_region_capture('region_ocr'),
                                        lambda: pipeline.start_region_capture('region_image'))
            shell.show()

            for button in (shell._btn_ocr, shell._btn_screenshot):
                button.click()
                _require(overlay.isVisible() and not shell.isVisible())
                _escape(overlay)
                _require(shell.isVisible() and not overlay.isVisible())
            shell._btn_data_dir.click()
            _require(bool(shell._storage_menu.actions()))
            shell._storage_menu.close()
            checks['launcher_actions_and_capture_cancel'] = len(cancelled) == 2 and not selected and not capture._started_once

            shell.showMinimized()
            _require(shell.isMinimized())
            shell.toggle_visibility()
            _until(lambda: shell.isVisible() and not shell.isMinimized())

            shell._btn_ocr.click()
            _pointer(overlay, QEvent.Type.MouseButtonPress, QPointF(10, 10), Qt.MouseButton.LeftButton)
            _escape(overlay)
            shell._btn_ocr.click()
            _pointer(overlay, QEvent.Type.MouseButtonRelease, QPointF(40, 40), Qt.MouseButton.NoButton)
            _require(overlay.isVisible() and not overlay._is_drawing)
            _dispatch_for(200)
            _require(not selected)
            _stroke(overlay, QPointF(20, 20), QPointF(60, 60))
            overlay.close()  # Closing during the compositor delay must retire delivery.
            _dispatch_for(200)
            checks['capture_cancel_restart_ignores_old_gesture'] = (
                not selected and not overlay.isVisible() and not capture._started_once)

            def settings_cancel(dialog):
                dialog._sp_archive_days.setValue(0)
                dialog._ocr_copy_mode.setCurrentIndex(dialog._ocr_copy_mode.findData('select'))
                dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Cancel).click()
            _modal(shell._btn_settings.click, [settings_cancel])
            _require(cfg.get('history', 'auto_archive_days') == 90 and not saved_settings and not autostart_calls)
            def settings_accept(dialog):
                dialog._sp_archive_days.setValue(0)
                dialog._ocr_copy_mode.setCurrentIndex(dialog._ocr_copy_mode.findData('select'))
                dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Ok).click()
            _modal(shell._btn_settings.click, [settings_accept])
            checks['settings_cancel_and_apply'] = (saved_settings == [True] and autostart_calls == [False]
                and ConfigManager().get('clipboard', 'ocr_copy_mode') == 'select'
                and maintenance.days == 0 and not watcher.active)

            for value in ('GUI searchable alpha', 'GUI secondary beta'):
                pipeline.save_requested.emit(ItemCreateDTO(item_type='text', source_mode='clipboard_text', text_content=value))
            _until(lambda: len(save_ids) == 2 and len(shell._items) == 2)
            item_id = save_ids[0]
            shell._btn_console.click()
            console = shell._console
            console.showMinimized()
            _require(console.isMinimized())
            shell.open_console()
            _until(lambda: console.isVisible() and not console.isMinimized())
            console._search.setText('searchable')
            _button(console, '搜尋').click()
            _require(len(console._current_items) == 1 and console._current_items[0].id == item_id)
            console._table.setCurrentCell(0, 0)
            _context_action(console, '釘選')
            _until(lambda: repo.get_by_id(item_id).is_pinned)
            shell._filter_buttons['pinned'].click()
            _require({item.id for item in shell._items} == {item_id})
            shell._selected_item_id = item_id
            def manage_tags(dialog):
                def enter_tag(child):
                    dialog._new_tag_name.setText('GUI fixture tag')
                    child.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Ok).click()
                _modal(dialog._btn_add_tag.click, [enter_tag,
                    lambda message: message.button(QMessageBox.StandardButton.Ok).click()])
                _require(dialog._tag_stats_table.rowCount() == 1)
                dialog._tag_stats_table.cellWidget(0, 3).click()
                _require(dialog._tag_stats_table.item(0, 2).text() == '1')
                dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Cancel).click()
            _modal(shell._btn_settings.click, [manage_tags])
            checks['history_search_tags_and_pin'] = (len(tags.get_tags_for_item(item_id)) == 1
                and console._search.text() == 'searchable' and len(saved_settings) == 1)

            _context_action(console, '編輯')
            editor = console._editor_windows[item_id]
            editor.showMinimized()
            _require(editor.isMinimized())
            console._open_editor(repo.get_by_id(item_id))
            _until(lambda: editor.isVisible() and not editor.isMinimized())
            checks['minimized_windows_restore'] = True
            editor._text_edit.setPlainText('GUI searchable edited')
            editor._note_edit.setPlainText('GUI note')
            _button(editor, '儲存').click()
            _until(lambda: repo.get_by_id(item_id).edited_text == 'GUI searchable edited')
            checks['editor_save_and_history_refresh'] = (
                repo.get_by_id(item_id).note_plaintext == 'GUI note'
                and shell._items[0].get_effective_text() == 'GUI searchable edited'
                and console._current_items[0].get_effective_text() == 'GUI searchable edited')

            image_file = directory / 'synthetic.png'
            pdf_file = directory / 'synthetic.pdf'
            with Image.new('RGB', (160, 120), 'white') as image:
                image.save(image_file)
                image.save(pdf_file, 'PDF', resolution=72)
                _, relative = files.save_capture(image)
            copy_text('GUI previous clipboard')
            pipeline.save_requested.emit(ItemCreateDTO(item_type='image', source_mode='region_ocr',
                raw_image_path=relative, image_width=160, image_height=120))
            _until(lambda: len(pipeline._ocr_selections) == 1)
            chooser = next(iter(pipeline._ocr_selections.values()))
            _require(app.clipboard().text() == 'GUI previous clipboard' and not chooser._copy.isEnabled())
            chooser._select_all.click()
            chooser._clear.click()
            _require(not chooser._copy.isEnabled())
            chooser._list.item(1).setCheckState(Qt.CheckState.Checked)
            _require(app.clipboard().text() == 'GUI previous clipboard')
            chooser._copy.click()
            checks['ocr_selection_copy_explicit'] = (app.clipboard().text() == 'GUI BETA'
                and len(selection_events) == 1 and repo.get_by_id(save_ids[-1]).text_content == 'GUI ALPHA\nGUI BETA')

            shell._btn_documents.click()
            document, document_controller = shell._documents, shell._document_controller
            with _replace(QFileDialog, 'getOpenFileNames', lambda *args, **kwargs: ([str(image_file), str(pdf_file)], '')):
                document._import_button.click()
            _until(lambda: len(document_controller.pages) == 2 and document._canvas.image_size[0] > 0)
            document._actual_size_button.click()
            before_zoom = document._canvas.zoom_percent
            document._zoom_in.click()
            _require(document._canvas.zoom_percent > before_zoom)
            document._zoom_out.click()
            _require(abs(document._canvas.zoom_percent - before_zoom) < .01)
            document._pan_button.click()
            _require(document._canvas._pan_mode)
            document._pan_button.click()
            canvas = document._canvas
            first = canvas.mapFromScene(QPointF(10, 10))
            last = canvas.mapFromScene(QPointF(120, 80))
            _stroke(canvas.viewport(), first, last)
            _require(document._layout.region_for('辨識1', 0) is not None)
            document._template.setPlainText('GUI {辨識1}')
            document._recognize_button.click()
            _until(lambda: len(document._results) == 2 and not document._busy)
            _require(document._output.toPlainText() == 'GUI GUI DOCUMENT\n\nGUI GUI DOCUMENT')
            document._copy_button.click()
            _require(app.clipboard().text() == document._output.toPlainText())
            document._clear_region_button.click()
            _require(document._layout.region_for('辨識1', 0) is None and not document._recognize_button.isEnabled())
            _modal(document._clear_documents_button.click,
                   [lambda message: message.button(QMessageBox.StandardButton.Yes).click()])
            _until(lambda: not document_controller.pages and not document._pages)
            checks['document_import_template_zoom_clear'] = (document._canvas.image_size == (0, 0)
                and not document._results and document._template.toPlainText() == 'GUI {辨識1}'
                and image_file.is_file() and pdf_file.is_file())
            document.close()

            shell._btn_handwriting.click()
            ink = shell._handwriting
            app.processEvents()
            point = ink.pad.canvas_rect().center()
            _stroke(ink.pad, point, point + QPointF(20, 20))
            ink.recognize_button.click()
            _until(lambda: len(ink._buttons) == 2)
            before_copy = app.clipboard().text()
            _require(before_copy != '甲')
            ink._buttons[0].click()
            _require(app.clipboard().text() == '甲')
            geometry = ink.pad.canvas_rect()
            _stroke(ink.pad, geometry.center(), geometry.center())
            _require(ink.pad.canvas_rect() == geometry and len(ink.pad.buffer.strokes) == 2)
            generation = ink.controller.generation
            ink.hide()
            _dispatch_for(420)  # Longer than the handwriting recognition debounce.
            _require(ink.controller.generation == generation and not ink._debounce.isActive()
                     and not ink._candidate_scroll.isActive() and ink.is_stopped)
            shell.open_handwriting()
            _require(ink.isVisible() and ink.controller.generation == generation)
            ink.undo_button.click()
            _require(len(ink.pad.buffer.strokes) == 1)
            ink.clear_button.click()
            checks['handwriting_stroke_candidate_copy'] = not ink.pad.buffer.strokes and not ink._buttons
            ink.close()

            shell._btn_comparison.click()
            comparison, comparison_controller = shell._comparison, shell._comparison_controller
            comparison._add_group_button.click()
            comparison._mode.setCurrentIndex(comparison._mode.findData('exact'))
            comparison._mini_button.click()
            comparison._pin_button.click()
            _require(len(comparison_controller.groups) == 2 and comparison._mini_mode
                     and comparison_controller.mode == 'exact'
                     and not comparison.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
            comparison._mini_button.click()
            comparison._setup_buttons['A'].click()
            _require(shell._comparison_overlay.isVisible())
            pipeline.start_region_capture('region_ocr')
            _require(overlay.isVisible() and not shell._comparison_overlay.isVisible()
                     and not comparison._selection_active and not comparison.isVisible())
            shell.open_comparison()
            _require(not overlay.isVisible())
            comparison._setup_buttons['A'].click()
            _escape(shell._comparison_overlay)
            _require(not comparison._selection_active and not comparison_controller.regions)
            comparison._close_button.click()
            checks['paired_comparison_controls'] = not comparison.isVisible() and not comparison_controller.running

            shell._btn_shared_search.click()
            search, search_controller = shell._shared_search, shell._shared_search_controller
            search._add.click()
            search._row_pitch.setValue(24)
            search._mode.setCurrentIndex(search._mode.findData('exact'))
            search._pin.setChecked(False)
            search._pin.setChecked(True)
            search._background_transparency.setValue(40)
            _require(search.highlight_overlay.background_opacity == 60
                     and search.highlight_overlay.windowOpacity() == 1.)
            search._background_transparency.setValue(100)
            _require(search.highlight_overlay.background_opacity == 0)
            # Exact mode keeps literal date comparison and disables the option;
            # return to normalized mode to exercise the real settings route.
            search._mode.setCurrentIndex(search._mode.findData('normalized'))
            search._date_equivalence.setChecked(False)
            _require(not search_controller.date_equivalence)
            search._date_equivalence.setChecked(True)
            _require(search_controller.date_equivalence)
            search._mode.setCurrentIndex(search._mode.findData('exact'))
            search._manual_button.click()
            _require(not search_controller.running and not search.manual_button.isVisible())
            search._select_b.click()
            _require(shell._shared_search_selector.isVisible())
            pipeline.start_region_capture('region_image')
            _require(overlay.isVisible() and not shell._shared_search_selector.isVisible()
                     and not search._selection_active and not search.isVisible())
            shell.open_shared_search()
            _require(not overlay.isVisible())
            search._select_b.click()
            _escape(shell._shared_search_selector)
            _require(len(search_controller.fields) == 2 and search_controller.row_pitch == 24
                     and search_controller.mode == 'exact' and search._pin.isChecked()
                     and not search_controller.b_region)
            search.close()
            checks['shared_search_controls'] = (not search.isVisible() and not search_controller.running
                                               and not search.manual_button.isVisible())

            shell.show()
            shell._btn_pause.click()
            _require(shell._clip_paused and watcher.is_paused and tray._act_pause.isChecked())
            tray._act_pause.trigger()
            _require(not shell._clip_paused and not watcher.is_paused and not shell._btn_pause.isChecked())
            pipeline.start_region_capture('region_ocr')
            _require(overlay.isVisible())
            tray._quit_action.trigger()
            _until(lambda: pipeline.closed)
            checks['tray_pause_and_quit_route'] = pipeline.closing and pipeline.closed and watcher._stopped
            _require(not overlay.isVisible())
            tool_names = ('_console', '_handwriting', '_documents', '_comparison', '_shared_search')
            owned_tools = tuple(getattr(shell, name) for name in tool_names)
            unexpected_modals = []
            modal_guard = QTimer()
            modal_guard.setInterval(5)

            def reject_unexpected_modal():
                dialog = app.activeModalWidget()
                if dialog is not None:
                    unexpected_modals.append(True)
                    dialog.close()

            modal_guard.timeout.connect(reject_unexpected_modal)
            modal_guard.start()
            try:
                for action in tray._tray.contextMenu().actions():
                    if not action.isSeparator():
                        action.trigger()
                for entry in (shell.toggle_visibility, shell.open_console, shell.open_handwriting,
                              shell.open_document_template, shell.open_comparison, shell.open_shared_search,
                              shell.open_settings, shell.trigger_capture_ocr, shell.trigger_capture_image):
                    entry()
                pipeline.start_region_capture('region_image')
                app.processEvents()
            finally:
                modal_guard.stop()
            checks['shutdown_rejects_late_tool_entries'] = (
                not unexpected_modals and tuple(getattr(shell, name) for name in tool_names) == owned_tools
                and not selected and not watcher.active and maintenance.is_stopped
                and not any(window.isVisible() for window in app.topLevelWidgets()
                            if id(window) not in before_windows)
                and shell.handwriting_is_stopped() and shell.documents_is_stopped()
                and shell.comparison_is_stopped() and shell.shared_search_is_stopped())
            _require(not forbidden_captures)
        finally:
            if pipeline is not None:
                pipeline.shutdown()
                _until(lambda: pipeline.closed, 10000)
            else:
                for queue_worker in (capture, ocr):
                    if queue_worker is not None:
                        queue_worker.stop()
                        queue_worker.wait(5000)
                if thread is not None:
                    thread.quit()
                    thread.wait(5000)
                if database is not None:
                    database.close()
            if tray is not None:
                tray._tray.hide()
                tray._tray.contextMenu().close()
                tray._tray.contextMenu().deleteLater()
                tray._tray.deleteLater()
            owned_windows = [window for window in app.topLevelWidgets() if id(window) not in before_windows]
            for window in owned_windows:
                window.close()
            checks['windows_and_workers_closed'] = (
                pipeline is not None and pipeline.closed and database._closed
                and not thread.isRunning() and not ocr.isRunning() and not capture.isRunning()
                and shell.handwriting_is_stopped() and shell.documents_is_stopped()
                and shell.comparison_is_stopped() and shell.shared_search_is_stopped()
                and not any(window.isVisible() for window in owned_windows))
            for window in owned_windows:
                window.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            app.processEvents()


def run_gui_probe(report_path):
    """Report a finite action matrix, not universal GUI or OCR accuracy proof."""
    from PySide6.QtCore import QMimeData
    from PySide6.QtWidgets import QApplication

    from .core.validation_report import executable_sha256, write_validation_report
    from .core.version import APP_VERSION

    app = QApplication.instance() or QApplication([])
    report = {'schema': 1, 'probe': 'gui_feature_lifecycle', 'version': APP_VERSION,
              'platform': sys.platform, 'frozen': bool(getattr(sys, 'frozen', False)),
              'qt_platform': app.platformName(), 'executable_sha256': None,
              'clean_machine_verified': False, 'passed': False,
              'fixture_kind': 'SYNTHETIC_GUI_CONTROLS',
              'model_inference_tested': False, 'ocr_accuracy_tested': False,
              'checks': dict.fromkeys(GUI_CHECK_NAMES, False)}
    commit = os.environ.get('GITHUB_SHA', '')
    if re.fullmatch('[0-9a-f]{40}', commit):
        report['source_commit'] = commit
    try:
        write_validation_report(report_path, report)
        report['executable_sha256'] = executable_sha256()
    except (OSError, ValueError, TypeError):
        return 1
    previous_home = os.environ.get('DESKTOP_OCR_HOME')
    quit_on_close = app.quitOnLastWindowClosed()
    app.setQuitOnLastWindowClosed(False)
    saved_clipboard = QMimeData()
    current = app.clipboard().mimeData()
    if current is not None:
        for mime in current.formats():
            saved_clipboard.setData(mime, current.data(mime))
    temporary = None
    try:
        with tempfile.TemporaryDirectory(prefix='inkpick-gui-probe-') as directory:
            # Windows TEMP may use an 8.3 name while fixture paths resolve to
            # the long name. Compare canonical paths on both sides; do not
            # weaken the oracle's owned-file boundary to a string prefix.
            temporary = Path(directory).resolve()
            os.environ['DESKTOP_OCR_HOME'] = str(temporary)
            _exercise_gui(app, temporary, report['checks'])
        report['checks']['temporary_files_cleaned'] = not temporary.exists()
        report['passed'] = all(value is True for value in report['checks'].values())
    except Exception:  # noqa: BLE001 - reports must never disclose text, paths, pixels or tracebacks
        report['passed'] = False
    finally:
        if previous_home is None:
            os.environ.pop('DESKTOP_OCR_HOME', None)
        else:
            os.environ['DESKTOP_OCR_HOME'] = previous_home
        formats = saved_clipboard.formats()
        if not formats:
            app.clipboard().clear()
        elif formats == ['text/plain']:
            app.clipboard().setText(saved_clipboard.text())
        else:
            app.clipboard().setMimeData(saved_clipboard)
        app.setQuitOnLastWindowClosed(quit_on_close)
        if temporary is not None:
            report['checks']['temporary_files_cleaned'] = not temporary.exists()
    if not report['passed']:
        report['error_code'] = 'GUI_PROBE_CHECK_FAILED'
    try:
        write_validation_report(report_path, report)
    except (OSError, ValueError, TypeError):
        return 1
    return 0 if report['passed'] else 1
