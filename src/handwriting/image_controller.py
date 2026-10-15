"""Manual, killable image-recognition subprocess using only bundled OCR bytes."""
from pathlib import Path
import sys
import struct
from .assets import HelperAssets
from .candidates import Candidate, is_cjk_scalar
from .controller import HandwritingController
from .protocol import ProtocolError, decode_candidate_records


class _CjkAlphabet:
    def __contains__(self, label):
        return is_cjk_scalar(label)


def decode_image_candidates(payload, alphabet):
    status, values = decode_candidate_records(payload, alphabet)
    if len(values) > 2 or any(value.normalized_rank for value in values):
        raise ProtocolError('Invalid image candidate budget/source')
    return status, tuple(Candidate(value.label, image_rank=index+1)
                         for index, value in enumerate(values))


class ImageHandwritingController(HandwritingController):
    def __init__(self, parent=None):
        self.worker_pid_verified = False
        arguments = ('--handwriting-image-helper',)
        if not getattr(sys, 'frozen', False):
            arguments = (str(Path(__file__).resolve().parents[1]/'main.py'), *arguments)
        assets = HelperAssets(Path(sys.executable).absolute(), arguments, _CjkAlphabet())
        super().__init__(parent, assets=assets, load_timeout_ms=15000,
                         request_timeout_ms=5000, result_decoder=decode_image_candidates,
                         memory_mib=512, ready_validator=self._validate_ready)

    def _validate_ready(self, payload, process_id):
        if len(payload) != 8:
            raise ProtocolError('Invalid image readiness length')
        status, count, worker_pid = struct.unpack('<HHI', payload)
        if status or count or worker_pid != process_id:
            raise ProtocolError('Image worker process ownership mismatch')
        self.worker_pid_verified = True
