# Matching C and this decompilation


This document answers two questions that come up together:

1. *Is this a real decompilation, or just a disassembly?*
2. *Isn't it incomplete because the C matching isn't done?*

The short answer: this is a byte-exact **assembly** decompilation, because the
original program is hand-written assembly. Matching C was not assumed to be
impossible — it was measured. Both available m68k compilers were tried, and a
patched vbcc backend was built specifically to remove its prologue and
alignment defaults. All ten screened candidates still fail the byte gate. In
parallel, a verified suite of semantic C models now covers the pure functions
where readable C is actually useful. The byte classification and semantic
review are complete on their own terms; matching C is a separate research
problem, not a missing checkbox.

## Two standards

The word "decompilation" covers two different deliverables:

| Standard | Claim | This project |
|---|---|---|
| **Matching C** (the SM64-style standard) | A C source compiles, with a particular compiler and flags, to the original bytes. | Not achieved; measured and structurally blocked in the available backends. |
| **Byte-exact assembly reconstruction** (this project) | Every byte has one owner and one evidence-backed classification; all code is recovered as named, commented assembly that reassembles to the original image. | Complete: 2,097,152 / 2,097,152 P1 bytes and 131,072 / 131,072 M1 bytes classified, zero unknown, zero open evidence. |

The two standards answer different questions. The first asks "can this code be
expressed in C and recompiled identically?" The second asks "is the program
fully recovered, understood and reconstructible?" This project satisfies the
second and now measures the first.

## Why matching C is not the natural target here

[docs/ORIGIN.md](ORIGIN.md) is the authorship census. In summary, over the
31,065 instructions of the recovered P1 corpus:

- **0** `link` / `unlk` instructions, **0** stack-argument reads, **0** caller
  stack cleanups, **0** `moveq`+`scc` boolean lowerings, **0** `move.w`+`ext`
  sign promotions — the universal m68k compiler idioms are absent.
- Arguments travel in registers (a6 is the object/context pointer), flags are
  used as a return channel, and routines share tails and multiple entry points.
- The same census over nine other Neo Geo titles from 1990–2001 finds no C
  calling convention anywhere in the sample.

If no C compiler produced this code, there is no original C source to recover.
A matching-C version would be a *retroactive re-expression* of assembly in C
that happens to compile back to the same bytes — useful research, but not
source recovery. That is the honest framing, and it is why the project's
authoritative artifact is the assembly corpus.

## The measured matching-C attempt

The gate is [tools/build_c.py](../tools/build_c.py): one `<address>.c` per
function, compiled and linked at the real address, then byte-diffed against the
pristine image with the same checks the assembly path uses. A file MATCHes only
when the bytes agree and the span is at least the inventory size; `asm`,
`__asm`, `.incbin` and `#embed` are rejected.

### GCC

`m68k-elf-gcc` has no register-argument ABI (no `-mregparm` equivalent). The
ROM passes arguments in D-registers, so GCC cannot reproduce the calling
convention at all. The four phase-3 attempts (`000027c6`, `00002908`,
`00002c20`, `000074c0`) are 0 MATCH: two are byte DIFFs and two are link
failures because the models call each other and the gate builds one file at a
time.

### Stock vbcc

vbcc supports register parameters with `__reg("d0")`, which is why it was the
leading candidate. Stock vbcc still cannot begin these functions: its m68k
backend emits an unconditional `cnop 0,4` before every function label and a
mandatory `movem.l lN,-(a7)` entry save. The four phase-3 attempts are again
0 MATCH, 4 DIFF, and every one differs at byte 0. The measured backend
limitations are recorded in [docs/TOOLING.md](TOOLING.md).

### Patched vbcc

To separate "the prologue blocks us" from "C cannot express this code", the
vendored backend was patched with two opt-in pragmas:

- `#pragma naked on` suppresses the entry save and frame setup/teardown.
- `#pragma noalign on` suppresses the `cnop 0,4` / `.align 4` pad.

The patch is 8 hunks against `tools/vbcc-src/vbcc/machines/m68k/machine.c`,
captured as the C-matching harness
and applied only when `NTMD_VBCC_NAKED=1` is set for
the C-matching harness. Without the pragmas, the
compiler output is byte-identical to the pristine backend.

All ten candidates that [docs/TOOLING.md](TOOLING.md) had screened as
theoretically reachable were then attempted with the patched compiler. The
result: **0 MATCH, 10 DIFF, 0 compile or assembler failures.**

| Address | Function | Declared | Span | First diff |
|---|---|---:|---:|---|
| `0000d7ec` | `vram_tile_rect_fill` | 60 | 76 | `+$2 got 3c want 1c` |
| `000616e8` | `hole_record_flag1_check` | 32 | 38 | `+$a got 70 want 08` |
| `0006dc44` | `dot_product_16_16` | 30 | 46 | `+$2 got 3f want 50` |
| `0006dd50` | `scroll_strip_spawn` | 122 | 202 | `+$0 got 26 want 48` |
| `00083c24` | `leaderboard_human_hole` | 44 | 56 | `+$3 got 20 want 80` |
| `000844f8` | `card_file_delete` | 40 | 40 | `+$27 got 73 want 75` |
| `00085e04` | `bcd_number_draw` | 258 | 384 | `+$0 got 9e want 48` |
| `000b986c` | `palette_fade_to_colour_start_c` | 36 | 40 | `+$2 got 30 want f0` |
| `000b9b4c` | `palette_slot_alloc_force` | 146 | 154 | `+$2 got 38 want 70` |
| `000b9d20` | `palette_slot_free` | 102 | 124 | `+$2 got 20 want 70` |

The closest is `000844f8` (`card_file_delete`): 39 of 40 bytes match, and the
only difference is the final `rte` versus `rts`. The backend ties the
scratch-register save list to the interrupt return opcode; no C source can have
one without the other.

The remaining blockers are backend code generation, not C source shape:

1. **Save-list composition.** The normal prologue saves
   `regused && !regscratch`; `d0/d1/a0/a1` are hard-wired scratch, and only
   `__interrupt` (which also forces `rte`) unions scratch registers into the
   list. There is no arbitrary save-list override.
2. **Call-site saves.** The ROM saves scratch registers with
   `movem.l list,-(a7)` around a call. vbcc only emits `movem` in a
   prologue/epilogue; it spills singly to a stack slot instead.
3. **Register allocation.** `REGPARM` copies are hoisted to entry, `__reg` is
   ignored for definitions at some optimization levels, and temporaries cannot
   be pinned to an exact register.
4. **Missing idioms.** No `btst`/CCR writes or condition-code returns, no
   general `DBcc`/`DBF` loop recognition, no PC-relative `lea` for numeric
   addresses, no `movem.w regs,abs.l`, no `clr.w` (lowered as `move.w #0`), no
   `bsr` (calls are absolute-long `jsr`), and `bge` where the ROM uses `bpl`.
5. **Naked is not a general frame.** It is usable only for a register-only
   leaf; a function with locals spills over the return address and caller
   frame.
6. **Branch sizing.** The backend emits unsuffixed branches and leaves sizing
   to the assembler; the ROM deliberately mixes `bra.s` and `bne.w`.

The full lane report, including the per-candidate reasoning and the concrete
next steps for anyone continuing the compiler work, is
the lane report.

## The semantic C suite

Matching C is not the only useful kind of C. Alongside the byte gate, the
project now carries a suite of **semantic models**: readable C for individual
functions whose value equivalence is tested against the real ROM, not against
a byte encoding.

Status as of 2026-10-18:

| | Count |
|---|---:|
| New semantic models written and verified | 16 |
| Pre-existing phase-3 attempts (documented DIFF; semantic models) | 4 |
| Candidates identified but not modeled (backlog) | 23 |
| Host differential comparisons (headline runs) | 1,600,772 |
| Real-ROM MAME vectors (headline runs) | 3,776 |

The verified set covers the arithmetic and geometry core (signed, unsigned and
fixed-point multiplies, divides, atan2, a sine-phase helper, vector length),
the RNG and range helpers, club/par/terrain lookups, a camera clamp, a lerp
and a BCD conversion. The roster with per-function counts and status is
the semantic-model corpus; the convention and the
status vocabulary are in the semantic-model corpus

### How a model is verified

Three independent checks, not one:

1. **A separately written Python reference.** The reference is derived from the
   assembly by a different agent than the C model; it is not a translation of
   the model. It encodes the domain (zero/maximum/sign boundaries, 16-bit wrap
   points, dispatch and table limits, trap paths excluded).
2. **Host differential.** The model is compiled with a host `gcc` shim
   (`__reg` defined away), loaded with `ctypes`, and compared exactly at the
   declared width over 100,000 random cases plus an edge corpus. The harness is
   the C-matching harness.
3. **The real ROM.** the C-matching harness boots
   the actual `turfmast` set in MAME, waits for the game program, hijacks the
   68000 in a scratch context (`SR=0x2700`, a scratch stack, a sentinel `rts`),
   runs the real function on the same inputs, and compares every output
   register. Batches are one vector per emulated frame and kick the watchdog;
   a trapping or non-returning vector becomes `TIMEOUT`, never a fabricated
   value.

The harness was validated with negative controls: a deliberately wrong
reference fails, a boot-timeout run reports `boot-fail`, and a divisor-zero
input reports `incomplete`. The two independent demos in the documentation
(`div_fixed_unsigned` 0x2908 and `div_fixed_signed` 0x2c20) passed 1,144 real
ROM vectors before any model used the oracle. The full interface and
limitations are in the C-matching harness.

Every model was then read against its authoritative listing and reviewed
twice. A model is accepted only when both reviews confirm; a false claim in a
header is a real finding even when the executable C arithmetic is exact. The
lane report records the reference-vs-model adjudications and the rejected
claims: the lane report.

### What the suite is not

- It is **not** matching C. None of these files is claimed to compile to the
  ROM bytes.
- It is **not** exhaustive. It covers pure or table-parameter functions where
  an independent reference and a register-level oracle are possible. Functions
  that depend on live work RAM, hardware registers, or callback objects need a
  custom staging script or are outside the current harness. The 23 backlog
  entries in `INDEX.tsv` are exactly that remaining work.
- It is **not** a build input. the rebuild and byte gates
  never read these files; the assembly corpus still builds the ROM.

## What "complete" means here

The byte classification is complete: every P1 and M1 byte has a category and
evidence, the completion gate reports zero uncovered and zero open evidence
bytes, and the rebuilt images match the original SHA-256 pins. The semantic
review is complete with three accepted residuals: 2,391 of 2,394 obligations
reviewed, and the three residuals are whole-program reachability questions, not
unknown code.

None of those numbers depends on matching C. The C suite is an additional
deliverable: readable semantics with a separate, testable correctness claim. It
does not change the byte gates, and the byte gates do not depend on it.

## What would change the verdict

Matching C could become reachable if a compiler or backend gained:

- an explicit entry/exit save-list override, decoupled from the return opcode;
- a call-site save region (`movem.l list,-(a7)` around a call);
- exact-register binding for temporaries at every optimization level;
- the missing instruction emitters (`btst`/CCR, `DBcc`/`DBF`, PC-relative
  `lea`, `movem.w` to absolute, `clr.w`, `bsr`, `bpl`);
- two-pass branch/DBcc sizing.

That is a compiler project, not a source-writing project. The spike's next-step
list is ordered by leverage in
the lane report. A second route
would be evidence of the original toolchain; the authorship census makes that
unlikely, and no compiler fingerprint survives in the sampled titles.

Even with a perfect backend, the result would not be "the recovered C source."
It would be a C re-expression constructed to fit hand-written assembly. That
distinction is the honest answer to "is the C matching done?": for this title,
there is no original C matching to finish.

## Reproduce

The C-matching harness, models and raw evidence are not published and
are not distributed; the measured results above are the public record.

## Files

| Artifact | What it is |
|---|---|
| [docs/ORIGIN.md](ORIGIN.md) | The authorship census: why the original is assembly. |
| [docs/TOOLING.md](TOOLING.md) | Toolchain pins and the measured compiler limitations. |
| [docs/DIVIDE_ANALYSIS.md](DIVIDE_ANALYSIS.md) | The shared divide kernel and its cross-ROM evidence. |

The patched-backend lane, the semantic-model lane, the harness, the models and
the raw evidence are part of the full working tree and are not distributed.
