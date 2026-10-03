# -*- coding: utf-8 -*-
"""Application-wide offline policy, before any OCR dependency is imported."""
import os

os.environ['ORT_DISABLE_TELEMETRY'] = '1'
