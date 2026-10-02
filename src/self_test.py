"""Frozen smoke probe; output goes to an explicitly chosen report path."""
import json
import os
import sys
import tempfile
import time
from pathlib import Path


def run_self_test(report_path):
    report = {'schema': 1, 'frozen': bool(getattr(sys,'frozen',False)),
              'platform': sys.platform, 'telemetry_env':os.environ.get('ORT_DISABLE_TELEMETRY'),
              'clean_machine_verified':False, 'passed':False}
    try:
        from PySide6.QtWidgets import QApplication
        from PySide6.QtGui import QImage
        app=QApplication.instance() or QApplication([])
        report['qt_platform']=app.platformName()
        assert not QImage(10,10,QImage.Format.Format_RGB32).isNull()
        from PIL import Image, ImageDraw, ImageFont
        import numpy as np
        from .ocr.engine import OcrEngine
        from .ocr.model_validator import verified_model_manifest
        from .data.database import Database
        from .data.repository import ItemRepository
        from .data.models import ItemCreateDTO, OcrResultDTO
        with tempfile.TemporaryDirectory(prefix='desktop-ocr-selftest-') as directory:
            t0=time.perf_counter();engine=OcrEngine(enable_second_pass=False)
            engine.load(); report['load_seconds']=time.perf_counter()-t0
            report['models']=verified_model_manifest()
            image=Image.new('RGB',(420,70),'white')
            ImageDraw.Draw(image).text((8,12),'Desktop OCR 12345',font=ImageFont.load_default(size=32),fill='black')
            result=engine.run_ocr(np.asarray(image)[:,:,::-1].copy())
            report['ocr_result']=result
            assert ''.join(result['text'].split())=='DesktopOCR12345', result['text']
            db=Database(str(Path(directory)/'test.db'))
            try:
                repo=ItemRepository(db)
                item_id=repo.insert(ItemCreateDTO(item_type='text',source_mode='import',text_content='test'))
                repo.update_ocr_result(item_id,OcrResultDTO(text=result['text'],confidence=result['confidence'],
                    status=result['status'],engine=result['engine'],model_version=result['model_version']))
                assert repo.get_by_id(item_id).ocr_model_version==result['model_version']
                assert db.get_connection().execute('PRAGMA integrity_check').fetchone()[0]=='ok'
            finally:
                db.close()
        report['passed']=True
    except Exception as exc:
        import traceback
        report['error']=str(exc); report['traceback']=traceback.format_exc()
    Path(report_path).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return 0 if report['passed'] else 1
