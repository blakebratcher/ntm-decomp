# P1 subsystems in [0x7e540, 0x100000) — the name-8 lane


Scope: every source in the P1 corpus whose org lies in
[0x7e540, 0x100000), in the fixed half of the P1 ROM. 173 sources: 151
functions + 22 blocks (11 of them published by this lane). Names live in
`config/names/name-8.tsv`, work-RAM and hardware registers in
`config/ram/name-8.tsv`, the per-source purpose/in/out/calls headers in the
sources themselves (generated from the lane tool).

Everything below is grounded in the instructions, the callers/callees in
the rebuild, the RAM each routine touches and the
companion workspace FINDINGS.md (read-only reference).

## 1. Shared actor conventions used across this range

Most of this lane is coroutine-style **actors** on `a6`:

- `(a6)` = resume PC. A state ends with `move.l #$next,(a6); rts`; the
  scheduler calls the actor every frame.
- `($c,a6)` = the parent actor (the one that spawned it).
- `($11,a6)` = a flag byte used as a handshake with the parent (`st`/`clr`/
  `move.b #-1`).
- `($14,a6)` = a frame counter, `($30/$32,a6)` = x/y, `($3c,a6)` = sort
  priority, `($20,a6)` = hardware palette slot, `($2c,a6)` = current frame,
  `($22,a6)`/`($2a,a6)` = the actor's palette-slot list and its count.
- Spawn: `lea target,a1; jsr $4c6` — the new actor starts at `target` with its
  resume PC, the spawner gets the new actor back in `a0`. `jsr $598`
  releases the children; `jsr $54a` ("jmp $54a" for the tail form) waits for
  them, then releases the actor itself (*corrected*, semantic-review-p1-7e726).
- Task install (no actor, just a one-shot routine): `lea target,a0; jmp $d510`.

## 2. Attract intro sequence and staff roll (0x7e56e - 0x7f0d0, tables 0x7f0d0/0x7ff4c)

*Corrected (semantic-review-p1-7e726, semantic-review-p1-7fca6,
semantic-review-p1-7d4be):* two different sequences live here; the old text
merged them into one "course introduction".

1. **Attract intro sequence.** `0x7e37e` (template 0x7e372) is spawned by
   attract_scene_sync_init 0x7e2d4 / 0x7e302 (at $7e320) from attract_demo_round_setup
   0x6b7ea, the attract demo setup (root 0x6b822).  It spawns one step actor
   per entry of **0x7f0d0** (0x7e56e, 0x7e600, 0x7e56e, 0x7e69a, 0x7e6e0,
   0x7e726, 0x7e7fa, 0x7e846, -1) with durations from 0x7f8c0; the last step
   ends in the title screen (0x7ec9a/0x7ecd0).  The step actors place one of
   the four bank sprite maps (table **0x7f104**: 0x2456a8/0x245ea8/0x2466a8/
   0x246ca8) through `0x7e48c` (sprite-allocator reset plus a placed map, no
   "blit at the pan offset") and scroll placed sprite chains with
   `0x7e4b4 sprite_scroll_spawn`; `0x7eec8`/`0x7eedc` print the copyright line.
2. **Staff roll.** `0x7f93c staff_roll_player` is spawned by
   results_sequence_actor 0x74946 (not per player by a pre-round screen).  It
   walks the 9-record table at **0x7ff4c** `{u16 duration, u32 step actor}`
   (ends with the $ffff of record 9 at 0x7ff82): steps 0/2/4/6 -> 0x7fe60,
   1/3/5/7 -> 0x7fdf2, 8 -> 0x7fec6; it also spawns the staff-text script
   player `0x7fb60` and the fade actor `0x7fd7e`, and advances `0x10ecb4`
   (the staff roll step, 0..8).  The scripts read "BIG TOURNAMENT GOLF STAFF"
   (export "NEO TURF MASTERS STAFF"), "BIG BOSS", ... "PRESENTED BY".

Step actors of the staff roll:

- `0x7fe60 intro_map_layers_ab` / `0x7fec6 intro_map_layers_last` show
  course image `((step>>1) + course_index) mod 4`: a palette stream
  (0x7ff92) plus two 20x16 sprite maps.
- `0x7fdf2 intro_pose_step` (odd steps) resets the sprite allocator,
  draws the pose backdrop and spawns pose_spawner 0x7b7fe with a pose letter
  from table 0x8029c (no course unload or bust).
- `0x7fb60 staff_roll_text_actor` plays the per-step sprite script from table
  **0x7ffee** (9 longs); script records `{u16 x, u16 y, u8 palette index,
  u8 op}` where op 0 ends a record (not the script), 0x20 = skip, 0xff = end
  of script, other = frame index -0x41 into **0x80210**.
- `0x7fca6 intro_banner_actor` (step 8 only) moves its banner to y $178 (y += 1 every second frame) and
  then stays; the old "`0x7efce`/`0x7ef3c` run scripted rise/orbit
  animations" is wrong (semantic-review-p1-7e726).

Palette control: `0x7fd7e palette_fade_step_actor` steps the three per-channel
fade levels 0x104fca/b/c toward a target and the steps use
`0xb9a22 palette_slot_alloc` + the word/long streams `0xb9cb6`/`0xb9bde`.

*Settled:* 0x7e372 falls into 0x7e37e, and 0x7e302 spawns it at $7e320
(root 0x6b822); 0x7e56e..0x7e846 are spawned by 0x7e37e from $7f0d0, not by
the 0x7ff4c table (which holds 0x7fe60/0x7fdf2/0x7fec6).

## 3. Sound-command module and the sound-test screen (0x802b4 - 0x809c0)

A 32-byte command queue for the audio Z80 plus its developers' test screen:

- `0x802de sound_cmd_putc` pushes a byte into the ring at **0x10ecb6**
  (head 0x10ecd8, tail 0x10ecd6, mask 0x1f); repeats of the M1 selector
  bytes (< $20) are dropped (semantic-review-p1-7fca6).
- `0x80328 sound_cmd_send_code` encodes a 16-bit code (selector high byte +
  request low byte): low bytes < 0x20 are ignored, the high bytes 1/2/3 and
  the title/demo demo-sound case (MVS flag 0x10fd82 set + backup byte
  0xd00046 set + BIOS_USER_MODE 0x10fdaf == 1, for high bytes $00 and
  $10..$1f) are dropped, else the high byte then the low byte are pushed.
- `0x803f6 sound_cmd_flush_vblank` (called from the vblank work 0xd430) writes
  0x0f to the audio latch **0x320000** when the pending word 0x1000d2 is set,
  otherwise reads the latch into 0x10ecda and sends one queued byte.
- `0x8044a coin_sound_request` is **cart vector 0x134** (COIN_SOUND):
  it sets the pending word 0x1000d2; nothing is flushed.
- `0x803ce sound_cmd_reply` decodes the latch reply `(b + 0x80) >> 1`; the boot
  loop at 0xd5aa stores it into **0x102ea2**, the byte the screen loader and
  the intro steps gate on.
- The screen text at **0x80842+** spells "SOUND TEST" with a 1P/2P joystick
  "CODE +/-" editor; `0x804fc`-`0x8080c` is its body (help strings
  0x80854/0x808e8, cursor string 0x8096e; four lines of code word + bank).
  It is spawned by root_attract_title_flow at 0xe602 (state 0xe5ce), gated
  by dev_flags bit 0 (*corrected*, semantic-review-p1-7fca6: not unrooted).

## 4. In-round panel/overlay display (0x809c0 - 0x80c0a)

`0x809c0 overlay_objects_build` (called by shot_swing_entry 0x58442, not the
shot sequencer 0x58274) uploads
one of three sprite-strip panels (0x8ac30 / 0x8ac4a / 0x8b12c, chosen by the
per-course obstacle object type 0x10d4c0 == 4/5/other, from the table 0x5a194 = $a,5,5,4; *corrected, semantic pass wave 3*: not a mode word) into the SCB VRAM through the LSPC port and
arms an overlay list (0x10ece2 = 0x14 slots, list heads 0x10ece8/0x10ece4).
`0x80aa0 overlay_task_install` (spawned at 0x2f0a) + `0x80b36
overlay_list_insert` (called at 0x3154) then place and draw items in a
descending-y list, hiding stale SCB entries (semantic-review-p1-7fca6; the
old "0x314a/0x3184" sites and the `p1mid_data_80aae` row no longer apply).

`0x80c0a`/`0x80c3e` read the two player records (0x107004 P1, 0x10702a P2,
current player pointer 0x107076) for the per-golfer byte of table 0x80bf8,
written to record +0x23 and used as the power meter's top extension
(0x72286) and bar frame index (0x72a3e), and the per-golfer (min,max)
hook/slice curve limits (table 0x80bfe).  (*Corrected, semantic pass wave 2*:
no shirt colour is involved; +0x23, not +3.)

## 5. Swing/putt engines and the bag (0x80c58 - 0x80f88)

Already named by earlier lanes; this lane added the headers:
`0x80ce6 swing_engine` (7-long rows, club-layer slots, contact/commentator
events), `0x80e5a swing_timing`, `0x80c58 putt_engine`, `0x80e8c putt_timing`,
`0x80e20 contact_sound_by_club`, `0x80eda swing_power_sound_send`, `0x80f56
bag_get_club` (6-long bag table 0x8318e, club type at +2 of the 4-byte entry),
`0x80e04`-style messages go out through `0x804de sound_cmd_send_gated`.
The 14 bytes at 0x80f7a are the first five cells of the `0x80f88` step-cell
table (`{u16 ptr, u16 n}`, n = 10 = vblanks per step) - a data row proposal.

## 6. Round leaderboard (0x83a0c - 0x83d58)

**0x10eda2**: 32 rows of 8 bytes
`{u32 name pointer, s8 score to par, u8 standing, s8 holes played (<= 0 =
tee-off wave not started), u8 flags}`; flags bit 1 = P1 row, bit 2 = P2
row, bit 0 = both human rows (no reader found), bit 3 = human row, bits 4-5
= the computer score-table class.

`0x83a0c leaderboard_init` fills rows 0..30 with a random permutation of the
course's 31 computer-golfer names (the name-pointer lists **0x83352**, 4
longs indexed by course id 0x10d4b8) and puts the humans in rows 31/30;
`0x83ba2 leaderboard_ai_advance` plays one hole for every row (RNG 0x568c2,
difficulty 0x102eb8, per-hole strokes-to-par tables 0x83c92/0x83ca2/0x83cb2
chosen by par 3/4/5 and class in 0x83c50; human rows take strokes - par
through 0x83c24), then `0x83b00 leaderboard_sort` + `0x83b72
leaderboard_assign_ranks` re-rank; `0x83d58 leaderboard_player_position`
counts the player's place; `0x83cfe leaderboard_player_retire` converts a human
row into a computer row.  The code spans 0x83a0c-0x83d9e.  (*Corrected,
semantic pass wave 3*: 32 rows, not 31; +0 is always a name pointer; nothing
"adds" an entry.)

## 7. Memory-card result screen (0x83dc6 - 0x8619e)

The 0x80-byte record at **0x10eea2** is read/written through the BIOS card
calls: command byte 0x10fdc4 (2 = read, 3 = write, 4 = delete), FCB 0x10fdce
(NGH $200), SUB 0x10fdd0 (0), data pointer START 0x10fdc8, byte count SIZE
0x10fdcc and result ANSWER 0x10fdc6; writes go through
0xc00468/0xc0046e with the VBlank flag 0x10fdc2 cleared.

- `0x840d8 card_data_check_and_reset`, `0x843fa card_read`, `0x8444a
  card_write` (fills the record header from 0x83d9e/0x83db2), `0x844f8
  card_file_delete` deletes this game's file before writing; its START/SIZE
  fields are unused (neo-po/neo-epo/sp-s2/UniBIOS 4.0 decode and Japan AES
  inputs-only delete-before-write observation, semantic-settle-card-demo).
  `0x843ae card_save_entry` owns that save sequence.
- `0x854a8 card_screen_main` is the screen actor; `0x83dea card_page_load` +
  `0x83e28 card_page_action` the page, `0x856a6 card_fields_draw` with the
  four number printers (`0x85c90` average score 'III.HH / RRR' - not a prize,
  semantic-review-p1-844f8; `0x85d2c` score, `0x85d9c` holes, `0x85dc4` rank) and `0x85e04 bcd_number_draw`.
- The result fields live at **0x10ef3e** (cumulative round score in 1/256
  units, +4 completed-round count, +6 holes,
  +8 best score, +9 best rank, +a card byte); `0x85f06
  result_prize_accumulate` updates them each hole, `0x858ec card_record_build`
  packs them plus the leaderboard (through the block `0x85a24`) into the card
  record. $858ec writes +$14=$ff, +$15=course, +$16=hole (0 in the
  terminal branch); $8444a supplies the title. $85a24 merges **32** rows.
  `0x85a74 card_record_load` returns for mask 3 or hole 0; otherwise it
  restores golfer/scores into the player record selected by $864ae[mask-1],
  restores the leaderboard and ranks all 32 rows. Its caller ensures mask
  1 or 2; course/golfer/hole bytes from an external card are not validated.
- `0x86070`/`0x8602e`/`0x8619e` are the AES-only (BIOS_MVS_FLAG == 0)
  difficulty select EASY/NORMAL/HARD/MVS whose result is stored into
  0x102eb8 (0/3/7/4 from table 0x863ac) after the fade, the difficulty
  that the soft-DIP DIFFICULTY seeds at 0xe56e (*corrected, semantic pass
  wave 3* and semantic-review-p1-844f8).

## 8. Input sampling with demo record/playback (0x84fdc - 0x853de)

Per-port state at **0x10ef22** (P1) / **0x10ef28** (P2), 8 bytes
`{prev, cur, edges, pressed, repeat timer, ...}`:

- `0x85086 input_sampler` (per frame) reads the BIOS latches 0x10fd96/
  0x10fd9c, computes edges, and holds them for the auto-repeat word
  **0x10ef3a** (high byte = delay, low = period).
- `0x8500e input_source_select` switches between the real ports (0x10ef3c = 0)
  and five recorded demo streams (table **0x84fc8**, 0x10ef3c = 1; 2 = stopped).
- `0x852a2 demo_input_record` is dormant: its only call at $85150 is gated
  by ROM byte $85138=0. It would pack timed XOR-bit toggles into the
  stream selected by the **pointer** $10ef30 (not a buffer located there).
  `0x8533e demo_input_playback` applies those toggles to synthetic bytes
  $10ef2e/$10ef2f through `0x853de demo_event_next`. $85386 appends delays;
  $853b0 merges the bit index into the old event cursor byte and advances
  that cursor; it does not immediately replay the event.
- The last event of all five ROM streams toggles P1 bit 7 (D): attract
  $6b886 and how-to-play $e790 end on it. Final cursors are
  $43/$54/$47/$51/$53, well before byte $200. How-to-play reads the real
  BIOS merge byte `input_merge_bios` at $e760 (A/B/C mask $70) and, in the
  dormant recording arm, $e77e (D bit 7). See semantic-settle-card-demo.

## 9. Palette subsystem (0xb96f0 - 0xb9dd6 and 0xca4b0 - 0xca99c)

**Slot record** (32 bytes at 0x102fc8 + slot*0x20): +0 flags (bit 0 in use,
bit 1 animated, bit 2 pinned, bit 3 skip upload, bit 7 free), +2 long source
palette (bank 0xad730 + id*0x40, or the temporary bank 0xb7ef0), +6 refcount,
+8 animation period, +a long animation target, +e animation step, +f
direction, +0x10 animation counter. The free ring is **0x102ec2** with cursors
0x102fc4 (alloc) / 0x102fc6 (free) and count 0x102fc2.

**Hardware palette shadow**: 0x104fd2, 32 bytes per slot, word 0 doubles as the
slot's dirty flag; the backdrop entry sits at +0x1fe0. Fade levels are the
bytes 0x104fca/b/c (0..0x1f, per channel D2/D3/D4; config names them
palette_dirty_flag2/3/4) with control flags 0x104fc8/0x104fcd/0x104fce/
0x104fcf.

Flow: actor code allocates/points slots (`0xb9a22`, `0xb9b4c`, `0xb9d90`,
streams `0xb9cb6`/`0xb9bde`) → the per-vblank driver `0xb9de6` applies fade/
animation steps (`0xca4e6` + `0xca55e`/`0xca5e2` blending through the ramp
table 0xca030, optional fade modes `0xca6fc`/`0xca766`) and writes the shadow
(`0xca4b0`, RGB555 table 0xba030) → `0xca892 palette_upload` (vblank) copies
dirty slots into palette RAM 0x400000 → fade actors (`0xb9740` fade in,
`0xb97d2`/`0xb9814` fade out, `0xb9890` fade-to-colour) drive the levels. The
wipe task 0xca926/0xca934 clears palette RAM in 512-byte blocks
(counter 0x104fd1).

## Open questions

1. **The sound-test module (0x804fa-0x8080c)**: *answered
   (semantic-review-p1-7fca6):* root_attract_title_flow spawns it at 0xe602,
   gated by dev_flags bit 0, which retail code only clears.  Its siblings
   0x803bc, 0x80460, 0x804b6 stay dead.
2. **The orphan card page [0x841d8,0x84398)** (its despawn tail 0x84398 is in
   the same span): the sibling of the card page 0x83dea (same print/spawn
   pattern, START branch at 0x841e6), but no reference anywhere.
3. **0x84ff4**: a 26-byte input-source query (`0x10ef3c == 2` -> carry), no
   reference anywhere.
4. **Entry into the attract intro sequence** (0x7e37e / 0x7e302): *settled
   (semantic-review-p1-7d4be, -7e726):* 0x6b7ea -> 0x7e2d4/0x7e302 ($7e320)
   -> 0x7e372 -> 0x7e37e.
5. **0x10fd83** (read by the intro steps, the title and the credit code): a
   BIOS-written byte.  Within the staff roll and title it selects the export
   text "NEO TURF MASTERS STAFF" over "BIG TOURNAMENT GOLF STAFF" (0x7fba6),
   the strip set and layout (0x7edf6) and suppresses sound $11ff (0x7edb0),
   which matches the BIOS convention 0 = Japan (semantic-review-p1-7e726).
6. The intro's step data region (0x7f078-0x7f93c) mixes word streams, frame
   records and the tables cited above; its full record grammar is still
   p1-mid's open item (this lane only pins the pieces its code consumes).

## Settled palette behavior (2026-10-14)

`palette_blank_bank_select` controls the final display bank in `palette_upload`:
zero leaves bank 1 (game palettes), nonzero selects bank 0 (black after the
wipe task clears it). Uploads still update bank 1 while it is hidden. The
wipe task itself ends on bank 1, so it overrides the final latch in frames
where it runs (semantic-settle-effects-palettes).

`palette_slot_alloc_force` has one caller, `pose_panel_actor` at $7b91a.
The zero-free branch returns at $b9b00 without popping its saved registers,
but static actor/palette accounting gives free >=15 at every call, making
that branch unreachable. Both normal $b9a22 and forced $b9b4c allocation
advance `palette_free_alloc_cursor` (semantic-settle-r2-palslot).
