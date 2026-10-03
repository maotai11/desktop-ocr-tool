"""Real bundled-model/SQLite probe; not proof of clean-machine acceptance."""
import os
import sys
import tempfile
import time
from pathlib import Path

from .core.validation_report import executable_sha256, write_validation_report
from .core.version import APP_VERSION


def run_self_test(report_path):
    report = {'schema': 2, 'probe': 'ocr_database', 'version': APP_VERSION,
              'frozen': bool(getattr(sys, 'frozen', False)),
              'platform': sys.platform, 'telemetry_env': os.environ.get('ORT_DISABLE_TELEMETRY'),
              'network_observation': 'NOT_RUN', 'checks': {},
              'clean_machine_verified': False, 'passed': False}
    try:
        report['executable_sha256'] = executable_sha256()
        write_validation_report(report_path, report)
        from PySide6.QtGui import QImage
        from PySide6.QtWidgets import QApplication
        app=QApplication.instance() or QApplication([])
        report['qt_platform']=app.platformName()
        if QImage(10, 10, QImage.Format.Format_RGB32).isNull():
            raise RuntimeError('Qt image allocation failed')
        report['checks']['qt'] = True
        import numpy as np
        from PIL import Image, ImageDraw, ImageFont

        from .data.database import Database
        from .data.models import ItemCreateDTO, OcrResultDTO
        from .data.repository import ItemRepository
        from .ocr.engine import OcrEngine
        from .ocr.model_validator import verified_model_manifest
        with tempfile.TemporaryDirectory(prefix='desktop-ocr-selftest-') as directory:
            t0=time.perf_counter();engine=OcrEngine(enable_second_pass=False)
            engine.load(); report['load_seconds']=time.perf_counter()-t0
            report['models']=verified_model_manifest()
            report['checks']['models'] = True
            image=Image.new('RGB',(420,70),'white')
            ImageDraw.Draw(image).text((8,12),'Desktop OCR 12345',font=ImageFont.load_default(size=32),fill='black')
            result=engine.run_ocr(np.asarray(image)[:,:,::-1].copy())
            report['ocr_result']=result
            if (''.join(result['text'].split()) != 'DesktopOCR12345'
                    or result['status'] not in ('done', 'needs_review')
                    or not result.get('engine') or not result.get('model_version')):
                raise RuntimeError(f'Known-text OCR/provenance mismatch: {result}')
            report['checks']['ocr'] = True
            db=Database(str(Path(directory)/'test.db'))
            try:
                repo=ItemRepository(db)
                item_id=repo.insert(ItemCreateDTO(item_type='text',source_mode='import',text_content='test'))
                repo.update_ocr_result(item_id,OcrResultDTO(text=result['text'],confidence=result['confidence'],
                    status=result['status'],engine=result['engine'],model_version=result['model_version']))
                saved = repo.get_by_id(item_id)
                if (saved.ocr_model_version != result['model_version']
                        or saved.ocr_engine != result['engine']
                        or saved.text_content != result['text']):
                    raise RuntimeError('Database OCR/provenance persistence mismatch')
                report['database_integrity'] = db.get_connection().execute('PRAGMA integrity_check').fetchone()[0]
                if report['database_integrity'] != 'ok':
                    raise RuntimeError('SQLite integrity check failed')
                report['checks']['database'] = True
            finally:
                db.close()
        report['passed']=True
    except Exception as exc:  # noqa: BLE001 - report every failed import/runtime probe
        import traceback
        report['error']=str(exc); report['traceback']=traceback.format_exc()
    try:
        write_validation_report(report_path, report)
    except OSError:
        return 1
    return 0 if report['passed'] else 1
