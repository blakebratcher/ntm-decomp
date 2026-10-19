# Subsystem: the round / course-play code (fixed P1 [0x63f00, 0x6cef0))


Lane **name-4**.  198 sources (165 functions + 33 flow-recovered blocks), all
named (`config/names/name-4.tsv`), all carrying a header comment block written
by the lane tool from the notes in that tool plus
the lane tool.  Nothing here changed a byte: comments and labels
only, `make check` green throughout.

This document explains what the region *is*.  Evidence conventions: `$xx` is a
ROM address, `0x10xxxx` work RAM, `A6` is always the current actor (coroutine)
pointer.  The companion RE workspace FINDINGS.md is cited as FINDINGS.

## 1. What the region contains

The range is the **round screen** (a hole being played) plus the pieces of the
select/title flow that live between 0x63f00 and 0x6cef0.  The round's vblank
hook `screen_hook_install_63b5e` (0x63b5e) sits just below the lane and its
per-frame uploader continues inside it; `shot_result_task` (0x6bee6) at the
top of the range's tail is the shot-result actor, not a per-frame task
(semantic-review-p1-6b9dc).

| Cluster | Addresses | Role |
|---|---|---|
| Backdrop bands | 0x63f00-0x641ae | SCB band-row upload + the 4-course backdrop blit and the five parallax rows |
| Course map cells | 0x659c0-0x65e80 | terrain cells: band draws, the 16x16 perspective tile grid, the cell tile table 0x10d996 |
| Golfer scene actors | 0x6784e-0x68204 | the 16-state scene dispatcher, pose handlers, animation stepper, scenery/shadow spawners |
| Actor script engine | 0x6854c-0x688ca | the 17-opcode coroutine script interpreter, six swing-contact scripts, the cc table |
| Ball effect actors | 0x68a96-0x6a058 | the ball effect director and its landing/impact/trail/bounce children |
| HUD + mode select | 0x6a134-0x6a708 | fix-layer records/panel renderers, the select-actor director and mode select |
| Mode-select panels + course select | 0x6a76a-0x6b08c | the mode-select panels 0x6a780-0x6ab9a (not aim/ball markers), the course-select director, cards and cursor (semantic-review-p1-69f74) |
| Round task + panels | 0x6b5fa-0x6cecc | the per-course course-select composite 0x6b5fa (not a portrait), the round frame task, fix-layer panels, results and end-of-round |

## 2. The actor / coroutine model (shared vocabulary)

Every "actor" is a coroutine: `(A6)` holds its resume PC, the scheduler
(`FUN_0000d4c8` ticks the {handler,actor} table `FUN_0000d510` fills at
0x102e60) calls the resume PC each frame and the actor re-writes `(A6)` before
returning.  Helpers used throughout this range:

| Helper | Meaning |
|---|---|
| `$4c6` | spawn a child from the template `A1` (fills the child's `+0x10` depth, links `+4` owner / `+8` sibling) |
| `$54a` | wait for the child (`A6+8`) to finish; `$598` despawns all children |
| `$5b4`/`0xd4c8` | the task walker that calls these resume PCs |
| `$f08`/`$f2e` | draw the actor's frame `(A6)+0x2c` into the priority list (`0x728`) or the plain list (`0x71e`) |
| `$b9a22` | allocate a hardware palette slot for palette-bank id `D1` |

Actor fields this range uses (offsets from A6): `+0x11`-`+0x14` scratch/phase
bytes, `+0x16` script or record pointer, `+0x20` palette slot, `+0x2c` frame
pointer (frame bank 0x88000-0xad730), `+0x30/+0x32` x/y, `+0x3a/+0x3b` zoom,
`+0x3c` priority, `+0x3e` flip flags, `+0x40`+ cluster-local state.

## 3. The actor script engine (0x68600 / 0x68618)

`script_install` (0x68600) points `(A6)+0x16` at a script and resets the
counters (`+0x1a` = 0, `+0x1b` = -1, `+0x1c` = -1); `script_interp`
(0x68618) reads one opcode byte and jumps through the 17-entry table at
0x68628.  Handler addresses below are the table entries; record layouts and
semantics are read off the handlers (re-verified 2026-10-12 in the semantic
pass: the original lane table had ops 8/10/11 rotated, gave op 15 no record,
called op 2 a wait and listed several handlers off by one row).  "Hold" ops
return to the caller (one tick per call, carry clear); the others loop back
to 0x68618 within the same tick.

| op | handler | record | meaning |
|---|---|---|---|
| 0 | 0x686ac | `{op, u8 n, u32 frame}` | hold: on entry with `+0x1a` = 0 load `n` and `+0x2c` = frame; draw through 0xf08 (0xf2e when `+0x12` bit 1); count `+0x1a` down, advance 6 when it reaches 0. `n` = 0 never advances (holds forever) |
| 1 | 0x686f4 | `{op, u8 -, u16 id}` | `palette_slot_alloc` (0xb9a22) with d1 = id |
| 2 | 0x6870a | `{op, u8 n, u32 addr}` | loop: jump to `addr` `n` times, then fall through (counter `+0x1b`, re-armed at -1) |
| 3 | 0x68732 | `{op, u24 addr}` | `jsr addr` |
| 4 | 0x6874a | `{op, u24 addr}` | `PC = addr` |
| 5 | 0x6875a | `{op, u8 -, u8 val, u8 off}` | `(A6+off).b = val` |
| 6 | 0x6876e | `{op, u8 off, u16 val}` | `(A6+off).w = val` |
| 7 | 0x68782 | `{op, u8 off, u32 val}` | `(A6+off).l = val` |
| 8 | 0x687b4 | `{op, u24 true, u8 cc, u8 off, u8 val, u8 -}` | byte compare: field `(A6+off)` against `val`, Bcc `cc` from the table 0x687d4 |
| 9 | 0x687ae | `{op}` | stop: return with carry set |
| 10 | 0x6886a | `{op, u24 handler}` | spawn a child (`actor_spawn_child` 0x4c6, A1 = handler); child `+0x3c` = own `+0x3c` |
| 11 | 0x68796 | `{op, u8 -, u8 val, u8 off}` | `((A6+0xc)+off).b = val` (store through the owner pointer) |
| 12 | 0x6884c | `{op, u24 true, u8 cc, u8 off, u16 val}` | word compare, as op 8 |
| 13 | 0x6888c | `{op, u8 -, u8 n, u8 sel}` | hold: on entry with `+0x1a` = 0 load `n` and call 0x6f02e with d0 = sel; draw through 0xf08; advance 4 when the count reaches 0 (`n` = 0 holds forever) |
| 14 | 0x688ca | `{op, u8 -, u16 arg}` | `jsr 0x804de` with d0 = arg |
| 15 | 0x6867e | `{op, u8 n, u32 frame}` | hold until the deferred record fires: call its routine; carry set cancels it (`+0x1c` = -1, `+0x1a` = 0) and jumps to its resume address; otherwise (or with no record) behave as op 0 on this record |
| 16 | 0x6866c | `{op, u8 -, u32 routine, u32 resume}` | defer: `+0x1c` = address of the routine long; advance 10 |

The compare ops compute field - value (`cmp d0,d1`); `cc` selects stubs at
0x687d4+4k in the order beq, bne, bpl, bmi, bcs, bls, bhi, bcc, blt, ble,
bgt, bge (0x6881c-0x68848); taken = `PC = u24 true`, not taken = `PC += 8`.
So `cc` = 6 (bhi) is "field > val, unsigned".

Scripts in the range: the six swing-contact scripts (pointer table 0x68534,
indexed by lie and power, walked cleanly), the ball effect script 0x688e0
(defer to 0x68c6c, resume 0x68958), the bounce script 0x6a0bc, and the
mode-select script 0x6a3de/0x6a428.

## 4. Data flow: course terrain to the SCB

1. `hole_screen_build` (`FUN_00061304`, below the lane; called once per hole,
   it does not walk cells) sets up three 8-byte extents `{low, high,
   lowflags, highflags}` at 0x10d972/0x10d97a/0x10d982 (0x62ad6/0x62b06;
   `FUN_00061e34` recomputes them through `FUN_00063602`)
   (semantic-review-p1-61620).
2. `extent_runs_draw` (0x659c0) draws the two terrain bands
   (0x65a06/0x65aaa) and the mid tile (0x65b8c) into the **cell tile table**
   at RAM 0x10d996 (56 rows x 9 cells of `{tile, attr}` longs, written by
   `FUN_0006299c`/`FUN_000629e2`), choosing the tile-offset stream by the
   delta of two extent bounds from the 33-record `{s32 threshold, u32 stream}`
   tables 0x64244/0x64748 and the variants 0x64c4c/0x65046/0x65440/0x65700
   (searched by `threshold_stream_search` 0x65b62) and sampling the 16x16 perspective grid
   0x65bd0 / 0x65cd0 (`cell_tile_grid_sample` 0x65dd0).
3. `backdrop_rows_setup` (0x63f86) + `scb1_ring_column_step` (0x64066) turn
   table rows into SCB entries: the `(row,layer)` tile/attr pair comes from
   `fix_row_cell_get/b` (0x64184/0x6417a, table A = the cell tile
   table above, table B a second 56x9 table at 0x10e176) and is poked at
   VRAM `(col+1)*0x40 + layer*2` through LSPC `VRAMADDR`/`VRAMRW`.
   A buffer row is one 16-pixel sprite column across the view; each cell
   is a 16-pixel tile band down that column ($640ee). The maps at
   $245328/$245528 stamp the teeing-ground pad in four/three depth bands
   through `tee_pad_draw` $61864 (semantic-settle-course-a).
4. `band_upload_arm` (0x63f00) + the uploader at 0x63baa stream the band
   scroll words from RAM 0x10d730 to VRAM one word per vblank.
5. `backdrop_band_blit` (0x63f38) blits the 4-course backdrop cell maps
   (table 0x612b4: `{u32 bank map, u32 palette stream}`) through
   `sprite_map_blit` (0xd1ee) and then loads the layer palettes through
   0xb9bde.

## 5. Ball flight feedback: the effect director

`ball_effect_state_machine` (0x68aee) runs four checks per frame and jumps to
the matching handler when one sets carry:

| state | address | carry when |
|---|---|---|
| 1 | 0x68c16 | ball settled, cell terrain 0xb, lie 0/1, `0x10000 < velocity < 0x100000` -> the settle script 0x688e0 |
| 2 | 0x68da4 | bend 1, terrain 0/3, vz in the bounce window, bounce count < 4, ball within 2.0 of the ground -> 0x68e32 |
| 3 | 0x68e32 | (the landing) spawns the impact/trail actors and sound 0x104a |
| 4 | 0x69188/0x69ed0 | the roll/bounce window -> 0x69f02 (the bounce effect) |

The children all use the shared animation driver `effect_anim_apply` (0x68cfc)
+ `effect_anim_advance` (0x68d36): a `{u32 frame table, u32 delay table, u32 y
delta table, u32 x delta table}` quadruple held in the actor's `+0x42/+0x46/
+0x4a/+0x4e` fields, with a `-1` frame pointer restarting the sequence.
`ball_cell_terrain_update` (0x68a96) publishes the ball's cell terrain code to
0x10ebae; `impact_actor_ground_check` (0x69188) and `impact_timer_check`
(0x69ed0) gate the timings.

## 6. HUD and panels (fix layer)

The round's fixed text is drawn by small writers that poke LSPC
`VRAMADDR`/`VRAMRW` with fix-map words (addresses 0x7000-0x74ff, 0x20 words per
row):

- `hud_records_draw` (0x6a134) / `hud_panel_clear_draw` (0x6a206) /
  `hud_player_panel_draw` (0x6a2f6): the per-vblank records + P1/P2 panels
  (called from the 0xd430 vblank work, FINDINGS session 8), gated by the
  game-mode byte 0x10ebbe and the BIOS mode 0x10fd82.
- `hud_digit_row_draw`/`hud_digit_tile_poke` (0x6a192/0x6a1f4): ASCII byte ->
  fix tile (`-0x30`).
- `default_win_panel_draw` (0x6c3b4) draws "PLAYER n VICTORY / PLAYER m
  DEFAULT" (match play with one player left; the kanji variant in Japan).
- `stroke_final_notice` (0x6c562) is the one-player "THE STROKE PLAY FINAL WILL
  BEGIN FROM HOLE NO. nn" notice (EN/ES/PT strings, a sprite frame in
  Japan), or the restart-from-the-start text; `restart_notice_es`-`c`
  (0x6c67c-0x6c694) are its localized strings.
- `match_hole_result_panel` (0x6c93e) is the match-play hole result: "HOLE NO. nn"
  plus a blinking "EVEN" or "PLAYER 1/2 WINS"; `result_even_draw`-`h`
  (0x6ca7a-0x6caee) are the EVEN/WINS draw and erase halves.  No score or
  par rows are drawn (*corrected, semantic pass wave 3*).

## 7. Round flow

`shot_result_task` (0x6bee6) is not a per-frame round driver but the
shot-result actor (semantic-review-p1-6b9dc): entry 0x6bee6 copies the lie,
entry 0x6bf06 (spawned when the ball stops) shows the lie display (record
from 0x6cef0, child 0x6d2a2) and then sets shot_end_code 1/2/$ff. The
contest-record panels 0x70258/0x70260 are spawned by 0x6cff4, the post-hole
contest sequence (not attract code).
`mode1_turn_panel_check` (0x6cce2) does not switch turns (hole_result_record 0x58b64
and active_player_select 0x589b0 do): in game_mode 1 it spawns a panel
(0x70260/0x70258) by the contest-hole byte. `other_ball_nearer_check` (0x6cc44) is
a shot-count + ball-distance test (probably a match-play concede offer,
unproven). `twop_dropback_panel` (0x6c6aa) is the Japanese restart notice; the
shared hold 0x6c6ce restores the snapshot and sets game_mode 0 (one-player
stroke play). `match_hole_tally` (0x6c89e) is the soft-DIP MATCH HOLE
tally on the +$44 hole stock through 0x5ab4e/0x5ab82/0x5abc6 (0x5abc2 is
mid-function). `credit_available_check` (0x6cd6e) is a CREDIT_CHECK (0xc00450) of
one credit on BIOS_CREDIT_DEC1 0x10fdb0, not a start poll (*corrected,
semantic pass wave 3*).

The select flow inside the range: `select_intro_state` (0x6ad48) ->
`select_actors_spawn` (0x6aea4, four course cards) -> the cursor actor 0x6b05c
(state 0x6b08c, with the input helper 0x6b236) writes the chosen course to
**0x10d4b8** -> `course_select_commit` (0x6b37a) reads it and loads its palette
stream (semantic-review-p1-69f74).  `mode_select_frame` (0x6a622) is the play-mode
select that publishes to 0x102eba: 0 = one-player stroke play (the only
choice with one player), 1/2 = the two-player choices, 2 = match play
(*corrected, semantic pass wave 3*: not "0 = attract, 2 = coin-accepted").

## 8. RAM structures decoded here

| Address | Size | Meaning |
|---|---|---|
| 0x10d730 | 0x80 | four band rows (0x20 each) uploaded to VRAM per vblank |
| 0x10d972..0x10d989 | 3 x 8 | three extents `{low, high, lowflags, highflags}` (0x62ad6); "committed" only reflects 0x61e34's save/restore (semantic-review-p1-61620) |
| 0x10d996 / 0x10e176 | 0x7e0 each | the two 56x9 `{tile, attr}` tables (cell rows / second band) |
| 0x10e95c | 0x21c | hole_marker_list, 12-byte `{object ptr, word, word}` records; there is no effect list at 0x10e950 (0x641ae pre-increments that base by $c) (semantic-review-p1-61620) |
| 0x10e956/0x10e958 | words | in state 2: extent 0 low>>4 / high>>4, used as fix_row_buffer row indices; 0x10e95a = cell state 0/1/2/3 |
| 0x10eb7e | long | nearest_object_ptr (negative = no type-6 object); 0x10eb78/0x10eb7c = hole_marker_list cursor/count |
| 0x10ebae/0x10ebba | words | ball cell terrain (current / published) |
| 0x10ebb0 | byte | ball effect active |
| 0x10ebb2/0x10ebb7/0x10ebb8 | bytes | trail stream pointer / shot effect id / shared x |
| 0x10ebbe | byte | game-mode bits (bit 0-3 HUD gates, bit 4 tile base, bit 7 clamp) |
| 0x10d4b8 | word | course index 0-3 |
| 0x107080/0x10707e | words | current_player_record + 0xa/+8 (pos_x high/low) |
| 0x106fd6/0x106fd7 | bytes | ball lie type / its variant byte |

`config/ram/name-4.tsv` names 65 of these with per-address evidence; the ones
whose role is still unclear are listed in section 10 instead of being given a
hollow name.

## 9. Tools

| Tool | Purpose |
|---|---|
| the lane tool | map/show/refs/ram evidence collector for the lane |
| the lane tool | writes the header blocks (ram/calls lines auto-derived from the source + symbol tables) |
| the lane tool | the authored header notes (data module) |
| the lane tool | walks actor scripts under the opcode size table (proves the format) |
| `tests/test_lane_name4_scripts.py` | tiles the six contact scripts + the ball script byte-exactly |

## 10. Open questions

1. **The exact SCB map base for the band rows.**  `scb1_ring_column_step`
   pokes VRAM `(col+1)*0x40 + layer*2` (+6 for table-A rows) and
   `sprite_map_blit` uses the same formula, so the *relative* layout is
   consistent, but the absolute SCB base is set by code outside the lane.
2. **The contact actor's lifetime.**  Its script has no stop opcode; the actor
   must be despawned by the swing engine (0x80ce6, outside the lane) or by the
   parent.  The last record of the 0x683fe variant is a `-1` frame sentinel.
3. **Terrain/lie code values.**  0x106fd6 (lie) takes 0/1/2/3/6/7/0xb and
   0x10ebae/0x10ebba (cell terrain) 0/3/0xb; the surface table at 0x7346 has
   types 0-8 (FINDINGS).  The mapping between the two numbering schemes is not
   decoded.
4. **0x102e55/0x102eb8/0x102eb9/0x102ebb/0x102cef/0x102cd2** semantics are
   only partly pinned (they gate panels and effect phases).
   *Semantic pass wave 3:* 0x102eb8 is seeded from the soft-DIP DIFFICULTY
   (0xe56e) and acts as a difficulty term; 0x102eb9 nonzero restricts the
   PLAYER_START handler 0xe238 to players at the continue prompt.
5. **Uncovered bytes.**  the completion gate leaves 8,628 bytes in 29
   ranges inside the lane (listed in the lane report); the largest is the
   6,584-byte 0x65e96-0x6784e record table, followed by small gaps inside the
   mode-select actors (0x6a780-0x6a8e2; not effect actors, semantic-review-p1-69f74)
   and the effect actors (0x6ab9a-0x6ad48, 0x6b9dc-0x6bb18) whose
   frame data (0x9dxxx) is consumed but not yet classified.
6. **Scene animation tables.**  [0x6778e, 0x6784e) inside the big unknown
   table is 8 x {4-word, 8-word} frame-offset lists consumed by the scene
   actor (0x67b46 -> 0x67ba8 with 4 frames, 0x67c4e with 8); see the lane
   report for the proof.  The rest of 0x65e96-0x6778e stays unknown.
7. **Region rows that overlap lane code.**  `config/regions/p1/mid.tsv` rows
   0x68274+0x2d8 and 0x6884c+0x24a cover the interpreter bodies and the
   contact scripts; now that 0x688e0 is proven to be a *script* (installed by
   0x68b88) the row is better split (proposal in the lane report).

## Settled effects and keyed holes (2026-10-14)

`hole_map_flag_draw` ($61652) matches the six cliff holes: course 0 holes
2/12, course 2 holes 4/17 and course 3 holes 8/11. Screenshots show cliffs on
all six; only 0/2, 0/12 and 2/4 have the waterfall key words
$0002/$000c/$0204 at $61522 and send sound $104e. Its M1 chain selects the
SSG noise definition $7d2e and a decaying noise sequence (file $19606),
correlated with waterfalls in inputs-only tee views. Which cell list or
backdrop band supplies the cliff pixels was not isolated
(semantic-settle-course-a).

`bounce_effect_script_init` ($69f74) reads the **scaled collision cross
product** `deflect_cross_scaled`, not a surface-dirty field. It uses the
long sign and thresholds the low word (NEG.W for negative). The lane's
velocity/contact-radius bound gives |value| <= 0.735 < 1, keeping the
$6a114 threshold index in 0..3 (semantic-settle-effects-palettes).
`marker_range_check` ($62378) returns carry clear for types 8/6/3/$b and
object flag +1 bit 3; these paths bypass interval comparisons.
