"""
Build the Windows release bundle with PyInstaller.

Usage:
    python scripts/build.py
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.version import (
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
    from src.ocr.model_validator import verified_model_manifest
    verified_model_manifest(root)


def source_manifest(root: Path):
    """Bind the candidate to both Git HEAD and the actual dirty working tree."""
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=root, timeout=30)
    commit = git('rev-parse', 'HEAD').decode().strip()
    tree = git('rev-parse', 'HEAD^{tree}').decode().strip()
    names = sorted(set(git('ls-files', '-z', '--cached', '--others', '--exclude-standard')
                       .decode('utf-8').split('\0')) - {''})
    files = {}
    for name in names:
        path = root / name
        if path.is_symlink():
            files[name] = {'symlink': os.readlink(path)}
        elif path.is_file():
            files[name] = {'sha256': sha256_file(path)}
        else:
            files[name] = {'missing': True}
    snapshot = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(',', ':'))
                              .encode('utf-8')).hexdigest()
    return {'commit': commit, 'head_tree': tree, 'working_tree_sha256': snapshot,
            'dirty': bool(git('status', '--porcelain').strip()), 'files': files}


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
    # An environment-controlled version must never become an output path escape.
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?"
                        r"(?:\+[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?", APP_VERSION):
        raise ValueError(f"Invalid release version: {APP_VERSION!r}")
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
        "version_hook": build_dir / "runtime_version.py",
        "validation_dir": artifacts_dir / "candidate-validation",
    }


def reset_output_dirs(paths: dict[str, Path]) -> None:
    # Refuse redirected output trees before deleting or creating anything.
    for key in ("artifacts_dir", "dist_dir", "build_dir", "release_dir", "validation_dir"):
        path = paths[key]
        if path.is_symlink():
            raise RuntimeError(f"Refusing symlink build output: {path}")
    for path in (paths['zip_path'], paths['zip_path'].with_suffix('.zip.sha256')):
        if path.is_symlink():
            raise RuntimeError(f"Refusing symlink build output: {path}")
    paths["artifacts_dir"].mkdir(exist_ok=True)

    for key in ("dist_dir", "build_dir", "release_dir", "validation_dir"):
        directory = paths[key]
        if directory.exists():
            shutil.rmtree(directory)
        directory.mkdir(parents=True, exist_ok=True)

    if paths["zip_path"].exists():
        paths["zip_path"].unlink()
    paths['zip_path'].with_suffix('.zip.sha256').unlink(missing_ok=True)


def write_version_hook(paths):
    # Environment overrides at build time must survive delivery to a machine
    # with no such variable, and cannot rename an already-built executable.
    paths['version_hook'].write_text(
        'import os\nos.environ["DESKTOP_OCR_VERSION"] = ' + json.dumps(APP_VERSION) + '\n',
        encoding='utf-8')


def pyinstaller_command(root: Path, paths: dict[str, Path], onefile=True):
    cmd = [sys.executable, '-m', 'PyInstaller', '--clean', '--noconfirm',
           '--onefile' if onefile else '--onedir', '--windowed', '--name', APP_NAME,
           '--paths', str(root), '--runtime-hook', str(root/'scripts/runtime_offline.py'),
           '--runtime-hook', str(paths['version_hook']),
           '--add-data', f"{root/'models'}{os.pathsep}models",
           '--collect-data', 'rapidocr_onnxruntime', '--collect-all', 'onnxruntime',
           '--collect-all', 'zhconv', '--collect-all', 'cv2',
           '--hidden-import', 'PySide6.QtCore', '--hidden-import', 'PySide6.QtGui',
           '--hidden-import', 'PySide6.QtWidgets', '--hidden-import', 'mss.windows',
           '--distpath', str(paths['dist_dir']), '--workpath', str(paths['build_dir']),
           '--specpath', str(paths['artifacts_dir'])]
    for name in ('paddle', 'paddleocr', 'paddlex', 'torch', 'torchvision', 'cnocr',
                 'cnstd', 'pytest', 'IPython', 'matplotlib', 'tkinter'):
        cmd += ['--exclude-module', name]
    cmd.append(str(root/'src/main.py'))
    return cmd


def build_with_pyinstaller(root: Path, paths: dict[str, Path], onefile=True) -> None:
    if sys.platform != 'win32':
        raise RuntimeError('Windows executable must be built on Windows; cross-build is unsupported')
    if not onefile:
        raise RuntimeError('This release driver packages one-file builds only')
    write_version_hook(paths)
    subprocess.run(pyinstaller_command(root, paths, onefile), cwd=root, check=True, timeout=900)


def _run_probe(executable: Path, mode: str, report_path: Path, timeout=180):
    env = os.environ.copy()
    env['ORT_DISABLE_TELEMETRY'] = '1'
    process = subprocess.Popen([str(executable), mode, str(report_path)], env=env, cwd=ROOT)
    try:
        code = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        if sys.platform == 'win32':
            # One-file's bootloader has a child; kill only this launched tree.
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                           check=False, capture_output=True, timeout=15)
        else:
            process.kill()
        process.wait(timeout=15)
        raise RuntimeError(f'{mode} exceeded {timeout}s; candidate rejected') from exc
    if code != 0:
        raise RuntimeError(f'{mode} failed with exit code {code}; see {report_path}')
    return json.loads(report_path.read_text(encoding='utf-8'))


def validate_candidate(paths):
    """Bounded integration gate on the exact EXE, never a clean-host claim."""
    reports = {}
    expected_hash = sha256_file(paths['out_exe'])
    models = json.loads((ROOT / 'models/models.lock.json').read_text(encoding='utf-8'))
    for mode, name, probe in (('--self-test', 'frozen-selftest.json', 'ocr_database'),
                              ('--smoke-app', 'frozen-app-smoke.json', 'application_lifecycle')):
        report_path = paths['validation_dir'] / name
        report = _run_probe(paths['out_exe'], mode, report_path)
        if (report.get('schema') != 2 or report.get('probe') != probe
                or report.get('passed') is not True or report.get('frozen') is not True
                or report.get('platform') != 'win32' or report.get('qt_platform') != 'windows'
                or report.get('version') != APP_VERSION
                or report.get('executable_sha256') != expected_hash
                or report.get('clean_machine_verified') is not False):
            raise RuntimeError(f'Invalid candidate report: {report_path}')
        if mode == '--self-test':
            if (report.get('telemetry_env') != '1' or report.get('database_integrity') != 'ok'
                    or set(report.get('models', {})) != set(models)
                    or any(report['models'][key].get('sha256') != info['sha256']
                           for key, info in models.items())
                    or report.get('checks') != {"qt": True, "models": True, "ocr": True, "database": True}):
                raise RuntimeError(f'Incomplete OCR/model gate: {report_path}')
        elif (report.get('phase_a') is not True or report.get('engine_ready') is not True
              or report.get('shutdown_clean') is not True
              or report.get('database_integrity') != 'ok'
              or report.get('threads_stopped') != {"capture": True, "ocr": True, "database": True, "hotkeys": True}):
            raise RuntimeError(f'Incomplete lifecycle gate: {report_path}')
        reports[name] = {'sha256': sha256_file(report_path), 'status': 'PASSED'}
    if sha256_file(paths['out_exe']) != expected_hash:
        raise RuntimeError('Executable changed while validating the candidate')
    return reports


def write_release_readme(path: Path) -> None:
    with path.open("w", encoding="utf-8") as handle:
        handle.write(f"{APP_DISPLAY_NAME} v{APP_VERSION}\n")
        handle.write("=" * 40 + "\n\n")
        handle.write("執行方式:\n")
        handle.write(f"  直接雙擊 {versioned_exe_name()}\n\n")
        handle.write("注意事項:\n")
        handle.write("  - 設定、資料與日誌會寫到 release 目錄旁的 config/data/logs。\n")
        handle.write("  - OCR 模型已內含；不需要 Python、pip 或首次下載。\n")
        handle.write("  - 首次使用剪貼簿監聽預設關閉；歷史及截圖為明文，無自動到期刪除。\n")
        handle.write("  - 此檔案是候選建置；是否可交付須核對 VALIDATION_STATUS.md 與對應 SHA256。\n\n")
        handle.write("快捷鍵:\n")
        handle.write("  Ctrl+Shift+O  框選 OCR\n")
        handle.write("  Ctrl+Shift+S  框選截圖\n")
        handle.write("  Ctrl+Shift+F  全螢幕擷取\n")
        handle.write("  Ctrl+Shift+Space  顯示或隱藏浮動視窗\n")
        handle.write("  Ctrl+Shift+M  開啟主控台\n")


def package_release(paths: dict[str, Path], validation_reports=None, source=None) -> None:
    shutil.copy2(paths["out_exe"], paths["release_exe"])
    write_release_readme(paths["release_dir"] / "README.txt")
    from importlib import metadata
    source = source or source_manifest(ROOT)
    source_path = paths['release_dir'] / 'SOURCE_MANIFEST.json'
    source_path.write_text(json.dumps(source, indent=2, ensure_ascii=False), encoding='utf-8')
    manifest = {"schema": 2, "version": APP_VERSION, "python": sys.version, "platform": sys.platform,
                    "executable_name": paths['release_exe'].name,
                    "executable_sha256": sha256_file(paths['release_exe']),
                    "models": json.loads((ROOT/'models/models.lock.json').read_text()),
                    "build_environment_dependencies": {d.metadata['Name']:d.version for d in metadata.distributions()},
                    "source": {key: value for key, value in source.items() if key != 'files'},
                    "source_manifest_sha256": sha256_file(source_path),
                    "candidate_probes": validation_reports or 'NOT_RUN',
                    "clean_machine_gate": 'NOT_RUN', "mixed_dpi_gate": 'NOT_RUN',
                    "dependency_license_review": 'NOT_RUN', "dependency_hash_lock": 'NOT_RUN'}
    inventory = paths['artifacts_dir'] / 'dependency-inventory.json'
    if inventory.exists():
        shutil.copy2(inventory, paths['release_dir'] / inventory.name)
        manifest['dependency_inventory'] = {'status': 'RECORDED_NOT_A_TRUSTED_LOCK',
                                            'sha256': sha256_file(inventory)}
    (paths['release_dir']/'BUILD_MANIFEST.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    for name in ('README.md', 'PACKAGING_NOTES.md', 'SMOKE_TEST_CHECKLIST.md'):
        shutil.copy2(ROOT/name, paths['release_dir']/name)
    if (ROOT/'docs/VALIDATION_STATUS.md').exists():
        shutil.copy2(ROOT/'docs/VALIDATION_STATUS.md', paths['release_dir']/'VALIDATION_STATUS.md')
    if validation_reports:
        shutil.copytree(paths['validation_dir'], paths['release_dir'] / 'candidate-validation')
    shutil.copy2(ROOT / 'scripts/validate_windows_bundle.ps1',
                 paths['release_dir'] / 'Validate-Candidate.ps1')
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
        # Refuse cross-builds before touching previous artifacts.
        if sys.platform != 'win32':
            raise RuntimeError('Windows executable must be built on Windows; cross-build is unsupported')
        verify_models(ROOT)
        subprocess.run([sys.executable, '-m', 'pip', 'check'], cwd=ROOT, check=True, timeout=60)
        source = source_manifest(ROOT)
        paths = resolve_release_paths(ROOT)
        assert_output_writable(paths["out_exe"])
        reset_output_dirs(paths)
        build_with_pyinstaller(ROOT, paths)

        exe_size_mb = paths["out_exe"].stat().st_size / 1024 / 1024
        print(f"\nexe ready: {paths['out_exe']} ({exe_size_mb:.1f} MB)")

        validation_reports = validate_candidate(paths)
        if source_manifest(ROOT) != source:
            raise RuntimeError('Source tree changed during build; candidate rejected')
        package_release(paths, validation_reports, source)
        zip_size_mb = paths["zip_path"].stat().st_size / 1024 / 1024

        print(f"release dir: {paths['release_dir']}")
        print(f"release exe: {paths['release_exe'].name}")
        print(f"release zip: {paths['zip_path']} ({zip_size_mb:.1f} MB)")
        (paths['zip_path'].with_suffix('.zip.sha256')).write_text(
            f"{sha256_file(paths['zip_path'])}  {paths['zip_path'].name}\n",encoding='ascii')
        return 0
    except subprocess.CalledProcessError as exc:
        print(f"build failed with exit code {exc.returncode}")
        return exc.returncode
    except Exception as exc:  # noqa: BLE001 - unexpected build failures reject delivery
        print(f"build failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
