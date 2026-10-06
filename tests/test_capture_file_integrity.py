"""New capture writes may not overwrite another item's original image."""
from pathlib import Path

from PIL import Image
import pytest


def test_capture_filename_collision_preserves_previous_image(file_mgr, monkeypatch):
    names = iter(['same.png', 'same.png', 'new.png'])
    monkeypatch.setattr(file_mgr, '_make_filename', lambda _: next(names))
    first, _ = file_mgr.save_capture(Image.new('RGB', (10, 10), 'red'))
    before = Path(first).read_bytes()
    second, _ = file_mgr.save_capture(Image.new('RGB', (10, 10), 'blue'))
    assert first != second
    assert Path(first).read_bytes() == before
    assert Image.open(second).getpixel((0, 0)) == (0, 0, 255)


def test_repeated_collision_reports_failure_without_overwrite(file_mgr, monkeypatch):
    monkeypatch.setattr(file_mgr, '_make_filename', lambda _: 'same.png')
    first, _ = file_mgr.save_capture(Image.new('RGB', (10, 10), 'red'))
    before = Path(first).read_bytes()
    with pytest.raises(FileExistsError):
        file_mgr.save_capture(Image.new('RGB', (10, 10), 'blue'))
    assert Path(first).read_bytes() == before


def test_failed_image_write_removes_only_owned_partial(file_mgr, monkeypatch):
    first, _ = file_mgr.save_capture(Image.new('RGB', (10, 10), 'red'))
    before = Path(first).read_bytes()
    def fail(*_, **__):
        raise OSError('injected write failure')
    monkeypatch.setattr(Image.Image, 'save', fail)
    with pytest.raises(OSError, match='injected'):
        file_mgr.save_capture(Image.new('RGB', (10, 10), 'blue'))
    assert list(Path(file_mgr._data_dir).rglob('*.png')) == [Path(first)]
    assert Path(first).read_bytes() == before


@pytest.mark.parametrize('extension', ['../escape', 'unknown'])
def test_capture_extension_rejected_before_write(file_mgr, extension):
    with pytest.raises(ValueError):
        file_mgr.save_capture(Image.new('RGB', (10, 10)), ext=extension)
    assert list(Path(file_mgr._data_dir).rglob('*.*')) == []
