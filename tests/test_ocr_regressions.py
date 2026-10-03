"""Deterministic OCR recovery contracts; no model-accuracy claim from mocks."""
import struct
import zlib

import numpy as np
import pytest
from PIL import Image

from src.ocr.engine import OcrEngine
from src.ocr.fusion import fuse_passes, fuse_tiles
from src.ocr.postprocessor import sort_boxes_and_merge
from src.ocr.preprocessor import (
    MAX_ENCODED_BYTES, MAX_TILE_PIXELS, MAX_TOTAL_TILE_PIXELS,
    plan_ocr_tiles, prepare_ocr_tile, restore_tile_results,
)


def raw(text='臺灣', x=0, y=0, w=40, h=20, conf=.9):
    return [[[x, y], [x + w, y], [x + w, y + h], [x, y + h]], text, conf]


def detail(item):
    return dict(box=item[0], text=item[1], confidence=item[2])


def ready(**kwargs):
    engine = OcrEngine(**kwargs)
    engine._ready = True
    engine._model_version = 'unchanged-v4-fixture'
    return engine


def test_weak_region_is_not_hidden_by_page_average():
    engine = ready()
    rows = [raw(conf=.99)] * 20 + [raw('𠮷', conf=.4)]
    assert engine._should_retry(rows)
    assert engine._process_results(rows, 0)['status'] == 'needs_review'


@pytest.mark.parametrize('failure_stage', ['enhance', 'inference'])
def test_retry_failure_preserves_first_pass(monkeypatch, failure_stage):
    engine = ready()
    calls = []

    def recognize(image):
        calls.append(image.shape)
        if len(calls) > 1:
            raise RuntimeError('retry unavailable')
        return [raw('原文保留', conf=.5)]

    monkeypatch.setattr(engine, '_do_ocr_array', recognize)
    if failure_stage == 'enhance':
        def fail(*args, **kwargs):
            raise RuntimeError('enhance unavailable')
        monkeypatch.setattr('src.ocr.engine.enhance_for_ocr', fail)
    result = engine.run_ocr(np.zeros((960, 960, 3), np.uint8))
    assert result['text'] == '原文保留'
    assert result['status'] == 'needs_review'
    assert 'first-pass text retained' in result['warnings'][0]
    assert result['model_version'] == 'unchanged-v4-fixture'


def test_retry_failure_with_no_first_pass_remains_failed(monkeypatch):
    engine = ready()
    calls = iter([[], RuntimeError('retry unavailable')])

    def recognize(image):
        value = next(calls)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(engine, '_do_ocr_array', recognize)
    result = engine.run_ocr(np.zeros((960, 960, 3), np.uint8))
    assert result['status'] == 'failed'
    assert not result['text']


def test_expanded_second_pass_keeps_recovered_low_confidence_text():
    first = [raw('臺灣', w=40, conf=.99)]
    second = [raw('臺灣稅務申報', w=120, conf=.7)]
    assert fuse_passes(first, second) == second


def test_overlapping_fragments_cannot_fake_complete_coverage():
    first = [raw('臺灣稅務申報期限', w=160, conf=.7)]
    partial = [raw('臺灣稅務', w=70, conf=.99), raw('臺灣稅務', x=2, w=70, conf=.99)]
    assert fuse_passes(first, partial) == first


def test_second_pass_does_not_append_full_line_and_fragments():
    first = [raw('臺灣稅務', w=80, conf=.4)]
    second = [raw('臺灣', w=40), raw('稅務', x=40, w=40)]
    assert sort_boxes_and_merge([detail(r) for r in fuse_passes(first, second)]) == '臺灣稅務'


def test_large_heading_does_not_merge_small_body_rows():
    rows = [raw('標題', y=0, w=100, h=100),
            raw('第一列', y=120, h=10), raw('第二列', y=136, h=10)]
    assert sort_boxes_and_merge([detail(r) for r in rows]).splitlines() == ['標題', '第一列', '第二列']


def test_numpy_boxes_and_flat_boxes_are_supported():
    left, right = detail(raw('臺灣')), detail(raw('稅務', x=42))
    left['box'] = np.asarray(left['box'])
    right['box'] = np.asarray([42, 0, 82, 20])
    assert sort_boxes_and_merge([right, left]) == '臺灣稅務'


@pytest.mark.parametrize('text', ['𠮷', '𠀋', '\uf900', '𠮷\U000e0100'])
def test_rare_cjk_and_variation_selectors_do_not_gain_a_space(text):
    assert sort_boxes_and_merge([detail(raw(text)), detail(raw('先生', x=42))]) == text + '先生'


def test_postprocessing_does_not_invent_rare_glyph_or_dictionary_corrections():
    engine = ready()
    text = '己已巳 𠮷 𠀋 尞 API_key NT$'
    assert engine._process_results([raw(text)], 0)['text'] == text


@pytest.mark.parametrize('shape', [(30, 2000, 3), (2000, 30, 3), (2160, 3840, 3), (1, 2000, 3)])
def test_tile_plan_bounds_hidden_detector_resize(shape):
    tiles = plan_ocr_tiles(shape)
    assert 0 < len(tiles) <= 64
    total = 0
    for tile in tiles:
        ph, pw = tile['inference_hw']
        assert 736 <= min(ph, pw) <= max(ph, pw) <= 1536
        assert ph * pw <= MAX_TILE_PIXELS
        total += ph * pw
    assert total <= MAX_TOTAL_TILE_PIXELS
    # Each axis has complete coverage with overlap, including the final tile.
    for axis, length in ((0, shape[1]), (1, shape[0])):
        intervals = sorted({(t['source_xywh'][axis], t['source_xywh'][axis] + t['source_xywh'][axis + 2]) for t in tiles})
        end = 0
        for begin, stop in intervals:
            assert begin <= end
            end = max(end, stop)
        assert end == length


@pytest.mark.parametrize('shape', [(64, 256, 3), (960, 960, 3), (1080, 1920, 3)])
def test_ordinary_images_keep_established_pipeline(shape):
    assert plan_ocr_tiles(shape) == []


@pytest.mark.parametrize('shape', [(0, 10, 3), (8000, 8000, 3), (1, 100_000, 3), (4000, 8000, 3)])
def test_excess_work_is_rejected_before_any_tile_allocation(shape):
    with pytest.raises(ValueError):
        plan_ocr_tiles(shape)


def test_tile_coordinates_and_padding_roundtrip():
    shape = (30, 2000, 3)
    tile = plan_ocr_tiles(shape)[1]
    image = prepare_ocr_tile(np.zeros(shape, np.uint8), tile)
    assert list(image.shape[:2]) == tile['inference_hw']
    sx, sy = tile['scale_xy']
    px, py = tile['padding_xy']
    local = raw('𠮷', x=px + 4 * sx, y=py + 3 * sy, w=10 * sx, h=12 * sy)
    restored = restore_tile_results([local], tile)[0]
    x, y = tile['source_xywh'][:2]
    assert restored[0] == raw(x=x + 4, y=y + 3, w=10, h=12)[0]
    assert restore_tile_results([raw(y=0, h=10)], tile) == []


def test_tile_fusion_retains_disjoint_repeated_text():
    result, conflicts = fuse_tiles([raw('同')], [raw('同', x=100)])
    assert len(result) == 2 and conflicts == 0


def test_tile_duplicate_and_agreeing_seam_are_reconciled():
    result, conflicts = fuse_tiles([raw('臺灣稅務申報', w=60, h=10)],
                                   [raw('稅務申報期限', x=20, w=60, h=10)])
    assert len(result) == 1 and result[0][1] == '臺灣稅務申報期限'
    assert conflicts == 0
    result, conflicts = fuse_tiles(result, result)
    assert len(result) == 1 and conflicts == 0


def test_tile_uncertain_partial_overlap_is_not_silently_deleted():
    first, second = raw('甲乙丙丁戊己', w=60, h=10), raw('未知辨識文字', x=40, w=60, h=10)
    result, conflicts = fuse_tiles([first], [second])
    assert result == [first, second]
    assert conflicts == 1


def test_tiled_pipeline_records_transform_and_real_engine(monkeypatch):
    image = np.repeat(np.arange(2000, dtype=np.uint8)[None, :, None], 30, axis=0).repeat(3, axis=2)
    tiles = plan_ocr_tiles(image.shape)
    engine, seen = ready(enable_second_pass=False), []

    def recognize(tile_image):
        tile = tiles[len(seen)]
        seen.append(tile_image.shape)
        px, py = tile['padding_xy']
        return [raw(f'T{len(seen)}', x=px + 30, y=py + 6, w=30, h=30)]

    monkeypatch.setattr(engine, '_do_ocr_array', recognize)
    result = engine.run_ocr(image)
    assert len(seen) == len(tiles)
    assert result['status'] == 'needs_review'
    assert result['engine'] == 'rapidocr_onnxruntime'
    assert result['model_version'] == 'unchanged-v4-fixture'
    assert result['preprocessing']['strategy'] == 'overlapping_tiles'
    assert result['preprocessing']['tiles'][1]['source_xywh'] == tiles[1]['source_xywh']
    assert len(result['detail']) == len(tiles)
    for row in result['detail']:
        assert 0 <= min(p[1] for p in row['box']) < max(p[1] for p in row['box']) <= 30


def test_failed_tile_never_returns_partial_success(monkeypatch):
    engine, calls = ready(enable_second_pass=False), []

    def recognize(image):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError('tile failed')
        return []

    monkeypatch.setattr(engine, '_do_ocr_array', recognize)
    image = np.repeat(np.arange(2000, dtype=np.uint8)[None, :, None], 30, axis=0).repeat(3, axis=2)
    result = engine.run_ocr(image)
    assert result['status'] == 'failed' and not result['text']
    assert len(calls) == 2 and '2/' in result['error']


@pytest.mark.parametrize('image', [None, np.empty((0, 1, 3), np.uint8), np.zeros((2, 2), np.uint8)])
def test_bad_input_returns_failed_instead_of_escaping(image):
    assert ready().run_ocr(image)['status'] == 'failed'


def test_image_header_is_checked_before_cv2_decode(tmp_path, monkeypatch):
    path = tmp_path / 'oversized.png'
    Image.new('RGB', (1, 1)).save(path)
    data = bytearray(path.read_bytes())
    data[16:24] = struct.pack('>II', 8000, 8000)
    data[29:33] = struct.pack('>I', zlib.crc32(data[12:29]))
    path.write_bytes(data)

    def forbidden(*args, **kwargs):
        raise AssertionError('decoder allocation occurred')

    monkeypatch.setattr('src.ocr.engine.cv2.imdecode', forbidden)
    result = ready().run_ocr_from_path(str(path))
    assert result['status'] == 'failed'
    assert '3200' in result['error']


def test_encoded_size_is_checked_before_header_or_decode(tmp_path):
    path = tmp_path / 'too_large.png'
    with path.open('wb') as file:
        file.truncate(MAX_ENCODED_BYTES + 1)
    result = ready().run_ocr_from_path(str(path))
    assert result['status'] == 'failed' and '32 MB' in result['error']


def test_unicode_path_still_decodes(monkeypatch, tmp_path):
    path = tmp_path / '臺灣𠮷.png'
    Image.new('RGB', (64, 64), 'white').save(path)
    engine = ready(enable_second_pass=False)
    monkeypatch.setattr(engine, '_do_ocr_array', lambda image: [raw('保留')])
    assert engine.run_ocr_from_path(str(path))['text'] == '保留'


def test_contained_seam_fragment_with_height_drift_is_removed():
    first = raw('臺灣稅務申', x=419, y=17, w=93, h=23, conf=.82)
    second = raw('臺灣稅務申報期限已繳稅款', x=421, y=19, w=367, h=19, conf=.91)
    third = raw('款', x=768, y=16, w=23, h=25, conf=.94)
    result, _ = fuse_tiles([first], [second, third])
    assert len(result) == 1 and result[0][1] == second[1]


def test_contained_seam_fragment_with_spaces_does_not_duplicate():
    first = raw('臺灣API_keyAB-1234567810 kg', x=499, y=209, w=192, h=19, conf=.96)
    second = raw('_key AB-12345678 10 kg', x=577, y=210, w=114, h=19, conf=.95)
    result, _ = fuse_tiles([first], [second])
    assert result == [first]  # Exact retained text is not normalized.


def test_raw_hypotheses_and_conflict_survive_conversion(monkeypatch):
    engine = ready()
    first, second = [raw('台湾', conf=.5)], [raw('臺灣稅務申報', w=120, conf=.7)]
    outputs = iter([first, second])
    monkeypatch.setattr(engine, '_do_ocr_array', lambda image: next(outputs))
    result = engine.run_ocr(np.zeros((960, 960, 3), np.uint8))
    assert result['status'] == 'needs_review'
    assert result['text'] == '臺灣稅務申報'
    assert result['hypotheses']['first_pass'][0]['raw_text'] == '台湾'
    assert result['hypotheses']['second_pass'][0]['raw_text'] == '臺灣稅務申報'
    assert any('disagrees' in warning for warning in result['warnings'])


def test_original_coordinates_do_not_mutate_reusable_provider_result():
    source = {'detail': [detail(raw('原文'))]}
    mapped = OcrEngine._original_coordinates(source, (50, 50), (100, 100))
    assert mapped['detail'][0]['box'][1][0] == 20
    assert source['detail'][0]['box'][1][0] == 40


def test_uniform_tiles_skip_inference_but_faint_tiles_do_not(monkeypatch):
    engine, calls = ready(enable_second_pass=False), []
    monkeypatch.setattr(engine, '_do_ocr_array', lambda image: calls.append(image.shape) or [])
    image = np.zeros((30, 2000, 3), np.uint8)
    result = engine.run_ocr(image)
    assert not calls
    assert all(t['inference_skipped'] for t in result['preprocessing']['tiles'])
    image[15, 10, :] = 1
    result = engine.run_ocr(image)
    assert len(calls) == 1
    assert result['preprocessing']['tiles'][0]['inference_skipped'] is False


@pytest.mark.parametrize('text,expected', [
    ('ABABAB', 'ABABABABAB'),
    ('000000', '0000000000'),
    ('121212', '1212121212'),
])
def test_periodic_seam_uses_closest_geometry_not_longest_match(text, expected):
    first = raw(text, x=0, w=60, h=10)
    second = raw(text, x=40, w=60, h=10)
    result, conflicts = fuse_tiles([first], [second])
    assert len(result) == 1 and result[0][1] == expected
    assert conflicts == 0
    assert result[0][0] == raw(w=100, h=10)[0]


@pytest.mark.parametrize('text,x', [('ABABAB', 30), ('000000', 35), ('121212', 30),
                                   ('000000', 15), ('ABABAB', 10), ('121212', 10)])
def test_ambiguous_periodic_seam_retains_hypotheses_and_flags_conflict(text, x):
    first = raw(text, x=0, w=60, h=10)
    second = raw(text, x=x, w=60, h=10)
    result, conflicts = fuse_tiles([first], [second])
    assert result == [first, second]
    assert conflicts > 0


def test_unique_text_overlap_without_geometric_support_is_not_stitched():
    first = raw('AABBCC', x=0, w=60, h=10)
    second = raw('BBCCDD', x=40, w=60, h=10)
    # The only text match has four characters; the boxes share only two.
    result, conflicts = fuse_tiles([first], [second])
    assert result == [first, second]
    assert conflicts > 0
