# Runtime findings

These are sanitized summaries of the runtime work. The raw evidence records,
instruction traces and scenario files are not included here.

## Hole advance and full rounds

An inputs-only MAME harness — coin, START and joystick, with no RAM pokes —
played **141 hole advances across 15 scenarios**, including 1P and 2P rounds, a
challenger join, a stroke-limit failure and the how-to-play demo. The harness
uses the game's normal input path; it does not manufacture game state.

This runtime evidence corroborates the static reconstruction. It is not the
classifier: the byte classification and the reachability proof stand on the
static evidence described in [METHOD.md](METHOD.md).

## AES memory-card support

Under MAME, the AES memory-card path loads and saves cards, exposes level
select, and runs the stats page. An earlier "no AES BIOS" conclusion was a
cart-lookup bug in the harness, not a game limitation.

## Reachable crash in the shipped game

`fixed_div_scaled` at `0x63504` divides by zero for 16 depth values. Two
minimum-power putter shots on course 1 hole 1 trigger a trap, a hang and a
watchdog reset. This is a bug in the original program, reproduced from the
recovered control flow.

## M1 timer timing

The M1 timer is encoded as `0x2b3`, which corresponds to approximately
**166.83 Hz** on the Z80 side.

## Corrected earlier readings

Several early interpretations were wrong and are recorded as corrections:

- The "aim marker" is a water sparkle.
- Sprite type 3 is the pin flag and cup.
- The "attract demo driver" is the post-hole contest sequence.
- The old `0x410` "encoding exception" was data misread as code.

These corrections are part of the project record: classification claims are
revised when the evidence changes, and the revision is documented rather than
silently overwritten.
