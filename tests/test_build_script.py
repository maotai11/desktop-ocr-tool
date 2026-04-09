# -*- coding: utf-8 -*-
from pathlib import Path

from scripts.build import resolve_release_paths, reset_output_dirs


def test_reset_output_dirs_removes_stale_release_contents(tmp_path):
    paths = resolve_release_paths(tmp_path)

    stale_release = paths["release_dir"]
    stale_release.mkdir(parents=True)
    (stale_release / "DesktopOCRTool-v1.5.0.exe").write_text("old", encoding="utf-8")
    (stale_release / "DesktopOCRTool-v1.6.0.exe").write_text("new", encoding="utf-8")
    paths["zip_path"].parent.mkdir(parents=True, exist_ok=True)
    paths["zip_path"].write_text("zip", encoding="utf-8")

    reset_output_dirs(paths)

    assert paths["dist_dir"].exists()
    assert paths["build_dir"].exists()
    assert paths["release_dir"].exists()
    assert list(paths["release_dir"].iterdir()) == []
    assert not paths["zip_path"].exists()
