# Subsystem note: fixed P1 [0x780fe, 0x7e540) - shot feedback and screen actors


Lane `name-7`, 2026-10-11.  Range: 25,214 bytes of fixed P1 between the
power-meter data tables and the round-start sequence.  160 sources (139
function entries + 21 recovered blocks) carry grounded names and headers;
`config/names/name-7.tsv` and `config/ram/name-7.tsv` are the tables.

This is an *understanding* pass: no instruction, operand, org line or data
byte was changed.  Every claim below is read off the instruction stream of
the named source, its callers/spawners (the lane tool) or the
companion workspace's decoded findings (`FINDINGS.md`, sessions 3-8).

## The actor model this range runs on

The whole range is a set of **coroutine actors** driven by the task system
the workspace decoded (`FUN_0000d4c8` ticks the {handler,actor} table that
`FUN_0000d510` fills at `$102e60`).  A slot is an `a6` record:

| Field | Meaning | Evidence |
|---|---|---|
| `(a6)` | resume PC - an actor is a state machine that installs its next state with `move.l #$STATE,(a6)` and returns | every actor here |
| `a6+0x8` | linked handler in the wait helpers | `0x54a`, `0x598` |
| `a6+0xc` | parent actor | `ranking_text_actor` reads `($c,a6)` |
| `a6+0x11` | handshake flag with the parent; a child sets it, the parent polls and clears it | `fade_request_wait` -> `hole_intro_setup` |
| `a6+0x13` | state/side byte (blink phase, player side, pose index) | `stock_rest_line_actor`, `pose_spawner` |
| `a6+0x14` | state timer in vblanks | all wait states |
| `a6+0x20/+0x22/+0x24/+0x26` | palette slots (per body part) | `pose_panel_script_start`, `char_select_portrait_draw` |
| `a6+0x2c` | frame pointer (into the 0x88000-0xAD730 frame bank) | `actor_draw` 0xf08 |
| `a6+0x30/+0x32` | x / y (SCB3-style, y counts up) | `0xf08`, `0xf2e` |
| `a6+0x3c` | priority (or zoom/scale in the meter actors) | `sprite_list_insert_prio` 0x728 |
| `a6+0x40/+0x42` | target x / y for the tween states (reused as a sound id by some actors) | `ranking_golfer_frame_actor` vs `match_standing_banner_actor` |

Common calls: `jsr $4c6` spawns a **template** (a1 -> the new slot's state
and initial fields; `lea X,a1; jsr $4c6` sites are the only spawn form -
0x80 sites in this range), `jsr $0xf08/$0xf2e` draw the actor's frame into
one of the two sprite-node lists, `jsr $0xb9a22` allocates a hardware
palette slot, `jsr $0x73912` inserts the sprite node, `jsr $0x804de` plays
a sound (d0 = id), and `jsr $0x54a` / `$0x598` yield to the scheduler
(`0x598` walks a parent chain).

## Subsystems

### 1. Pre-round "YOUR RANKING" screen, [0x782c2, 0x78c22) + the data head [0x780fe, 0x782c2)

*Rewritten (semantic-review-p1-782e6, semantic-review-p1-7732c):* this is not
a shot-meter range.  The data head [0x780fe, 0x782c2) belongs to the putt
power meter `0x77ecc` (golfer power class from `0x72eb8`, step words
`0x72eac`, marker scale table **0x782c2** `power_meter_scale_table`);
`0x78030` is a draw subroutine it calls every frame, not a state.

`power_meter_init_actor` (0x782c6, spawned by demo_round_intro 0xe9e2 after
leaderboard_rows_init when game_mode != 2) clears `$10ebe2` and hands over to
`ranking_screen_actor` (0x782e6), the pre-round ranking screen: it loads the
per-course palette stream `0x78b50[course]` and sprite map `0x78b60[course]`,
spawns the golfer pose sliders (0x786a6/0x786e2 one golfer, 0x787b6 the P1
side, 0x7883a the P2 side when game_mode != 0), the static course sprites
0x78a3e/0x78a5a, the record sprites 0x78a9c/0x78aa6/0x78ab0 and the zoom
sprite 0x78aba, then the place digits (units 0x78956, tens 0x7892e, suffix
0x7890a) of the human player's leaderboard place (0x73796) and the
typewriter 0x788c4 for the string "YOUR RANKING" (0x78c14).  An A/B/C edge
sets `$10ebe2 = 0xff` (skip); the end sets 1.

Tables: `0x780fe` (3 words: meter speed per power class), `0x78104`+ (frame
pointer ladders), `0x78224/0x78236/0x78242` (per-class frame tables),
`0x78230` (per-class thresholds 0x1fff/0x17ff/0x0fff), `0x782ae/0x782b8`
(marker frame durations), the 12-byte sprite records `0x78b14/0x78b20/
0x78b2c/0x78b38/0x78b44`, palette streams `0x78b50`, sprite maps `0x78b60`,
digit frames `0x78b70`, 4 suffix frames `0x78b98`, course frames/palettes
`0x78ba8-0x78bd7`, golfer frames/palettes `0x78bd8/0x78bf0/0x78c08` and the
string `0x78c14`.  The `0xff` golfer-id guard (`$107005 == 0xff` and
game_mode 0, use `$10702b`) selects a lone P2 player (leaderboard_rows_init:
$ff = not playing), not attract mode.

### 2. Results / scorecard, [0x78c22, 0x7a150)

*Corrected (semantic-review-p1-782e6):* this is the pre-hole intro screen,
not a results screen.  `hole_intro_launch` (0x78c22, spawned once per
hole by hole_start_continue at `$eb66`) loads the palette stream `0x7a436`,
spawns a fade waiter and runs `hole_intro_setup` (0x78c40): clear the
flag block, reset the sprite-area allocator (0xd0f6 draws nothing),
background fill 0x79092 (map `0x23bf40` in the bank half), wait for the
fade, spawn `hole_intro_driver` (0x78d0a) and install
`hole_intro_wait` (0x78c98), which ends on the driver's signal (an A/B/C
edge only sets the skip flag `$10ebf8` while the auto bits are set).

The driver packs the hole outcome into **`$10ebf9`** (*corrected, semantic
pass wave 3*: it compares no scores). Outside game_mode 2, `0x5aa76` gives
this hole's contest byte: $02 = a longest-drive hole (YARDS text), $04 = a
nearest-pin hole. In game_mode 2, `0x5a8ba` gives P1's holes won - lost:
$20 = P1 behind, $08 = |standing| == 18 - hole, $10 = |standing| == 19 -
hole (decoded 0x78d0a-0x78d88).  It then runs the panel sequence: wipe panels 0x790a8/0x790d2
(right) and 0x79106/0x79130 (left), the board 0x7915a/0x7919c, the board
body 0x79892/0x79906/0x799ea/0x79a86/0x79b30/0x79bd2, the scorecard
0x79720, the panel script 0x79626 and the per-hole sound actor 0x78f2a (three
sound commands per hole from 6-byte records: $1145 + hole - 1, an unknown
word, $113f + par).

**Text on the fix layer** is a small subsystem of its own:
`fix_text_writer` (0x794e0) walks a 0xff-terminated string and writes
(tile, attr|char) words straight at the LSPC ports `$3c0000/$3c0002`
(VRAMADDR/VRAMRW), 0x20 = advance, the attribute byte at a6+0x45 toggles
bit 7 per word written (semantic-review-p1-79204).  `u16_to_3digit_ascii` (0x79586) formats numbers with
leading-space suppression.  `panel_script_actor` (0x79626) runs the byte
script `0x7aad0` through a 6-entry jump table `0x7aab8` whose ops are the
small functions 0x796b8..0x7971c (yardage digits from `0x7a9e0` in ops 1/2,
par from 0x58988
(op 4 at 0x79702 adds $30 to the par; *corrected, semantic pass wave 3*: not
score-vs-par), column set, empty).  The board's rotating/zooming panel uses the
sprite-map blitter 0xd2ce/0xd3ec with a zoom word (`board_zoom_actor`
0x79b30) and the shared cos table `0x54a3c` (trig_tables+$80,
cos(i*pi/128)*256; `zoom_move_to_golfer_step` 0x79e66; *corrected, semantic pass
wave 2*: not a sin table).

### 3. Shot announcement and penalty banner, [0x79c34, 0x7a150)

*Corrected (semantic pass, wave 2); the section was titled "Shot intro and
aim marker" and described an aim marker, a power gauge and a shot-result
animation, none of which the code shows.*  `shot_intro_actor` (0x79c34) is
spawned by golfer_actor_spawn 0x54cb0 and by 0x6daee (0x6db44), live games
only (attract_demo_index = 0).  It loads the palette stream `0x7a44a`,
spawns the announcement card 0x79cf4 and sends one sound command from
`0x7a240` (shot-1) or `0x7a260` (shot-par+4), +8 for P2.  The card slides
down to y = 0x17e while the fix banner 0x79eee/0x79f7e draws "TEE SHOT" /
"Nth SHOT" or, when `$107082` bit 7 is set (the green/putt shot), "FOR
<score>".  Bit 7 set: the card stays.  Bit 7 clear: it zooms out toward
(`$106fd2`, $139) through the cos table (0x79e66).  0x7a040 is an
independent looping palette animation spawned at swing entry / green-shot
setup (0x58502, 0x58712); its $ffff marker ends a stream when `$10d4d2` < 0
(holes without a hole_band entry).  0x7a150 is the shot-clock "+1 STROKE
PENALTY" banner spawned by shot_clock_actor 0x57658.

### 4. Celebration and hole-result award, [0x7ac3e, 0x7b2dc)

*Corrected (semantic-review-p1-79204, semantic-review-p1-7af54).*  Entered
from the round actor `0x6d2ae` when shot_kind (`$107082`) == 1 and
ball_holed_flag (`$107083`) is set: `celebration_trigger_actor` (0x7ac3e)
raises `$10ebfa` and spawns the banner 0x7ac7a; `celebration_spin_actor`
(0x7ace4, it does not slide in x) spawns the wheel 0x7adcc (init 0x7ad74):
angle 0..0x3b, radius scaled by the parent's zoom, angle/15 selects a
handler from the code table `0x7bce6`, frames only from `0x7bcf6` and
$8fbc8, placed through `polar_offset_xy` (0x7af54, a polar offset:
d1 = d3 + cos(a)*r>>8, d2 = d4 + sin(a)*r>>8).  The award `0x7afec`
involves no rank or prize: the bucket is strokes - par from 0x7b282 (hole in
one -4) into the 10-byte records `0x7bd3e`; 0x7b21e picks the 6-byte sound
records `0x7bc9e` (+7 for the long-putt variant, using the shot distance
from `0x59daa`); it spawns the award sprites 0x7b2dc; 0x7b070 reads input
only in game_mode 2 (match play).

### 5. Score-pose screen, [0x7b398, 0x7ba38)

`score_pose_screen_actor` (0x7b398, entered from `0x6cff4`/`0x6d4b0`) has no
input loop; it calls `fix_rows_1e_1f_fill_pal` (0x7b50c, the 50 Hz-only fill of
fix rows $1e/$1f), the sprite-allocator reset 0x7b4fe (not a panel) and the
backdrop 0x7b5a2 (`0x7ba5c`/`0x7ba6c` streams, map `0x7ba8a[course]` - the
same 4-image course table the bank lane found), then spawns the pose actor
with `pose_index_lookup` (0x7b60c: strokes - par, hole, tables 0x7c10a/
0x7c116/0x7c12e/0x7c1b2, letter offset 0x7bdfe and the side offset).  The
pose itself is `pose_spawner` (0x7b7fe): the head actor 0x7b8a2 (table
`0x7bfc6 + 6*golfer` = {palette, frame table}, with the parent's +$13 pose)
plus one actor per record of `0x7be16[pose]` (`{u16 k, s16 x, s16 y, u32
frame}`, palette ids from the `0x7bdb6` word stream) - the workspace
validated 9/12 of these poses byte-exact (session 4).  The pose panel
0x7b908 is a scrolling strip with a random script phase, not a blinking
frame (semantic-review-p1-7af54).

### 6. Hole-stock panel, [0x7c24e, 0x7ced8)

*Rewritten (semantic-review-p1-7c2d2):* there is no coin handling here.
`continue_screen_actor` (0x7c24e) is the hole-stock panel shown after every
stock change (spawned by 0x6d55e, 0x6c89e, 0x6cff4 with +$45 = 1 for CONTEST
WON, and the match-decided screen 0x7cd68).  `hole_stock_panel_driver`
(0x7c2d2) animates "REST n HOLE(S)" (0x7cabc) from the old to the new stock
with the "+n HOLE(S)" child 0x7c620 and the hole-result banner 0x7c70c/
0x7c7d6 (BIRDIE/BOGEY/... and WIN/LOSS from strokes - par `0x6e428` and the
soft-DIP HOLE PENALTY class `0x6d49c`).  Only at stock 0 does it offer a
continue: the countdown digit 0x7c852 (9..0, table 0x7d163), the blinking
message 0x7c91c (graphics from the pointer list 0x7d0e0; no timer or colon),
BIOS_PLAYER_MOD `$10fdb6` = 2 and the soft DIP CONTINUE `$10fd8d`; otherwise
GAME OVER (0x7cc9c).  `$10ebfc` is a rendezvous counter between the two
panels in match play.  Text streams go to the fix layer through
`fix_text_run` (0x7cd3c) and the 15x12 frame `stock_panel_frame_draw`
(0x7ccc0).

### 7. How-to-play overlay and golfer select, [0x7d2e4, 0x7dfcc)

*Corrected (semantic-review-p1-7c2d2, semantic-review-p1-7d4be).*
`howto_overlay_actor` (0x7d2e4) .. 0x7d496 are the how-to-play overlay, not a
putting camera: 0x7d3b4 uses the language index from 0xf4a0 (not a random
marker) and the two panels 0x7d44e/0x7d4be; `a6+0x12` bit 1 is the
script_interp draw selector of 0x68618; 0x7d36e is a sprite slide, not a
view pan.  The ball trail is `ball_trail_actor` (0x7d960) +
`ball_trail_publish` (0x7da78): the ring buffer `$10ebfe` (30 entries x 6
bytes, `$b4` bytes) is shifted by the camera scroll `$10d4a2/4` and sized
by the ball height against the thresholds at `0x405c`; `$10ecb2` is the
trail session counter.  `char_select_label_actor` (0x7dfcc) and
`char_select_cursor_actor` (0x7de9c) are golfer-select drawers (label and
selection cursor from the `0x7e17a`/`0x7e1f2`/`0x7e22a` records), not ball
HUD.

### 8. Attract intro sequence and the panel helpers, [0x7e054, 0x7e540)

*Corrected (semantic-review-p1-7d4be, semantic-review-p1-7e726):*
`attract_scene_next_step` (0x7e37e) is the attract intro sequence that runs
before each attract demo (spawned via attract_scene_sync_init 0x7e2d4 from
attract_demo_round_setup 0x6b7ea) and ends in the title screen, not a VS /
round-start sequence: one actor per step from the step table `0x7f0d0`
(0x7e56e..0x7e846) with per-step durations `0x7f8c0`, gated on `$102ea2`,
`$102eae`, `$10fd82`/`$10fd91`, ending with the streams `0x7f0f4`/`0x7f114`.
`attract_scene_sync_wait` (0x7e302) spawns the start/coin panel 0x6b94c (no
clock); `char_select_credit_prompt` (0x7e054) is not a round-flow state.
`sprite_map_reset_blit` (0x7e48c, resets the sprite allocator and places a
map), `sprite_scroll_spawn` (0x7e4b4; d0 first sprite slot, d1 move period -
1, d2/d3 dx/dy), `sprite_scroll_actor` (0x7e4dc) - which writes the LSPC
VRAMADDR shadow `$102e5e` - and `sprite_scroll_task` (0x7e536, SCB4/
SCB3 writes) are the sprite helpers that other screens call.

## Data flow

```
demo_round_intro (0xe9e2)          hole_start_continue (0xeb66) round actor (0x6d2ae)
        |                                   |                        |
   ranking screen 0x782c6           hole_intro_launch      celebration/award
        |                                   |  (pre-hole intro)      |
   ranking_screen_actor 0x782e6      hole_intro_driver 0x78d0a  pose screen 0x7b398
        |  (pose/place/text actors)         |  ($10ebf9 bits)         | (pose actors 0x7b7fe)
   $10ebe2 skip flag              panel/board/text actors      head + per-record actors
        |                                   |                        |
  shot intro 0x79c34 -> aim marker 0x79cf4  fix_text_writer 0x794e0  0xf08/0xf2e -> sprite nodes
        |                                   |                        |
  shot result 0x7a040 ---- ball trail 0x7d960 ----> LSPC $3c0000 (fix layer) / SCB1-4
```

## Open questions

1. `0x7db7e-0x7de9c` (798 bytes): clean code that draws the bank sprite map
   `0x247e70` and places things from the `0x568c2` random value.  *Settled
   (semantic-review-p1-7d4be):* 0x7db2c jsr's 0x7db7e, 0x7db30 spawns 0x7db94
   and 0x7dc12 spawns 0x7de4c (the golfer-select screen).
2. The `$10ebf9` bit meanings are inferred from the branches that set them
   (0x78d0a); the exact score comparison the bits encode is only partly
   decoded (`0x5aa76`/`0x5a8ba` live outside the lane).  *Settled (semantic
   pass wave 3):* see section 2: contest-hole kind outside match play, the
   holes-up standing in match play.
3. The trailing bodies of several state chains sit in the 17 uncovered gaps
   (1,684 bytes) - see the lane report; they install the next state
   (`move.l #$NEXT,(a6)`) but the chain head that reaches them is not
   covered, so they stay unknown here.
4. Sound ids are treated as opaque (0x804de's table is outside the lane).
5. `a6+0x40` is a target x in the tween actors and a sound id in the
   match-play standing banner (semantic-review-p1-79204); the struct is
   reused per actor type, so headers state the local meaning only.
