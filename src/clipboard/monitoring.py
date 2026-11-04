"""Apply clipboard collection choices immediately, without replaying old data."""
from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Signal

from .watcher import ClipboardWatcher

logger = logging.getLogger(__name__)


class ClipboardMonitoringController(QObject):
    """One live text listener, with revocable callbacks owned by the GUI thread.

    Images are deliberately unsupported. Disabled monitoring, disabled text
    collection and manual pause all detach the listener entirely. Refreshing
    never reads or imports the current clipboard; only future changes count.
    """

    state_changed = Signal(bool)

    def __init__(self, cfg, save_callback, parent=None):
        super().__init__(parent)
        self._cfg = cfg
        self._save_callback = save_callback
        self._manual_paused = False
        self._stopped = False
        self._generation = 0
        self._watcher = None
        self._text_handler = None
        self._policy = None
        self.refresh()

    @property
    def active(self):
        return self._watcher is not None and not self._stopped

    @property
    def is_paused(self):
        """The explicit pause choice, independent of the monitoring setting."""
        return self._manual_paused

    def _enabled(self):
        return (not self._stopped and not self._manual_paused
                and self._cfg.get('clipboard', 'monitor_clipboard', default=False) is True
                and self._cfg.get('clipboard', 'auto_save_text', default=True) is True)

    def _detach(self):
        self._generation += 1
        watcher, handler = self._watcher, self._text_handler
        self._watcher = self._text_handler = None
        if watcher is None:
            return
        if handler is not None:
            try:
                watcher.text_captured.disconnect(handler)
            except (RuntimeError, TypeError):
                pass
        watcher.close()
        watcher.deleteLater()

    def refresh(self):
        if self._stopped:
            return
        enabled = self._enabled()
        ignore_self = self._cfg.get('clipboard', 'ignore_self', default=True) is not False
        policy = (enabled, ignore_self, self._manual_paused)
        if policy == self._policy:
            return
        self._detach()
        self._policy = policy
        if enabled:
            watcher = ClipboardWatcher(parent=self, ignore_self=ignore_self)
            generation = self._generation

            def forward(text):
                self._receive_text(generation, watcher, text)

            self._watcher, self._text_handler = watcher, forward
            watcher.text_captured.connect(forward)
        self.state_changed.emit(self.active)

    def _receive_text(self, generation, watcher, text):
        # A queued old signal must not revive collection after off/on, pause,
        # settings changes or application shutdown. Check policy again here,
        # even if its UI notification has not yet reached refresh().
        if generation != self._generation or watcher is not self._watcher or not self._enabled():
            return
        limit = self._cfg.get('clipboard', 'max_text_length', default=50000)
        if type(limit) is not int or limit < 0:
            limit = 50000
        if not isinstance(text, str) or not text or len(text) > limit:
            return
        try:
            self._save_callback(text)
        except Exception as exc:  # noqa: BLE001 - never log collected content
            logger.warning('剪貼簿文字收錄未完成 (%s)', type(exc).__name__)

    def pause(self, paused):
        if self._stopped:
            return
        self._manual_paused = bool(paused)
        self.refresh()

    def stop(self):
        if self._stopped:
            return
        self._stopped = True
        self._detach()
        self.state_changed.emit(False)
