"""Source/frozen manual-raster lifecycle gate, not an accuracy benchmark."""
import hashlib
import json
import sys
from src.core.branding import resource_root
from src.core.validation_report import executable_sha256, write_validation_report
from src.core.version import APP_VERSION


_CONTROLLED_FAILURE_REASONS = {
    '手寫辨識逾時，請重試': 'TIMEOUT',
    '手寫引擎或模型缺漏／版本不符，請使用完整免安裝資料夾': 'ASSETS_UNAVAILABLE',
    '無法安全啟動手寫引擎': 'JOB_ASSIGNMENT_FAILED',
    '手寫引擎通訊失敗': 'IPC_WRITE_FAILED',
    '手寫引擎版本或回傳格式不符，請更新完整免安裝資料夾': 'PROTOCOL_INVALID',
    '手寫引擎輸出超出限制': 'OUTPUT_LIMIT',
    '手寫引擎無法啟動': 'PROCESS_START_FAILED',
    '手寫引擎已停止，請重試': 'PROCESS_EXITED',
    '手寫辨識失敗，請重試': 'RECOGNITION_FAILED',
}
IMAGE_FAILURE_REASONS = frozenset((*_CONTROLLED_FAILURE_REASONS.values(), 'PROBE_DEADLINE', 'OTHER'))


def _image_failure_reason(args):
    if not args:
        return 'PROBE_DEADLINE'
    if len(args) != 1 or type(args[0]) is not str:
        return 'OTHER'
    return _CONTROLLED_FAILURE_REASONS.get(args[0], 'OTHER')


def run_image_probe(report_path):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from src.handwriting.image_controller import ImageHandwritingController
    from src.handwriting.protocol import VERSION
    from src.ocr.model_validator import verified_model_manifest
    app = QApplication.instance() or QApplication([])
    report = dict(schema=1, probe='handwriting_image_lifecycle', version=APP_VERSION,
        frozen=bool(getattr(sys,'frozen',False)), platform=sys.platform,
        qt_platform=app.platformName(), executable_sha256=executable_sha256(),
        helper_protocol=VERSION, passed=False, inference_completed=False,
        worker_pid_verified=False, job_assigned=False, cancelled_and_reaped=False,
        children_reaped=False, clean_machine_verified=False,
        accuracy_evidence='UPSTREAM_TRAINING_RASTER_FUNCTIONAL_ONLY', candidates=[])
    def save():write_validation_report(report_path,report)
    save()
    try:
        directory=resource_root()/'models/handwriting'
        manifest=json.loads((directory/'manifest.json').read_text(encoding='utf-8'))
        fixture=(directory/'validation.json').read_bytes()
        if hashlib.sha256(fixture).hexdigest()!=manifest['validation_sha256']:
            raise ValueError('Fixture hash mismatch')
        strokes=next(case['strokes'] for case in json.loads(fixture)['cases'] if case['label']=='一')
        rec=verified_model_manifest(profile='v6-small')['rec']
        report.update(model_sha256=rec['sha256'], character_sha256=rec['character_sha256'])
    except (OSError,ValueError,KeyError,StopIteration):
        report['error']='IMAGE_PROBE_ASSET_FAILURE';save();return 1
    controller=ImageHandwritingController()
    stage='inference'
    failed=False
    deadline=QTimer();deadline.setSingleShot(True)
    def abort(*args):
        nonlocal failed
        failed=True;report['error']='IMAGE_PROBE_RUNTIME_FAILURE'
        report['failure_reason']=_image_failure_reason(args)
        controller.stop()
    def result(generation,values):
        report['inference_completed']=True
        report['candidates']=[value.label for value in values]
        report['worker_pid_verified']=controller.worker_pid_verified
        report['job_assigned']=sys.platform!='win32' or bool(controller._job and controller._job.handle)
        save()
    def stopped():
        nonlocal stage
        if not failed and stage=='inference' and report['inference_completed']:
            stage='cancel';controller.resume();controller.submit(strokes)
            QTimer.singleShot(10,controller.stop)
            return
        deadline.stop()
        report['cancelled_and_reaped']=stage=='cancel' and controller.is_stopped
        report['children_reaped']=controller.is_stopped
        report['passed']=bool(not failed and report['inference_completed'] and
            report['worker_pid_verified'] and report['job_assigned'] and
            report['cancelled_and_reaped'] and report['children_reaped'] and '一' in report['candidates'])
        save();app.quit()
    controller.candidates.connect(result)
    controller.failed.connect(abort)
    controller.stopped.connect(stopped)
    deadline.timeout.connect(abort)
    deadline.start(25000)
    controller.submit(strokes)
    app.exec()
    return 0 if report['passed'] else 1
