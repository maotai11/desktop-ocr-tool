"""A native pixel claim is unavailable on non-native Qt platforms."""
import sys

import pytest

from src.shared_search_windows_probe import verify_overlay_capture


@pytest.mark.parametrize('platform,qt_platform', [('linux', 'windows'), ('win32', 'offscreen'),
                                                ('win32', 'minimal')])
def test_native_overlay_proof_does_not_claim_unsupported_platform(monkeypatch, platform, qt_platform):
    monkeypatch.setattr(sys, 'platform', platform)

    class App:
        def platformName(self):
            return qt_platform

    assert verify_overlay_capture(App()) is False
