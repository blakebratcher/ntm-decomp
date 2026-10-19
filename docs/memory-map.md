# Neo Geo memory map (as used by Neo Turf Masters)


Primary source: MAME 0.289, `src/mame/snk/neogeo.cpp`
(local copy used for line citations: `a local copy of the MAME 0.289 source`, fetched
2026-10-10; the same lines are in the upstream tree at tag `mame0289`).

## P-ROM file layout

`200-p1.p1` is 2 MiB. Per the driver's
`ROM_LOAD16_WORD_SWAP("200-p1.p1", 0x100000, 0x100000, ...)` +
`ROM_CONTINUE(0x000000, 0x100000)` (`ROM_START(turfmast)`, neogeo.cpp:6857):

- file bytes `[0x000000, 0x100000)` → banked program (appears at CPU 0x200000)
- file bytes `[0x100000, 0x200000)` → fixed program (CPU 0x000000)
- every 16-bit word is byte-swapped on disk relative to 68000 order
  (MAME's `ROM_LOAD16_WORD_SWAP` convention).

Validation: in CPU order the header `NEO-GEO` sits at 0x100, the initial SP is
0x0010F300, the reset PC 0xC00402 (BIOS), and the USER vector (0x122) JMPs to
0xE36E. The workspace ELF wrapper (the companion RE workspace (not in this repository) in the companion RE
workspace) asserts exactly this.

## 68000 address map

| Range | Size | Name / notes | Source |
|---|---|---|---|
| 0x000000–0x00007F | 128 B | vectors, served by `banked_vectors_r` | `neogeo_main_map` (ngarcade) |
| 0x000080–0x0FFFFF | 1 MiB − 128 B | **fixed P-ROM** | cart install, neogeo.cpp:1407/1409 |
| 0x100000–0x10FFFF (mirror ×16) | 64 KiB | **work RAM** | `map(0x100000, 0x10ffff).mirror(0x0f0000).ram()` |
| 0x200000–0x2FFFFF | 1 MiB | **banked P-ROM window** | `install_read_bank(0x200000, 0x2fffff, m_bank_cartridge)`, neogeo.cpp:1412 |
| 0x2FFFF0–0x2FFFFF | 16 B | **bank select / protection latches** | `install_write_handler(..., write_banksel)`, neogeo.cpp:1413 |
| 0x300001 (mirror ×0x1FFFE) | — | watchdog reset | `neogeo_main_map` |
| 0x300080 (mirror) | — | TEST port | `neogeo_main_map` |
| 0x320000 | 2 B | audio command to Z80 | `audio_command_w`, base_main_map |
| 0x360000–0x37FFFF | — | unmapped | base_main_map |
| 0x380000–0x3800FF (mirror) | — | system registers (`REG_P1CNT` etc.) | base_main_map |
| 0x3A0000–0x3A001F (mirror) | — | system control latches (HC259; `REG_PALBANK` etc.) | base_main_map |
| 0x3C0000–0x3C000F (mirror) | — | LSPC video registers (`REG_VRAMADDR`, `REG_VRAMRW`, `REG_VRAMMOD`, …) | base_main_map |
| 0x3E0000–0x3FFFFF | — | unmapped | base_main_map |
| 0x400000–0x401FFF (mirror) | 8 KiB | **palette RAM** | base_main_map |
| 0x800000–0x800FFF | — | memory card (AES) | `neogeo_main_map` |
| 0xC00000–0xC1FFFF (mirror ×8) | 128 KiB | **system BIOS** | `neogeo_main_map` |
| 0xD00000–0xD0FFFF (mirror) | 64 KiB | **backup RAM** (saveram) | `neogeo_main_map` |
| 0xE00000–0xFFFFFF | — | unmapped | `neogeo_main_map` |

## Banking model

`write_banksel(data)` (neogeo.cpp:1141):

- `bank = data & 0x07`; the banked bytes come from P-ROM region offset
  `(bank + 1) * 0x100000`.
- A bank request past the end of the ROM is logged and forced to bank 0.
- This cart's 2 MiB P-ROM holds exactly one 1 MiB bank (region offset
  0x100000), so the 0x200000–0x2FFFFF window always shows that bank and the
  latches are effectively unused by the game.

This matches the layout used by the Ghidra ELF wrapper (fixed at 0x000000,
bank at 0x200000), which is why banked code is addressed at 0x2xxxxx in
listings and sources.

## Z80 (M1) side

The 128 KiB `200-m1.m1` is mapped for the Z80 with banking handled by the
cart's NEO-ZMC-compatible logic (neogeo.cpp audio map); the Z80 drives the
YM2610 and streams the V-ROM samples. See the working notes phase 4.
