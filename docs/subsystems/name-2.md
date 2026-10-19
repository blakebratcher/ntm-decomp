# name-2 subsystem notes — P1 fixed code [0xd54a, 0x59cf2)


Scope: every source in the P1 corpus whose `org` lies in
the range (191 sources: 167 functions + 24 blocks, 16,308 bytes of code), plus
the work-RAM they touch. Nothing here changes a byte; the names live in
`config/names/name-2.tsv` and `config/ram/name-2.tsv`, the prose in the header
blocks of each source. Evidence is the instructions themselves plus the
companion workspace `FINDINGS.md` where it decodes the same code.

The range is three things at once:

| Range | Content |
|---|---|
| 0x0d54a-0x0ffff | boot, cartridge vectors, IRQ handlers, fix-layer drawers, the start/credit gate, the title/attract/round/scorecard state machine, the select screen |
| 0x54c3c-0x55e3a | the golfer/club/aim actor set (spawned per shot) |
| 0x56022-0x57xxx | club icons, wind machinery, a path-following sprite, the MVS shot clock, the LEVEL SELECT heading actor, palette streams and the hole records (0x57b44); no scorecard and no ball actor (*corrected, semantic pass wave 3*) |
| 0x58274-0x59cf2 | the shot sequencer, hole/turn/score logic, the terrain/perspective course renderer |

Almost 295 KB of the 313 KB range is *data*: the four course blobs
(`fixdata.tsv` course0-3_holes, ~281 KB), the soft-DIP layouts, and the many
small tables the code reads (each row's evidence names its consumer). Those
were classified by the p1-lo / fixdata lanes; this lane only documents the
code that reads them.

## 1. Machine skeleton (0xd54a-0xf4c0)

**Boot.** The BIOS jumps to the cart USER vector (`user_entry` 0xe36e) with
`$10fdae` = user request. The game installs the raster hook `$102ea8`,
unmasks interrupts (`andi #$f8ff,sr`; the CPU stays in supervisor mode) and
dispatches through the table `$e39e`: 0 = startup init `$e3ae` (backup-RAM
defaults, then SYSTEM_RETURN), 1 = SYSTEM_RETURN (eye-catcher), 2/3 =
`$e3ee` (game init `$e4c6`/`$d67c`, then the actor main loop `$5b4`).
`boot_init_watchdog` (0xd694, fall-through from `game_init_core`) builds the LSPC
(VRAM clear through `$678`), the 0x50-entry actor pool (`$428`) and the
input sampler, then returns at 0xd6c2. The former 0xd696 name was on an
operand; its apparent pointer at 0x1998 is quarter-sine sample 648, not a
boot task. `player_start_handler` (0xe238, cart vector $128) is the
PLAYER_START handler: it filters BIOS_START_FLAG, seeds each starting
player's hole stock (player_status +$44) from the soft DIPs HOLE(1st)
`$10fd88` / HOLE(cont) `$10fd8a`, and raises `challenger_request`
(`$102e58`) when a second player joins; `start_latch_consume` (0xd6c4)
then installs the challenger join `challenger_join_enter` (0xef7e) on the
`$100160` actor. No coins or credits are counted there (*corrected,
semantic pass wave 3*: not "credit bytes into credit counters", not "drops
to user mode").  `$10fd84-$10fd93` is BIOS_GAME_DIP, the soft-DIP block
(TIME, HOLE(1st), HOLE(cont), HOLE PENALTY, MATCH HOLE, CONTINUE,
DIFFICULTY, page break, PLAY MANUAL, DEMO SOUND, CREDIT/LEVEL, LANGUAGE;
labels from the cart's tables at 0xda2c/0xdcc8/0xdf64).

**Interrupts.** `vblank_irq` (0xe440) is autovector 25 (vblank, level 1)
and 27 (level 3): save all, VRAMMOD = 0, `jsr $d430` (per-vblank work: IRQ
ack 4, watchdog poke while `$102e59 <= $14`, palette upload `$ca892`, VRAM
queue flush `$5d8`, the task dispatcher `$d4c8`, audio `$803f6`),
`video_50hz_detect` (0xe4ae, the 50 Hz flag `$102eae` from REG_LSPCMODE
bit 3, not a board revision) and the BIOS tick `$c004ce/$c0044a`.
`irq2_raster_hook` (0xe41c) is autovector 26 (raster, level 2): ack 2, call
the current `raster_hook` (`$102ea8`, really the raster hook: only this
handler calls it) and restore VRAMADDR from `$102e5e`.
Vectors 5/6/10 (line-A)/11 (line-F) run `trap #$f`, whose vector 47 (and
TRAPV, 7) is the `$e416` hang loop; 28-31 (levels 4-7) are `rte`
(*corrected, semantic pass wave 3*).

**Two coroutine layers.**

- *Actors* (`$428`/`$4c6`/`$562`/`$598`): 0x60-byte records from
  `$100100`; slot 0-3 static, rest a free list at `$102080`. Record layout
  (offsets used all over this lane):
  `+0` resume PC / state, `+4/+8` sibling links, `+c` parent,
  `+10` depth, `+11` wake flag, `+12` flags, `+14` wait counter,
  `+16` current frame list, `+1c` last parent frame, `+20` palette slot +
  `+22/+24/+26/+28` slots 1-4, `+2c` frame pointer, `+30/+32` x/y,
  `+34` hide flag, `+36-+39` 8.8 velocity bytes, `+3a/+3b` zoom x/y,
  `+3c` priority, `+3e` flip, `+3f` club type (golfer actor, bag_get_club),
  `+40` per-golfer x bias from `$54fb2` (golfer actor; the wind indicator
  uses `+40` as its cycled palette bank), `+42/+46` swing/putt row tables
  walked by swing_engine through the cursor `+16`, `+4a` the zoom-in anchor
  x, `+50/+52` bag bounds; golfer `+34` bit 7 is set by swing_engine row
  marker 2 and read by the lie-6 overlays as their hand-over trigger
  (*corrected, semantic pass wave 2*: not "club id / club palette slot /
  pose tables / x offset"). `actor_draw` (0xf08) feeds `$728`/`$71e`; the
  standard state idiom is `tst.b ($11,a6) / bne next / rts` plus
  `clr.b ($11,a6); move.l #$STATE,(a6)`.
- *Tasks* (`$d510`/`$d4c8`): 8 slots of {handler, actor} at `$102e60`,
  round-robined every vblank.

- *Resume hook* `$102eb0` and raster hook `$102ea8`: `$102eb0` is the root
  flow's resume PC (`$e8d4`/`$e968`/`$ea10`/`$eca4`, stored with
  `move.l #$eca4,($102eb0)`), jumped through only at 0xeca2/0xee6e; it is
  not a per-frame hook (*corrected, semantic pass wave 3*).

## 2. Screen flow (0xe7c2-0xf4a0)

```
root actor $100160: root_attract_title_flow 0xe558 (demos / title / START /
        how-to-play) -> post_start_enter 0xe7c4 (post-START: memory-card screen
        $85484) -> post_start_mode_select 0xe7f4 (mode select 0x6a43e;
        $10fdaf = BIOS_USER_MODE 2)
  -> golfer select golfer_select_enter 0xe8d4 -> course select 0xe968
  -> pre-round preround_intro_wait 0xe9e2 (game_mode 2: vs screen; else
        leaderboard + 0x782c6)
         |-- dev_flags $100000 bit 0 (debug) --> 0xead0 dev_hole_select
         |-- else ----------------------------> 0xeb2e hole start
hole:   0xeb2e loads the hole, pre-hole screen 0x78c22; 0xeb7e spawns
        shot_sequencer 0x58274 once; 0xebb2 waits (woken at 0x5866a)
        -> nobody playing: game end 0xee70
        -> game_mode 2 with one player left: drop-back 0xec0e/0xec36/0xec56
           (VICTORY/DEFAULT 0x6c3a2, stroke-play final notice 0x6c51e,
           jmp resume_hook)
        -> else scorecard_enter 0xeca4 -> scorecard_flow 0xed08
scorecard_flow 0xed08 -> next hole 0xeb2e (dev_flags bit 0: 0xead0);
        game_mode 2: match decided -> prompt 0xedbc (rematch 0xe8da /
        resume_hook / game end); all square after 18 -> playoff from hole 1;
        else past hole 18 -> game end 0xee70 -> card save, exit_to_bios 0xef6a
challenger: start_latch_consume 0xd6c4 -> challenger_join_enter 0xef7e (challenger
        join) -> challenger_join_setup 0xefd2 (player_state_save,
        game_mode 2) -> challenger_message_screen 0xefea (message) ->
        challenger_message_actor 0xf036 -> golfer select 0xe8d4
```
(*Corrected, semantic pass wave 3*: 0xe8d4/0xe9e2/0xe8da are live-game
golfer select, pre-round and rematch, not demo paths; the demos run through
attract_round_setup 0xf22e.)

`game_mode` (`$102eba`) is the play mode: `$ff` unset (title/attract), 0 =
one-player stroke play (the demos also run as 0; the only choice of the mode
select with one player), 1 = the other two-player choice (probably two-player
stroke play, unproven; never written at runtime), 2 = match play (also set by
the challenger join 0xefd8);
`attract_demo_index` (`$102ebb`) is 0 for a live game, 1-4 for the four
attract demos and 5 for the how-to-play demo after START (it selects the demo
golfer `$f2d0`, course `$f2ea` and wind `$56f66`; corrected from MAME write
taps in the lane report). The BIOS start gate is
`start_credit_trigger` (0xd926) plus the `$102ea6` release byte
(`start_block_set/release` 0xd9e2/0xd9ec), which every screen transition
toggles; `input_active_player` (0xf1dc) copies the active player's input
triplet `$10d514-16`/`$10d51f-21` into the game bytes `$10d52a-c`.

## 3. The shot pipeline (0x54c3c-0x59c18)

1. `shot_sequencer` (0x58274) increments the stroke count (player record
   +2), computes the pin distance `$10d4aa` and the lie `$106fd6` (map query
   `$5b980`), seeds the aim angle `$107080` with atan2 of the target axes,
   and spawns hole_screen_build `$61304` and the golfer set
   `golfer_actor_spawn` (0x54c3c).  On lie 3 (the green) it branches to the
   green-shot setup 0x58680 instead and spawns neither.  (*Corrected,
   semantic pass wave 2*: not a "camera scale" or a "HUD actor".)
2. `golfer_setup` (0x54dd2) installs the per-golfer pose tables
   (swing `$54f82`, putt `$54f9a`, 6 longs each; x bias `$54fb2`; 4 palette
   ids `$54fbe`), then `golfer_swing_actor` (0x54eaa) drives
   `swing_timing` (0x80e5a) + `swing_engine` (0x80ce6) or the putt engines.
   The club chain: `club_min_index`/`club_max_index` (0x55e3a/0x55e06, the
   lie also limits the clubs) feed `club_select_actor`'s step bounds and
   `club_auto_pick`'s clamp; `club_auto_pick` (0x55d64) auto-picks the nearest club
   for target_distance; `club_hud_draw` (0x55c74) draws the club name +
   BCD distance HUD; `club_icon_draw` (0x561a0); then the hook/slice
   shot-curve selector (0x56284/0x562d2, `shot_curve` `$106fe4`, 4 = straight;
   B = toward hook, C = toward slice).  (*Corrected, semantic pass wave 2*:
   `$106fe4` is the shot curve, not a loft.)
3. `club_select_actor` (0x55abe) is the club-selection actor (bag bounds, auto-pick,
   up/down stepping via 0x55b78/0x55ba0); it draws no aim.
   `ball_marker_actor` (0x566ca) is the ball-position sprite on the map; the
   arrow is its child `aim_marker_actor_556dc` (0x556dc).
   `aim_offset_actor` (0x56582) steps the aim offset from left/right;
   `wind_nudge_input` (0x55640) lets the direction input change the wind
   (`$106fe2` direction, `$106fe0` strength) only with the dev flag `$100001`
   bit 1.  0x5548c (`wind_indicator_actor`) is the on-screen wind indicator:
   arrow frame from `$106fe2`, strength digits through `wind_strength_digits_draw`
   (0x56c38, two digits), palette cycle at a rate from `$106fe0`.  0x56d02
   (`qcf_a_motion_detect_dead`, which starts at 0x56d02, not 0x56cfa) is a dead
   joystick-motion detector, unrelated to wind.  `wind_setup` (0x56e80) sets
   the wind per hole from hole_load, and 0x56eda re-rolls it per shot from
   shot_sequencer.  (*Corrected, semantic pass wave 2.*)
4. `shot_swing_entry` (0x58442) first waits for a child's +$11 signal and
   serves only the non-green lies (the green path is 0x58680 -> 0x58752 ->
   0x58798).  It sets `shot_kind` (`$107082`, the shot kind: 1 =
   full swing), spawns
   a path-following sprite (0x570ec, placed once by 0x57590 on a $c80 map
   tile on the ball's row and stepped by the map-keyed streams of 0x5713c;
   not the ball) and calls the launch `0x4cbe` (outside this lane). The MVS
   shot clock 0x57658 is spawned at shot setup and counts down the soft-DIP
   TIME setting `$10fd84` (BCD mm:ss); at 0 it spawns the "+1 STROKE
   PENALTY" banner 0x7a150.  (*Corrected, semantic pass wave 3*: no ball
   actor here, no BIOS timer.)
5. `hole_complete_check` (0x58aee) decides when the hole ends;
   `hole_result_decide` (0x58c00) decides a match-play hole (winner/loser);
   `hole_result_record` (0x58b64) records the tee honour (+$24) and the per-hole
   bytes; who plays next is `active_player_select`/`active_player_pick_far`
   (0x589b0/0x589f6) (*corrected, semantic pass wave 3*).  `green_stroke_wait`
   (0x58752) is the green path's stroke wait: it waits for the meter lock,
   launches the ball and snapshots the aim vector as a side step;
   `green_roll_wait` (0x58798) is the green path's post-launch state (lie copy,
   wait for `$107085`).  (*Corrected, semantic pass wave 2*.)

**Terrain and the course renderer.** `map_cell_query` (0x59312) is the single
terrain reader: `(x>>4, y>>4)` into the shadow map `$1070a2` with the row
stride from the render context `$107086 +$c`. The render context is
initialised by `course_render_init` (0x593c0) and holds
`+0/+2/+4` VRAM base/end/cursor, `+6/+8` x/y scroll, `+c` map rows,
`+12/+14` strip counters, `+16/+18` view offsets, `+1a` palette bank nibble,
`+1b` rotation phase. `course_strip_draw` (0x598f8) walks the map and writes
(VRAM addr, tile) pairs to `$3c0000`, converting each terrain word through
`terrain_tile_convert` (0x59972: attribute tables `$599d2`, the 16x16 byte
map `$58d18`, the block table `$58e18`); `course_strip_redraw_fwd/back`
(0x59784/0x597fc) and `view_rotate_left/right` (0x59742/0x596f4) re-render
when the camera moves. `green_grid_draw` (0x58c64) draws the putting grid
from the same map.

## 4. Key data structures

- **Actor record** — 0x60 bytes, layout above; pool 0x100100-0x102080.
- **Player record** — 0x26 bytes at `$107004` (P1) / `$10702a` (P2);
  `current_player_record` (`$107076`) points at the active one. Fields used
  here: `+0` player slot 0/1, `+1` golfer id ($ff = unused record), `+2`
  strokes on this hole, `+3` lie/club byte, `+4/+8` ball x/y (×4),
  `+10..+21` per-hole bytes (strokes, or in game_mode 2 the match result
  0 halved / 1 won / 2 lost), `+22` hole-done byte (0 playing, 1 holed out,
  $ff over par+3 or gave up), `+24` tee honour (*corrected, semantic pass
  wave 3*: not 0x1a bytes, no `+26` field, `+2` is not a "ball stage").
- **Bag record** — 4 bytes `{u16 distance_x4, u16 club_type}`; per-golfer
  pointer table at `$8318e`; club-name streams `$55ef0`; icon table `$832f6`;
  fit bounds `$50/$52` of the aim actor.
- **Hole record** — 0x18 bytes; table of 4 course pointers at `$57b44`, 19
  records per course indexed by the 1-based hole (`$10d4bc`), the course
  `$10d4b8` (`hole_record_load` 0x5893a). Record +0 points to the hole data
  block (par at +1, hole_load input, stored as `$10707a`), +4/+$c sprite
  frames, +8..+$16 display x/y words (*added, semantic pass wave 3*).
- **Render context** — `$107086`, 0x1c bytes (above); the map shadow is
  `$1070a2`.
- **Wind pair** — `$106fe2` direction (16 steps = 360°) and `$106fe0`
  strength 0-15; the launch `0x4cbe` converts them to wx/wy via the sine
  table, so the wind is fully determined by these two bytes.
- **Task table** — `$102e60`, 8 × {handler, actor}.

## 5. Open questions

1. **`$100001` bit 1** gates the wind nudge (0x55640). *Settled (semantic
   pass wave 2):* `$100001` is the low byte of `dev_flags` (`$100000`, game
   work RAM), not a BIOS byte. The corpus only clears it (0xe230,
   0xe3c2/0xe3cc, 0xe4de), so the wind nudge is a dev feature, probably never
   active in retail. Only two readers in the whole corpus (0x55640 bit 1,
   0x5b0f0 bit 3).
2. **Wind from the pad** — the code unambiguously lets the direction input
   change wind direction/strength during the aim phase. Whether that is the
   normal mechanic, a debug leftover, or only reachable in some modes is not
   settled; it is the one place where the FINDINGS and the code needed
   reconciling (the FINDINGS only say the launch *reads* the pair).
   *Settled statically (semantic pass wave 2):* dev-gated (item 1). The
   nudge reads the P2 auto-repeat byte when the P1 record is active (the
   inverse of 0xf1dc).
3. **`$10fdbe/$10fdc2` record queue** (0x56bf8): the 22-byte records look
   like VRAM commands but the only consumer found is 0x8444a; the queue's
   owner is unidentified.
4. **`$102cd6`** (ball_surface_word): cleared at every start/shot boundary by
   six sites but no reader found inside this lane's code.
5. **`text_language_index`** (0xf4a0): the callers index 4-entry tables, the
   inputs are BIOS bytes `$10fd83`/`$10fd93`. *Settled (semantic pass wave
   3):* a text-language index (0 Japan / sprite art, 1 English, 2 Spanish,
   3 Portuguese; `$10fd93` is the European soft-DIP LANGUAGE byte), proven
   by the `$f026` challenger strings.
6. **Uncovered spans inside the range** (1,834 bytes, 33 spans) — the ones
   with decoded evidence are listed in the lane report; the notable
   ones are 0xd8ea-0xd91e and 0xd958-0xd9be (the start/credit gate head and
   its consume action), 0xf100-0xf1dc (the select-screen text streams, whose
   consumers are named; now the data row `p1ev_f100_strings`: a palette
   stream plus the three challenger strings) and 0xd9f4-0xda00 (the six-byte
   fix-tile strings drawn by 0xda00).

## Settled shot-clock behavior (2026-10-14)

The MVS time-up banner reads SLOW PLAY / +1 STROKE PENALTY. The forfeited
stroke was already counted at shot setup $5831c; time-up moves directly to
`shot_result_task` with the ball unmoved and does not add a second stroke.
Inputs-only DISCOVER traces at 6200/6358/6441 show expiry, result entry and
the next shot setup with the same stroke count until that new setup
(semantic-settle-round-flow).
