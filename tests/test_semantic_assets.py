import pathlib
import struct
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'tools'))
import semantic_assets


def test_frame_extent_uses_inline_tile_grid_not_fixed_piece_size():
    content = bytes([2, 3])
    content += struct.pack('>hhBB4H', -8, 8, 2, 2, 10, 11, 12, 13)
    content += struct.pack('>hhBBH', 20, -10, 1, 1, 99)
    frame = semantic_assets.parse_frame(content, 0, len(content))
    assert frame['end'] == len(content)
    assert frame['flags'] == 3
    assert frame['parts'][0]['tiles'] == [10, 11, 12, 13]
    assert frame['parts'][1]['x'] == 20


def test_empty_frame_is_rejected_by_consumer_not_hidden_by_parser():
    frame = semantic_assets.parse_frame(bytes([0, 0]), 0, 2)
    assert frame['end'] == 2
    assert frame['consumer_safe'] is False


def test_frame_rejects_truncated_grid():
    content = bytes([1, 0]) + struct.pack('>hhBBH', 0, 0, 2, 2, 99)
    with pytest.raises(ValueError, match='tile grid'):
        semantic_assets.parse_frame(content, 0, len(content))


@pytest.mark.parametrize('plane,color', [(0, 1), (1, 2), (2, 4), (3, 8)])
def test_sprite_tile_plane_and_half_order(plane, color):
    first = bytearray(64)
    second = bytearray(64)
    target = first if plane < 2 else second
    target[32 + plane % 2] = 1
    pixels = semantic_assets.sprite_tile(first, second, 0)
    assert pixels[0][0] == color
    assert pixels[0][8] == 0
    assert sum(sum(row) for row in pixels) == color


def test_sprite_last_pixel_uses_right_half_bit_seven():
    first = bytearray(64)
    second = bytearray(64)
    second[31] = 0x80
    pixels = semantic_assets.sprite_tile(first, second, 0)
    assert pixels[15][15] == 8


def test_fix_tile_column_pair_order():
    content = bytearray(32)
    content[0] = 0x21
    content[16] = 0x43
    pixels = semantic_assets.fix_tile(content, 0x1000)
    assert pixels[0] == [3, 4, 0, 0, 1, 2, 0, 0]


def test_palette_bank_reports_invalid_channels_and_padding():
    valid = bytes([31, 16, 0, 0]) * 16
    assert semantic_assets.palette_records(valid)['palettes'] == 1
    invalid = bytes([32, 16, 0, 1]) + valid[4:]
    result = semantic_assets.palette_records(invalid)
    assert result['invalid_channels'] == 1
    assert result['nonzero_padding'] == 1


def test_hole_objects_stop_on_negative_type_and_keep_coordinates():
    content = bytearray(24)
    struct.pack_into('>I', content, 6, 10)
    struct.pack_into('>BBhh', content, 10, 0, 16, -1, 20)
    struct.pack_into('>BBhh', content, 16, 2, 3, 40, -10)
    content[22:24] = bytes([0xff, 0xff])
    objects = semantic_assets.hole_objects(content, 0)
    assert objects == [{'type': 0, 'flags': 16, 'x': -1, 'y': 20},
                       {'type': 2, 'flags': 3, 'x': 40, 'y': -10}]


def test_hole_objects_reject_unterminated_list():
    content = bytearray(16)
    struct.pack_into('>I', content, 6, 10)
    with pytest.raises(ValueError, match='unterminated'):
        semantic_assets.hole_objects(content, 0)
