"""Keep Qt's module dependencies without collecting unused TLS backends.

The desktop OCR runtime has no Qt network client. The upstream extra collector
can find OpenSSL DLLs from unrelated tools on a build machine's PATH. Do not
promote those binaries into this offline deliverable. CPython's own SSL runtime
is collected independently and remains subject to the native payload audit.
"""
from PyInstaller.utils.hooks.qt import add_qt6_dependencies


def _tls_artifact(entry):
    source, destination = (str(value).replace('\\', '/').lower() for value in entry[:2])
    names = (source.rsplit('/', 1)[-1], destination.rsplit('/', 1)[-1])
    return (any('/plugins/tls/' in value or value.endswith('/plugins/tls')
                for value in (source, destination))
            or any(name in ('libcrypto-3.dll', 'libssl-3.dll') for name in names))


hiddenimports, binaries, datas = add_qt6_dependencies(__file__)
binaries = [entry for entry in binaries if not _tls_artifact(entry)]
datas = [entry for entry in datas if not _tls_artifact(entry)]
# Deliberately omit collect_qtnetwork_files(): no arbitrary PATH OpenSSL input.
