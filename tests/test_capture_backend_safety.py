"""Validate requested capture geometry without requiring a live display."""
import numpy as np
import pytest

from src.capture.backend_mss import MssBackend


@pytest.fixture
def fake_mss(monkeypatch):
    class FakeMss:
        monitors = [
            {'left': -1920, 'top': 0, 'width': 3840, 'height': 1080},
            {'left': 0, 'top': 0, 'width': 1920, 'height': 1080},
            {'left': -1920, 'top': 0, 'width': 1920, 'height': 1080},
        ]

        def __init__(self):
            self.regions = []

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def grab(self, region):
            self.regions.append(dict(region))
            return np.zeros((region['height'], region['width'], 4), np.uint8)

    fake = FakeMss()
    monkeypatch.setattr('src.capture.backend_mss.mss.mss', lambda: fake)
    return fake


@pytest.mark.parametrize('index', [-1, 0, 3])
def test_invalid_monitor_never_falls_back_to_primary(fake_mss, index):
    backend = MssBackend()
    assert backend.capture_region(0, 0, 10, 10, index) is None
    assert backend.capture_monitor(index) is None
    with pytest.raises(ValueError):
        backend.get_monitor_info(index)
    assert fake_mss.regions == []


@pytest.mark.parametrize('rect', [(-1, 0, 10, 10), (0, -1, 10, 10),
                                 (0, 0, 0, 10), (0, 0, 10, -1),
                                 (1915, 0, 10, 10), (0, 1075, 10, 10)])
def test_invalid_region_never_captures_other_pixels(fake_mss, rect):
    assert MssBackend().capture_region(*rect, 1) is None
    assert fake_mss.regions == []


def test_negative_desktop_origin_keeps_monitor_relative_coordinates(fake_mss):
    image = MssBackend().capture_region(30, 40, 100, 50, 2)
    assert image.shape == (50, 100, 3)
    assert fake_mss.regions == [{'left': -1890, 'top': 40, 'width': 100, 'height': 50}]
