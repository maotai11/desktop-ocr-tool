# -*- coding: utf-8 -*-
import sys
import os

# Frozen EXE: patch PaddleX dependency checker BEFORE any paddleocr/paddlex imports.
# importlib.metadata lacks .dist-info in the bundle → is_extra_available raises
# DependencyError during pipeline creation. Patching here ensures all downstream
# module-level `from paddlex.utils.deps import ...` bindings get the patched versions.
if getattr(sys, 'frozen', False):
    try:
        import paddlex.utils.deps as _pdx_deps
        _pdx_deps.is_dep_available = lambda *a, **kw: True
        _pdx_deps.is_extra_available = lambda *a, **kw: True
        if hasattr(_pdx_deps, 'ensure_dep'):
            _pdx_deps.ensure_dep = lambda *a, **kw: None
        if hasattr(_pdx_deps, 'check_deps'):
            _pdx_deps.check_deps = lambda *a, **kw: None
    except Exception as _e:
        import logging as _logging
        _logging.getLogger('paddlex_patch').warning(f'PaddleX deps patch failed: {_e}')

# 開發模式：確保 project root 在 sys.path（frozen EXE 由 PyInstaller 處理，不需要）
if not getattr(sys, 'frozen', False):
    _ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    if _ROOT not in sys.path:
        sys.path.insert(0, _ROOT)

from src.core.logger import setup_logger
setup_logger()

from src.app import main

if __name__ == '__main__':
    sys.exit(main())
