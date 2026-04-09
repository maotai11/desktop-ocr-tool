# -*- coding: utf-8 -*-
"""
Build the Windows release bundle with PyInstaller.

Usage:
    python scripts/build.py
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.version import (  # noqa: E402
    APP_DISPLAY_NAME,
    APP_NAME,
    APP_VERSION,
    release_bundle_name,
    release_dir_name,
    versioned_exe_name,
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_models(root: Path) -> None:
    lock_path = root / "models" / "models.lock.json"
    if not lock_path.exists():
        print("warning: models.lock.json not found; skipping model hash verification")
        return

    with lock_path.open(encoding="utf-8") as handle:
        lock = json.load(handle)

    print("verifying model hashes...")
    for info in lock.values():
        model_path = root / info["path"]
        if not model_path.exists():
            raise FileNotFoundError(f"missing model file: {info['path']}")
        expected = info.get("sha256")
        if expected and sha256_file(model_path) != expected:
            raise RuntimeError(f"sha256 mismatch: {info['path']}")
        print(f"  ok {info['path']}")


def assert_output_writable(path: Path) -> None:
    if not path.exists():
        return
    probe = path.with_suffix(path.suffix + ".chk")
    try:
        path.rename(probe)
        probe.rename(path)
    except OSError as exc:
        raise RuntimeError(
            f"{path} is locked; close the running app before building"
        ) from exc


def resolve_release_paths(root: Path) -> dict[str, Path]:
    artifacts_dir = root / "artifacts"
    dist_dir = artifacts_dir / "dist"
    build_dir = artifacts_dir / "build"
    release_dir = artifacts_dir / release_dir_name()
    zip_path = artifacts_dir / f"{release_bundle_name()}.zip"
    out_exe = dist_dir / f"{APP_NAME}.exe"
    release_exe = release_dir / versioned_exe_name()
    return {
        "artifacts_dir": artifacts_dir,
        "dist_dir": dist_dir,
        "build_dir": build_dir,
        "release_dir": release_dir,
        "zip_path": zip_path,
        "out_exe": out_exe,
        "release_exe": release_exe,
    }


def reset_output_dirs(paths: dict[str, Path]) -> None:
    paths["artifacts_dir"].mkdir(exist_ok=True)

    for key in ("dist_dir", "build_dir", "release_dir"):
        directory = paths[key]
        if directory.exists():
            shutil.rmtree(directory)
        directory.mkdir(parents=True, exist_ok=True)

    if paths["zip_path"].exists():
        paths["zip_path"].unlink()


def build_with_pyinstaller(root: Path, paths: dict[str, Path]) -> None:
    sep = os.pathsep
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--clean",
        "--onefile",
        "--windowed",
        "--name",
        APP_NAME,
        "--add-data",
        f"{root / 'config' / 'default_settings.json'}{sep}config",
        "--paths",
        str(root),
        "--hidden-import",
        "src",
        "--hidden-import",
        "src.app",
        "--hidden-import",
        "src.core",
        "--hidden-import",
        "src.data",
        "--hidden-import",
        "src.ui",
        "--hidden-import",
        "src.workers",
        "--hidden-import",
        "src.ocr",
        "--hidden-import",
        "src.capture",
        "--hidden-import",
        "src.clipboard",
        "--collect-all",
        "numpy",
        "--collect-data",
        "rapidocr_onnxruntime",
        "--hidden-import",
        "rapidocr_onnxruntime",
        "--hidden-import",
        "onnxruntime",
        "--hidden-import",
        "onnxruntime.capi",
        "--hidden-import",
        "onnxruntime.capi._pybind_state",
        "--collect-data",
        "onnxruntime",
        "--collect-all",
        "cv2",
        "--hidden-import",
        "PIL",
        "--hidden-import",
        "PIL.Image",
        "--hidden-import",
        "PIL.ImageOps",
        "--hidden-import",
        "PIL.ImageFilter",
        "--hidden-import",
        "mss",
        "--hidden-import",
        "mss.windows",
        "--collect-all",
        "zhconv",
        "--collect-data",
        "paddlex",
        "--collect-binaries",
        "paddle",
        "--hidden-import",
        "paddleocr",
        "--hidden-import",
        "paddle",
        "--hidden-import",
        "paddlex",
        "--exclude-module",
        "torch",
        "--exclude-module",
        "torchvision",
        "--exclude-module",
        "torchaudio",
        "--exclude-module",
        "cnocr",
        "--exclude-module",
        "cnstd",
        "--exclude-module",
        "pytorch_lightning",
        "--exclude-module",
        "torchmetrics",
        "--exclude-module",
        "tensorflow",
        "--exclude-module",
        "paddle",
        "--exclude-module",
        "paddleocr",
        "--exclude-module",
        "paddlex",
        "--exclude-module",
        "pytest",
        "--exclude-module",
        "IPython",
        "--exclude-module",
        "matplotlib",
        "--exclude-module",
        "tkinter",
        "--exclude-module",
        "yt_dlp",
        "--exclude-module",
        "mutagen",
        "--exclude-module",
        "curl_cffi",
        "--hidden-import",
        "PySide6.QtCore",
        "--hidden-import",
        "PySide6.QtGui",
        "--hidden-import",
        "PySide6.QtWidgets",
        "--distpath",
        str(paths["dist_dir"]),
        "--workpath",
        str(paths["build_dir"]),
        "--specpath",
        str(paths["artifacts_dir"]),
        str(root / "src" / "main.py"),
    ]

    print("\nrunning PyInstaller...")
    subprocess.run(cmd, cwd=root, check=True)


def write_release_readme(path: Path) -> None:
    with path.open("w", encoding="utf-8") as handle:
        handle.write(f"{APP_DISPLAY_NAME} v{APP_VERSION}\n")
        handle.write("=" * 40 + "\n\n")
        handle.write("執行方式:\n")
        handle.write(f"  直接雙擊 {versioned_exe_name()}\n\n")
        handle.write("注意事項:\n")
        handle.write("  - 設定、資料與日誌會寫到 release 目錄旁的 config/data/logs。\n")
        handle.write("  - 第一次啟動需要幾秒鐘載入 OCR 模型。\n\n")
        handle.write("快捷鍵:\n")
        handle.write("  Ctrl+Shift+O  框選 OCR\n")
        handle.write("  Ctrl+Shift+S  框選截圖\n")
        handle.write("  Ctrl+Shift+F  全螢幕擷取\n")
        handle.write("  Ctrl+Shift+Space  顯示或隱藏浮動視窗\n")
        handle.write("  Ctrl+Shift+M  開啟主控台\n")


def package_release(paths: dict[str, Path]) -> None:
    shutil.copy2(paths["out_exe"], paths["release_exe"])
    write_release_readme(paths["release_dir"] / "README.txt")
    shutil.make_archive(
        str(paths["zip_path"]).removesuffix(".zip"),
        "zip",
        str(paths["artifacts_dir"]),
        paths["release_dir"].name,
    )


def main() -> int:
    if sys.version_info < (3, 11):
        print(f"error: Python 3.11+ required, got {sys.version}")
        return 1

    print(f"Python {sys.version_info.major}.{sys.version_info.minor} OK")
    print(f"building {APP_NAME} v{APP_VERSION}")

    try:
        verify_models(ROOT)
        paths = resolve_release_paths(ROOT)
        assert_output_writable(paths["out_exe"])
        reset_output_dirs(paths)
        build_with_pyinstaller(ROOT, paths)

        exe_size_mb = paths["out_exe"].stat().st_size / 1024 / 1024
        print(f"\nexe ready: {paths['out_exe']} ({exe_size_mb:.1f} MB)")

        package_release(paths)
        zip_size_mb = paths["zip_path"].stat().st_size / 1024 / 1024

        print(f"release dir: {paths['release_dir']}")
        print(f"release exe: {paths['release_exe'].name}")
        print(f"release zip: {paths['zip_path']} ({zip_size_mb:.1f} MB)")
        return 0
    except subprocess.CalledProcessError as exc:
        print(f"build failed with exit code {exc.returncode}")
        return exc.returncode
    except Exception as exc:
        print(f"build failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
