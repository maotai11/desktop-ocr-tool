"""One QThread lifetime per worker; no empty-queue/start race."""
import queue
import threading
from PySide6.QtCore import QThread


class QueueWorker(QThread):
    def __init__(self, parent=None, capacity=64):
        super().__init__(parent)
        self._queue = queue.Queue()
        self._submission_lock = threading.Lock()
        self._capacity = capacity
        self._started_once = False
        self._accepting = True

    def _ensure_started(self):
        if not self._started_once:
            self._started_once = True
            self.start()

    def submit(self, task):
        with self._submission_lock:
            if not self._accepting:
                raise RuntimeError('程式正在關閉，已停止接受新工作')
            if self._queue.qsize() >= self._capacity:
                raise RuntimeError('工作佇列已滿，請等待目前工作完成')
            self._queue.put_nowait(task)
            self._ensure_started()

    def stop(self):
        """Reject new submissions and drain accepted tasks before exit."""
        with self._submission_lock:
            if self._accepting:
                self._accepting = False
                if self._started_once:
                    self._queue.put_nowait(None)

    def tasks(self):
        while True:
            task = self._queue.get()
            try:
                if task is None:
                    return
                yield task
            finally:
                self._queue.task_done()
