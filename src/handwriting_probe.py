"""Real bundled helper/Qt lifecycle probe; never a human-accuracy certificate."""
import hashlib
import json
from pathlib import Path
import secrets
import sys

from src.core.branding import resource_root
from src.core.validation_report import executable_sha256, write_validation_report
from src.core.version import APP_VERSION


def run_handwriting_probe(report_path, wait_for_firewall=False):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from src.clipboard.writer import get_clipboard_epoch, get_last_write_id, write_text_to_clipboard
    from src.core.constants import CUSTOM_MIME_TYPE
    from src.handwriting.assets import MODEL_SHA256, verified_assets
    from src.handwriting.controller import HandwritingController
    from src.handwriting.protocol import encode_strokes, VERSION

    app = QApplication.instance() or QApplication([])
    report = {'schema': 1, 'probe': 'handwriting_lifecycle', 'version': APP_VERSION,
              'frozen': bool(getattr(sys, 'frozen', False)), 'platform': sys.platform,
              'qt_platform': app.platformName(), 'executable_sha256': executable_sha256(),
              'clean_machine_verified': False, 'phase': 'preparing', 'passed': False,
              'accuracy_evidence': 'UPSTREAM_TRAINING_REPLAY_ONLY', 'cases': [],
              'queue_latest_only': False, 'clipboard_roundtrip': False, 'self_mime_preserved': False,
              'helper_stopped': False, 'job_assigned': False, 'firewall_gate_acknowledged': False}
    def save():
        write_validation_report(report_path, report)
    save()
    try:
        root = resource_root()
        assets = verified_assets(root)
        directory = root / 'models/handwriting'
        manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
        helper = json.loads((root / 'native/handwriting/runtime/helper.lock.json').read_text(encoding='utf-8'))
        fixture_bytes = (directory / 'validation.json').read_bytes()
        if hashlib.sha256(fixture_bytes).hexdigest() != manifest['validation_sha256']:
            raise ValueError('Handwriting fixture identity mismatch')
        fixtures = json.loads(fixture_bytes.decode('utf-8'))
        if fixtures['schema'] != 1 or fixtures['evidence'] != 'UPSTREAM_TRAINING_REPLAY_ONLY' or len(fixtures['cases']) != 16:
            raise ValueError('Handwriting fixture contract mismatch')
        for case in fixtures['cases']:
            if case['label'] not in assets.alphabet:
                raise ValueError('Handwriting fixture alphabet mismatch')
            encode_strokes(case['strokes'])
        report.update(helper_sha256=helper['sha256'], model_sha256=MODEL_SHA256,
                      alphabet_sha256=manifest['alphabet_sha256'], validation_sha256=manifest['validation_sha256'],
                      helper_source_sha256=helper['source']['sha256'],
                      helper_architecture=helper.get('machine', 'LINUX_X64_ASSESSMENT'), helper_protocol=VERSION)
    except (ValueError, KeyError, OSError, UnicodeError):
        report['error'] = 'HANDWRITING_ASSET_VALIDATION_FAILED'
        save()
        return 1

    controller = HandwritingController(assets=assets)
    expected_generation = None
    index = 0
    stage = 'fixtures'
    failed = False
    stopping = False
    deadline = QTimer()
    deadline.setSingleShot(True)
    gate_timer = QTimer()
    gate_timer.setInterval(100)
    gate_path = Path(str(report_path) + '.start')

    def stop(error=None):
        nonlocal failed, stopping
        if stopping:
            return
        stopping = True
        failed = error is not None
        gate_timer.stop()
        if error:
            report['error'] = error
        report['phase'] = 'stopping'
        save()
        controller.stop()

    def finished():
        deadline.stop()
        report['helper_stopped'] = controller.is_stopped
        report['phase'] = 'completed'
        # Early handshake details are runner coordination, not release metadata.
        report.pop('helper_path', None)
        report.pop('firewall_nonce', None)
        report['passed'] = bool(not failed and len(report['cases']) == 16
                                and report['queue_latest_only'] and report['clipboard_roundtrip']
                                and report['self_mime_preserved'] and report['helper_stopped']
                                and (sys.platform != 'win32' or report['job_assigned']))
        save()
        # Qt offscreen has a baseline-only QMimeData finalization fault. This
        # explicit synthetic Linux probe releases its own test payload. Native
        # Windows deliberately retains it through exit, so its lifetime is part
        # of the actual source/frozen process-exit gate, never hidden by cleanup.
        if sys.platform != 'win32':
            app.clipboard().clear()
        app.quit()

    def submit_next():
        nonlocal expected_generation, stage
        if index < 16:
            expected_generation = controller.submit(fixtures['cases'][index]['strokes'])
        else:
            stage = 'latest_queue'
            # Same GUI turn: first active, second pending, third replaces pending.
            controller.submit(fixtures['cases'][0]['strokes'])
            controller.submit(fixtures['cases'][1]['strokes'])
            expected_generation = controller.submit(fixtures['cases'][9]['strokes'])

    def result(generation, values):
        nonlocal index
        values = tuple(value.label for value in values)
        if generation != expected_generation or not values:
            stop('HANDWRITING_GENERATION_OR_OUTPUT_FAILED')
            return
        report['job_assigned'] = sys.platform != 'win32' or bool(controller._job and controller._job.handle)
        if stage == 'latest_queue':
            report['queue_latest_only'] = fixtures['cases'][9]['label'] in values
            stop(None if report['queue_latest_only'] else 'HANDWRITING_LATEST_QUEUE_FAILED')
            return
        label = fixtures['cases'][index]['label']
        rank = values.index(label) + 1 if label in values else None
        report['cases'].append({'label': label, 'candidates': list(values), 'rank': rank, 'passed': rank is not None})
        if rank is None:
            stop('HANDWRITING_TRAINING_SMOKE_FAILED')
            return
        if index == 0:
            text = values[0] + '𠮷禰\U000e0100'
            epoch = get_clipboard_epoch()
            write_text_to_clipboard(text)
            report['clipboard_roundtrip'] = app.clipboard().text() == text and get_clipboard_epoch() == epoch + 1
            report['self_mime_preserved'] = (bytes(app.clipboard().mimeData().data(CUSTOM_MIME_TYPE)).decode('ascii')
                                             == get_last_write_id())
        index += 1
        save()
        QTimer.singleShot(0, submit_next)

    def start():
        report['phase'] = 'probing'
        save()
        submit_next()

    def check_gate():
        if not gate_path.exists():
            return
        try:
            if gate_path.is_symlink() or gate_path.stat().st_size > 1024:
                raise ValueError('Invalid gate')
            gate = json.loads(gate_path.read_text(encoding='utf-8-sig'))
            if gate != {'nonce': report['firewall_nonce'], 'helper_sha256': report['helper_sha256']}:
                raise ValueError('Gate identity mismatch')
        except (ValueError, KeyError, OSError):
            stop('HANDWRITING_FIREWALL_GATE_INVALID')
            return
        gate_timer.stop()
        report['firewall_gate_acknowledged'] = True
        start()

    controller.candidates.connect(result)
    controller.failed.connect(lambda _: stop('HANDWRITING_HELPER_FAILED'))
    controller.stopped.connect(finished)
    deadline.timeout.connect(lambda: stop('HANDWRITING_PROBE_TIMEOUT'))
    deadline.start(60000)
    if wait_for_firewall:
        report.update(phase='waiting_for_firewall', helper_path=str(assets.program), firewall_nonce=secrets.token_hex(16))
        save()
        gate_timer.timeout.connect(check_gate)
        gate_timer.start()
    else:
        QTimer.singleShot(0, start)
    app.exec()
    return 0 if report['passed'] else 1
