import math
import pathlib
import struct

REPO = pathlib.Path(__file__).resolve().parents[1]


def test_boot_mid_instruction_value_is_not_a_named_entry():
    addresses = set()
    for path in (REPO / 'config' / 'names').glob('*.tsv'):
        for line in path.read_text().splitlines():
            if line.strip() and not line.lstrip().startswith('#'):
                addresses.add(int(line.split('\t')[0], 16))
    assert 0xd696 not in addresses


def test_apparent_boot_pointer_is_quarter_sine_value():
    import pytest
    path = REPO / 'build' / 'work' / 'p1.fixed.cpu.bin'
    if not path.exists():
        pytest.skip('run make extract')
    rom = path.read_bytes()
    for address in (0x1994, 0x1998, 0x199c):
        index = (address - 0xf78) // 4
        value = struct.unpack_from('>I', rom, address)[0]
        assert value == round(65536 * math.sin(index * math.pi / 2048))
    assert struct.unpack_from('>I', rom, 0x1998)[0] == 0xd696
