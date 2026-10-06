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
            import gc
            import hashlib
            import json
            import cv2
            from .ocr.model_validator import MODEL_PROFILES, DEFAULT_MODEL_PROFILE, model_root
            fixture_root = model_root() / 'models' / 'validation'
            fixtures = json.loads((fixture_root / 'fixtures.json').read_text(encoding='utf-8'))
            report['model_profiles'] = {}
            report['models'] = verified_model_manifest()
            for profile in MODEL_PROFILES:
                t0 = time.perf_counter()
                engine = OcrEngine(enable_second_pass=False, model_profile=profile)
                engine.load()
                profile_report = {'models': verified_model_manifest(profile=profile),
                                  'model_identity': engine.model_identity,
                                  'load_seconds': time.perf_counter() - t0,
                                  'passed': False, 'fixtures': []}
                report['model_profiles'][profile] = profile_report
                image = Image.new('RGB', (420, 70), 'white')
                ImageDraw.Draw(image).text((8, 12), 'Desktop OCR 12345',
                    font=ImageFont.load_default(size=32), fill='black')
                result = engine.run_ocr(np.asarray(image)[:, :, ::-1].copy())
                profile_report['ocr_result'] = result
                if (''.join(result['text'].split()) != 'DesktopOCR12345'
                        or result['status'] not in ('done', 'needs_review')
                        or not result.get('engine') or not result.get('model_version')):
                    raise RuntimeError(f'Known-text OCR/provenance mismatch: {profile}: {result}')
                for fixture in fixtures['cases']:
                    path = fixture_root / fixture['file']
                    if (path.parent != fixture_root or hashlib.sha256(path.read_bytes()).hexdigest()
                            != fixture['image_sha256']):
                        raise ValueError('Bundled validation fixture hash mismatch')
                    observed = engine.run_ocr(cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR))
                    profile_report['fixtures'].append({'id': fixture['id'],
                        'image_sha256': fixture['image_sha256'],
                        'ground_truth': fixture['ground_truth'], 'result': observed})
                    # Literal comparison deliberately retains spaces, punctuation and case.
                    if observed['text'] != fixture['ground_truth'] or observed['status'] == 'failed':
                        raise RuntimeError(f'Literal OCR fixture mismatch: {profile}/{fixture["id"]}')
                profile_report['passed'] = True
                if profile == DEFAULT_MODEL_PROFILE:
                    report['ocr_result'] = result
                    report['load_seconds'] = profile_report['load_seconds']
                del engine
                gc.collect()
            report['checks']['models'] = True
            report['checks']['ocr'] = True
            result = report['ocr_result']
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
