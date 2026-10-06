import hashlib
import json

import numpy as np
import pytest

from src.ocr.bounded_recognizer import BoundedRecognizer
from src.ocr.model_validator import MODEL_PROFILES, verified_model_manifest


class FakeRecognizer:
    def __init__(self):
        self.calls = []
        self.rec_batch_num = 6

    def __call__(self, crops, *args, **kwargs):
        self.calls.append(crops)
        return [('text', .9)] * len(crops), .01


def crop(height=48, width=320):
    return np.zeros((height, width, 3), dtype=np.uint8)


def test_recognizer_width_boundary_and_batch_one():
    raw = FakeRecognizer()
    rec = BoundedRecognizer(raw)
    assert raw.rec_batch_num == 1
    assert rec([crop(width=4096)])[0] == [('text', .9)]
    with pytest.raises(ValueError, match='文字行'):
        rec([crop(width=4097)])
    assert len(raw.calls) == 1


def test_long_crop_among_short_crops_rejected_before_any_native_call():
    raw = FakeRecognizer(); rec = BoundedRecognizer(raw)
    with pytest.raises(ValueError):
        rec([crop()] * 5 + [crop(height=1, width=1000)])
    assert not raw.calls


@pytest.mark.parametrize('bad', [np.zeros((0, 5, 3), np.uint8), np.zeros((5, 0, 3), np.uint8),
    np.zeros((5, 5), np.uint8), np.zeros((5, 5, 4), np.uint8), np.zeros((5, 5, 3), float), None])
def test_invalid_crops_never_enter_native(bad):
    raw = FakeRecognizer(); rec = BoundedRecognizer(raw)
    with pytest.raises(ValueError): rec([bad])
    assert not raw.calls


def test_total_budget_spans_calls_and_reset_is_explicit():
    raw = FakeRecognizer(); rec = BoundedRecognizer(raw)
    for _ in range(16): rec([crop(width=4096)])
    with pytest.raises(ValueError, match='工作量'):
        rec([crop()])
    assert len(raw.calls) == 16
    rec.reset_budget(); rec([crop()])
    assert len(raw.calls) == 17


def test_many_crops_rejected_up_front():
    raw = FakeRecognizer(); rec = BoundedRecognizer(raw)
    with pytest.raises(ValueError): rec([crop()] * 513)
    assert not raw.calls


def test_all_profiles_have_verified_distinct_recognizers():
    profiles = {p: verified_model_manifest(profile=p) for p in MODEL_PROFILES}
    assert profiles['v6-medium']['det']['sha256'] == profiles['v6-small']['det']['sha256']
    assert profiles['v6-medium']['rec']['sha256'] != profiles['v6-small']['rec']['sha256']
    with pytest.raises(ValueError, match='Unknown'):
        verified_model_manifest(profile='../outside')


def test_manifest_tamper_and_path_escape(tmp_path):
    (tmp_path / 'models').mkdir()
    lock = {k: {'path': 'models/x', 'size_bytes': 1, 'sha256': hashlib.sha256(b'x').hexdigest()}
            for k in ('det', 'rec', 'cls')}
    (tmp_path / 'models/x').write_bytes(b'x')
    p = tmp_path / 'models/models.lock.json'; p.write_text(json.dumps(lock))
    verified_model_manifest(tmp_path)
    (tmp_path / 'models/x').write_bytes(b'y')
    with pytest.raises(ValueError, match='SHA256'): verified_model_manifest(tmp_path)
    lock['det']['path'] = '../outside'; p.write_text(json.dumps(lock))
    with pytest.raises(ValueError, match='escapes'): verified_model_manifest(tmp_path)


def test_unknown_profile_fails_load_but_constructor_allows_settings_recovery():
    from src.ocr.engine import OcrEngine
    e = OcrEngine(model_profile='obsolete')
    with pytest.raises(ValueError, match='Unknown'): e.load()
    assert not e.is_ready()


@pytest.mark.parametrize('old_value', [1921, 1930, 1984, 2048])
def test_config_legacy_2048_migrates_without_losing_user_preferences(tmp_path, monkeypatch, old_value):
    from src.core import config
    monkeypatch.setattr(config, 'get_project_root', lambda: str(tmp_path))
    (tmp_path / 'config').mkdir()
    p = tmp_path / 'config/settings.json'
    p.write_text(json.dumps({'ocr': {'max_image_short_side': old_value},
        'clipboard': {'monitor_clipboard': True}, 'general': {'data_directory': './my-data'}}))
    cfg = config.ConfigManager()
    assert cfg.get('ocr', 'max_image_short_side') == 1920
    assert cfg.get('ocr', 'model_profile') == 'v6-small'
    assert cfg.get('clipboard', 'monitor_clipboard') is True
    assert cfg.get('general', 'data_directory') == './my-data'
    assert json.loads(p.read_text())['ocr']['max_image_short_side'] == 1920


def test_profile_setting_saved_and_cancel_does_not_hot_switch(qtbot, tmp_path, monkeypatch):
    from src.core import config
    from src.ui.settings_dialog import SettingsDialog
    monkeypatch.setattr(config, 'get_project_root', lambda: str(tmp_path))
    cfg = config.ConfigManager()
    dialog = SettingsDialog(cfg); qtbot.addWidget(dialog)
    dialog._model_profile.setCurrentIndex(dialog._model_profile.findData('v6-medium'))
    dialog.reject()
    assert cfg.get('ocr', 'model_profile') == 'v6-small'
    dialog = SettingsDialog(cfg); qtbot.addWidget(dialog)
    dialog._model_profile.setCurrentIndex(dialog._model_profile.findData('v6-medium'))
    dialog._save()
    assert cfg.get('ocr', 'model_profile') == 'v6-medium'


def test_total_short_crop_limit_is_204_not_512():
    raw = FakeRecognizer(); rec = BoundedRecognizer(raw)
    rec([crop()] * 204)
    with pytest.raises(ValueError, match='工作量'): rec([crop()])
    rec.reset_budget()
    with pytest.raises(ValueError, match='工作量'): rec([crop()] * 205)


def test_budget_counts_second_pass_without_reset():
    raw = FakeRecognizer(); rec = BoundedRecognizer(raw)
    rec([crop()] * 100); rec([crop()] * 100)
    with pytest.raises(ValueError, match='工作量'): rec([crop()] * 5)


def test_small_roi_bounded_padding_preserves_edges_and_coordinates():
    from src.ocr.preprocessor import (
        plan_ocr_tiles,
        prepare_ocr_tile,
        restore_tile_results,
    )
    image = np.full((20, 100, 3), 240, dtype=np.uint8)
    image[0, 15:20] = 0  # A black stroke touches the upper source boundary.
    tile = plan_ocr_tiles(image.shape)[0]
    assert tile['strategy'] == 'bounded_small_region'
    assert tile['resized_hw'] == [60, 300]
    padded = prepare_ocr_tile(image, tile)
    px, py = tile['padding_xy']
    assert tuple(padded[py - 1, px + 50]) == (240, 240, 240)
    assert padded.shape == (736, 736, 3)
    box = [[px, py], [px+300,py], [px+300,py+60], [px,py+60]]
    assert restore_tile_results([[box,'test',.9]],tile)[0][0] == [[0.,0.],[100.,0.],[100.,20.],[0.,20.]]


@pytest.mark.parametrize('fill', [0, 73, 255])
def test_uniform_small_roi_does_not_invent_text(fill):
    from src.ocr.engine import OcrEngine
    engine = OcrEngine(); engine._ready = True
    # If uniform detection fails, this fake deliberately raises on inference.
    engine._engine = lambda *_: (_ for _ in ()).throw(AssertionError('uniform crop inferred'))
    result = engine.run_ocr(np.full((20, 30, 3), fill, np.uint8))
    assert result['text'] == '' and result['status'] != 'failed'


def test_saved_model_selection_loads_actual_engine_after_restart(qtbot, tmp_path, monkeypatch):
    import gc
    import weakref

    from src.core import config
    from src.ocr.engine import OcrEngine
    from src.ui.settings_dialog import SettingsDialog

    monkeypatch.setattr(config, 'get_project_root', lambda: str(tmp_path))
    cfg = config.ConfigManager()
    for profile in ('v6-medium', 'v6-small', 'v6-medium'):
        dialog = SettingsDialog(cfg); qtbot.addWidget(dialog)
        dialog._model_profile.setCurrentIndex(dialog._model_profile.findData(profile))
        dialog._save()
        restarted = config.ConfigManager()
        engine = OcrEngine(model_profile=restarted.get('ocr', 'model_profile'))
        engine.load()
        assert engine.model_identity['profile'] == profile
        manifest = verified_model_manifest(profile=profile)
        assert manifest['rec']['sha256'] in engine._model_version
        assert engine.model_identity['recognition']['character_sha256'] == manifest['rec']['character_sha256']
        reference = weakref.ref(engine)
        del engine
        gc.collect()
        assert reference() is None


def test_empty_closed_frame_uses_interior_background_without_erasing_source():
    import cv2

    from src.ocr.preprocessor import plan_ocr_tiles, prepare_ocr_tile, small_region_padding_color

    source = np.full((40, 180, 3), 255, np.uint8)
    source[[0, -1], :] = 0; source[:, [0, -1]] = 0
    original = source.copy()
    color, reason = small_region_padding_color(source)
    assert color == (255., 255., 255.) and reason == 'closed_perimeter_uniform_interior'
    tile = plan_ocr_tiles(source.shape)[0]; padded = prepare_ocr_tile(source, tile)
    px, py = tile['padding_xy']; rh, rw = tile['resized_hw']
    assert np.array_equal(source, original)
    assert np.array_equal(padded[py:py+rh,px:px+rw], cv2.resize(source,(rw,rh),interpolation=cv2.INTER_CUBIC))
    assert np.all(padded[0] == 255)


@pytest.mark.parametrize('foreground,background', [(0,255),(255,0)])
def test_frame_containing_any_stroke_keeps_original_padding(foreground, background):
    from src.ocr.preprocessor import small_region_padding_color

    source = np.full((40,180,3),background,np.uint8)
    source[[0,-1],:] = foreground; source[:,[0,-1]] = foreground
    source[20,50:100] = foreground  # Legitimate 一 inside a frame: do not discard it.
    color, reason = small_region_padding_color(source)
    assert color == (float(foreground),) * 3 and reason == 'edge_median'


@pytest.mark.parametrize('profile', list(MODEL_PROFILES))
def test_real_new_model_empty_frame_is_not_recognized_as_one(profile):
    from src.ocr.engine import OcrEngine

    source = np.full((40,180,3),255,np.uint8)
    source[[0,-1],:] = 0; source[:,[0,-1]] = 0
    engine = OcrEngine(model_profile=profile); engine.load()
    result = engine.run_ocr(source)
    assert result['text'] == '' and result['status'] != 'failed'
    assert result['preprocessing']['strategy'] == 'bounded_small_region'


@pytest.mark.parametrize('contrast', [1,3,8,9,32])
def test_faint_content_does_not_trigger_empty_frame_override(contrast):
    from src.ocr.preprocessor import small_region_padding_color

    image = np.full((40,180,3),255,np.uint8)
    image[[0,-1],:] = 0; image[:,[0,-1]] = 0
    image[20,50:100] = 255 - contrast
    assert small_region_padding_color(image) == ((0.,0.,0.), 'edge_median')
