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
)


def run_comparison_probe(report_path):
    """Exercise the actual capture cropper, controller and shared OCR queue.

    The screen provider is replaced only inside this isolated CLI probe. Pixel
    labels are an explicit deterministic oracle; no real screen is captured,
    and this must never be described as model accuracy or inference evidence.
    """
    import numpy as np
    from PySide6.QtCore import QEventLoop, QTimer, Qt
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
        report['passed'] = not failed and all(report['checks'].values())
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
    if not report['passed']:
        report['error_code'] = 'COMPARISON_PROBE_CHECK_FAILED'
    try:
        write_validation_report(report_path, report)
    except (OSError, ValueError, TypeError):
        return 1
    return 0 if report['passed'] else 1
