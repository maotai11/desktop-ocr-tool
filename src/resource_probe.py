"""Real-model manual invoice workload for an external, whole-process sampler.

The generated target is a calibration fixture, not customer-invoice accuracy
evidence. Only scalar checks are written; screen pixels and OCR text stay local.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import time
from uuid import uuid4

import numpy as np

from .core.validation_report import executable_sha256, write_validation_report
from .core.version import APP_VERSION


class WorkloadFailure(RuntimeError):
    pass


def _require(condition, code):
    if not condition:
        raise WorkloadFailure(code)


def _until(app, predicate, seconds=60):
    from PySide6.QtCore import QEventLoop
    deadline = time.monotonic() + seconds
    while not predicate():
        if time.monotonic() >= deadline:
            raise WorkloadFailure('WORKLOAD_TIMEOUT')
        app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 10)
        time.sleep(.005)


def fixture_image():
    """A fully synthetic, calibrated invoice and two-row ledger target."""
    from PySide6.QtCore import Qt, QRect
    from PySide6.QtGui import QColor, QFont, QImage, QPainter
    image = QImage(1000, 430, QImage.Format.Format_RGB32)
    image.fill(QColor('white'))
    painter = QPainter(image)
    painter.setPen(QColor('black'))
    font = QFont('Arial')
    font.setPixelSize(27)
    painter.setFont(font)
    invoice_rect = (10, 20, 330, 380)
    labels = ['AMOUNT', 'TAX', 'DATE', 'INVOICE', 'SELLER']
    values = ['1,000', '50', '1150108', 'AB12345678', '04595257']
    keys = ['net_amount', 'tax_amount', 'issue_date', 'invoice_number', 'seller_tax_id']
    overrides = {}
    for index, (key, label, value) in enumerate(zip(keys, labels, values)):
        y = 25 + index*70
        painter.drawText(QRect(15, y, 135, 50), Qt.AlignmentFlag.AlignVCenter, label)
        painter.drawText(QRect(155, y, 180, 50), Qt.AlignmentFlag.AlignVCenter, value)
        overrides[key] = ((155-10)/330, (y-20)/380, 180/330, 50/380)
    table_rect = (360, 20, 620, 150)
    tx, ty, tw, _ = table_rect
    widths = [55, 85, 65, 130, 155, 130]
    edges = [0]
    for width in widths:
        edges.append(edges[-1]+width)
    painter.fillRect(QRect(tx+2, ty+47, 51, 47), QColor('#0050dc'))
    font.setPixelSize(20)
    painter.setFont(font)
    headings = ['', 'AMOUNT', 'TAX', 'DATE', 'INVOICE', 'SELLER']
    row = ['', '1000', '50', '2026-01-08', 'AB12345678', '04595257']
    other = ['', '9999', '999', '2026-02-09', 'CD98765432', '00000000']
    columns = {}
    for index, width in enumerate(widths):
        x = tx+edges[index]
        for y, text in ((ty+1, headings[index]), (ty+46, row[index]), (ty+96, other[index])):
            painter.drawText(QRect(x+3, y, width-6, 44), Qt.AlignmentFlag.AlignCenter, text)
        if index:
            columns[keys[index-1]] = (edges[index]/tw, 0., width/tw, 1.)
    painter.setPen(QColor('black'))
    for y in (0, 45, 95, 149):
        painter.drawLine(tx, ty+y, tx+tw-1, ty+y)
    for x in edges:
        painter.drawLine(tx+x, ty, tx+x, ty+149)
    painter.end()
    return image, invoice_rect, table_rect, overrides, columns


def run_resource_probe(report_path):
    from PySide6.QtCore import QCoreApplication, QEvent, Qt
    from PySide6.QtGui import QPainter
    from PySide6.QtWidgets import QApplication, QWidget

    from .core.config import ConfigManager
    from .core.resource_budget import memory_preflight, runtime_memory_accounting
    from .data.database import Database
    from .data.file_manager import FileManager
    from .data.repository import ItemRepository
    from .ocr.engine import OcrEngine
    from .ocr.model_validator import verified_model_manifest
    from .ui.widget import FloatingWidget
    from .workers.db_worker import create_db_worker_in_thread
    from .workers.ocr_worker import OcrWorker

    report_path = str(Path(report_path).absolute())
    Path(report_path).parent.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    report = {'schema': 1, 'probe': 'resource_invoice_workload', 'version': APP_VERSION,
              'platform': sys.platform, 'frozen': bool(getattr(sys, 'frozen', False)),
              'executable_sha256': executable_sha256(), 'qt_platform': app.platformName(),
              'source_commit': os.environ.get('GITHUB_SHA'),
              'run_id': os.environ.get('GITHUB_RUN_ID'), 'run_attempt': os.environ.get('GITHUB_RUN_ATTEMPT'),
              'passed': False, 'phase': 'starting', 'checks': {},
              'manual_rounds_completed': 0, 'fields_each': 5, 'native_capture_used': False,
              'large_source_pixels': 6_000_000, 'clean_machine_verified': False,
              'accuracy_evidence': 'SYNTHETIC_CALIBRATED_VALUES_ONLY'}

    def phase(value):
        report['phase'] = value
        write_validation_report(report_path, report)

    widget = worker = db_thread = database = surface = None
    try:
        cfg = ConfigManager()
        data_dir = Path(cfg.get_data_directory())
        data_dir.mkdir(parents=True, exist_ok=True)
        database = Database(str(data_dir/'resource-probe.db'))
        repo = ItemRepository(database)
        files = FileManager(str(data_dir))
        db_worker, db_thread = create_db_worker_in_thread(repo, files)
        engine = OcrEngine(model_profile='v6-small')
        worker = OcrWorker(engine)
        widget = FloatingWidget(repo, files, db_worker, worker, cfg, str(data_dir))
        worker.engine_ready.connect(widget.on_ocr_engine_ready)
        failures = []
        worker.engine_failed.connect(lambda *_: failures.append('MODEL_LOAD_FAILED'))
        phase('models_loading')
        worker.start_loading()
        _until(app, lambda: engine.is_ready() or failures, 60)
        _require(not failures, 'MODEL_LOAD_FAILED')
        manifest = verified_model_manifest(profile='v6-small')
        report['model_profile'] = 'v6-small'
        report['model_recognizer_sha256'] = manifest['rec']['sha256']
        report['checks']['shared_small_engine_verified'] = engine.model_identity['profile'] == 'v6-small'

        image, invoice_rect, table_rect, invoice_overrides, columns = fixture_image()
        screen = app.primaryScreen()
        _require(screen is not None, 'PRIMARY_SCREEN_REQUIRED')
        area = screen.availableGeometry()
        _require(area.width() >= image.width()+20 and area.height() >= image.height()+20,
                 'SYNTHETIC_TARGET_SCREEN_TOO_SMALL')

        class Target(QWidget):
            def paintEvent(self, event):
                painter = QPainter(self)
                painter.drawImage(0, 0, image)

        surface = Target()
        surface.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                               Qt.WindowType.WindowStaysOnTopHint)
        surface.setFixedSize(image.size())
        surface.move(area.left()+10, area.top()+10)
        surface.show()
        surface.raise_()
        app.processEvents()
        widget.show()
        widget.open_invoice_check()
        controller, window = widget._invoice_controller, widget._invoice_window
        scale = screen.devicePixelRatio()
        screen_rect = screen.geometry()
        for side, rect in (('invoice', invoice_rect), ('table', table_rect)):
            x, y, width, height = rect
            region = (round((surface.x()-screen_rect.x()+x)*scale),
                      round((surface.y()-screen_rect.y()+y)*scale),
                      round(width*scale), round(height*scale), 1)
            controller.set_region(side, region)
        controller.set_overrides('invoice', invoice_overrides)
        controller.set_overrides('table', columns)
        report['native_capture_used'] = type(controller._capture).__name__ == 'PairCaptureService'
        captured = []
        controller.capture_started.connect(lambda: captured.append(True))
        phase('invoice_check')
        for _ in range(3):
            window.action_bar.check.click()
            _until(app, lambda: controller.state in ('ready', 'error'), 90)
            _require(controller.state == 'ready' and controller.last_result is not None,
                     'MANUAL_SNAPSHOT_FAILED')
            checks = controller.last_result['checks']
            _require(len(checks) == 5 and all(check['invoice']['value'] is not None
                     and check['invoice']['value'] == check['row']['value'] for check in checks),
                     'SYNTHETIC_VALUES_DIFFER')
            row = controller.last_result['active_row']
            _require(row[1] >= round(45*scale) and row[1]+row[3] <= round(95*scale),
                     'ADJACENT_ROW_ENTERED')
            report['manual_rounds_completed'] += 1
            write_validation_report(report_path, report)
        report['checks']['three_manual_checks'] = report['manual_rounds_completed'] == 3
        report['checks']['only_current_row'] = True
        before = len(captured)
        end = time.monotonic()+.4
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(.005)
        report['checks']['no_automatic_capture'] = len(captured) == before == 6
        window.hide()
        widget.hide()

        phase('image_helper')
        from .handwriting_image_probe import run_image_probe
        helper_path = data_dir/'resource-helper.json'
        _require(run_image_probe(str(helper_path)) == 0, 'REAL_IMAGE_HELPER_FAILED')
        import json
        helper = json.loads(helper_path.read_text(encoding='utf-8'))
        report['checks']['helper_inference_and_reap'] = (
            helper['passed'] is True and helper['inference_completed'] is True
            and helper['worker_pid_verified'] is True and helper['children_reaped'] is True)
        helper_path.unlink()

        phase('bounded_raster')
        memory_preflight(additional_bytes=146_000_000)
        import cv2
        large = np.full((2000, 3000, 3), 255, dtype=np.uint8)
        for y in range(80, 2000, 125):
            for x in range(30, 2900, 375):
                cv2.putText(large, '12345', (x, y), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 2)
        token = 'resource-raster-' + uuid4().hex
        completed = []
        def raster_done(observed_token, page, field, result):
            if observed_token == token:
                completed.append(result)
        worker.document_ocr_done.connect(raster_done)
        _require(worker.queue_document_ocr(token, 0, 'bounded_raster', large), 'RASTER_QUEUE_REJECTED')
        del large
        _until(app, lambda: bool(completed), 120)
        worker.document_ocr_done.disconnect(raster_done)
        report['checks']['bounded_raster_inference'] = completed[0].get('status') in ('done', 'needs_review')
        _require(report['checks']['bounded_raster_inference'], 'BOUNDED_RASTER_FAILED')
        report['checks']['no_history_or_capture_files'] = (
            not repo.list_recent(limit=1) and not list(data_dir.glob('captures/**/*.png')))
        report['runtime_memory'] = runtime_memory_accounting()
    except Exception as exc:
        report['error_code'] = str(exc) if isinstance(exc, WorkloadFailure) else type(exc).__name__
    finally:
        report['phase'] = 'shutdown'
        try:
            write_validation_report(report_path, report)
        except OSError:
            report['error_code'] = 'REPORT_WRITE_FAILED'
        if widget is not None:
            widget.begin_shutdown()
        if worker is not None:
            worker.stop()
            try:
                _until(app, lambda: not worker.isRunning(), 30)
                worker.wait()
            except WorkloadFailure:
                report['error_code'] = 'OCR_SHUTDOWN_FAILED'
        if db_thread is not None:
            db_thread.quit()
            db_thread.wait(5000)
        if database is not None and (db_thread is None or not db_thread.isRunning()):
            database.close()
        if surface is not None:
            surface.close()
            surface.deleteLater()
        if widget is not None:
            stopped = widget.invoice_check_is_stopped()
            report['checks']['ownership_drained'] = bool(stopped and (worker is None or not worker.isRunning())
                and (db_thread is None or not db_thread.isRunning()))
            widget.close()
            widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        report['passed'] = (bool(report['checks']) and all(value is True for value in report['checks'].values())
                            and report['native_capture_used'] is True and 'error_code' not in report)
        report['phase'] = 'complete'
        try:
            write_validation_report(report_path, report)
        except OSError:
            return 1
    return 0 if report['passed'] else 1
