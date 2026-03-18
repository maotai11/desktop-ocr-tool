# -*- coding: utf-8 -*-
import sys
import os

# Ensure project root in sys.path
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src.core.logger import setup_logger
setup_logger()

from src.app import main

if __name__ == '__main__':
    sys.exit(main())
