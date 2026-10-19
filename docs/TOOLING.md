# Toolchain pins and provenance


Everything is pinned and checksummed; nothing third-party is vendored in the
repo. Build-time fetches live under `tools/` (gitignored).

## vasm 2.0f — 68000 assembler

- Source: `http://sun.hasenbraten.de/vasm/release/vasm.tar.gz` (daily snapshot
  of vasm **2.0f**, (c) 2002–2026 Volker Barthelmann).
- SHA-256 of the pinned snapshot: `c84b2de1cbb87831795fe64a85c5d9a7002a766e3a7c30b0a2d7d5e99d878f49`
  (pinned in `tools/setup_vasm.sh`).
- Build: `make CPU=m68k SYNTAX=mot` → `vasmm68k_mot` (motosyntax, `-Fbin`).
- License: freeware by Volker Barthelmann; the distribution terms are in the
  bundled manual (`vasm.pdf`, also at the site above). The assembler itself is
  not redistributed by this repo.
- AUR fallback: the `vasm` package (Arch) builds the same source.

## Ghidra 12.1.2 — analysis source of truth

- Installed at `/opt/ghidra` (Apache-2.0). The companion RE workspace
  (the companion RE workspace (outside this repository)) drives it headlessly:
  `ghidra/run.sh` rebuilds the project from the P-ROM wrapped as a 68000 ELF
  (the companion RE workspace (not in this repository)), `ghidra/scripts/` holds the analyzers, and the
  per-function exports land in `out2/asm_all/` and `out2/decomp_all/`.
- `tools/ghidra_import.py` consumes the listings; Ghidra itself never runs
  inside this repo.

## MAME 0.289 — acceptance and cross-checks

- Installed at `/usr/bin/mame` (`mame0289-dirty`, local build); `unidasm`
  (part of MAME, BSD-3-Clause / GPL-2.0) disassembles with
  `unidasm <file> -arch m68000 -basepc <pc>`.
- Headless, isolated runs follow the workspace harness pattern
  (the companion RE workspace (outside this repository)): `-video none
  -sound none` under `xvfb-run`, scratch cfg/nvram dirs, ROM path
  `/path/to/roms`, BIOS from `$NEOGEO_BIOS`.
  Acceptance runs for rebuilt ROMs will point `-rompath` at the rebuild
  instead.

## m68k-elf GCC — phase-3 C recompilation

- AUR packages `m68k-elf-gcc` (16.1.0) + `m68k-elf-binutils` (2.46.1),
  GPL. All four pieces build from source (~40-70 min total on the studio
  rig); install them stepwise to dodge pacman's inner conflict between
  `m68k-elf-gcc` and its `-bootstrap` split package:

      gpg --keyserver keyserver.ubuntu.com --recv-keys 738409F520DF9190  # binutils release key
      paru -S --noconfirm m68k-elf-binutils
      paru -S --noconfirm m68k-elf-gcc-bootstrap
      paru -S --noconfirm m68k-elf-newlib
      paru -R --noconfirm m68k-elf-gcc-bootstrap
      paru -S --noconfirm m68k-elf-gcc

  (`m68k-elf-gcc-bootstrap` is only a makedep of newlib; the final gcc
  installs cleanly once it is gone.)
- Not required for `make check`; only for `make c-check`
  (tools/build_c.py). C attempts are judged by the same byte gate as the
  assembly path. **ABI caveat (verified with GCC 16.1.0): m68k GCC has no
  register-args option** (no `-mregparm` equivalent) — the ROM passes
  arguments in D-registers, so GCC output cannot byte-match the calling
  convention. Byte-matching C would need a compiler with configurable ABIs
  (vbcc is the leading candidate); until then C attempts document semantics
  and the assembly stays authoritative.

## vbcc / vlink — the byte-matching C path (experimental)

- Source snapshots: `phoenix.owl.de/tags/vbcc.tar.gz`
  (sha256 `1f07497c76125eee1f40414779fb9c9aa547cc2a413b73d5b5adb31f9146bcbb`)
  and `vlink.tar.gz`
  (sha256 `8d151cdd30a4feb575a364e68810c2bc300fe1a7c074dbbee6fd1175a6c5bfae`);
  built by the C-matching harness. vbcc/vlink are freeware by Volker
  Barthelmann — terms in the sources and at <http://sun.hasenbraten.de/vbcc/>.
- Why: vbcc supports **register parameters** (`__reg("d0")`) — the ROM's
  calling convention — which m68k GCC cannot express (see phase 3 above).
- Build quirks recorded: the snapshot's Makefile ships a stale
  `CC = … -DHAVE_AOS4` (override it) and dtgen's cross-compiler prompt must
  be answered NO (the snapshot has no host description files).
- Not required for `make check`; `make c-check` remains GCC-based by default.
  The `--compiler vbcc` path uses the vendored backend.
- **2026-10-18 spike:** a purpose-patched backend adds opt-in
  `#pragma naked on` / `#pragma noalign on`
  (the C-matching harness, applied only when
  `NTMD_VBCC_NAKED=1 sh the C-matching harness`). All ten screened candidates
  were attempted with it: **0 MATCH, 10 DIFF** on the byte gate. The patch
  removes the prologue and alignment blockers; the remaining failures are
  save-list composition, call-site saves, register allocation and missing
  idioms. See the lane report and `docs/C_MATCHING.md`.

### Why the tested vbcc pipeline cannot byte-match these leafs (measured 2026-10-11)

A probe swarm swept the four physics-core attempts (0x27c6, 0x2908, 0x2c20,
0x74c0) across opt levels 0-4, pragmas and dozens of C shapes, tracing every
blocker into the backend source
(`tools/vbcc-src/vbcc/machines/m68k/machine.c`):

- `cnop 0,4` is emitted unconditionally before every function label
  (machine.c:930); for any org that is 2 mod 4 the compiled span starts with
  a NOP pad the ROM does not have (0x27c6).
- Every function opens with a mandatory `movem.l lN,-(a7)` entry save
  (machine.c:982-985); an empty list still assembles to `48e7 0000` under
  vasm `-no-opt`, so a compiled span can never begin with the ROM openings
  (`2200`, `4a80`, `7e00`, `2039`): byte 0 never matches.
- Idioms with no emission path at all: `bpl` (the backend only has bge),
  `bsr` (C calls are always absolute-long `jsr`), `addi.l` immediates,
  `not.b` byte toggles, word-sized register push/pop, `addx.w` with a 16-bit
  middle carry, and forced loop unrolling.
- Register allocation is not steerable: locals land in callee-saved d2+ or
  frame slots; the ROM's in-place d0/d1 folds with a single scratch register
  cannot be requested from C.

Consequence: 1,091 of the 1,103 inventory functions cannot match at byte 0
by construction. The ten screened candidates whose ROM opening could
coincide with vbcc's skeleton (0x00d7ec, 0x0616e8, 0x06dc44, 0x06dd50,
0x083c24, 0x0844f8, 0x085e04, 0x0b986c, 0x0b9b4c, 0x0b9d20) were all
attempted in the 2026-10-18 spike with the patched backend; none matched
(the closest is 0x844f8 at 39/40 bytes, `rte` vs `rts`). The semantic-model files
stand as semantic models: four phase-3 attempts and a verified suite of
pure-function models (see `docs/C_MATCHING.md` and the semantic-model corpus).
`make c-check` still gates attempts and now also rejects asm/embed smuggling,
zero-length spans, and spans shorter than the inventory size.

Scope note: this negative is about **vbcc-as-built**. It does not settle who
generated the code or whether the original toolchain is reproducible — the
0x2908 divide kernel is byte-identical across turfmast, mslug2 and mslugx
(`tools/rom_fingerprint_scan.py`), i.e. a carried-over engine routine. The
open C-matching thread and attack order are in the working notes.

## Python

- `python3` (stdlib only) drives extract / import / build / progress; tested
  with pytest. No third-party Python packages.
- **`tools/corpus.py`** is the form-agnostic view of a P1 source (see
  the lane report): a source exists in two byte-identical
  forms - raw Ghidra-listing style and the `tools/symbolize.py --write`
  form (symbols.i include, symbolic operands, `; name:` entry markers).
  Tools and tests that read sources for orgs, labels, assembled bytes or
  operand addresses go through corpus.py (`org_of`, `labels`,
  `entry_label`, `assemble`, `listing`, `load_symbols`, `canonicalize`,
  `line_addrs`, `iter_lines`, `refs`) so both forms answer identically.
  `canonicalize` resolves globals and `l_XXXX` locals back to raw hex
  text-only; kept live labels (39 files) need a per-file `listing` for
  intra-file resolution.
- **`make symbolize`** runs `tools/symbolize.py --write`: it byte-gates a
  scratch rewrite first and only then publishes symbols.i and the rewritten
  sources.  Apply it once, last, after the consistency pass; re-running it
  is a no-op (idempotency is tested).  Every vasm invocation that assembles
  sources (build_p1, corpus, p1_recover.assemble_source, the lane listing
  helpers) passes `-I the rebuild` so the symbolized form assembles.

## Prior art consulted

- No existing matching decomp of Neo Turf Masters (or another Neo Geo title)
  was found as of 2026-10-10 — this repo appears to be the first of its kind
  for this game.
- Adjacent resources: `zengfr/arcade-romhacking-framework` (68k/Neo Geo
  romhacking framework), the NeoGeoSDK homebrew SDK (m68k toolchain notes,
  byte-identical ROM builds), `Clownacy/clown68000` and `Stovent/m68000`
  (68k emulation/disassembly libraries), the NeoGeo Development Wiki
  (memory map and [sound-driver
  inventory](https://wiki.neogeodev.org/index.php?title=Sound_drivers_list)),
  and MAME's driver source (primary).

### Follow-up evidence audit

See [DIVIDE_ANALYSIS.md](DIVIDE_ANALYSIS.md): the 809-byte cross-ROM run is
not the whole function. Entries and zero-divisor branches differ; the
782-byte body and relocated signed wrapper match. Original source language
is unresolved. Numeric C models do not cover the exception/ABI contract.
`make c-check` now returns failure on compiler/link/objcopy errors too.
`make progress` derives unique coverage from a fresh byte-gated manifest;
the inventory audit records unresolved extent differences.

## Third-party tooling licenses

| Tool | Version | License | Use |
|---|---|---|---|
| vasm | 2.0f | freeware, (c) 2002–2026 Volker Barthelmann — terms in the manual at <http://sun.hasenbraten.de/vasm/> | 68000 assembler |
| Ghidra | 12.1.2 | Apache-2.0 | analysis source of truth (runs outside this repo) |
| MAME (incl. `unidasm`) | 0.289 | BSD-3-Clause / GPL-2.0-or-later | acceptance runs, disassembly cross-checks |

Tools are fetched at build time and pinned by checksum; they are not vendored
in this repository. Pins and provenance are in the sections above.
