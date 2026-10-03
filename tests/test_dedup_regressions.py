"""Exact-image and recent-item contracts; similarity never discards work."""
from datetime import datetime, timedelta

from PIL import Image

from src.data.hasher import sha256_image, sha256_text


def test_image_hash_includes_alpha(tmp_path):
    opaque, transparent = tmp_path / 'opaque.png', tmp_path / 'transparent.png'
    Image.new('RGBA', (10, 10), (255, 0, 0, 255)).save(opaque)
    Image.new('RGBA', (10, 10), (255, 0, 0, 0)).save(transparent)
    assert sha256_image(str(opaque)) != sha256_image(str(transparent))


def test_canonical_hash_matches_equivalent_opaque_pixels(tmp_path):
    rgb, rgba = tmp_path / 'rgb.png', tmp_path / 'rgba.png'
    Image.new('RGB', (10, 10), (20, 30, 40)).save(rgb)
    Image.new('RGBA', (10, 10), (20, 30, 40, 255)).save(rgba)
    assert sha256_image(str(rgb)) == sha256_image(str(rgba))


def test_small_pixel_change_is_not_discarded(tmp_path):
    first, second = tmp_path / 'first.png', tmp_path / 'second.png'
    image = Image.new('RGB', (100, 100), 'white')
    image.save(first)
    image.putpixel((50, 50), (254, 255, 255))
    image.save(second)
    assert sha256_image(str(first)) != sha256_image(str(second))


def test_equal_timestamp_uses_latest_item(item_repo, make_text_dto):
    first = item_repo.insert(make_text_dto('first'))
    second = item_repo.insert(make_text_dto('second'))
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    item_repo._conn.execute('UPDATE items SET created_at=? WHERE id IN (?,?)',
                            (timestamp, first, second))
    item_repo._conn.commit()
    assert item_repo.get_last_by_source_mode('clipboard_text').id == second
    assert item_repo.should_deduplicate('clipboard_text', sha256_text('second'))
    assert not item_repo.should_deduplicate('clipboard_text', sha256_text('first'))


def test_future_timestamp_cannot_extend_dedup_window(item_repo, make_text_dto):
    item_id = item_repo.insert(make_text_dto('same'))
    future = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%S')
    item_repo._conn.execute('UPDATE items SET created_at=? WHERE id=?', (future, item_id))
    item_repo._conn.commit()
    assert not item_repo.should_deduplicate('clipboard_text', sha256_text('same'))


def test_legacy_perceptual_hash_never_deduplicates(item_repo, make_image_dto):
    item_repo.insert(make_image_dto())
    assert not item_repo.should_deduplicate('region_ocr', image_hash='deadbeef')
