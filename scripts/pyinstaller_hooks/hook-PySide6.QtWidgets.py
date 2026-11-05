"""Collect QtWidgets dependencies under the application's shared offline policy."""
from scripts.pyinstaller_hooks.offline_qt import collect_offline_qt

hiddenimports, binaries, datas = collect_offline_qt(__file__)
