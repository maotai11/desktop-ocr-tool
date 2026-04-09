# -*- coding: utf-8 -*-
from src.core import constants
from src.core.version import APP_DISPLAY_NAME, APP_NAME, APP_VERSION


def test_constants_share_canonical_version_metadata():
    assert constants.APP_NAME == APP_DISPLAY_NAME
    assert constants.APP_NAME_EN == APP_NAME
    assert constants.APP_VERSION == APP_VERSION
