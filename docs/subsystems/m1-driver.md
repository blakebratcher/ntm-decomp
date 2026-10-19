# M1 sound driver (Z80) — architecture


Scope: the code in the M1 corpus
(14,412 bytes, 7,456 instructions, 328 sources, all byte-exact against
`200-m1.m1`). Named here with the labels the sources now carry
(`config/m1/names.tsv`). Byte/data classification of the ROM around the code
is in the lane report; the byte-level sequence command set
is decoded in [m1-sequence-format.md](../m1-sequence-format.md) (lane m1-3).
Addresses are Z80 addresses, which equal M1 file offsets for the fixed window
(0x0000-0x7fff).

The driver is the Nazca in-house code base shared with Metal Slug (92.7% of
the code window), not a member of the signed MAKOTO/Mr.Pac/"Sound Driver"
families — see [docs/m1.md](../m1.md) for that evidence.

## 1. Hardware surface

| Z80 port | Meaning (MAME 0.289 `neogeo.cpp` `audio_io_map`) |
|---|---|
| 0x00 | command latch from the 68k (read clears it) — the NMI source |
| 0x04/0x05 | YM2610 port A (address/status, data) |
| 0x06/0x07 | YM2610 port B (address/status, data) |
| 0x08 | write: NMI enable; read: bank-window select |
| 0x0c | reply latch to the 68k |
| 0x18 | write: NMI disable; data byte ignored by the Neo Geo handler |

`ym_reg_write` (RST 8, 0x0008) is the only YM write path: it busy-waits on the
port-A status byte (`in a,(0x04)`; bit 7 = busy), saves the (port, register)
pair in `ym_saved_port_reg` (0xf80b), writes the register number to port C and
the value to port C+1. Callers set `ld c,0x04` (port A) or `ld c,0x06`
(port B) and pass the register in D, the value in E. The bank-window select
is not a write: `in a,(c)` with C = window index and B = bank entry programs
that window (0x0b = the 16 KiB 0x8000 window, 0x0a = 8 KiB 0xc000, 0x09 =
4 KiB 0xe000, 0x08 = 2 KiB 0xf000).

RST 30 (0x0030) clears fields +0x00 and +0x0d of the IX record. RST 38 is the
IM 1 interrupt (the YM2610 timer A IRQ); the NMI is the 68k command latch.

## 2. Boot, main-loop structure, command intake

```
0x0000 m1_reset          di; out (0x18),a; jp driver_init
0x1e00 driver_init       reply latch = 0; sp = 0xfff0; bank_window_reset;
                         cmd_queue_init; cmd_state_reset; ssg_init;
                         fm_voice_init; adpcm_a_init; adpcm_b_init
                         (banked record tables are not touched yet)
                         then ym_timer_init, im 1, out (0x08) (NMI on), ei
0x1e3f main_loop         frame_counter++; cmd_process; svc_ssg_tick;
                         svc_fm_voices_tick; svc_adpcm_a_tick;
                         svc_adpcm_b_tick; svc_aux_owners_tick;
                         svc_seq_channels_tick;
                         wait for frame_semaphore; latch ticks_this_pass;
                         repeat
```

* **Timer tick** — `ym_timer_init` (0x1e6b) programs timer A
  (0x24=0xac, 0x25=0x03), encoded value 0x2b3, and control 0x27=0x35
  (load A, enable A IRQ, reset A/B status; timer B is not loaded).
  At the configured 8 MHz clock, the emulator timer period is 333*144 clocks
  = 0.005994 seconds (166.8335 Hz), not one tick per video frame.
  A read-only rebuilt-ROM probe observed 2,588 handler writes with that
  median interval; see hardware evidence.
  Every tick `timer_irq_handler` (0x1e7a, reached through the RST 38
  vector 0x0038) resets timer status through 0x27, raises `frame_semaphore` (0xf801, saturating at
  2), increments `tick_count` (0xf802), latches the YM status byte into
  `ym_status` (0xf80a) and restores the YM register selection the main thread
  had set (0x1ea0-0x1ea6). The main loop consumes the semaphore once per
  logical driver iteration and copies `tick_count` into `ticks_this_pass` (0xf803), which the
  tempo service uses to advance its accumulator; the historical names
  `frame_counter`, `frame_semaphore` and `ticks_this_pass` refer to driver
  passes and timer IRQs, not video frames. LFO/ramp services step once per
  enabled sub-record per pass, without repeating missed steps.
* **Command intake** — the 68k raises the NMI by writing the sound latch.
  `sound_cmd_nmi` (0x1ed6, vector 0x0066) reads port 0x00 and treats two bytes
  specially: 0x01 runs `sound_cmd_stop_all` (0x1f0a) — silence SSG/FM/ADPCM-B,
  reset the stack, reply 0x01 on port 0x0c, then park with interrupts
  disabled — and 0x03 runs
  `sound_cmd_restart` (0x1f4b) — reset the stack and return to 0x0000. Every
  other byte is pushed into the 16-byte ring at `cmd_ring` (0xfff0-0xffff,
  indices `cmd_queue_head`/`cmd_queue_tail` = 0xf80f/0xf810).
* **Command pump** — `cmd_process` (0x2050, once per main-loop pass) pops one byte
  (`cmd_queue_pop`, 0x1ebb) and dispatches it:

  | byte | path |
  |---|---|
  | 0x00-0x0e | stored in `sub_cmd` (0xf81c) and dispatched through the 16-entry table 0x2076 |
  | 0x0f | skips only the selector store; dispatches table slot 15 (0x2156), preserving prior `sub_cmd` |
  | 0x10-0x1f | stored in `sub_cmd`, then returns (0x2068) |
  | ≥ 0x20 | music request: `last_music_cmd` (0xf81b) = byte, `song_number` (0xf815) = byte - 0x20, then dispatched through the 16-entry table 0x20b3 using the stored `sub_cmd` |

  The two tables are the driver's command protocol: the 68k first sends a
  *sub-command* byte (which selects fades, queueing or a plain start) and then
  the music request 0x20+song; the direct commands (0x02 stop music, 0x04/0x05
  stop all sequences, 0x06 reset the 0xfdf2 owners, 0x0c raise the stop flag)
  are handled by table 0x2076. Table slots that point at `cmd_nop` (0x20d3, a
  bare `ret`) are intentionally ignored bytes.
* **Reachability correction** — selectors 0x10..0x1f are stored before the
  direct-table guard, so the high-selector path 0x2115/0x2126 is live.
  Byte 0x0f is also live: P1's pending sound-flush path actually sends it.
  A numeric byte-range guard is not a closed-world dead-code proof; the
  sequence interpreter and its alternate callers require their own audit.

## 3. Channels and voice records

Everything the driver plays is a 0x23-byte *channel record*; the record points
at a *voice record* that the services in each main-loop pass turn into YM register writes.
The voice record class is the first byte of the voice *definition* and is
stored at `(ix+2)` of the voice record:

| class | voice records | service (per pass) | YM writes |
|---|---|---|---|
| 0 SSG | 3 at 0xf81f, stride 0x4c | `svc_ssg_tick` 0x2593 | port A 0x00-0x0a (tone, noise, mixer, volumes) |
| 1 FM voice | 4 at 0xf907, stride 0x67 | `svc_fm_voices_tick` 0x2c5f | port A/B 0xa1/0xa5/0xb1/0xb5 and 0xa2/0xa6/0xb2/0xb6 (F-Number, block, feedback/algorithm, L/R+AMS/PMS), key on/off 0x28 |
| 2 ADPCM-A | 6 at 0xfaa5, stride 0x29 | `svc_adpcm_a_tick` 0x379c | port B 0x08+n level, 0x10+n/0x18+n start, 0x20+n/0x28+n stop, 0x00 key on/off, port A 0x1c EOS reset |
| 3 ADPCM-B | 1 at 0xfb9b | `svc_adpcm_b_tick` 0x4024 | port A 0x10 control, 0x11 L/R, 0x12-0x15 start/stop, 0x19/0x1a delta-N, 0x1b level |

Playback is driven by *sequence channels*: 14 music channels at 0xfbfc
(stride 0x23, serviced by `svc_seq_channels_tick` 0x4ca6) and 8 more sequence
owners at 0xfdf2 (stride 0x23, serviced by `svc_aux_owners_tick` 0x514f),
started only from start records by 0x507b; the "drum" in their names is
unproven, no code here selects a voice class (semantic-review-m1-0504e,
semantic-review-m1-03c72). A
sequence channel holds the sequence pointer, the sequence's bank entry, the
voice-definition pointer, the note/timing state and a flags byte at +0x09
(bit 2 playing, bit 3 set on release/reset but also cleared on every
0xfdf2 service pass, bit 4 holds/defers key-off, bit 6
slide/envelope tick due, ...). `seq_channel_voice_get` (0x4ae2) resolves the
voice record from the channel; `voice_channel_alloc` (0x4389) picks the voice
record for a new note by class, priority and time stamp (`cmd_serial` 0xf819).

`voice_rec_init_music`/`fm_voice_rec_init`/`adpcm_a_rec_init`/
`adpcm_b_rec_init` (0x1fe5/0x2bb7/0x3783/0x3fe0) build the four record shapes;
`voice_rec_release` (0x201b) frees one when the sequence releases it.

## 4. From a sound command to YM writes

```
68k writes the sound latch
  -> NMI: sound_cmd_nmi (0x1ed6) pushes the byte into cmd_ring
  -> main loop: cmd_process (0x2050)
       music request 0x20+n + sub-command 0x00
         -> cmd_play_song (0x20d4)
              banked_ptr_load (0x1fa6) on the record at 0x0084
              -> the song's channel/voice table
              song_list_start (0x4fa3): the song-list initializer, despite
              its name (no direct ADPCM-B init; semantic-data-m1-seqdata)
              -> seq_owner_start (0x5251) for up to 14 music owners
       high-selector requests / command 0x0f
         -> aux_start_record_run (0x507b) start records: aux_owner_alloc (0x50db)
              + seq_owner_start (0x5251) for the 0xfdf2 owners
              (semantic-review-m1-0504e)
  -> per main-loop pass: svc_seq_channels_tick (0x4ca6) / svc_aux_owners_tick (0x514f)
       channel_slide_tick (0x52ea)  - pitch slide from the 0x1a00 table
       seq_run (0x532b)             - the sequence interpreter
            byte < 0xc0: note byte -> note/timing fields -> voice_note_start
            byte 0xc0-0xd9: command through the jump table 0x53a3
            byte >= 0xda: end the sequence (0x578d releases the channel)
         voice_note_start (0x4a03)
              voice_def_resolve (0x4b5d) -> banked voice-definition pointer
              voice_channel_alloc (0x4389) -> the voice record in IX
              per-type handlers through tables 0x4aa0/0x4abc/0x4ad8
                (note-on / key trigger / release, one per voice class)
  -> per main-loop pass: the four voice services turn the record fields into YM writes
```

`lfo_mod_step` (0x231e), `eg_step` (0x222d), `channel_ramp_step` (0x248e)
and `note_to_fnum` (0x23f6) are the shared parameter generators: they run the
record's state machine against the curve tables at 0x1400-0x1d00 and the
frequency tables at 0x1100/0x1200 and produce the F-Number, level and detune
bytes the services write out. The master fade (`master_fade` 0xf804,
`fade_step` 0xf806, `fade_direction` 0xf808) scales all of them and is set by
the fade sub-commands through `fade_step_set` (0x4f88) and `curve_mul`
(0x4f69).

## 5. Banked data

The 0x8000 window is banked by `banked_ptr_load` (0x1fa6): a 3-byte
`[offset, bank]` record resolves to `hl = 0x8000 + (offset & 0x3fff)` with the
bank entry `(bank << 2) | (offset >> 14)` programmed into the four windows
(cached in `bank_entry` 0xf811). Consumers: the song table, the start-record
tables and the voice-definition pointer table (0x0080-0x00c8 records are
consumed by 0x20d4/0x2126 and 0x4b5d), the song-list and start-record
sequences, and the voice definitions (semantic-data-m1-tables: 0x57a1 is not a
song index and there is no separate SFX table). The contents, extents and the
MAME-validated mapping are documented in the lane report;
`docs/m1-sequence-format.md` (lane m1-3) decodes the byte-level commands.

## 6. YM2610 register map as used here

Port A (0x04/0x05) — ymfm `YM2610::write_data`:

| regs | block |
|---|---|
| 0x00-0x0d | SSG (fine/coarse tune, noise, mixer, volumes, envelope) |
| 0x10-0x1b | ADPCM-B (control, L/R, start/stop address, delta-N, level) |
| 0x1c | ADPCM-A end-of-sample flag reset (bits A1-A6/B) |
| 0x21/0x22 | LSI test / LFO frequency |
| 0x24-0x27 | timer A MSB/LSB, timer B, timer control |
| 0x28 | FM key on/off |
| 0x30-0x6f | FM operator parameters (DT/MUL, TL, KS/AR, AM/DR per operator and channel) |
| 0xa0-0xb6 | FM channel F-Number, block, feedback/algorithm, L/R+AMS/PMS |

Port B (0x06/0x07) — ymfm `YM2610::write_data_hi`: 0x00-0x2d ADPCM-A
(key on/off, master volume, per-channel L/R+level and start/stop addresses)
and 0x30-0xff selects FM registers with the address's bank bit set ($130-$1ff),
not aliases of the lower bank. The driver uses both port
pairs for FM: voice records 1-2 are written through port A and 3-4 through
port B, with the same register numbers.

## 7. Open probes

1. **Port-B FM bank resolved in emulator source.** `svc_fm_voices_tick` drives FM voice records 3 and 4
   through port B (0x2c5f groups at 0x3173/0x33b1) and 1-2 through port A,
   using the ch2/ch3 register numbers in both cases. ymfm maps port-B
   0x30-0xff onto the upper FM register bank by retaining address bit8.
   The two port pairs are distinct channel banks, not mirrors; see the
   primary-source analysis in hardware evidence.
2. **Command paths corrected.** 0x2115/0x2126 and 0x2156 are live under the
   decoded selector/byte guards (see §2). The sequence helper 0x53f1 still
   needs its own complete caller audit; a nearby byte guard alone is not proof.
3. **Record fields still read structurally only.** `(ix+0x10)/0x11` is used as
   a signed pitch accumulator (0x530d/0x5318), and the m1-3 lane's command
   decode should be consulted before assigning names to the remaining
   sub-record bytes; `config/m1/ram.tsv` names only the fields with a
   consumer-level proof.
4. **Shared tempo accumulator.** `tick_due` (0xf809) is raised when the
   accumulator at0xfdf0 has a nonzero high byte after adding one/two/three
   contributions under consumed IRQ count0xf803 (counts>=3 use three).
   The highbyte alias0xfdf1 is reduced to(H-1)&3, not reloaded after
   underflow. Two nonlinear tempo-control ramps feed this contribution;
   0xfde6..0xfdef are not an ADPCM-B sequence owner. Natural control/overflow
   and batching effects remain open; see `../lanes/semantic-m1-tempo-service.md`
   and `../lanes/semantic-m1-tempo-curves.md`.

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
