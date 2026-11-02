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


def bundled_model_profiles(root: Path):
    from scripts.verify_candidate_bundle import check_profile_manifests, portable_models
    from src.ocr.model_validator import MODEL_PROFILES, verified_model_manifest
    profiles = {profile: portable_models(verified_model_manifest(root, profile=profile))
                for profile in MODEL_PROFILES}
    check_profile_manifests(profiles)
    return profiles


def bundled_validation_fixtures(root: Path):
    from scripts.verify_candidate_bundle import check_validation_fixtures
    directory = root / 'models/validation'
    fixtures = json.loads((directory / 'fixtures.json').read_text(encoding='utf-8'))
    check_validation_fixtures(fixtures)
    for case in fixtures['cases']:
        if sha256_file(directory / case['file']) != case['image_sha256']:
            raise ValueError(f'Literal OCR fixture hash mismatch: {case["id"]}')
    return fixtures


def verify_models(root: Path) -> None:
    # Every selectable profile must already be available and verified offline.
    bundled_model_profiles(root)
    bundled_validation_fixtures(root)


def source_manifest(root: Path):
    """Bind the candidate to both Git HEAD and the actual dirty working tree."""
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=root, timeout=30)
    commit = git('rev-parse', 'HEAD').decode().strip()
    tree = git('rev-parse', 'HEAD^{tree}').decode().strip()
    names = sorted(set(git('ls-files', '-z', '--cached', '--others', '--exclude-standard')
                       .decode('utf-8').split('\0')) - {''})
    tracked = set(git('ls-files', '-z', '--cached').decode('utf-8').split('\0'))
    generated = {'test-results.xml', 'source-selftest.json', 'source-app-smoke.json', 'source-handwriting.json', 'source-handwriting-image.json'}
    excluded = set(names) & generated - tracked
    names = [name for name in names if name not in excluded]
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
    status = git('status', '--porcelain').decode('utf-8').splitlines()
    dirty = any(line not in {'?? ' + name for name in excluded} for line in status if line.strip())
    return {'commit': commit, 'head_tree': tree, 'working_tree_sha256': snapshot,
            'dirty': dirty, 'excluded_generated_validation_files': sorted(excluded), 'files': files}


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
        "version_info": build_dir / "windows_version_info.txt",
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
    numeric = tuple(map(int, re.match(r'(\d+)\.(\d+)\.(\d+)', APP_VERSION).groups())) + (0,)
    paths['version_info'].write_text(
        'VSVersionInfo(ffi=FixedFileInfo(filevers=' + repr(numeric) + ', prodvers=' + repr(numeric) +
        ', mask=0x3f, flags=0, OS=0x40004, fileType=1, subtype=0, date=(0,0)), kids=['
        'StringFileInfo([StringTable("040904B0", [' +
        ','.join('StringStruct(' + repr(key) + ',' + repr(value) + ')' for key, value in (
            ('FileDescription', APP_DISPLAY_NAME), ('ProductName', APP_NAME),
            ('InternalName', APP_NAME), ('OriginalFilename', APP_NAME + '.exe'),
            ('FileVersion', APP_VERSION), ('ProductVersion', APP_VERSION))) +
        '])]), VarFileInfo([VarStruct("Translation", [1033, 1200])])])\n', encoding='utf-8')


def bundled_data_files(root: Path):
    """Only ship locked profile assets and validation images, never baseline models."""
    from importlib.util import find_spec

    from src.ocr.model_validator import MODEL_PROFILES
    profiles = bundled_model_profiles(root)
    fixtures = bundled_validation_fixtures(root)
    relative = {'models/' + name for name in MODEL_PROFILES.values()}
    relative.update(info['path'] for models in profiles.values() for info in models.values())
    relative.add('models/validation/fixtures.json')
    relative.update('models/validation/' + case['file'] for case in fixtures['cases'])
    files = [(root / name, str(Path(name).parent)) for name in sorted(relative)]
    # RapidOCR reads its YAML before applying our explicit det/rec/cls paths.
    # Its vendor models are intentionally absent: they must never become fallbacks.
    spec = find_spec('rapidocr_onnxruntime')
    if spec is None or not spec.submodule_search_locations:
        raise RuntimeError('The pinned RapidOCR runtime is required to build')
    config = Path(next(iter(spec.submodule_search_locations))) / 'config.yaml'
    if not config.is_file():
        raise RuntimeError('RapidOCR runtime config.yaml is missing')
    files.append((config, 'rapidocr_onnxruntime'))
    return files


def pyinstaller_command(root: Path, paths: dict[str, Path], onefile=True):
    cmd = [sys.executable, '-m', 'PyInstaller', '--clean', '--noconfirm', '--noupx',
           '--onefile' if onefile else '--onedir', '--windowed', '--name', APP_NAME,
           '--paths', str(root), '--runtime-hook', str(root/'scripts/runtime_offline.py'),
           '--icon', str(root/'assets/branding/inkpick.ico'), '--version-file', str(paths['version_info']),
           '--runtime-hook', str(paths['version_hook']),
           '--copy-metadata', 'rapidocr-onnxruntime',
           '--copy-metadata', 'onnxruntime', '--collect-binaries', 'onnxruntime',
           '--collect-all', 'zhconv', '--collect-all', 'cv2',
           '--hidden-import', 'PySide6.QtCore', '--hidden-import', 'PySide6.QtGui',
           '--hidden-import', 'PySide6.QtWidgets', '--hidden-import', 'mss.windows',
           '--distpath', str(paths['dist_dir']), '--workpath', str(paths['build_dir']),
           '--specpath', str(paths['artifacts_dir'])]
    for source, destination in bundled_data_files(root):
        cmd += ['--add-data', f'{source}{os.pathsep}{destination}']
    from scripts.handwriting_bundle import data_files, HELPER_PATH
    for source, destination in data_files(root):
        cmd += ['--add-data', f'{source}{os.pathsep}{destination}']
    cmd += ['--add-binary', f'{root / HELPER_PATH}{os.pathsep}{Path(HELPER_PATH).parent}']
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
    from scripts.handwriting_bundle import identity
    handwriting = identity(root)
    if handwriting['helper'].get('platform') != 'win32':
        raise RuntimeError('A Windows x64 handwriting helper is required')
    subprocess.run(pyinstaller_command(root, paths, onefile), cwd=root, check=True, timeout=900)
    from scripts.verify_candidate_bundle import inspect_frozen_payload
    # Reject accidental vendor/default/baseline ONNX additions before running an EXE.
    inspect_frozen_payload(paths['out_exe'], bundled_model_profiles(root),
                           validation_fixtures=bundled_validation_fixtures(root), handwriting=handwriting)
    from scripts.verify_windows_branding import verify
    verify(paths['out_exe'], root)


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
    from scripts.verify_candidate_bundle import check_model_profiles
    from src.ocr.model_validator import DEFAULT_MODEL_PROFILE
    profiles = bundled_model_profiles(ROOT)
    manifest = {'models': profiles[DEFAULT_MODEL_PROFILE], 'model_profiles': profiles,
                'default_model_profile': DEFAULT_MODEL_PROFILE,
                'ocr_validation_fixtures': bundled_validation_fixtures(ROOT)}
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
                    or report.get('checks') != {"qt": True, "models": True, "ocr": True, "database": True}):
                raise RuntimeError(f'Incomplete OCR/model gate: {report_path}')
            try:
                check_model_profiles(report, manifest)
            except ValueError as exc:
                raise RuntimeError(f'Incomplete OCR/model-profile gate: {report_path}: {exc}') from exc
        elif (report.get('phase_a') is not True or report.get('engine_ready') is not True
              or report.get('shutdown_clean') is not True
              or report.get('database_integrity') != 'ok'
              or report.get('threads_stopped') != {"capture": True, "ocr": True, "database": True, "hotkeys": True}):
            raise RuntimeError(f'Incomplete lifecycle gate: {report_path}')
        reports[name] = {'sha256': sha256_file(report_path), 'status': 'PASSED'}
    if sha256_file(paths['out_exe']) != expected_hash:
        raise RuntimeError('Executable changed while validating the candidate')
    from scripts.handwriting_bundle import identity
    from scripts.verify_candidate_bundle import check_handwriting_probe
    handwriting = identity(ROOT)
    path = paths['validation_dir'] / 'frozen-handwriting.json'
    report = _run_probe(paths['out_exe'], '--self-test-handwriting', path)
    try:
        check_handwriting_probe(report, {'version': APP_VERSION, 'executable_sha256': expected_hash,
                                         'handwriting': handwriting}, True)
    except ValueError as exc:
        raise RuntimeError('Incomplete handwriting candidate gate') from exc
    reports[path.name] = {'sha256': sha256_file(path), 'status': 'PASSED'}
    from scripts.verify_candidate_bundle import check_handwriting_image_probe
    path = paths['validation_dir'] / 'frozen-handwriting-image.json'
    report = _run_probe(paths['out_exe'], '--self-test-handwriting-image', path)
    try:
        check_handwriting_image_probe(report, dict(manifest, executable_sha256=expected_hash), True)
    except ValueError as exc:
        raise RuntimeError('Incomplete handwriting image candidate gate') from exc
    reports[path.name] = {'sha256': sha256_file(path), 'status': 'PASSED'}
    return reports


def write_release_readme(path: Path) -> None:
    path.write_text(f"""{APP_DISPLAY_NAME} v{APP_VERSION}
========================================

啟動
1. Windows 10 1809 以上／Windows 11，64 位元。
2. 將整份 ZIP 解壓到可寫入的資料夾，再雙擊 {versioned_exe_name()}。
3. 已內附 Python 執行環境、Qt、兩組 OCR 模型及手寫引擎；目標電腦不需安裝 Python、pip 或語言套件，首次啟動也不下載模型，可斷網使用。

擷取文字與複製模式
1. 點「擷取文字」或按 Ctrl+Shift+O，再拖曳框選螢幕區域。
2. 程式只辨識框選內容；預設在辨識完成後自動複製全部文字，直接 Ctrl+V 貼上。
3. 若只想複製其中一部分，開啟「設定 → 剪貼簿 → 辨識完成後」，改為「先選文字再複製」並儲存，立即生效。
4. 選字模式的順序是：框選螢幕 → OCR 辨識 → 選擇要複製的文字 → 按「複製」。
5. 可以在原圖框選／點選文字框、勾選段落，或在預覽反白一小段。按住 Ctrl 拖曳可加選；「全選」可複製整份結果。
6. 選字模式不會預先改動剪貼簿，取消或關閉選取視窗也不複製；完整結果仍留在歷史。若未保留原圖，仍可勾選文字。

截圖、手寫與歷史
- 「截圖」會儲存框選圖片；是否同時辨識依設定中的截圖選項。
- 「手寫」一次寫一個字，再點選候選字複製；可展開更多候選或手動啟動影像補辨，不會直接向其他程式輸入。
- 點「歷史紀錄」可搜尋、編輯、釘選、刪除及匯出；浮動窗沒有搜尋框，Ctrl+Shift+Q 會直接開啟歷史搜尋。
- 逗號、撇號與換行的複製清理預設關閉，只改貼上的內容，不改歷史原文。移除換行會接起同一筆的各行。

查看實際留檔與暫存
浮動窗底部「檔案位置」會顯示目前真正使用的路徑，包含：
- 截圖：data/captures；標註附件：data/annotations；縮圖：data/thumbnails。
- 匯出檔案：data/exports；歷史資料：data/app.db 及資料庫暫存檔。
- 設定檔案：config；執行記錄 (Log)：logs。
- 程式暫存：這次單檔 EXE 解壓的 _MEI 資源資料夾，正常結束後由執行器清理，執行中請勿刪除。
一般可攜版的 config/data/logs 在 EXE 旁；使用自訂資料位置或 DESKTOP_OCR_HOME 時，以「檔案位置」顯示為準。歷史與附件不自動到期，Log 每日輪替並最多保留 30 份備份。它們是本機明文檔案。

快捷鍵
Ctrl+Shift+O      框選文字辨識
Ctrl+Shift+S      框選截圖
Ctrl+Shift+F      全螢幕擷取
Ctrl+Shift+Space  顯示／隱藏浮動窗
Ctrl+Shift+M      開啟歷史視窗
Ctrl+Shift+Q      搜尋歷史

從舊版升級
先關閉舊程式並備份原本的 config/data/logs。將新版 EXE 放回同一個可寫入資料夾，保留原有資料；不要用新版 ZIP 覆蓋自己的資料。若換資料夾，請一併移轉原有 config/data，並更新捷徑與自動啟動路徑。

驗證、授權與限制
這是候選測試版。公開 ZIP 包含 EXE、授權與手寫模型來源；請另外保存同版 Release 的離線 README.txt，完整圖文說明在儲存庫 README.md。執行前可從同版 SHA256SUMS 核對 EXE/ZIP。validation-metadata.zip 記錄同一次 Windows 建置與離線探針結果，不代表已在你的乾淨電腦、觸控或混合 DPI 環境實測。
手寫字表含 11,853 字，不含「尞、𠮷、𠀋」；模型筆跡測試不是真人手寫準確率。姓名、金額、日期及帳號請自行核對。
程式本身不傳送 OCR 圖片或手寫筆跡；若開啟 Windows／第三方雲端剪貼簿，複製內容可能由該服務傳送。
本版下載與檔案雜湊： https://github.com/maotai11/desktop-ocr-tool/releases/tag/v{APP_VERSION}
原始碼與最新說明： https://github.com/maotai11/desktop-ocr-tool
""", encoding="utf-8")


def package_release(paths: dict[str, Path], validation_reports=None, source=None) -> None:
    from src.ocr.model_validator import DEFAULT_MODEL_PROFILE
    profiles = bundled_model_profiles(ROOT)
    from scripts.handwriting_bundle import identity
    handwriting = identity(ROOT)
    shutil.copy2(paths["out_exe"], paths["release_exe"])
    write_release_readme(paths["release_dir"] / "README.txt")
    from importlib import metadata
    source = source or source_manifest(ROOT)
    source_path = paths['release_dir'] / 'SOURCE_MANIFEST.json'
    source_path.write_text(json.dumps(source, indent=2, ensure_ascii=False), encoding='utf-8')
    manifest = {"schema": 2, "version": APP_VERSION, "python": sys.version, "platform": sys.platform,
                    "executable_name": paths['release_exe'].name,
                    "executable_sha256": sha256_file(paths['release_exe']),
                    "models": profiles[DEFAULT_MODEL_PROFILE],
                    "default_model_profile": DEFAULT_MODEL_PROFILE,
                    "model_profiles": profiles,
                    "handwriting": handwriting,
                    "ocr_validation_fixtures": bundled_validation_fixtures(ROOT),
                    "build_environment_dependencies": {d.metadata['Name']:d.version for d in metadata.distributions()},
                    "source": {key: value for key, value in source.items() if key != 'files'},
                    "source_manifest_sha256": sha256_file(source_path),
                    "candidate_probes": validation_reports or 'NOT_RUN',
                    "clean_machine_gate": 'NOT_RUN', "mixed_dpi_gate": 'NOT_RUN',
                    "dependency_license_review": 'NOT_RUN', "dependency_hash_lock": 'NOT_RUN'}
    if sys.platform == 'win32':
        from scripts.verify_windows_branding import verify
        manifest['windows_branding'] = verify(paths['release_exe'], ROOT)
    inventory = paths['artifacts_dir'] / 'dependency-inventory.json'
    if inventory.exists():
        shutil.copy2(inventory, paths['release_dir'] / inventory.name)
        manifest['dependency_inventory'] = {'status': 'RECORDED_NOT_A_TRUSTED_LOCK',
                                            'sha256': sha256_file(inventory)}
    (paths['release_dir']/'BUILD_MANIFEST.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    for name in ('README.md', 'PACKAGING_NOTES.md', 'SMOKE_TEST_CHECKLIST.md',
                 'THIRD_PARTY_NOTICES.md', 'LICENSE-APACHE-2.0.txt'):
        shutil.copy2(ROOT/name, paths['release_dir']/name)
    if (ROOT/'docs/VALIDATION_STATUS.md').exists():
        shutil.copy2(ROOT/'docs/VALIDATION_STATUS.md', paths['release_dir']/'VALIDATION_STATUS.md')
    # Keep README's new engineering/evidence links usable inside the ZIP too.
    documentation = paths['release_dir'] / 'docs'
    documentation.mkdir(exist_ok=True)
    for name in ('VALIDATION_STATUS.md', 'HANDWRITING_IMPROVEMENT_20261015.md'):
        shutil.copy2(ROOT / 'docs' / name, documentation / name)
    shutil.copytree(ROOT / 'docs/handwriting-evidence', documentation / 'handwriting-evidence')
    if validation_reports:
        shutil.copytree(paths['validation_dir'], paths['release_dir'] / 'candidate-validation')
    shutil.copy2(ROOT / 'scripts/validate_windows_bundle.ps1',
                 paths['release_dir'] / 'Validate-Candidate.ps1')
    # Corresponding machine-readable model source and both licenses accompany
    # the same release, independent of upstream links becoming unavailable.
    source_dir = paths['release_dir'] / 'handwriting-source'
    shutil.copytree(ROOT / 'models/handwriting/source', source_dir / 'tegaki-zh_TW')
    shutil.copy2(ROOT / 'models/handwriting/manifest.json', source_dir / 'MODEL_MANIFEST.json')
    shutil.copytree(ROOT / 'native/handwriting/vendor', source_dir / 'zinnia/vendor')
    shutil.copytree(ROOT / 'native/handwriting/patches', source_dir / 'zinnia/patches')
    for name in ('UPSTREAM_MANIFEST.json', 'helper.cpp', 'model_and_strokes.h', 'CMakeLists.txt'):
        shutil.copy2(ROOT / 'native/handwriting' / name, source_dir / 'zinnia' / name)
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
