import pathlib
import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'tools'))
from p1_recover import flow, parse_listing


def test_listing_continuation_is_part_of_instruction_not_a_new_entry():
    text = ('00:000075AE 23FC000100000010\t 11: move.l #$10000,($102c64).l\n'
            '00:000075B6 2C64\n'
            '00:000075B8 203900102C70\t 12: move.l ($102c70).l,d0\n')
    assert parse_listing(text)[0x75ae][0] == 10
    assert 0x75b6 not in parse_listing(text)
    assert flow(0x75ae, 10, 'move.l #$10000,($102c64).l') == ([0x75b8], False)


def test_raw_data_is_not_a_flow_root():
    assert parse_listing('00:00001000 4E754E75\t 5: dc.b $4e,$75,$4e,$75\n') == {}


def test_direct_and_indirect_flow():
    assert flow(0x100, 4, 'bsr.w $200') == ([0x104, 0x200], False)
    assert flow(0x100, 4, 'bra.w $200') == ([0x200], False)
    assert flow(0x100, 4, 'dbf d0,$200') == ([0x104, 0x200], False)
    assert flow(0x100, 2, 'jmp (a0)') == ([], True)
    assert flow(0x100, 2, 'jsr (a0)') == ([0x102], True)
    assert flow(0x100, 4, 'jmp ($20,pc,d0.w)') == ([], True)
    assert flow(0x100, 2, 'rts') == ([], False)
