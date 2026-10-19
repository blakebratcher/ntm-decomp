# Method: terminology, byte-exactness and evidence

This page defines the terms the project uses and the standard every
classification claim meets. It is the best starting point for the public
documentation.

## Terminology

Three different activities are often called "disassembly" or "decompilation":

- **Raw disassembly.** A mechanical rendering of machine code as mnemonics.
  Addresses and opcodes are preserved; function boundaries, names, data
  formats and intent are not recovered. A raw listing is useful as a reference
  but is not a source reconstruction.
- **Compiler decompilation.** Recovering a higher-level language from a binary
  that was produced by a compiler, usually aiming at source that a compiler can
  rebuild into the same bytes.
- **Source reconstruction.** Recovering readable, named, commented source from
  a binary in whatever language the original used, with structure and data
  formats recovered from evidence.

This project is the third kind. The original P1 and M1 are hand-written
assembly, so the recovered source is assembly. The project is a **byte-exact
assembly decompilation**: every instruction and data span is recovered, named
and classified, and the rebuild reassembles the images byte-for-byte.
The name "decompilation" describes recovering source from a binary; it does not
imply that the original was compiler output.

## What "byte-exact" means

Byte-exactness is a property of the reconstruction:

1. **Whole-image match.** Reassembling the recovered code and re-emitting the
   classified data reproduces the original P1 and M1 images exactly. The
   the rebuild checks full-image SHA-256 pins.
2. **Span match.** Each code span is assembled at its real CPU address and
   byte-diffed against the corresponding bytes of the pristine image.

It is **not** a claim that every byte is code, that the original was compiler
output, or that the recovered names are the names the original authors used.
Separately, the completion gate proves that every byte has exactly one
evidence-backed category.

## Byte categories

| Category | Meaning |
|---|---|
| Live code | Reached from the reset/BIOS entry points and reassembled from source |
| Named data | Tables, pointers and structures with a consumer or format proof |
| Extracted assets | Graphics, palettes and audio with consumer evidence; emitted by the rebuild |
| Fill | Constant runs proven unreferenced |
| Dead code / dead data | Proven unreachable or unreadable by the closed-world proof |

Every span is a one-line claim in `config/regions/<cpu>/*.tsv`, with its
evidence or format proof. The categories are mutually exclusive: one owner per
byte.

## Evidence standard

A byte is classified only when the evidence supports the claim:

- **Consumer evidence.** An instruction or table that reads or calls the span.
- **Format proof.** A structure, pointer table or encoding that is consistent
  across every use and every sampled instance.
- **Static roots.** The closed-world proof starts at the cartridge vectors and
  the BIOS entry points and follows every reachable path.
- **Complete indirect targets.** Every `jmp (An)` and `jsr (An)` site has a
  complete target set; the proof does not assume a target is absent because it
  was not seen.
- **Runtime evidence.** MAME's current-PC trace records which instruction
  starts actually executed. This separates real execution from 68000 prefetch
  fetches.
- **Dead proofs.** A span is dead only when no live path can reach it, not
  merely because a search did not find a reference.

A play-through is one runtime input, not the classifier. It can corroborate a
static proof, but it cannot by itself prove that an unvisited byte is data.

## How code and data were distinguished

The pipeline is static-first:

1. Extract and word-swap the user's verified dump.
2. Build an initial function inventory from Ghidra and an independent decoder.
3. Recover functions and control-flow blocks, including fall-through, shared
   tails and multiple entry points.
4. Classify tables and assets from consumer evidence and format proofs.
5. Run the closed-world reachability proof from the vectors and BIOS entries,
   with complete indirect-target sets.
6. Mark unreachable code and unreadable data as dead with a proof.
7. Assemble every span and byte-diff it against the pristine image.
8. Check the full-image SHA-256 pins and run the runtime acceptance evidence.

The full pipeline runs all eight steps. The public tree ships the
classification tables and the corpus-independent tooling, so a reader can
review the claims and verify a user-supplied dump against the pins, but cannot
reassemble the images.

## What's published

- **Published:** methodology, classification metadata, SHA-256 pins, corpus
  conventions, corpus-independent tools and tests, and sanitized findings.
- **Not published:** the recovered source corpus, generated data sections,
  raw evidence records, extraction/build artifacts, and development history.


## Limits

- **No original C to match.** The original is assembly; the matching-C
  experiment is a documented negative, not a pending goal.
- **Three accepted reachability residuals.** They concern whole-program
  questions, not unknown bytes. They are named and explained in the semantic ledger.
- **Classification is evidence-bound.** A name or role can be described at
  different levels of confidence; the evidence column records which kind of
  proof supports each span.
