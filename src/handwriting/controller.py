"""GUI-thread QProcess owner with one active and one replaceable request."""
from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from .assets import verified_assets
from .protocol import (READY, RECOGNIZE, RESULT, START, STOP, HEADER, MAX_REPLY_BYTES,
                       ProtocolError, ReplyDecoder, decode_candidate_records, encode_strokes, frame)
from .windows_job import WindowsJob


class HandwritingController(QObject):
    candidates = Signal(int, object)
    failed = Signal(str)
    # Request-local errors retain their generation. Fatal helper errors are
    # attributed to the newest pending request, since that request also fails.
    request_failed = Signal(int, str)
    stopped = Signal()
    busy_changed = Signal(bool)

    def __init__(self, parent=None, *, assets=None, load_timeout_ms=10000, request_timeout_ms=3000,
                 result_decoder=decode_candidate_records, memory_mib=256, ready_validator=None):
        super().__init__(parent)
        self._assets = assets
        self._result_decoder = result_decoder
        self._memory_mib = memory_mib
        self._ready_validator = ready_validator
        self._process = None
        self._job = None
        self._decoder = ReplyDecoder()
        self._pending = self._active = None
        self._generation = 0
        self._ready = False
        self._closing = False
        self._stderr_bytes = self._stdout_bytes = 0
        self._load_timeout_ms, self._request_timeout_ms = load_timeout_ms, request_timeout_ms
        self._deadline = QTimer(self)
        self._deadline.setSingleShot(True)
        self._deadline.timeout.connect(lambda: self._fail('手寫辨識逾時，請重試'))
        self._stop_timer = QTimer(self)
        self._stop_timer.setSingleShot(True)
        self._stop_timer.timeout.connect(self._kill)

    @property
    def is_stopped(self):
        return self._process is None

    def submit(self, strokes):
        if self._closing:
            return None
        self._generation += 1
        generation = self._generation
        try:
            payload = encode_strokes(strokes)
            if self._assets is None:
                self._assets = verified_assets()
        except (ValueError, OSError, UnicodeError):
            self._emit_error(generation, '手寫引擎或模型缺漏／版本不符，請使用完整免安裝資料夾')
            return None
        self._pending = generation, payload
        self.busy_changed.emit(True)
        if self._process is None:
            self._start()
        elif self._ready and self._active is None:
            self._dispatch()
        return generation

    def _emit_error(self, generation, message):
        self.request_failed.emit(generation, message)
        self.failed.emit(message)  # Compatibility for diagnostics/probes.

    def _latest_request_generation(self):
        request = self._pending or self._active
        return request[0] if request else self._generation

    def _start(self):
        self._decoder = ReplyDecoder()
        self._stderr_bytes = self._stdout_bytes = 0
        self._ready = False
        process = QProcess(self)
        self._process = process
        process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        process.setProgram(str(self._assets.program))  # absolute, no shell or PATH lookup
        process.setArguments(list(self._assets.arguments))
        process.started.connect(self._started)
        process.readyReadStandardOutput.connect(self._read_stdout)
        process.readyReadStandardError.connect(self._read_stderr)
        process.errorOccurred.connect(self._process_error)
        process.finished.connect(self._finished)
        self._deadline.start(self._load_timeout_ms)
        process.start()

    def _started(self):
        if self._closing:
            self._kill()
            return
        try:
            self._job = WindowsJob(self._memory_mib)
            self._job.assign(self._process.processId())
        except OSError:
            self._fail('無法安全啟動手寫引擎')
            return
        self._write(frame(START, 0))  # fail-closed Job assignment BEFORE model/native work

    def _write(self, data):
        if self._process is None or self._process.write(data) != len(data):
            self._fail('手寫引擎通訊失敗')

    def _dispatch(self):
        if self._pending is None or self._closing:
            return
        self._active, self._pending = self._pending, None
        self._decoder = ReplyDecoder()
        self._stdout_bytes = 0
        self._deadline.start(self._request_timeout_ms)
        self._write(frame(RECOGNIZE, *self._active))

    def _read_stdout(self):
        process = self._process
        if process is None:
            return
        process.setReadChannel(QProcess.ProcessChannel.StandardOutput)
        while process.bytesAvailable():
            data = bytes(process.read(HEADER.size + MAX_REPLY_BYTES + 1))
            self._stdout_bytes += len(data)
            try:
                if self._stdout_bytes > HEADER.size + MAX_REPLY_BYTES:
                    raise ProtocolError('Helper output budget exceeded')
                reply = self._decoder.feed(data)
                if reply is None:
                    continue
                if not self._ready:
                    if self._ready_validator is not None:
                        self._ready_validator(reply.payload, process.processId())
                        status, values = 0, ()
                    else:
                        status, values = self._result_decoder(reply.payload, self._assets.alphabet)
                    if reply.kind != READY or reply.generation != 0 or status or values:
                        raise ProtocolError('Invalid readiness handshake')
                    self._ready = True
                    self._deadline.stop()
                    self._dispatch()
                else:
                    status, values = self._result_decoder(reply.payload, self._assets.alphabet)
                    if (reply.kind != RESULT or self._active is None
                            or reply.generation != self._active[0]):
                        raise ProtocolError('Unexpected result generation')
                    generation = self._active[0]
                    self._active = None
                    self._deadline.stop()
                    if status:
                        self._emit_error(generation, '手寫辨識失敗，請重試')
                    elif not self._pending:
                        self.candidates.emit(generation, values)
                    self._dispatch()
                    if self._active is None:
                        self.busy_changed.emit(False)
            except ProtocolError:
                self._fail('手寫引擎版本或回傳格式不符，請更新完整免安裝資料夾')
                return

    def _read_stderr(self):
        process = self._process
        if process is None:
            return
        process.setReadChannel(QProcess.ProcessChannel.StandardError)
        while process.bytesAvailable():
            data = process.read(4096)
            self._stderr_bytes += len(data)
            if self._stderr_bytes > 16384:
                self._fail('手寫引擎輸出超出限制')
                return
        # Never display helper text as UI markup or user content.

    def _process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            if not self._closing:
                self._fail('手寫引擎無法啟動')
            # FailedToStart has no finished signal, including a shutdown race.
            self._finished()
            return
        if self._closing:
            return
        self._fail('手寫引擎已停止，請重試')

    def _fail(self, message):
        self._deadline.stop()
        generation = self._latest_request_generation()
        self._pending = self._active = None
        self._ready = False
        if not self._closing:
            self._emit_error(generation, message)
        self.busy_changed.emit(False)
        self._kill()

    def _kill(self):
        if self._process is not None:
            self._process.kill()

    def stop(self):
        self._closing = True
        self._pending = self._active = None
        self._deadline.stop()
        self.busy_changed.emit(False)
        if self._process is None:
            self.stopped.emit()
        elif self._process.state() == QProcess.ProcessState.Running:
            self._write(frame(STOP, 0))
            self._process.closeWriteChannel()
            self._stop_timer.start(750)
        else:
            self._kill()

    def resume(self):
        if self.is_stopped:
            self._closing = False

    def _finished(self, *args):
        process, self._process = self._process, None
        self._deadline.stop()
        self._stop_timer.stop()
        was_busy = self._active is not None or self._pending is not None
        generation = self._latest_request_generation()
        self._pending = self._active = None
        self._ready = False
        if self._job:
            self._job.close()
            self._job = None
        if process is not None:
            process.deleteLater()
        self.busy_changed.emit(False)
        if was_busy and not self._closing:
            self._emit_error(generation, '手寫引擎已停止，請重試')
        self.stopped.emit()
