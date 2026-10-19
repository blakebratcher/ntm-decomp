# Subsystems in P1 [0x59cf2, 0x63f00) — lane name-3


Understanding pass over the 156 code sources whose `org` lies in this range.
Nothing in `src/` changed except labels and comments; the byte gate stayed
green at every commit.  Numbers: **41,486 bytes in range**, of which **16,200
are code sources** (156 files: 143 functions + 13 reached blocks) and 25,286
are the inter-code data islands already classified by the p1-lo / p1-mid lanes
(flag tables, stamp ladders, course-select tables). **238 bytes are still
unclassified**, all of them small table heads (list at the end).

Names: `config/names/name-3.tsv` (156 rows), RAM: `config/ram/name-3.tsv`
(157 rows incl. the LSPC registers).  Source headers carry purpose / in / out /
clobbers / RAM / calls; 34 files additionally have inline notes.

## The five subsystems

### 1. Round state, HUD distances and the shot record (0x59cf2–0x5a450)

A leaf cluster of small routines called by the swing/putt code outside the
range, plus one round-setup chain:

- `hud_pin_distance_draw` / `hud_shot_distance_draw` (0x59d10 / 0x59d74) are
  the two HUD readouts, drawn alternately by the HUD actor at 0x59c18.  Both
  compute a 16.16 magnitude (`pin_distance_calc` vs `shot_distance_calc`),
  convert to yards, BCD it (`bcd_convert`) and write four fix digits through
  `number_draw_4_blank` ($567c2). Pin distance stores the integer word at
  `target_distance` before shifting right two (four units per yard); shot
  distance divides the 16.16 magnitude by four in $59daa. Labels are REST
  ($2385..) and DRIVE ($1389..), both with a Y unit at column 11. The
  readouts differ in palette (attr $2300 vs $1300) and in
  which vector they measure: ball→pin ($10D4C6/CA) vs ball→shot origin
  ($10D4F0/F4, latched by the ball actor at address time).
- Three magnitude helpers coexist: `vec2_length_word` (0x59dc4, 16-bit inputs,
  the `divu` ratio form), `vec2_length_16_16` (0x59e0e, used by
  `vec2_normalize`) and `vec2_length_16_16_div` / `..._sqrt` (0x59eb2 /
  0x59ef8) for the shot-distance and the pointer-table path.
- `round_player_init` → `scorecard_clear`; `player_state_save` /
  `player_state_restore` snapshot the active player record into $107050 when
  a challenger joins (only caller 0xefd2) and restore it when that match
  drops back to one player (the notice hold 0x6c6ce); restore also zeroes
  the other record, and the resume hook $102EB0 = $E968 is part of the
  snapshot (*corrected, semantic pass wave 3*: nothing "undoes a shot").
- `hole_load` (0x5a054) is the hub: it consumes the fixdata hole record
  (RLE tilemap → `tilemap_rle_decode`, the pin from the 8 pairs at hole
  record +2 → `pin_position_set`, the tee object (type 7) → `tee_position_set`,
  wind → `wind_random_set`, hole band → `hole_band_lookup`)
  and lays both players on the tee with the ground height from `FUN_0005929A`
  (at that point the type-6 point list `type6_point_list` and band are still the
  previous hole's; the list holds type-6 object x/y, not pins: semantic-review-p1-54c3c).
- `atan2_12bit` (0x5a450) is an asin over a 518-entry sine-threshold table
  (0x5a4ae-0x5a8ba), valid for unit vectors, used by the shot sequencer
  (*corrected, semantic pass wave 3*: not an octant arctangent; the pin is
  not an object scan).

### 2. Ball actor and terrain query (0x5a8ba–0x5bc98)

The ball is an a6 coroutine whose states are installed in `(a6)`:

```
0x5AC64 setup   -> (entry 0x5AC54) latch player pos into a6+$42/46 and the
                   shot origin $10D4F0/F4; spawn the other player's ball
                   $5B510 when game_mode != 0, and $68AEE only outside an
                   attract demo and not for a putt
0x5AD28 wait    -> wait for swing_lock bit 7
0x5AD44 driver  -> hold timer; bit 1 -> 0x5ADDC; holed -> 0xB6A0; bit 0 ->
                   0x95AA, else 0x76BC / 0x75AA
0x5AE8C idle    -> the pad is read only with dev_flags bit 0 (never), then
0x5AEA0 publish -> per frame: player record +4/+8/+C, camera scroll step
                   ($10D4A2/A4/A6), $10D4F8/FA, $10D504/506/508/50C,
                   draw via $5B030 unless the band test $5AFF0 hides it
0x5ADDC stopped -> motion over (bit 1): terrain query $5B980 -> $106FD6/FD8,
                   near-tee test $6181E, spawns $5B62C / $5B67E
0x5B28C slide   -> relocation slide: the ball moves to the 0x5B3BA target,
                   then is placed, saved or released
0x5B3BA search  -> relocation search: from the shot origin for ball_lie_copy
                   5/7/8, else from the ball; +y until surface 0/1/2;
                   entered for surface 6 and spawned by shot_result_task
```
(*Corrected, semantic pass wave 3*: the input state does not poll the pad,
0x5ADDC is the motion-over state, 0x5B28C/0x5B3BA do not touch the hole or
the flag cell.)

`terrain_type_query` (0x5B980) is the physics-facing core: map word position
→ surface type in {0,1,2,3,5,6,7,8,$B} (never 4; 4 is the tee value forced
by 0x5AE80), via the decoded column-major tilemap $1070A2, 16x16 bit shapes
at $5B73A (8 orientations through the jump table $5BA6A) and the 16x16 byte
shapes $5BC98/$5DC98/$5FC98 selected by tile ranges ($400-$4FF, $B80-$C7F,
$B00-$B7F; rotations through $5BC32), then the result table $5BAE2.  7 has
several tile sources besides the band test.  (*Corrected, semantic pass
wave 3*: not "0..8"; nothing proves "blocked/water" or "7 = out of
bounds".)

Support: `ball_clamp_bounds` (course border $20, clamps a copy and sets
bit 1 = motion over), `ball_debug_mover` (a debug mover, dev_flags only: C
toggles bit 1, the D-pad moves the ball by $C000, or $200 with C held),
`mul3_div64` (3/64 scaling), `ball_relocate_search` (relocation search),
`contest_record_update` (the contest record in $10D4E4 with the holder
in $10D4EC: max shot distance on a longest-drive hole, min ball-pin distance
on a nearest-pin hole; 0x5a9a2 seeds the record at hole_load).
(*Corrected, semantic pass wave 3.*)

### 3. Input sampling and the credit screen (0x60198–0x604b6)

`input_sampler_reset` / `input_sampler_merge` fold the BIOS pad bytes
($10FD96/$10FD9C) and the two sampler halves into the game input words
$10D52A–D; `button_edge_check` (0x60268) returns `$10D52B & $70` and is the
single most-called test in the corpus (20 callers).  `credit_screen_state`
(0x603c0) drives the insert-coin / press-start prompt from the coin states
$10FDB6/7 and the credit timer $102E55, drawing text streams through
`FUN_0000D828`; 0x60444 / 0x60488 / 0x60440 are its prompt draws and thunk.

### 4. The hole screen (0x61304–0x6367e)

`hole_screen_build` (0x61304) is a straight-line constructor: it resets a
scratch block ($10EB78 = the marker list cursor, $10D83A = the slope, …) and
calls ~26 sub-builders in a fixed order, tagging progress in $10EB92
($7701…$7719) and poking the watchdog ($300001) between groups.  The order is
the pipeline:

```
cup_grid_build (0x62A4A)  hole record +$E list -> 52-entry cup grid $10D840
cup_grid_reset/extent_scan/slope_calc (0x62AB8/62AD6/62C42)
                          -> green extents $10D972/$10D982, slope $10D83A
far_extent_extrapolate (0x61E34), pin_marker_blend (0x61FDE), pin_marker_bars_draw
                          -> the pin marker columns $10D97A..$10D988
distance_marker_scan (0x61B16)  marker list +$A -> bracket $10D5F3..$10D5FA
green_object_scan (0x62CE2)     green objects -> 12-byte records $10E95C
hole_marker_sort (0x62304), marker_actors_spawn (0x62450) -> $6784E actors
hole_map_scale_bar_draw / hole_map_overlay_draw / hole_map_row_blit
                                -> the fix rows $10D996 + sprite map $10E176
```

Two blitters underpin all of it, entered through two labels and sharing one
body: `fix_row_blit_entry` (0x628DC, a1 = $10D996, 56 rows × 9 cells of
{u16 tile, u16 attr}, row stride $24) and `sprite_map_blit_entry` (0x628D2,
a1 = $10E176).  The stamp ladders of the green (`green_marker_draw_a/b/c` +
`green_stamp_draw`, 0x63108/0x63244/0x634A0/0x63174) walk the p1-mid-validated
{u16 threshold, u32 bank_ptr, u8 w, u8 h} records and stamp banked strips.

The hole map is a *rotated frame*: `cup_axis_calc` / `cup_local_x` /
`cup_local_y` (0x63546/0x635CC/0x63602) project world points onto the axis
($10EB9C,$10EBA0) with the offsets $10EBA4/A8, and `ramp_7x_over_x16` plus
`fixed_div_scaled` (0x63516/0x63504) turn depths into marker indices.

### 5. The screen-wipe vblank hook chain (0x6367E–0x63F00)

`hole_screen_activate` (0x6367E) programs the fix/sprite VRAM windows through
$3C0000/$3C0002, clears the two 64-long row buffers $10D62C/$10D730, disables
the raster IRQ (REG_TIMERSTOP $3C000E = 0), installs the vblank hook $63B5E
into $102EA8 and seeds the per-course VRAM source words $10EBCE/D2/D4.

`screen_hook_install_63b5e` (0x63B5E) is the hook installer the workspace found
for the round screens (FINDINGS session 8): it programs REG_LSPCMODE ($3C0006)
with the word at $102EAC ($30xx/$90xx around the mode byte), seeds the VRAM
window $10D61A/$10D61E and copies the RAM hook slot $10D622 into $102EA8.

The wipe itself is a chain of tiny vblank steps, each installed by the previous
one (`hole_wipe_hook_a` → `_a2` → `_b` → `_b2` → `_c` → `_d` → `_e` →
table $10D628 → `_f` → `_f2` → `_g`): every step counts $10D61A down and
streams the staged rows ($10D61E cursor) into VRAM as `move.l` long writes
(VRAMADDR/VRAMRW pairs, e.g. VRAMADDR=$8201 VRAMRW=$0400), with
`hole_wipe_span_calc` (0x639B0) deriving the row spans from the marker
distances and `hole_wipe_rows_build` (0x638D4) building the rows from the
per-direction tables ($606F4/$6093C/$60B84/$60DCC, selected by the aim
angle $106FEA, which FUN_00056582 keeps in step with the swing direction).  REG_TIMERLOW ($3C000A) = $7FFF in the tail disables the
raster timer again.

## Data structures

| Struct | Where | Layout |
|---|---|---|
| Player record | $107004 / $10702A, current ptr $107076 | +0 player slot 0/1, +1 golfer id, +2 strokes this hole, +4/+8 pos 16.16, +$C z 16.16, +$22 hole-done byte (0/1 holed/$ff), +$24 tee honour (*corrected, semantic pass wave 3*: +2 is not a shot state, +$22 not a palette slot) |
| Hole record (consumed) | pointer in $10707A | fixdata format; this lane consumes +2 (pin pairs), +6 (object list), +$A (marker list), +$E (cup grid list), +$12/+$14 (w/h cells) |
| Object record | (hole+6) list | 6 bytes {type, flags, x, y, ?}, first byte < 0 ends; types seen: 0, 2, 3, 4, 5, 6, 7, 8, $A, $B, $C |
| Marker record | $10E95C, count $10EB7C, max $2D | 12 bytes {ptr, 16.16 x-or-marker_depth, 16.16 y-or-object_depth}; type-8 appends retain fraction words; spare records retain prior builds |
| Cup grid | $10D840 | 52 × 3 words {projected x, y, flag}, sentinel $7FF0 |
| Fix row buffer | $10D996 | 56 rows × 9 cells of {u16 tile, u16 attr} = 2016 bytes |
| Sprite map buffer | $10E176 | same cell format, the sprite_map_blit source |
| Wipe rows | $10D62C → $10D730 | 64 longs of staged {VRAM addr, data} pairs |
| Per-player round state | $10D52E / $10D58E | 96 bytes each; +$44 = hole stock (seeded from the HOLE soft DIPs by 0xe238, -1 per hole, + for under-par holes / hole in one / contest wins; *corrected, semantic pass wave 3*: not a shot counter) |
| Hole band | $10D4D2/$10D4D4 | pair from the $5A1DC table keyed by (course<<16\|hole) |

## Evidence trail

- the lane tool — per-source callers/callees/RAM/ROM/hw scan
  over every lane's sources (it learned the PC-relative `jsr ($x,PC)` form,
  which had hidden most of the intra-cluster edges).
- the lane tool — instruction-level readers/writers for any RAM
  address (used for every name in `config/ram/name-3.tsv`).
- the lane tool — S-ROM fix-tile renderer: the fix words this
  range writes are tile codes, so the glyph can be read back.  Confirmed the
  scorecard/small font digits at S-ROM tiles $330–$339 and the club-name
  streams at $55F4C.
- the lane tool — the header/inline comment applier
  (idempotent, `--strip` to remove).
- Cross-checked against the companion workspace FINDINGS (round handler
  0x63B5E, terrain/physics surfaces, the $55xxx club tables, the sink decode at
  $958C) — never copied.

## Open questions

1. **$3C0006 (REG_LSPCMODE) writes.** The hooks write $30xx/$50xx/$90xx with
   the display mode ($102EAC, values 3–6 from the wind strength via
   `lspc_mode_set`) in the low byte, together with REG_TIMERHIGH/LOW and
   REG_VRAMMOD = $2FF/$145.  The exact bit meaning of the high byte and of the
   $2FF/$145 modulo is not decoded; MAME's lspc device is the next stop.
2. **The $5A1DC table (52 bytes, unclassified).** Six 8-byte records
   {u32 key, u16, u16} + $FFFFFFFF, consumed by `hole_band_lookup` with key =
   course<<16 | hole.  Keys 2 and $C fit (course 0, holes 2/12); $200, $2001
   and $3000 do not decompose, and the stored pair can exceed the course cell
   range, so either the table is keyed differently or the pair is at another
   scale.  A consumer-side parser should settle it.  *Settled (semantic pass
   wave 3):* all six keys decompose (course 0 holes 2/12, course 2 holes
   4/17, course 3 holes 8/11); the pairs are map-unit y ranges ($160..$840)
   inside each hole, between its tee and green.
3. **`ball_band_hide_check` (0x5AFF0) is not the sink.** The workspace decode
   puts cup capture at $958C/$95F4; this function is the coarse "ball y inside
   the hole band and below ground" test; on carry the ball is not drawn
   (*corrected, semantic pass wave 3*: $8CD9E is the putt frame chosen in
   ball_draw, not this test's). Settled by semantic-settle-misc-p1 and
   semantic-settle-round-flow: this is a sunken water crossing.
   `ground_height_query` returns -$d55 (ball_z -53.33) except on $12af
   cells, which return 0. Type-14 rock pillars stand in four of the six
   band holes; inputs-only ROUND_C4 course 0 hole 2 shows the sea gap and
   pillar. The other band keys are 0/12, 2/4, 2/17, 3/8 and 3/11.
4. **HUD icon tiles.** 0x59CF2/0x59D5E draw single tiles $2379/$1379 at the
   HUD strip; the S-ROM art renders as a small tapering glyph but the symbol is
   identified as Y (yards) in semantic-settle-round-flow. The labels
   $2385.. and $1389.. read REST and DRIVE, respectively; inputs-only
   DISCOVER frame 4000 shows REST 176Y / DRIVE 265Y matching the probes.
5. **Shot bookkeeping bytes $10D4ED/EF.**  Cleared by `hole_load`; the
   round-end screens are the readers needed to name them.  ($10D4EE, which
   `match_play_standing` reads, is not cleared there: it is the match-play playoff
   flag, set at 0xed72 and cleared at 0xe8f6; *corrected, semantic pass
   wave 3*.)
6. **The per-player counter at +$44** (`hole_stock_inc/dec/get`) is
   named from its saturating behaviour and its clear at hole load; whether it
   is strokes or a handicap-like allowance needs the score screens.
   *Answered (semantic pass wave 3):* not strokes, and hole_load decrements it
   (no clear). It is a hole stock: seeded from the soft DIPs HOLE(1st)/
   HOLE(cont) by the PLAYER_START handler 0xe238, -1 at every hole_load,
   + |strokes - par| for an under-par hole (+3 for a hole in one), +1 for a
   contest win, +1/-1 by the MATCH HOLE soft DIP, -1 for a hole at or over the
   HOLE PENALTY threshold; zeroed for the loser of a decided match.
7. **`obstacle_y_order_check` (0x5A3E4)** returns a maximum that `hole_load`
   discards - either dead code or the return value was used in an older build.
   *Settled (semantic pass wave 3):* a y-order assertion: trap #$f when the
   +4 words of the type 4/5/$A/$C records decrease (all 72 holes pass).
8. **`match_holes_up` (0x5A8BA)** counts 2s in a byte list at a0+$10; its two
   callers are in the score screens, the list's owner is outside this range.
   *Corrected (semantic pass wave 3):* it sums the match-play bytes with 2
   counted as -1 (holes won - lost); callers 0x6dc84 (aim markers) and
   0x78d44.

## Remaining unclassified bytes in range (238)

(*Historical table; the spans are classified now. Rows corrected in the
semantic pass, wave 3, where the lane's reading was wrong.*)

All are small table heads that sit immediately before a classified region row
(they are in the p1-lo / p1-mid classification lanes' scope); each has a named
consumer from this lane, so a row is one line away:

| Span | Bytes | What it is / consumer found here |
|---|---|---|
| 05a1dc-05a210 | 52 | the $5A1DC hole-band table (`hole_band_lookup`) |
| 05a314-05a31c | 8 | tail of the wind on/off flag table $5A306 (`wind_random_set`) |
| 05ab1e-05ab2c | 14 | head of the contest-hole tables (`contest_holes_pick` reads $5AB1E masks / $5AB3E course holes) |
| 05ac54-05ac62 | 14 | code: the ball actor entry (`ball_actor_entry`), not an island |
| 05b0ea-05b0f0 | 6 | code: `ball_draw_on_ground`, not table data |
| 05b216-05b224 | 14 | code: the 0x5b216 child sprite actor of the ball |
| 05b606-05b60c | 6 | code: the 0x5b5f4 path that installs the $5B60C state |
| 05b678-05b682 | 10 | code: the release trampoline 0x5b678 and the score-call actor 0x5b67e |
| 05b71a-05b724 | 10 | head of the sound-pair table $5B71A (`score_call_actor` reads it) |
| 0601b0-060200 | 80 | input sampler fragment (calls FUN_00085086 / fills $10EF24..; p1-mid range) |
| 0604ac-0604b6 | 10 | tail of `credit_screen_state` |
| 0614e4-0614f2 | 14 | head of the $614F2 table (`hole_map_flag_draw` reads $614DA+$A) |

## Proposed shared-file edits (coordinator)

1. `config/regions/p1/lo.tsv`: add rows for the table heads with named
   consumers above ($5A1DC hole band, $5A306 wind table, $5AB1E plan table,
   $5B71A lookup, $5BAE2 result table).
2. `config/regions/p1/mid.tsv`: extend the $601B0 input-sampler fragment and
   the $614E4 row head to their evidenced extents.
3. `config/names.tsv`: the three pre-existing names in this range
   (0x5A9A2, 0x60268, 0x63B5E) now repeat in `config/names/name-3.tsv` with
   fuller evidence; the coordinator can keep either copy (same names).
4. Suggested promotion of the two p1-mid findings that this lane's headers now
   link: 0x5AFF0 renamed `ball_band_hide_check` (not a cup check) and
   0x5A19C renamed `hole_band_lookup` (not a cup lookup) - the FINDINGS sink
   decode at $958C stays the cup capture.

## Settled course-rendering details (2026-10-14)

`nearest_mound_find` ($621ec, historical name) selects the nearest type-6
mound by |dx|+|dy| only when player record +$c ground height is nonzero;
`nearest_object_ptr` is -1 otherwise. `mound_edge_y_set` ($62266) writes a
screen **y**, $139 + max($20, projected scalar), range $159..$1a8. Consumers
turn ($1a8-y)/2 into the wipe-row cutoff. `wipe_column` is the SCB4 X offset
for sprite 1 on courses 2/3: view-relative wind bands 0..7 add $10 per tick,
8..$f subtract $10 ($638b8).

`hole_map_palette_remap` ($63638) first loads palette stream $63678
`{slot $17, palette $f1, -1}`, then patches attribute high bytes of every
504 cell at `fix_row_attr0 + 4*n`. ROM pairs at $63670 are
$10->$14, $11->$14, $12->$15, $13->$16; the search lacks an end test,
but the settle map-row proof restricts live inputs to $10/$11.
`hole_marker_sort` reads the spare second record even at count 1; records
past the count are previous builds or pruned ptr=-1, rather than necessarily
power-on zeros. These details follow semantic-settle-course-a/b and
semantic-settle-r2-maprow.

`camera_clamp_b` reads the old player-record position: $5af30 calls it before
$5af64/$5af68 publish the new longs. The edge test therefore lags one frame.
`scroll_delta_split` peels at most one $10 chunk per axis, then dispatches
the remainder; a universal <=$10 call bound is not established by the splitter.
The remaining camera-domain question belongs to the active camera lane.
