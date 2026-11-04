"""Owner-thread scheduling for bounded, non-destructive history archiving."""
from datetime import datetime
from threading import Event
from uuid import uuid4

from PySide6.QtCore import QObject, QTimer, Signal, Slot


class HistoryMaintenanceController(QObject):
    request = Signal(object)
    archived = Signal(int)
    failed = Signal(str)
    stopped = Signal()

    DAILY_INTERVAL_MS = 24 * 60 * 60 * 1000
    BATCH_SIZE = 200

    def __init__(self, worker, cfg, parent=None, *, clock=None):
        super().__init__(parent)
        self._cfg = cfg
        self._clock = clock or datetime.now
        self._started = self._closing = self._stopped_emitted = False
        self._generation = 0
        self._days = None
        self._last_attempt_day = None
        self._run_now = None
        self._event = None
        self._pending_request_id = None
        self._restart_pending = False
        self._daily_timer = QTimer(self)
        self._daily_timer.setInterval(self.DAILY_INTERVAL_MS)
        self._daily_timer.timeout.connect(self.request_check)
        self._batch_timer = QTimer(self)
        self._batch_timer.setSingleShot(True)
        self._batch_timer.timeout.connect(self._send_batch)
        self.request.connect(worker.archive_inactive)
        worker.history_maintenance_done.connect(self._done)

    @property
    def is_stopped(self):
        return (not self._daily_timer.isActive() and not self._batch_timer.isActive()
                and self._pending_request_id is None)

    @property
    def days(self):
        return self._days or 0

    def start(self):
        if self._closing or self._started:
            return
        self._started = True
        self.refresh()

    def refresh(self):
        """Apply settings now, cancelling the old generation before replacing it."""
        if self._closing:
            return
        days = self._cfg.get('history', 'auto_archive_days', default=90)
        invalid = type(days) is not int or not 0 <= days <= 36500
        if invalid:
            days = 0
        if days != self._days:
            self._cancel_run()
            self._days = days
            self._last_attempt_day = None
            self._restart_pending = days > 0
        if not self._started:
            return
        if days > 0:
            if not self._daily_timer.isActive():
                self._daily_timer.start()
            self.request_check()
        else:
            self._daily_timer.stop()
        if invalid:
            self.failed.emit('自動封存天數無效，已暫停自動整理。')

    def request_check(self, *, force=False):
        """Coalesce save/update hooks; at most one attempt per local day."""
        if (self._closing or not self._started or not self._days
                or self._pending_request_id is not None or self._run_now is not None):
            return
        now = self._clock()
        if not force and not self._restart_pending and self._last_attempt_day == now.date():
            return
        self._last_attempt_day = now.date()
        self._restart_pending = False
        self._run_now = now
        self._event = Event()
        self._batch_timer.start(0)

    @Slot(int)
    def on_item_saved(self, _item_id):
        """Typed queued hook; new writes do not bypass the daily rate limit."""
        self.request_check()

    def _cancel_run(self):
        self._generation += 1
        self._batch_timer.stop()
        if self._event is not None:
            self._event.set()
        self._run_now = None

    @Slot()
    def _send_batch(self):
        if self._closing or self._run_now is None or self._pending_request_id is not None:
            return
        request_id = uuid4().hex
        self._pending_request_id = request_id
        self.request.emit({'request_id': request_id, 'generation': self._generation,
                           'days': self._days, 'now': self._run_now,
                           'limit': self.BATCH_SIZE, 'cancelled': self._event})

    @Slot(object)
    def _done(self, response):
        if (self._pending_request_id is None or not isinstance(response, dict)
                or response.get('request_id') != self._pending_request_id):
            return
        self._pending_request_id = None
        if self._closing:
            self._emit_stopped()
            return
        archived = response.get('archived_ids', [])
        if archived:
            self.archived.emit(len(archived))
        if response.get('generation') != self._generation:
            if self._restart_pending:
                self.request_check()
            return
        if response.get('error'):
            self._run_now = None
            self.failed.emit('自動封存未完成；歷史資料與附件仍保留，請稍後再整理。')
        elif response.get('cancelled'):
            self._run_now = None
        elif response.get('has_more'):
            self._batch_timer.start(0)
        else:
            self._run_now = None

    def stop(self):
        if self._closing:
            return
        self._closing = True
        self._daily_timer.stop()
        self._cancel_run()
        if self._pending_request_id is None:
            self._emit_stopped()

    def _emit_stopped(self):
        if not self._stopped_emitted:
            self._stopped_emitted = True
            self.stopped.emit()
