
from scripts.build import reset_output_dirs, resolve_release_paths
from src.core.version import versioned_exe_name


def test_reset_output_dirs_removes_stale_release_contents(tmp_path):
    paths = resolve_release_paths(tmp_path)

    stale_release = paths["release_dir"]
    stale_release.mkdir(parents=True)
    (stale_release / "DesktopOCRTool-v1.5.0.exe").write_text("old", encoding="utf-8")
    (stale_release / versioned_exe_name()).write_text("new", encoding="utf-8")
    paths["zip_path"].parent.mkdir(parents=True, exist_ok=True)
    paths["zip_path"].write_text("zip", encoding="utf-8")

    reset_output_dirs(paths)

    assert paths["dist_dir"].exists()
    assert paths["build_dir"].exists()
    assert paths["release_dir"].exists()
    assert list(paths["release_dir"].iterdir()) == []
    assert not paths["zip_path"].exists()


def test_build_collection_exclusions_and_models_do_not_conflict(tmp_path):
    from model_profile_fixtures import fixture_profiles
    for name, data in fixture_profiles()[1].items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    from scripts.build import pyinstaller_command
    args=pyinstaller_command(tmp_path,resolve_release_paths(tmp_path))
    included={args[i+1] for i,x in enumerate(args[:-1]) if x in ('--collect-all','--collect-data','--collect-binaries','--hidden-import')}
    excluded={args[i+1] for i,x in enumerate(args[:-1]) if x=='--exclude-module'}
    assert not included & excluded
    assert '--runtime-hook' in args
    assert args[args.index('--copy-metadata') + 1] == 'rapidocr-onnxruntime'
    assert any('models' in x for x in args)


def test_build_data_allowlist_excludes_baseline_and_vendor_models(tmp_path):
    import os
    from pathlib import Path

    from model_profile_fixtures import fixture_profiles

    from scripts.build import bundled_data_files, pyinstaller_command
    from src.ocr.model_validator import MODEL_PROFILES
    profiles, assets = fixture_profiles()
    for name, data in assets.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    for name in ('models/rec/pp-ocrv4_rec.onnx', 'models/det/pp-ocrv4_det.onnx',
                 'models/models-v4.lock.json', 'models/validation/unapproved.png'):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'baseline-must-not-ship')
    files = bundled_data_files(tmp_path)
    assert all(source.is_file() for source, _ in files)
    destinations = {str(Path(destination) / source.name).replace('\\', '/')
                    for source, destination in files}
    allowed_models = {info['path'] for models in profiles.values() for info in models.values()}
    allowed_locks = {'models/' + name for name in MODEL_PROFILES.values()}
    allowed_fixtures = {name for name in assets if name.startswith('models/validation/')}
    assert destinations == allowed_models | allowed_locks | allowed_fixtures | {'rapidocr_onnxruntime/config.yaml'}
    args = pyinstaller_command(tmp_path, resolve_release_paths(tmp_path))
    collected = {args[i + 1] for i, arg in enumerate(args[:-1])
                 if arg in ('--collect-data', '--collect-all')}
    assert 'rapidocr_onnxruntime' not in collected
    data_args = [args[i + 1] for i, arg in enumerate(args[:-1]) if arg == '--add-data']
    assert len(data_args) == len(destinations)
    assert f'{tmp_path / "models"}{os.pathsep}models' not in data_args


def test_onnxruntime_collects_native_libraries_without_demo_models(tmp_path, monkeypatch):
    from scripts import build

    monkeypatch.setattr(build, 'bundled_data_files', lambda root: [])
    args = build.pyinstaller_command(tmp_path, build.resolve_release_paths(tmp_path))
    pairs = list(zip(args, args[1:]))
    assert ('--collect-all', 'onnxruntime') not in pairs
    assert ('--collect-data', 'onnxruntime') not in pairs
    assert ('--collect-binaries', 'onnxruntime') in pairs
    assert ('--copy-metadata', 'onnxruntime') in pairs
