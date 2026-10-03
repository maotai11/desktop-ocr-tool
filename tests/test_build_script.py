# -*- coding: utf-8 -*-
from pathlib import Path

from scripts.build import resolve_release_paths, reset_output_dirs
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
    from scripts.build import pyinstaller_command
    args=pyinstaller_command(tmp_path,resolve_release_paths(tmp_path))
    included={args[i+1] for i,x in enumerate(args[:-1]) if x in ('--collect-all','--collect-data','--collect-binaries','--hidden-import')}
    excluded={args[i+1] for i,x in enumerate(args[:-1]) if x=='--exclude-module'}
    assert not included & excluded
    assert '--runtime-hook' in args
    assert any('models' in x for x in args)
