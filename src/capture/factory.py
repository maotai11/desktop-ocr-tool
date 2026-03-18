# -*- coding: utf-8 -*-
from .backend_mss import MssBackend

_backend: "MssBackend | None" = None


def get_capture_backend() -> MssBackend:
    global _backend
    if _backend is None:
        _backend = MssBackend()
    return _backend
