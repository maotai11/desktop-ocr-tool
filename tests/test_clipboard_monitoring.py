"""Live privacy choices, listener ownership and stale clipboard callbacks."""
import pytest
from PySide6.QtCore import QObject, Signal

from src.clipboard.monitoring import ClipboardMonitoringController


class Config:
    def __init__(self, **options):
        self.options = {'monitor_clipboard': False, 'auto_save_text': True,
                        'ignore_self': True, 'max_text_length': 50000, **options}

    def get(self, section, key, default=None):
        assert section == 'clipboard'
        return self.options.get(key, default)


@pytest.fixture
def monitored(qapp, monkeypatch):
    instances, controllers = [], []

    class Watcher(QObject):
        text_captured = Signal(str)

        def __init__(self, parent=None, ignore_self=True):
            super().__init__(parent)
            self.ignore_self, self.closed = ignore_self, False
            instances.append(self)

        def close(self):
            self.closed = True

    monkeypatch.setattr('src.clipboard.monitoring.ClipboardWatcher', Watcher)

    def create(**options):
        config, saved = Config(**options), []
        controller = ClipboardMonitoringController(config, saved.append)
        controllers.append(controller)
        return controller, config, saved, instances

    yield create
    for controller in controllers:
        controller.stop()


def test_live_enable_disable_and_text_choice_without_restart(monitored):
    controller, cfg, saved, watchers = monitored()
    assert not controller.active and not watchers
    cfg.options['monitor_clipboard'] = True
    controller.refresh()
    assert controller.active and len(watchers) == 1 and not saved
    watchers[-1].text_captured.emit('啟用後的文字')
    assert saved == ['啟用後的文字']
    cfg.options['auto_save_text'] = False
    controller.refresh()
    assert not controller.active and watchers[-1].closed
    watchers[-1].text_captured.emit('關閉後不應保存')
    assert saved == ['啟用後的文字']
    cfg.options['auto_save_text'] = True
    controller.refresh()
    assert controller.active and len(watchers) == 2
    watchers[-1].text_captured.emit('重新收錄')
    cfg.options['monitor_clipboard'] = False
    controller.refresh()
    assert saved == ['啟用後的文字', '重新收錄'] and not controller.active


def test_refresh_has_one_listener_and_manual_pause_survives_settings_changes(monitored):
    controller, cfg, saved, watchers = monitored(monitor_clipboard=True)
    for _ in range(10):
        controller.refresh()
    assert len(watchers) == 1
    controller.pause(True)
    assert controller.is_paused and not controller.active and watchers[0].closed
    cfg.options['monitor_clipboard'] = False
    controller.refresh()
    cfg.options['monitor_clipboard'] = True
    controller.refresh()
    assert not controller.active and len(watchers) == 1
    controller.pause(False)
    assert not controller.is_paused and controller.active and len(watchers) == 2
    watchers[-1].text_captured.emit('只收一次')
    assert saved == ['只收一次']


@pytest.mark.parametrize('choice', ['monitor_clipboard', 'auto_save_text'])
def test_callback_rechecks_settings_even_before_refresh_notification(monitored, choice):
    controller, cfg, saved, watchers = monitored(monitor_clipboard=True)
    cfg.options[choice] = False
    watchers[-1].text_captured.emit('設定已關閉')
    assert not saved


def test_stale_callback_cannot_publish_after_off_on_or_shutdown(monitored):
    controller, cfg, saved, watchers = monitored(monitor_clipboard=True)
    stale = controller._text_handler  # Simulate a Qt callback queued before disconnect.
    cfg.options['monitor_clipboard'] = False
    controller.refresh()
    cfg.options['monitor_clipboard'] = True
    controller.refresh()
    stale('前一代的文字')
    current = controller._text_handler
    current('目前的文字')
    controller.stop()
    current('結束後的文字')
    controller.refresh()
    controller.pause(False)
    assert saved == ['目前的文字'] and not controller.active
    assert all(watcher.closed for watcher in watchers)


def test_ignore_self_change_rebuilds_listener_and_revokes_old_callbacks(monitored):
    controller, cfg, saved, watchers = monitored(monitor_clipboard=True)
    stale = controller._text_handler
    cfg.options['ignore_self'] = False
    controller.refresh()
    assert watchers[0].closed and not watchers[1].ignore_self
    stale('舊設定回呼')
    watchers[-1].text_captured.emit('使用新設定')
    assert saved == ['使用新設定']


def test_length_limit_applies_at_each_delivery_without_recreating_listener(monitored):
    controller, cfg, saved, watchers = monitored(monitor_clipboard=True, max_text_length=3)
    watchers[0].text_captured.emit('1234')
    watchers[0].text_captured.emit('123')
    cfg.options['max_text_length'] = 1
    watchers[0].text_captured.emit('12')
    watchers[0].text_captured.emit('1')
    watchers[0].text_captured.emit('')
    assert saved == ['123', '1'] and len(watchers) == 1


def test_real_clipboard_is_not_replayed_or_modified_when_toggling(qapp):
    qapp.clipboard().setText('啟用前已存在')
    cfg, saved = Config(), []
    controller = ClipboardMonitoringController(cfg, saved.append)
    try:
        cfg.options['monitor_clipboard'] = True
        controller.refresh()
        qapp.processEvents()
        assert not saved and qapp.clipboard().text() == '啟用前已存在'
        qapp.clipboard().setText('新的外部文字')
        qapp.processEvents()
        assert saved == ['新的外部文字']
        cfg.options['monitor_clipboard'] = False
        controller.refresh()
        qapp.clipboard().setText('停用後文字')
        qapp.processEvents()
        assert saved == ['新的外部文字']
        assert qapp.clipboard().text() == '停用後文字'
    finally:
        controller.stop()


def test_current_self_owned_mime_is_ignored_after_live_enable(qapp):
    from src.clipboard.writer import copy_text
    saved = []
    controller = ClipboardMonitoringController(Config(monitor_clipboard=True), saved.append)
    try:
        copy_text('本工具自己的文字')
        qapp.processEvents()
        assert not saved
        qapp.clipboard().setText('其他程式文字')
        qapp.processEvents()
        assert saved == ['其他程式文字']
    finally:
        controller.stop()


def test_windows_clipboard_sequence_discards_notification_from_before_enable(qapp, monkeypatch):
    from PySide6.QtGui import QClipboard
    from src.clipboard.watcher import ClipboardWatcher
    sequence = [10]
    monkeypatch.setattr('src.clipboard.watcher._clipboard_sequence', lambda: sequence[0])
    qapp.clipboard().setText('先前的內容')
    watcher, saved = ClipboardWatcher(), []
    watcher.text_captured.connect(saved.append)
    try:
        watcher._on_changed(QClipboard.Mode.Clipboard)
        assert not saved
        sequence[0] = 11
        watcher._on_changed(QClipboard.Mode.Clipboard)
        assert saved == ['先前的內容']
        watcher._on_changed(QClipboard.Mode.Clipboard)
        assert saved == ['先前的內容']
        watcher.close()
        sequence[0] = 12
        watcher._on_changed(QClipboard.Mode.Clipboard)
        assert saved == ['先前的內容']
    finally:
        watcher.close()
