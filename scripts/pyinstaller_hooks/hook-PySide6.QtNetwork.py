"""Keep Qt's module dependencies without collecting unused TLS backends.

The desktop OCR runtime has no Qt network client. The upstream extra collector
can find OpenSSL DLLs from unrelated tools on a build machine's PATH. Do not
promote those binaries into this offline deliverable. CPython's own SSL runtime
is collected independently and remains subject to the native payload audit.
"""
from scripts.pyinstaller_hooks.offline_qt import collect_offline_qt

hiddenimports, binaries, datas = collect_offline_qt(__file__)
