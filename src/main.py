# -*- coding: utf-8 -*-
import sys
import os

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
