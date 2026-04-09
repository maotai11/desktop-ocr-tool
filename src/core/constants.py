# -*- coding: utf-8 -*-
from .version import APP_DISPLAY_NAME, APP_NAME as APP_NAME_EN, APP_VERSION

APP_NAME = APP_DISPLAY_NAME
APP_MUTEX_NAME = "Global\\DesktopOCRToolInstance"
APP_REGISTRY_KEY = "DesktopOCRTool"

OCR_STATUS_NONE = "none"
OCR_STATUS_PENDING = "pending"
OCR_STATUS_PROCESSING = "processing"
OCR_STATUS_DONE = "done"
OCR_STATUS_NEEDS_REVIEW = "needs_review"
OCR_STATUS_CONFIRMED = "confirmed"
OCR_STATUS_FAILED = "failed"

SOURCE_MODE_REGION_OCR = "region_ocr"
SOURCE_MODE_REGION_IMAGE = "region_image"
SOURCE_MODE_FULLSCREEN = "fullscreen"
SOURCE_MODE_CLIPBOARD_TEXT = "clipboard_text"
SOURCE_MODE_CLIPBOARD_IMAGE = "clipboard_image"
SOURCE_MODE_IMPORT = "import"

ITEM_TYPE_TEXT = "text"
ITEM_TYPE_IMAGE = "image"
ITEM_TYPE_MIXED = "mixed"

SCHEMA_VERSION = 4
DEDUP_SECONDS = 60
CUSTOM_MIME_TYPE = "application/x-desktopocrtool-id"
