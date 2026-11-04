"""Synthetic paired-capture/serial-worker lifecycle probe, without model inference."""
from __future__ import annotations

import os
import re
import sys
from threading import Event
from time import monotonic

from .core.validation_report import executable_sha256, write_validation_report
from .core.version import APP_VERSION

CHECK_NAMES = (
    'fixed_regions_reused', 'same_snapshot_pairs', 'normalized_match',
    'mismatch_detected', 'uncertain_not_match', 'clipboard_unchanged',
    'cancelled_results_ignored', 'workers_stopped',
    'unchanged_frames_skipped', 'changed_frames_resumed', 'close_stops_polling', 'pin_toggle_available',
    'mini_results_visible', 'mini_results_live_updated',
    'all_groups_visible', 'all_groups_live_updated',
    'batch_groups_isolated', 'batch_serialized', 'batch_unchanged_skipped', 'batch_cancel_stops_all',
)
BATCH_CHECK_NAMES = CHECK_NAMES[-6:]


def run_comparison_probe(report_path):
    """Exercise the actual capture cropper, controller and shared OCR queue.

    The screen provider is replaced only inside this isolated CLI probe. Pixel
    labels are an explicit deterministic oracle; no real screen is captured,
    and this must never be described as model accuracy or inference evidence.
    """
    import numpy as np
    from PySide6.QtCore import QEventLoop, QTimer, QRect, Qt
    from PySide6.QtWidgets import QApplication

    from .comparison import capture as capture_module
    from .comparison.controller import ComparisonController
    from .workers.ocr_worker import OcrWorker
    from .ui.comparison_window import ComparisonWindow

    app = QApplication.instance() or QApplication([])
    report = {
        'schema': 1, 'probe': 'comparison_lifecycle', 'version': APP_VERSION,
        'platform': sys.platform, 'frozen': bool(getattr(sys, 'frozen', False)),
        'qt_platform': app.platformName(), 'executable_sha256': None,
        'clean_machine_verified': False, 'passed': False,
        'fixture_kind': 'SYNTHETIC_COMPARISON_PAIRS',
        'model_inference_tested': False, 'ocr_accuracy_tested': False,
        'checks': dict.fromkeys(CHECK_NAMES, False),
    }
    commit = os.environ.get('GITHUB_SHA', '')
    if re.fullmatch('[0-9a-f]{40}', commit):
        report['source_commit'] = commit
    try:
        # A crash or interrupted invocation must not retain a previous PASS.
        write_validation_report(report_path, report)
        report['executable_sha256'] = executable_sha256()
    except (OSError, ValueError, TypeError):
        return 1

    regions = {'A': (0, 0, 8, 8, 1), 'B': (12, 0, 8, 8, 1)}
    captures, observed_regions, recognized_pixels, results = [], [], [], []
    cancelled_grab_started, release_cancelled_grab = Event(), Event()

    class SyntheticScreen:
        monitors = (
            {'left': 0, 'top': 0, 'width': 100, 'height': 100},
            {'left': 0, 'top': 0, 'width': 100, 'height': 100},
        )

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def grab(self, rectangle):
            expected = {'left': 0, 'top': 0, 'width': 20, 'height': 8}
            if rectangle != expected:
                raise ValueError('Unexpected synthetic capture bounds')
            grab = len(captures) + 1
            captures.append(dict(rectangle))
            if grab == 6:
                cancelled_grab_started.set()
                if not release_cancelled_grab.wait(2):
                    raise ValueError('Cancellation handshake timeout')
            cycle = (1, 1, 2, 3, 4, 5)[grab - 1]
            pixels = np.zeros((8, 20, 4), dtype=np.uint8)
            # Irrelevant pixels outside A/B change even on the duplicate pair.
            pixels[:, 8:12, :3] = grab
            pixels[:, :8, :3] = cycle * 10 + 1
            pixels[:, 12:, :3] = cycle * 10 + 2
            return pixels

    class PixelOracle:
        def set_progress_callback(self, callback):
            pass

        def is_ready(self):
            return True

        def run_ocr(self, image, mode):
            value = int(image[0, 0, 0])
            if image.shape != (8, 8, 3) or not np.all(image == value):
                raise ValueError('Invalid synthetic crop')
            recognized_pixels.append(value)
            text, status = {
                11: ('甲 12，', 'done'), 12: ('甲12', 'done'),
                21: ('甲12', 'done'), 22: ('甲13', 'done'),
                31: ('甲12', 'needs_review'), 32: ('甲12', 'done'),
                41: ('', 'done'), 42: ('', 'done'),
            }[value]
            return {'text': text, 'status': status}

    original_screen = capture_module.mss.mss
    worker = controller = window = None
    loop = QEventLoop()
    deadline, poll = QTimer(), QTimer()
    deadline.setSingleShot(True)
    poll.setInterval(10)
    stage = 'recognize'
    failed = False
    closed_at = None
    before_clipboard = app.clipboard().text()

    def stop_workers():
        if controller is not None:
            controller.shutdown()
        if worker is not None:
            worker.stop()

    def abort(*args):
        nonlocal failed, stage
        failed, stage = True, 'stopping'
        release_cancelled_grab.set()
        stop_workers()

    def updated(result):
        nonlocal stage
        if stage != 'recognize':
            abort()
            return
        results.append(dict(result))
        if len(results) == 1:
            window.move(80, 80)
            window.set_mini(True)
            window.show()
            app.processEvents()
            report['checks']['mini_results_visible'] = (
                window._mini_mode and window.width() <= 360 and window.height() <= 380
                and all(preview.isVisible() and window.rect().contains(
                    QRect(preview.mapTo(window, preview.rect().topLeft()), preview.size()))
                        for preview in window._mini_previews.values()))
        elif len(results) == 2:
            report['checks']['mini_results_live_updated'] = (
                window._mini_mode and window._mini_previews['A']._full_text == result['a']
                and window._mini_previews['B']._full_text == result['b']
                and result['a'] != result['b']
                and all(preview.isVisible() for preview in window._mini_previews.values()))
            window.hide()
        if len(results) == 4:
            stage = 'start_cancel'
            controller.pause()

    def state_changed(state):
        if state == 'unchanged':
            report['checks']['unchanged_frames_skipped'] = (
                len(captures) == 2 and len(results) == 1 and recognized_pixels == [11, 12])

    def check_progress():
        nonlocal stage, closed_at
        if stage == 'start_cancel':
            stage = 'wait_cancel'
            if not controller.start():
                abort()
        elif stage == 'wait_cancel' and cancelled_grab_started.is_set():
            window.show()
            window._close_button.click()
            closed_at = monotonic()
            release_cancelled_grab.set()
            stage = 'cancelled'
        elif stage == 'cancelled' and controller._capture_token is None:
            report['checks']['cancelled_results_ignored'] = (
                len(results) == 4 and not controller.running
                and not controller._cycle_timer.isActive()
                and not controller._retry.isActive()
                and not controller._images and not controller._results
                and recognized_pixels == [11, 12, 21, 22, 31, 32, 41, 42])
            stage = 'closed_wait'
        elif stage == 'closed_wait' and monotonic() - closed_at > 1.1:
            report['checks']['close_stops_polling'] = (
                not window.isVisible() and not controller.running and len(captures) == 6
                and not controller._deadline.isActive() and controller._last_fingerprint is None
                and controller._pending_fingerprint is None and len(results) == 4)
            stage = 'stopping'
            stop_workers()
        elif stage == 'stopping' and controller.is_stopped and not worker.isRunning():
            deadline.stop()
            poll.stop()
            report['checks']['workers_stopped'] = not worker._document_pending
            loop.quit()

    try:
        capture_module.mss.mss = SyntheticScreen

        def paired_capture(current, layout):
            observed_regions.append(dict(current))
            return capture_module.capture_pair(current, layout)

        capture_worker = capture_module.ComparisonCaptureWorker(capture=paired_capture)
        worker = OcrWorker(PixelOracle())
        controller = ComparisonController(worker, capture_worker=capture_worker)
        window = ComparisonWindow(controller)
        controller.state_changed.connect(state_changed)
        pin_ok = True
        for compact in (False, True):
            window.set_compact(compact)
            for pinned in (False, True):
                window.set_pinned(pinned)
                flags = window.windowFlags()
                pin_ok = pin_ok and bool(flags & Qt.WindowType.WindowStaysOnTopHint) == pinned
                pin_ok = pin_ok and bool(flags & Qt.WindowType.WindowCloseButtonHint)
        report['checks']['pin_toggle_available'] = pin_ok and window._close_button.isEnabled()
        controller.updated.connect(updated)
        controller.failed.connect(abort)
        worker.engine_failed.connect(abort)
        if not all(controller.set_region(name, region) for name, region in regions.items()):
            raise ValueError('Synthetic regions refused')
        controller.set_interval(1)
        poll.timeout.connect(check_progress)
        deadline.timeout.connect(abort)
        poll.start()
        deadline.start(14000)
        if not controller.start():
            raise ValueError('Synthetic comparison did not start')
        loop.exec()
        verdicts = [result['verdict'] for result in results]
        report['checks'].update(
            fixed_regions_reused=(len(observed_regions) == 6
                                  and all(item == regions for item in observed_regions)),
            same_snapshot_pairs=(len(captures) == 6
                                 and recognized_pixels == [11, 12, 21, 22, 31, 32, 41, 42]),
            normalized_match=bool(verdicts and verdicts[0] == 'match'),
            mismatch_detected=bool(len(verdicts) > 1 and verdicts[1] == 'mismatch'),
            changed_frames_resumed=(len(results) == 4
                                    and [result['cycle'] for result in results] == [1, 2, 3, 4]),
            uncertain_not_match=verdicts[2:] == ['uncertain', 'uncertain'],
            clipboard_unchanged=app.clipboard().text() == before_clipboard,
        )
        report['passed'] = not failed and all(report['checks'][name]
                                             for name in CHECK_NAMES if name not in BATCH_CHECK_NAMES)
    except Exception:  # noqa: BLE001 - publish a fixed failure code, never private diagnostics
        report['passed'] = False
    finally:
        deadline.stop()
        poll.stop()
        release_cancelled_grab.set()
        stop_workers()
        # These are deterministic synthetic providers, never arbitrary native
        # inference. Always reap them before restoring the screen provider.
        if controller is not None:
            controller._capture.wait(3000)
        if worker is not None:
            worker.wait(3000)
        if window is not None:
            window.shutdown()
            window.deleteLater()
        capture_module.mss.mss = original_screen
    if report['passed']:
        report['checks'].update(_run_batch_checks(app))
        report['passed'] = all(report['checks'].values())
    if not report['passed']:
        report['error_code'] = 'COMPARISON_PROBE_CHECK_FAILED'
    try:
        write_validation_report(report_path, report)
    except (OSError, ValueError, TypeError):
        return 1
    return 0 if report['passed'] else 1


def _run_batch_checks(app):
    """Two independent pairs, exact unchanged skipping and batch cancellation.

    Like the single-pair scenario, this drives the actual cropper, controller
    and serialized OCR queue. Pixel labels are synthetic, never real content.
    """
    import numpy as np
    from PySide6.QtCore import QEventLoop, QTimer, QRect

    from .comparison import capture as capture_module
    from .comparison.controller import ComparisonController
    from .workers.ocr_worker import OcrWorker
    from .ui.comparison_window import ComparisonWindow

    checks = dict.fromkeys(BATCH_CHECK_NAMES, False)
    calls, captures, events, results, unchanged = [], [], [], [], []
    counts = {1: 0, 2: 0}
    entered, release = Event(), Event()
    before_clipboard = app.clipboard().text()
    region_sets = {
        1: {'A': (0, 20, 8, 8, 1), 'B': (12, 20, 8, 8, 1)},
        2: {'A': (0, 40, 8, 8, 1), 'B': (12, 40, 8, 8, 1)},
    }

    class BatchScreen:
        monitors = (
            {'left': 0, 'top': 0, 'width': 100, 'height': 100},
            {'left': 0, 'top': 0, 'width': 100, 'height': 100},
        )

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def grab(self, rectangle):
            group = {20: 1, 40: 2}.get(rectangle.get('top'))
            if (group is None or rectangle != {'left': 0, 'top': group * 20,
                                               'width': 20, 'height': 8}):
                raise ValueError('Unexpected batch synthetic bounds')
            counts[group] += 1
            captures.append(group)
            events.append(('capture', group))
            count = counts[group]
            base = (100 if count < 3 else 110 if count == 3 else 120) if group == 1 else 200
            pixels = np.zeros((8, 20, 4), dtype=np.uint8)
            pixels[:, :8, :3], pixels[:, 12:, :3] = base + 1, base + 2
            pixels[:, 8:12, :3] = len(captures)  # Outside both selected crops.
            return pixels

    class BatchOracle:
        def set_progress_callback(self, callback):
            pass

        def is_ready(self):
            return True

        def run_ocr(self, image, mode):
            value = int(image[0, 0, 0])
            if image.shape != (8, 8, 3) or not np.all(image == value):
                raise ValueError('Invalid batch synthetic crop')
            calls.append(value)
            events.append(('ocr', value))
            if value == 121:
                entered.set()
                if not release.wait(3):
                    raise ValueError('Batch cancellation handshake timeout')
            text = {101: '甲 12，', 102: '甲12', 201: '乙12', 202: '乙13',
                    111: '新資料', 112: '新資料', 121: '過期結果'}[value]
            return {'text': text, 'status': 'done'}

    original_screen = capture_module.mss.mss
    worker = controller = window = None
    loop = QEventLoop()
    poll, deadline = QTimer(), QTimer()
    poll.setInterval(10)
    deadline.setSingleShot(True)
    stage, cancelled_at, failed = 'running', None, False

    def stop():
        if controller is not None:
            controller.shutdown()
        if worker is not None:
            worker.stop()

    def abort(*args):
        nonlocal failed, stage
        failed, stage = True, 'stopping'
        release.set()
        stop()

    def state_changed(state):
        if state == 'unchanged':
            unchanged.append(controller.scanning_group_id)

    def published(result):
        results.append(dict(result))
        if len(results) == 2:
            window.move(120, 0)
            window.show()
            app.processEvents()
            normal = all(card.isVisible() and window._scroll.viewport().rect().contains(
                QRect(card.mapTo(window._scroll.viewport(), card.rect().topLeft()), card.size()))
                for card in window._group_result_cards.values())
            window.set_mini(True)
            app.processEvents()
            mini = all(card.isVisible() and window._mini_groups_scroll.viewport().rect().contains(
                QRect(card.mapTo(window._mini_groups_scroll.viewport(), card.rect().topLeft()), card.size()))
                for card in window._mini_group_result_cards.values())
            checks['all_groups_visible'] = (
                normal and mini and len(window._group_result_cards) == 2
                and len(window._mini_group_result_cards) == 2)
            window.hide()
        elif len(results) == 3:
            checks['all_groups_live_updated'] = all(
                mapping[first].previews['A']._full_text == '新資料'
                and mapping[second].previews['A']._full_text == '乙12'
                and mapping[second].previews['B']._full_text == '乙13'
                for mapping in (window._group_result_cards, window._mini_group_result_cards))

    def progress():
        nonlocal stage, cancelled_at
        if stage == 'running' and entered.is_set():
            controller.pause()
            cancelled_at = monotonic()
            release.set()
            stage = 'cancelled'
        elif (stage == 'cancelled' and not worker._document_pending
              and monotonic() - cancelled_at > 1.1):
            checks['batch_cancel_stops_all'] = (
                not controller.running and len(captures) == 7 and len(results) == 3
                and calls == [101, 102, 201, 202, 111, 112, 121]
                and not any(timer.isActive() for timer in
                            (controller._cycle_timer, controller._retry, controller._deadline))
                and not controller._images and not controller._results
                and all(group['fingerprint'] is None for group in controller._groups.values())
                and app.clipboard().text() == before_clipboard)
            stage = 'stopping'
            stop()
        elif stage == 'stopping' and controller.is_stopped and not worker.isRunning():
            deadline.stop()
            poll.stop()
            loop.quit()

    try:
        capture_module.mss.mss = BatchScreen
        capture_worker = capture_module.ComparisonCaptureWorker(capture=capture_module.capture_pair)
        worker = OcrWorker(BatchOracle())
        controller = ComparisonController(worker, capture_worker=capture_worker)
        first = controller.active_group_id
        if not all(controller.set_region(name, value) for name, value in region_sets[1].items()):
            raise ValueError('First synthetic pair refused')
        second = controller.add_group('第二組')
        if second is None or not all(controller.set_region(name, value)
                                     for name, value in region_sets[2].items()):
            raise ValueError('Second synthetic pair refused')
        window = ComparisonWindow(controller)
        controller.updated.connect(published)
        controller.failed.connect(abort)
        controller.state_changed.connect(state_changed)
        worker.engine_failed.connect(abort)
        controller.set_interval(1)
        poll.timeout.connect(progress)
        deadline.timeout.connect(abort)
        poll.start()
        deadline.start(16000)
        if not controller.start():
            raise ValueError('Synthetic batch refused')
        loop.exec()
        summaries = {group['id']: group['result'] for group in controller.groups}
        checks['batch_groups_isolated'] = (
            [result['group_id'] for result in results] == [first, second, first]
            and [result['verdict'] for result in results] == ['match', 'mismatch', 'match']
            and summaries[first]['a'] == '新資料' and summaries[first]['b'] == '新資料'
            and summaries[second]['a'] == '乙12' and summaries[second]['b'] == '乙13')
        checks['batch_unchanged_skipped'] = (
            unchanged == [first, second, second]
            and [result['cycle'] for result in results] == [1, 1, 2]
            and summaries[second]['timestamp'] == results[1]['timestamp'])
        checks['batch_serialized'] = events == [
            ('capture', 1), ('ocr', 101), ('ocr', 102),
            ('capture', 2), ('ocr', 201), ('ocr', 202),
            ('capture', 1), ('capture', 2),
            ('capture', 1), ('ocr', 111), ('ocr', 112),
            ('capture', 2), ('capture', 1), ('ocr', 121)]
        if failed:
            checks = dict.fromkeys(BATCH_CHECK_NAMES, False)
    except Exception:  # noqa: BLE001 - fixed booleans only, no user/private diagnostics
        checks = dict.fromkeys(BATCH_CHECK_NAMES, False)
    finally:
        poll.stop()
        deadline.stop()
        release.set()
        stop()
        if controller is not None:
            controller._capture.wait(3000)
        if worker is not None:
            worker.wait(3000)
        if window is not None:
            window.shutdown()
            window.deleteLater()
        capture_module.mss.mss = original_screen
    return checks
