# -*- coding: utf-8 -*-
import sys
import os

# 開發模式：確保 project root 在 sys.path（frozen EXE 由 PyInstaller 處理，不需要）
if not getattr(sys, 'frozen', False):
    _ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    if _ROOT not in sys.path:
        sys.path.insert(0, _ROOT)

if __name__ == '__main__' and len(sys.argv) == 3 and sys.argv[1] == '--self-test':
    from src.self_test import run_self_test
    sys.exit(run_self_test(sys.argv[2]))

from src.app import main

if __name__ == '__main__':
    sys.exit(main())
