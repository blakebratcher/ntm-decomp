# ORIGIN: was Neo Turf Masters' 68000 program compiled from C?


**Verdict: hand-written assembly (high confidence). No C compiler
fingerprint survives the census, in NTM or in any other sampled Neo Geo
title from 1990-2001. "Matching C" is therefore not a meaningful goal for
this decompilation; the C files remain semantic models.**

Evidence: the lane tool (authorship metrics over the
verified byte-exact corpus, plus a per-span unidasm pass for byte
addresses) and the same tool run as a linear sweep over other Neo Geo
sets. Evidence JSONs are not published. Date: 2026-10-11.

## 1. NTM corpus census (1,298 sources, 31,065 instructions)

The corpus is the 1,113 function sources + 185 reachable blocks, parsed
from the verified `.s` texts (branch encodings are byte-exact; inline
`dc.*` data stays visible).

### 1.1 No C calling convention exists anywhere

| Metric | Count | Compiler would show |
|---|---|---|
| `link` instructions | **0** | 1 per function with locals |
| `unlk` | **0** | 1 per function with locals |
| `rtd` | **0** | callee-cleanup ABIs |
| reads via `(d>0,a7)` | **2, in 1 function** | 1+ per argument |
| writes via `(d>0,a7)` | **2, in 1 function** | frame locals |
| caller stack cleanup after calls | **0** | 1 per call (cdecl) |
| `a7_adjust` (addq/lea to a7) | **0** | frame teardown |
| `(d,a6)` accesses | 4,419 (14.2% of insns) | frame pointer = a6 *after link* |

The two `(2,a7)` accesses live in the P1 corpus, the
32x32 multiply library: `($2,a7)` is a hand-made scratch slot for the
multiply's carry word, pushed with `move.l d2,-(a7)` and popped with
`(a7)+` — a scratch idiom, not an argument frame. A per-span unidasm sweep
of the *whole* ROM (including the 35 KB of unclassified bytes) finds 11
`(d,a7)` reads total: these 2 real scratch reads, 1 inside a 16-bit data
table in a gap (CPU 0x0f2d0), and 8 fill/table bytes (`0f0f 0f0f` fill
at 0x56fb6/0x7a0be, table words at 0x57892/0x68164/0x6c51a) inside
padded function extents and data-region spans. **Zero stack-argument
reads in the ROM.**

`a6` is the object/context pointer convention: 413 functions read a6 as
their very first register use and immediately dereference fields
(`movea.l ($8,a6),a1`). 88 functions touch a5 at all; a3/a4 are nearly
unused (83 reads total for a4). Arguments travel in registers, spread
across the whole file: first-use-before-def reads are a6 (413), d0 (298),
d4 (218), a2 (206), d1 (162), d6 (158), a0 (156), d2 (140), d5 (113),
d7 (95), d3 (86), a1 (81) — a register-allocation profile, not a C ABI.
The caller side mirrors it: registers written in the straight-line
window before each call are d0 (846), d1 (715), a1 (601), a6 (416),
a0 (382), d2 (206), d6 (142), d5 (135), d3 (134), d4 (122), d7 (81),
a5 (70) — value args in d0/d1, pointers in a0/a1, context in a6, the
rest as scratch, symmetric with the callee-side consumption.

### 1.2 No compiler prologue/epilogue shapes

- entry instructions are heterogeneous: `move` 376, `lea` 127, `jsr` 119,
  `movea` 109, `moveq` 106, `tst` 92 ... — no uniform prologue exists.
- `movem` total 452, but only **47 entry saves**, with scattered masks
  (`d1/d0` x10, five full-register interrupt handlers, singletons). Only
  16 of 47 are even approximately compiler-style contiguous
  callee-save blocks (d2+/a2+); the five `a0-a6/d0-d7` saves are
  interrupt handlers. Compilers save d2-d7/a2-a5 as a block in every
  function that uses them.
- last instructions: `rts` 720, `jmp` 252, `bra` 164 (tail calls),
  `trap` 13, and 77 functions end in a *non-terminator* (`move` etc.) —
  i.e. flow runs into the next routine. 119 adjacent functions are
  contiguous with the predecessor ending in a non-terminator
  (fall-through / Ghidra split points), and 223 control transfers from
  one function land *strictly inside* another (e.g. `sprite_node_render`
  at 0x878 and its Ghidra-split continuation at 0xa00 cross-jump into
  each other's bodies: shared tails, multiple entries — the classic
  hand-asm structure Ghidra's function splitter cannot represent).
- 208 of 1,298 sources (16%) contain inline `dc.*` data (strings,
  pointer tables, the 0x56f90 `move.b ($1a,pc,d1.w),d0` lookup tables).

### 1.3 No compiler code-generation idioms

| Idiom | NTM count | Compiler would show |
|---|---|---|
| `move.w X,dn` + `ext.w/ext.l dn` (16->32 promotion) | **1** | 1 per short load |
| `moveq #0,dn` + `scc dn` (bool lowering) | **0** | 1 per boolean |
| `tst` immediately after `move` to same reg | 25 | many (flag churn) |
| switch double-table (`add.w dn,dn; move.w (pc,dn.w); jmp (pc,dn.w)`) | 0 | 1 per switch |
| `mul` (fixed-point math, not array indexing) | 112 | — |
| `swap`-based 32-bit math | 220 | rare in compilers |
| computed `jmp (a0)` / `jmp (a0,d0.w)` dispatch | 24 | jump tables, different shape |
| `dbra`-family loops | 143 (all dbcc variants) | pattern-matched only |

### 1.4 Branch-size discipline

From the per-span unidasm pass: 442 short branches vs 4,025 word
branches, and **3,474 of the word branches (86%) have displacements that
fit in a signed byte** — but the split is sharply directional: of the
word branches, **3,460 are forward and shortable** while only 14
backward word branches are shortable; of the short branches, 260 go
backward and 182 forward. I.e. forward branches are word-sized 94% of
the time even when a byte would do (the divide library's own loop uses
`bcs.w +6`), while backward branches are short 65% of the time when
shortable. That asymmetry is the signature of an assembler that can size
backward references as it goes but defaults forward references to `.w`
(author habit or a non-back-patching assembler) — a C compiler with
linker relaxation would shrink all shortable branches; one without would
emit ~0 short branches. Neither matches. The 442 short branches are
scattered across 228 sources (max 20 per function) with no cluster.

### 1.5 Structural summary

- alignment: 687 functions at 4-byte, 611 at 2-byte-only boundaries,
  0 odd — no alignment padding convention (vbcc's `cnop 0,4` would have
  padded the 2-mod-4 entries, consistent with the recorded vbcc negative).
- calls: 2,887 = 2,051 absolute-long `jsr` (71%) + 699 PC-relative
  `jsr` + 120 `bsr` + 17 register calls. Fixed-base absolute linkage is
  the house norm (the cross-ROM table: the Nazca line runs 72-86%
  absolute with a 4-12% `bsr` share).
- 111 call sites branch on the callee's condition codes immediately
  after the call (flags as a return channel) — an assembler-library
  contract, not a C ABI.

## 2. Cross-ROM census (linear sweep, RTS-segmented, clean filter)

Same metrics over `-p1`-family members of other sets, word-swapped,
unidasm-decoded. Sanity gate: members with >25% illegal words are flagged
encrypted and excluded (kof99, garou, mslug3 all fail: 0-19 `rts` in
~0.6-1.7M decoded words — they are SMA/CMC-encrypted and out of scope by
the no-decryption rule). Limitation: no symbol boundaries; segments are
RTS-delimited and a 5%-illegal filter drops data runs; per-segment stats
are comparative, not authoritative. Per 1,000 instructions:

| Set | Year | Insns | link | unlk | caller cln | a7 reads | movem | scc | dbcc | mul | swap | bool pair | ext+ | % short br | % abs jsr |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| maglord | 1990 | 33,277 | 2 | 2 | 8 | 6 | 12.7 | 0.0 | 8.7 | 3.7 | 5.6 | 0 | 0 | 46% | 76% |
| 2020bb | 1991 | 25,461 | 0 | 0 | 0 | 3 | 2.7 | 0.0 | 7.4 | 4.1 | 6.1 | 0 | 0 | 71% | 50% |
| samsho4 | 1996 | 104,156 | 3 | 0 | 0 | 38 | 5.8 | 0.6 | 3.5 | 3.4 | 2.0 | 0 | 0 | 53% | 77% |
| **turfmast** | **1996** | **31,728** | **0** | **0** | **0** | **11*** | **14.3** | **1.8** | **4.4** | **3.4** | **6.7** | **0** | **0** | **9.5%** | **72%** |
| mslug2 | 1998 | 101,577 | 0 | 0 | 0 | 68 | 6.4 | 1.6 | 0.4 | 1.5 | 3.8 | 0 | 0 | 4.5% | 77% |
| mslugx | 1998 | 169,352 | 0 | 0 | 1 | 162 | 4.9 | 12.6 | 0.4 | 1.4 | 6.3 | 0 | 0 | 3.4% | 78% |
| kof98 | 1998 | 74,765 | 0 | 0 | 0 | 54 | 2.9 | 0.5 | 2.3 | 1.0 | 2.1 | 0 | 0 | 51% | 48% |
| lastbld2 | 1998 | 86,243 | 8 | 2 | **899** | 60 | **91.7** | 0.0 | 4.5 | 2.1 | 3.4 | 0 | 0 | 59% | 77% |
| sengoku3 | 2001 | 48,388 | 0 | 0 | 0 | 7 | 5.5 | 0.8 | 0.2 | 0.4 | 4.8 | 0 | 0 | 4.8% | 86% |
| zupapa | 2001 | 34,292 | 0 | 0 | 0 | 83 | 2.7 | 0.1 | 3.8 | 1.1 | 0.3 | 0 | 0 | 8.2% | 53% |

(\* the 11 turfmast sweep hits are the 2 multiply-scratch reads plus
fill/table misdecodes; byte-level classification in 1.1.)

Readings:

- **No title in the sample, 1990-2001, has a C calling convention.**
  `link`/`unlk` are 0-8 per 25-170k instructions everywhere, caller
  cleanups 0-8 everywhere except lastbld2's 899, and the two universal
  compiler idioms (`moveq+scc` bools, `move.w+ext` promotion) are
  **zero in every title**.
- The one partial exception is **Last Blade 2 (1998)**: 8 link frames,
  899 caller stack cleanups, 91.7 movem per 1,000 insns — a small,
  self-contained stack-call subsystem (an in-house C compiler's output
  for some subsystem is plausible there; it is a research lead, not a
  proof, and it is *not* present in NTM).
- **NTM is a textbook member of the Nazca house style**: 72-86%
  absolute `jsr` with a small `bsr` share (4-12%), a6-relative object
  access (13.5% of instructions), `.w`-default branches (~9% short),
  callee-sets-flags contracts, register arguments,
  shared-tail/multi-entry routines. mslug2/mslugx/sengoku3 (same
  lineage) cluster with it; SNK main line (maglord/samsho4/kof98)
  differs in branch ratio and base-register choice but is equally
  register-convention.
- Cross-ROM corroboration of the shared divide kernel (DIVIDE_ANALYSIS.md)
  now reads as a **carried-over hand-asm library routine**, not a compiled
  runtime call target.

## 3. What this implies for the C requirement

1. **"Matching C" is ill-posed for this ROM.** There is no calling
   convention, prologue, register-allocation scheme or code-generation
   idiom that a 1990s m68k C compiler emits, so no compiler output can
   byte-match these functions in aggregate. The existing vbcc negative
   (docs/TOOLING.md) is now explained: it is not a vbcc limitation, it is
   the absence of any C in the ROM.
2. The C files under the semantic-model corpus remain **semantic models**, as already
   documented; the assembly corpus stays authoritative. The measured
   matching-C attempt (GCC, stock vbcc, and a purpose-patched vbcc backend;
   ten screened candidates; 0 MATCH) is in `docs/C_MATCHING.md`. No
   per-function compiler override or toolchain hunt is warranted
   (DIVIDE_ANALYSIS.md's open thread closes with this census).
3. Future lanes should treat the Nazca house style as the reference:
   register arguments (a6 = context, d0/d1/d4 = values), flags as a
   return channel, `.w` branches by default, absolute-long linkage, no
   frames. Recovered code that violates these conventions deserves a
   second look at its boundary evidence.

## 4. Caveats

- Cross-ROM figures come from linear sweeps without symbol boundaries;
  RTS segmentation and the 5%-illegal filter exclude data runs but can
  drop unusual code too. They are comparative, not authoritative per
  function.
- Encrypted programs (kof99, garou, mslug3, kof2000+) were excluded by
  rule; if a C compiler was adopted at SNK it was after the sampled
  window or in a subsystem smaller than this census can resolve.
- The per-function metrics are linear-order approximations (first/last
  mention), fine for ABI-level claims, not a dataflow proof.
