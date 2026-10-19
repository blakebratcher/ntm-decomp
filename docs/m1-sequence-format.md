# M1 sound driver: sequence and record formats


A reader's guide to the Z80 sound driver's data, derived from the
recovered code (the M1 corpus) and the lane-3 walker
(the lane tool).  Every address cited below is a file offset
into the 128 KiB `200-m1.m1`; consumer addresses are instruction sites
in the committed sources.  Runtime support comes from the MAME trace of
lane 2 (the rebuild, the lane tool).

## Banking

The driver reads almost everything through the 0x8000 bank window.
A `[off_lo off_hi bank]` record resolved by the 0x1fa6 helper lands at

    file = (bank & 1) * 0x10000 + off

The resolver programs the window with entry `(bank << 2) | (off >> 14)`
and returns `hl = 0x8000 + (off & 0x3fff)`.  The fixed window
(0x0000-0x7fff) maps to the file 1:1; a window entry maps one 16 KiB
file chunk to Z80 0x8000-0xbfff.  The interpreter's stream pointer is a
Z80 address, so jump/call/loop arithmetic is 16-bit in Z80 space.

## The note interpreter (0x532b / 0x5362)

Per voice-tick the driver reads one byte from the stream pointer
(0xff0d) and dispatches (block 0x5362, jump table 0x53a3):

- **< 0xc0 — a note.**  Bits 0-2 of the note byte select which of three
  optional parameter bytes follow (bit clear = byte present): the
  optional bytes go to (iy+0x18)/(iy+0x16), (iy+0x19), (iy+0x1a).
  Note bytes 0xb8-0xbf additionally read one volume byte into
  (iy+0x17); note bytes 0x00-0xb7 compute a transposed semitone index
  into (iy+0x17) instead (block 0x56ea/0x56fc).
- **0xc0-0xd9 — commands**, dispatched through 26 three-byte `jp`
  stubs at 0x53a3 (index = (byte - 0xc0) * 3):
- **0xda-0xff — end of sequence** (block 0x578d): clears owner +0x12,
  sets flags (iy+0x09) = 0x08, clears the voice pointer +0/+1 and sets
  priority +2 = 0xff; there is no music/SFX distinction here (decode of
  0x578d).  Every byte >= 0xda ends the stream, but no reachable
  sequence uses one: walks end at an empty-stack 0xc5 or an endless
  0xd0 (semantic-data-m1-seqdata).

| Byte | Operands | Effect (consumer block) |
|---|---|---|
| <0xc0 | note + bit-gated params | note as above (0x56ea/0x56fc) |
| 0xc0 | 1 | byte -> (iy+0x0a), voice-definition selector index (0x56b1; semantic-review-m1-0504e) |
| 0xc1 | 2 | bytes -> (iy+0x0a/0x0b), voice-definition selector index / table group, read by 0x4b5d (0x56bd; semantic-review-m1-0504e) |
| 0xc2 | word | pc-relative jump (0x56ce) |
| 0xc3 | 1 | nonzero sets bit 0 of (iy+0x09) (0x569a) |
| 0xc4 | word | pc-relative call: saves the return address at iy+0x1c+depth-2 and depth -= 2; if the 3-word stack is full (depth 0) it is a tail jump (0x565d) |
| 0xc5 | 0 | return: jumps to the word at iy+0x1c+depth, depth += 2; on an empty stack (depth >= 6) stops the voice (0x559e/0x55a9) |
| 0xc6 | 1 | byte & 0x7f -> (iy+0x0c) (0x5574) |
| 0xc7 | 1 | byte & 0x7f -> (iy+0x0d) (0x5582) |
| 0xc8 | 1 | byte & 0x7f -> (iy+0x0e) (0x5590) |
| 0xc9 | 1 | byte gates bit 4 of (iy+0x09) (0x555c) |
| 0xca | word + byte | floor(signed14(word & 0x3fff)/32) -> (iy+0x10/0x11), byte -> (iy+0x16) (0x5541; semantic-data-m1-seqdata) |
| 0xcb | 1 | byte -> (iy+0x16), delay counter (0x5535) |
| 0xcc | 1 | byte -> (iy+0x0f): 3 if ==0x40, 2 if <0x40, else 1 (0x551d) |
| 0xcd | 2 | params -> helper 0x4f39 (0x550f) |
| 0xce | 1 | byte & 0x7f -> (iy+0x12), pitch (0x54fc) |
| 0xcf | 0 | push a zero loop counter: free offset -= 1, cell = 0; stops when the offset was already 0; resets +0x17..+0x1a (0x55d4; semantic-review-m1-0504e) |
| 0xd0 | count byte + word | loop on the top one-byte slot: stored 0 loads the count (0 = endless, 1 = expire, n = body runs n times), stored 1 expires (pops without clearing the cell), larger stores count-1 and branches; with no loop slot it stops the voice (0x5605/0x5624/0x5648; semantic-review-m1-0504e, semantic-data-m1-seqdata) |
| 0xd1 | 1 | byte gates bit 6 of (iy+0x09) (0x54c5) |
| 0xd2 | 1 | byte -> (iy+0x13) (0x54f0) |
| 0xd3 | 2 | params -> 0x4b0b, pitch bend (0x5420) |
| 0xd4 | 2 | params -> 0x4b26, pitch bend (0x5434) |
| 0xd5 | 2 | params -> 0x4b41, pitch bend (0x5448) |
| 0xd6 | 2 | params -> 0x4b4f, pitch bend (0x545c) |
| 0xd7 | amount byte + wait byte | added to (iy+0x10/0x11) (0x5470; operands per semantic-data-m1-seqdata; never executed by a reachable sequence) |
| 0xd8 | amount byte + wait byte | subtracted from (iy+0x10/0x11) (0x5499; operands per semantic-data-m1-seqdata; never executed) |
| 0xd9 | 1 | byte -> YM2610 port write (0x53f4) |
| 0xda-0xff | 0 | end of sequence (0x578d) |

The call/loop stack lives at iy+0x1c..iy+0x21 (six bytes, three words
or six loop counters) with the depth byte at iy+0x22, initialised to 6
by the voice starter 0x5251.  A jump/call word is a signed 16-bit
offset added to the Z80 address after the command's operands (C2, C4
and D0 alike; semantic-data-m1-seqdata).

## Start lists, voice-definition index, sequences

Corrected 2026-10-14 (semantic-data-m1-tables, semantic-data-m1-seqdata,
semantic-review-m1-0504e): the old "music" and "SFX" labels of this
section were swapped or wrong. The two sequence starters are 0x4fa3
(song lists, the 14 music owners at 0xfbfc) and 0x507b (start records,
the 8 owners at 0xfdf2); 0x5251 is called only from those two.

- **Voice-definition pointer table 0x57a1** (1,024 `[word 00 00]`
  records, ends 0x67a1; record 0x80): not a song index. 0x4b72 indexes
  it by high*512 + low*4 with the selector set by sequence commands
  C0/C1; 65 distinct targets, the voice definitions described below.
- **Start-record tables 0x8227 and 0x85a7** (224 `[off bank pad]`
  entries each, records 0x88 and 0x8c of the 0x80 array; consumer block
  0x2126, high-selector requests): entries name 0x507b start records,
  not 18-byte song records.
- **Start records 0x8927-0xa77c**: four stop request words (+0..+7,
  passed to 0x5210), a tag byte (+8), then five-byte
  `[off_lo off_hi bank pad priority]` sequence entries until a zero
  off+bank triplet, then one unread zero. Consumer 0x507b/0x50a7 reads
  each pointer and 0x50b4 the priority; 0x50d1/0x50d4 advance IX by
  five. `parse_music_voice_list` in the lane tool validates
  all 425 table targets: 402 one-entry and 23 two-entry records. The
  three former five-byte unknown gaps at 0x8d4a/0x8d61/0xa6b1 hold the
  second priority, the null triplet and the unread pad
  (the lane evidence).
- **Start-record sequences 0x19391-0x1b1ef**: packed note/command
  streams, 448 entry points, each walk confined to its own span; 444
  stop at an empty-stack 0xc5 and 4 loop forever (0xd0 count 0). No
  reachable sequence ends on a 0xda-0xff byte.
- **Fixed-list sequences 0x1b1ef-0x1b2ce**: entered by the 0x24de song
  list (0x2101) and the 0x24f7 start record (command 0x0f, 0x2156);
  they select the built-in definitions 0xff00-0xff02 (table 0x2509 ->
  0x1980/0x19b2/0x19e4).

## Song table, song lists, song-list sequences

- **Song table 0x7d5e** (20 `[off bank pad]` entries, record 0x84;
  consumer `cmd_play_song` 0x20d4, sub_cmd 0): entries 0 and 1 are
  zero, 2..19 name song lists; the code does not bound the index.
- **Song lists 0x7dae-0x81f0**: byte 0 indexes the tempo table 0x4b84,
  then up to 14 five-byte `[word bank 00 priority]` entries until a zero
  word+bank terminator. Consumer 0x4fa3, which starts one music owner
  per entry through 0x5251.
- **Song-list sequences 0xa77c-0x19391**: one note/command stream per
  song-list entry (211 targets), extent [entry, next entry); not SFX
  and not sample data (ADPCM samples are in the V ROMs). 112 stop at an
  empty-stack 0xc5, 99 loop forever.

## Voice definitions 0x67a1-0x7d5e (not interpreter streams)

These are voice definitions, not songs (semantic-data-m1-tables); "song"
below is kept only where it names an existing row. Ten of the 65 never
start a note (0x67a1, 0x67b9, 0x6c1b, 0x6eb9, 0x78e0, 0x7ba8, 0x7bc0,
0x7bd8, 0x7c68, 0x7d46). For class 2 and 3 each row table / sample
header block sits directly before the definition that uses it.

The pointer-table targets are records consumed by the voice-handler trees,
entered with hl = record base (0x4a30; the type byte at [0] selects
the tree through 0x4389).  They are packed, record N ending where
record N+1 (or the song table at 0x7d5e) begins:

| Type | Size | Consumer | Layout |
|---|---|---|---|
| 0 | 24 B | tree 0x2aa8 | [0] type; [1..2] fields; [3..7] five 0x219b fields; [8..13], [14..19] two 0x22a8 groups; [20..23] one 0x242e group |
| 1 | 50 B | trees 0x3568 + 0x35c5/0x35cd | [0] type; [1..4] fields; [5] field; [6..33] four 7-byte groups (ldir into the voice block); [34..39], [40..45] two 0x22a8 groups; [46..49] one 0x242e group |
| 2 | to next record | tree 0x3f41 + handler 0x3f60 | [0] type; [1..5] 0x219b fields; [12..14] [off bank] -> 5-byte-record array; [16] index base; [17..] index array read at [17 + vol - base] |
| 3 | to next record | tree 0x427f + tail walk 0x42ef/0x432b | [0] type; [1..5] 0x219b; [6..11], [12..17] two 0x22a8 groups; [18..21] one 0x242e group; [22..] threshold table of 6-byte records [threshold param off bank pad] |

The fixed sizes are proven by the target strides (every type-0 target
is 24 bytes after the previous, every type-1 target 50 bytes, and the
last type-0 record ends exactly at the song table).  Two inter-record
structures are referenced from within the records: the 46-record
5-byte array at 0x6c33 (song 0x6d19's [12..14] pointer, indexed by
0x3f60) and the four 12-byte records at 0x78f8 (song 0x7928's tail
records).  The fixed songs hold 0xda-0xfe bytes mid-record, which is
how they were recognised as tree data rather than interpreter streams.

## LFO and pitch-ramp definition groups (settled 2026-10-14)

The loaders $22a8 and $242e interpret the six-byte LFO and four-byte ramp
groups inside the voice definitions. One LFO/ramp step is one main-loop
pass that services the enabled voice path. Timer A is nominally 5.994 ms
(166.83 ticks/s); overruns reduce the pass rate, and missed steps are not
replayed. The inputs-only settle runs measured 0.960..0.983 passes/tick;
these measurements describe those runs rather than a universal bound.
Current RAM names `ticks_this_pass`/$f803 and `frame_semaphore`/$f801
refer to timer IRQs and passes, not video frames. $f803 counts IRQs latched
at the previous wait exit; $4e33 adds at most three tempo contributions.

| LFO byte | Meaning and record field (loader $22a8) |
|---|---|
| g0 | bit 7 selects whether owner D adds to rate (0) or depth (1); low seven bits select waveform, record +0/+7 |
| g1 | initial delay in enabled LFO steps, saved at +$0d and copied to +4 on key restart |
| g2 | base rate B at +1; phase advances B+1 per active step, with rate-zero disabling checks in $231e |
| g3 | depth C at +2: `(2*g3 + ((g3>>6)&1)) & $ff` |
| g4 | growth/fade-in control, transformed through $1400/$1500/$1600 into the +9/+$0b growth word; accumulator +$0a/+$0c starts at zero |
| g5 | coefficient E at +3, transformed like g3; mode 1 adds M(2D,E) to depth |

D comes from owner +$0c (pitch, command C6) or +$0d (level, C7). The
reachable retail sequences contain C6 but no C7 or C8; level D and the
ramp owner modifier therefore remain zero. `M` is the driver's
log/antilog multiplication through pages $1400/$1500, approximately
x*y/256 with explicit zero/unity cases; it is not exact real multiplication.
The LFO growth high byte scales C to C', then the waveform sample E is
scaled to A. Pitch consumers add `2*A-C'`; level consumers apply
`A-C'-1` as a gain below unity 255. Pitch words use **256 units/octave**,
so one pitch unit is 4.6875 cents.

| Waveform selector ($236c) | Output sample | Period in active steps |
|---|---|---|
| 1 | rising saw, phase-low | 256/(B+1) |
| 2 | falling saw, inverted phase-low | 256/(B+1) |
| 3 | square, high-phase bit selects $ff/0 | 512/(B+1) |
| 4 | random sample-and-hold | new sample at phase-low wrap |
| other (retail 0) | triangle, phase-low XOR high-phase mask | 512/(B+1) |

At the nominal rate, triangle/square frequency is
`(B+1)*166.83/512` Hz. Depth is the peak excursion C' in pitch units, or
the dip below gain 255 for level modulation, after growth scaling.
This describes the encoded control arithmetic, not a physical-chip audition.

| Ramp byte | Meaning (loader $242e, restart $2461) |
|---|---|
| r0 | bit 0 becomes record +6 bit 7 (return-to-note shape); high nibble is the alternate key-trigger path flag at +$0c |
| r1 | delay in serviced steps, saved +9 and loaded to +2 at key trigger |
| r2 | slope n, rate word +0/+1 = 16*n; retail key-trigger owner modifier +$0e is zero |
| r3 | signed x, target `P[abs(x)]/4` from semitone tables $1100/$1180; -128 clamps to magnitude 127; sign becomes +6 bit 6 |

$248e reads B=rate-low, C=rate-high. After delay, $24a8 subtracts B from
fraction +3 and C+borrow from integer word +4/+5, clamping on underflow.
The result decreases by n/16 pitch units per step (n*0.293 cents/step,
n*48.9 cents/s at the nominal pass rate). The initial target corresponds
to |x| quarter-semitones. Mode bits 7/6 select 00: rise from the note,
01: fall from the note, 10: start above and return, 11: start below and
return. Pitch consumers add the shaped HL result to the pitch word.

Evidence: independent ROM decodes $22a8..$24dd, main-loop $1e3f..$1e79,
FM consumer $2cad..$2ce7; the complete producer, definition and inputs-only
measurement ledger is semantic-settle-m1.
