import os

APP_NAME = "DesktopOCRTool"
APP_DISPLAY_NAME = "桌面 OCR 擷取工具"
APP_VERSION = os.environ.get("DESKTOP_OCR_VERSION", "1.6.1")


def release_dir_name() -> str:
    return f"release-v{APP_VERSION}"


def release_bundle_name() -> str:
    return f"{APP_NAME}-v{APP_VERSION}"


def versioned_exe_name() -> str:
    return f"{release_bundle_name()}.exe"
