import os

APP_NAME = "InkPick"
# Persistent paths, mutex/MIME/registry keys retain the old compatibility ID.
APP_DISPLAY_NAME = "InkPick 拾字筆"
APP_VERSION = os.environ.get("DESKTOP_OCR_VERSION", "1.8.0-rc.1")


def release_dir_name() -> str:
    return f"release-v{APP_VERSION}"


def release_bundle_name() -> str:
    return f"{APP_NAME}-v{APP_VERSION}"


def versioned_exe_name() -> str:
    return f"{release_bundle_name()}.exe"
