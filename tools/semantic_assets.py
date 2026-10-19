#!/usr/bin/env python3
"""Decode owner-ROM asset structures using their actual program consumers."""
import argparse
import collections
import hashlib
import json
import pathlib
import struct

REPO = pathlib.Path(__file__).resolve().parents[1]
FRAME_START = 0x88000
FRAME_END = 0xad730
PALETTE_END = 0xb96f0


def parse_frame(content, start, limit):
    if start < 0 or limit > len(content) or start + 2 > limit:
        raise ValueError(f'truncated frame header at {start:#x}')
    count, flags = content[start:start + 2]
    position = start + 2
    parts = []
    safe = 1 <= count <= 127
    for part_index in range(count):
        if position + 6 > limit:
            raise ValueError(f'truncated frame part {part_index} at {position:#x}')
        horizontal, vertical, width, height = struct.unpack_from('>hhBB', content, position)
        position += 6
        size = 2 * width * height
        if position + size > limit:
            raise ValueError(f'truncated tile grid at {position:#x}, size {size}')
        tiles = list(struct.unpack_from(f'>{width * height}H', content, position))
        parts.append({'x': horizontal, 'y': vertical, 'width': width,
                      'height': height, 'tiles': tiles})
        position += size
        safe = safe and width > 0 and 1 <= height <= 32
    return {'start': start, 'end': position, 'flags': flags,
            'parts': parts, 'consumer_safe': safe}


def frame_bank(content, start=FRAME_START, end=FRAME_END):
    frames = []
    position = start
    while position < end:
        frame = parse_frame(content, position, end)
        frames.append(frame)
        position = frame['end']
    if position != end:
        raise ValueError(f'frame bank ends at {position:#x}, expected {end:#x}')
    return frames


def sprite_tile(first, second, index):
    if len(first) != len(second) or len(first) % 64:
        raise ValueError('C-ROM halves must have equal, 64-byte-aligned lengths')
    if index < 0 or (index + 1) * 64 > len(first):
        raise ValueError(f'sprite tile index out of range: {index}')
    origin = index * 64
    pixels = []
    for row in range(16):
        output = []
        for column in range(16):
            offset = origin + (32 if column < 8 else 0) + row * 2
            bit = column & 7
            planes = (first[offset], first[offset + 1], second[offset], second[offset + 1])
            output.append(sum(((value >> bit) & 1) << plane
                              for plane, value in enumerate(planes)))
        pixels.append(output)
    return pixels


def fix_tile(content, value):
    if value < 0:
        raise ValueError('negative fix tile value')
    origin = (value & 0xfff) * 32
    if origin + 32 > len(content):
        raise ValueError(f'fix tile index out of range: {value & 0xfff}')
    pixels = [[0] * 8 for _ in range(8)]
    for block, column in enumerate((4, 6, 0, 2)):
        for row in range(8):
            packed = content[origin + block * 8 + row]
            pixels[row][column] = packed & 15
            pixels[row][column + 1] = packed >> 4
    return pixels


def palette_records(content):
    if len(content) % 64:
        raise ValueError('palette bank must contain complete 64-byte palettes')
    return {'palettes': len(content) // 64, 'color_records': len(content) // 4,
            'invalid_channels': sum(value > 31 for offset, value in enumerate(content)
                                    if offset % 4 != 3),
            'nonzero_padding': sum(value != 0 for value in content[3::4])}


def hole_objects(content, header):
    if header < 0 or header + 10 > len(content):
        raise ValueError('truncated hole header')
    position = struct.unpack_from('>I', content, header + 6)[0]
    objects = []
    while position < len(content) and content[position] < 128:
        if position + 6 > len(content):
            raise ValueError('unterminated hole object list')
        kind, flags, horizontal, vertical = struct.unpack_from('>BBhh', content, position)
        objects.append({'type': kind, 'flags': flags, 'x': horizontal, 'y': vertical})
        position += 6
    if position >= len(content):
        raise ValueError('unterminated hole object list')
    return objects


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=pathlib.Path,
                        default=REPO / 'build' / 'analysis' / 'semantic-assets.json')
    args = parser.parse_args()
    fixed_path = REPO / 'build' / 'work' / 'p1.fixed.cpu.bin'
    content = fixed_path.read_bytes()
    frames = frame_bank(content)
    parts = [part for frame in frames for part in frame['parts']]
    tiles = [tile for part in parts for tile in part['tiles']]
    assets = {name: REPO / 'build' / 'assets' / name
              for name in ('200-c1.c1', '200-c2.c2', '200-s1.s1')}
    first = assets['200-c1.c1'].read_bytes()
    second = assets['200-c2.c2'].read_bytes()
    fixed = assets['200-s1.s1'].read_bytes()
    sprite_tile(first, second, 0)
    fix_tile(fixed, 0)
    objects = []
    for course in range(4):
        directory = struct.unpack_from('>I', content, 0x57b44 + course * 4)[0]
        for hole in range(18):
            header = struct.unpack_from('>I', content, directory + hole * 24)[0]
            objects.append(hole_objects(content, header))
    report = {
        'frame_bank': {'start': hex(FRAME_START), 'end': hex(FRAME_END),
                       'frames': len(frames), 'parts': len(parts),
                       'tile_words': len(tiles), 'maximum_tile_word': max(tiles, default=0),
                       'unsafe_frame_offsets': [hex(frame['start']) for frame in frames
                                                if not frame['consumer_safe']],
                       'part_count_histogram': dict(collections.Counter(len(frame['parts']) for frame in frames)),
                       'height_histogram': dict(collections.Counter(part['height'] for part in parts)),
                       'flag_histogram': dict(collections.Counter(frame['flags'] for frame in frames))},
        'palette_bank': palette_records(content[FRAME_END:PALETTE_END]),
        'course_object_prerequisites': {
            'holes_checked': len(objects),
            'missing_type_zero': sum(not any(record['type'] == 0 for record in records)
                                     for records in objects),
            'missing_type_zero_after_first': sum(
                not any(record['type'] == 0 for record in records[1:]) for records in objects),
            'minimum_records': min(map(len, objects)),
            'maximum_records': max(map(len, objects))},
        'graphics': {'sprite_tiles': len(first) // 64, 'fix_tiles': len(fixed) // 32,
                     'frame_tile_words_beyond_asset': sum(tile >= len(first) // 64 for tile in tiles)},
        'sha256': {str(path.relative_to(REPO)): hashlib.sha256(path.read_bytes()).hexdigest()
                   for path in (fixed_path, *assets.values())},
        'qualification': 'Contiguous-bank format validation and codec layout, not all pointer '
                         'consumers, live bounds, visual identities or whole-game completion.'}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
