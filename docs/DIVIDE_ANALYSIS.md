# Division library and boundary audit — 2026-10-11


## Verified cross-ROM result

The division library is shared across Neo Turf Masters, Metal Slug 2 and
Metal Slug X. The **whole unsigned function is not byte-identical**. Its
782-byte body after the zero checks is identical; the entry branch differs.
All measurements below use the owner's local ROMs, CPU byte order, MAME
unidasm, and vasm round trips for the changed NTM sources.

| Game | CPU entry | Size including RTS | Signed wrapper | Zero-divisor TRAP #15 |
|---|---:|---:|---:|---:|
| turfmast | `0x2908` | 792 | `0x2c20` | `0x28e0` |
| mslug2 | `0xb2a8e` | 794 | `0xb2da8` | `0xb29be` |
| mslugx | `0x4e648` | 794 | `0x4e962` | `0x4e578` |

NTM uses a short branch for the zero divisor; the other two use word
branches. Each decoded routine contains 347 instructions, including the
return. The shared 782-byte body has SHA-256
`77d69117fb0ece48271e2260529ee4f6fbf8de491651457dbbacbbceb7501354`.
The 40-byte signed wrappers match after normalizing their one BSR displacement.

The previously reported 809-byte equal run is real, but its exact NTM span
is **[0x2912, 0x2c3b)**. It starts after the entry checks, crosses the
unsigned return, and ends inside the signed wrapper's call displacement.
The corresponding member spans are [0xb2a9a, 0xb2dc3) and
[0x4e654, 0x4e97d). Those starts are not function entries.

Reproduce without exporting ROM bytes:

```sh
make extract
python3 tools/divide_contracts.py --zips /path/to/roms > the rebuild
python3 tools/rom_fingerprint_scan.py --zips /path/to/roms --window 2912:30 --min-run 780
```

The contract report records input member hashes, CPU entries, member layout,
body hash and static direct-call candidates. NTM's fixed window begins at
member offset 0x100000; the other two begin at zero. The general fingerprint
scanner reports **member offsets**, not CPU addresses. It searches conventional
P-ROM member names with word swapping; it is not a loader for arbitrary
interleaving/encryption. Its source window is the fixed NTM half. Search-cap
hits are explicitly labeled. No new compiler attribution follows from a miss.

## Contract and callers

The decoded normal path takes unsigned operands in D0.l and D1.l. It sets
D2 to the divisor, then performs 48 restoring-division steps, shifting through
D0.l, D1.w and D3.l. It returns the low quotient in D0, with D1/D2/D3
clobbered. For nonzero divisors at most 0x80000000 this implements
`floor((a << 16) / b) mod 2^32`; D3 holds the remainder. For larger unsigned
divisors, retain the actual wrapping carry-chain behavior: the mathematical
formula is not a universal replacement. Signed-wrapper operand magnitudes
are within the former domain.

The entry checks D0 first: a zero numerator returns immediately, including
0/0, without initializing the normal-path clobbers. A nonzero numerator
with zero divisor branches to TRAP #15. The signed wrapper folds each sign,
stores its sign flag in D7, saves/restores that flag as a word around the
call, then conditionally negates D0. It does not implement a conventional
callee-save ABI for D2–D7.

NTM has seven direct-call candidates to the unsigned entry in the fixed
window: 0x27da, 0x2c38, 0x59e30, 0x59e4e, 0x59e7c, 0x59e9a and 0x59ecc.
The first is the Newton square-root loop and the second the signed wrapper.
The later sites are within the magnitude routines already listed by Ghidra.

Each Metal Slug build has two candidates in its fixed window: the signed
wrapper and a PC-relative JSR (mslug2 0xb14b0; mslugx 0x4ce32). Independent
decoding around the latter shows the same preparation: load an actor's
long value into D0 and fold its sign; zero D1, load a word, swap and
arithmetic-shift it right once; call the division; then subtract the returned
D0 from 0x10000. Actor field offsets shift by two bytes between builds.
This corroborates the D0/D1 contract. The report also lists calls to the
signed wrappers. Opcode scanning does not prove all candidates executable;
P2 banks and indirect calls remain outside the call census.

The existing C models are **not a complete machine-state or exception
oracle**. In particular, unsigned C division currently falls through its
loop for nonzero/zero instead of delivering the ROM's TRAP #15. Earlier
200k-input claims were about the stated numeric domain, not this exception
path, ABI clobbers, or host platforms where `unsigned long` is 64 bits.

## Compiler attribution: still unresolved

Shared library ancestry is established. Source language, compiler identity,
and whether this library was handwritten assembly are not established.
An unrolled multiword arithmetic routine can be an assembly runtime library
called from either C or assembly. The mixture of BSR, PC-relative JSR and
absolute JSR also does not by itself prove linker relaxation.

A bounded web search of original-development tooling leads and developer
interviews did not produce a Nazca compiler identification or a matching
runtime source. The NeoGeo tooling inventory is a lead, not evidence that
Nazca used any listed tool. No credible compiler candidate emerged, so no
per-function compiler override or speculative toolchain installation was added.
The existing vbcc experiment still reports four differences at byte zero.
That is a backend-specific result, not a proof that matching C is impossible.

## Corrected NTM physics sources

- `0x74c0` spans **234 bytes**, ending exclusively at 0x75aa, including the
  RTS at 0x75a8. The prior 232-byte claim omitted the return instruction.
- The listed `0x7558` entry was the last word of the absolute JSR operand
  starting at **0x7554**. Decoding there invented a `move.l -(a0),d6`.
  This false function was removed from the inventory and source tree.
- `0x75aa` is a real 248-byte routine ending exclusively at 0x76a2. It calls
  normalize, then updates wind contribution, drag, sidespin, lift and gravity.
  It is now `airborne_velocity_update`, fully decoded and byte-verified.
- The declared unsigned divide size **792 was already correct**. Its source
  had an extra 14-byte raw tail from the next function. That tail, and the
  signed wrapper's analogous tail, were removed. No change to 0x410.

The importer uses `last_instruction_address + 0x10` to allow uncertain
Ghidra extents. This explains many padded spans. It must not be mistaken for
proof of a boundary. the progress check now audits the actual assembled sizes,
reports missing/unlisted entries, and counts interval unions across functions
and data. the inventory audit retains the outstanding discrepancies;
blindly changing all declarations to those padded sizes would hide the issue.
Against the current HEAD sources, unique coverage increases from 383,440
to 383,674 bytes: **234 newly covered bytes**. The earlier 394,736 gross
figure in the handoff was stale as well as overlap-inflated (HEAD actually
assembled 394,526 gross bytes).

## Validation

- the byte gate: P1 and M1 spans and full hashes match; regression tests cover
  overlap counting, invalid spans, failed-build publication, repeated scan
  anchors and C tool failure exit status.
- the MAME acceptance: game entry 709, title 718, attract 2341, shot 4167; PASS.
- vbcc: 0 matching C functions, 4 differences. This is an expected negative.
- the C gate now fails on compiler/linker/objcopy errors as well as byte
  differences; previously those tool failures could fall through to exit 0.

A full reconstruction, complete inventory reconciliation, data-table
internals and deeper M1 analysis remain open. This audit does not claim those
fronts complete.
