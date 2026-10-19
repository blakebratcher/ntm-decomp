# Subsystem report: P1 fixed 0x72b96, 0x780fe) - the round/scorecard/result screens


Naming and understanding pass (lane `name-6`).  Read with
[the lane report; evidence was collected by
the lane tool (incoming references from every assembled source,
resolved callees, absolute RAM operands, raw-ROM pointer scan) and by the
companion workspace the companion RE workspace (outside this repository)
(swing protocol, club table, score poses, boot chain).

## What this range is

The P1 fixed half splits into a kernel/physics area (0x00000-0x0d000), the
course/asset area (0x0f4ce-0x88000) and the frame/palette banks
(0x88000-0x100000).  The lane range sits in the asset half but is *code*: the
screens that run between shots and between holes, plus the swing-meter actor.
Nothing here is part of the ball physics (that is 0x27c6/0x2908/0x2c20/0x74c0/
0x75aa in the low area) - the range *uses* those results through RAM.

Verified relationships to the decoded systems outside the range:

- `0x102eba` (game_mode: 0 = one-player stroke play, 1 = two-player stroke
  play, 2 = match play; *corrected*, see name-4.md section 7) gates nearly
  every path in the range (16 sites); the course loader clears it at 0xf22e.
- `0x107004`/`0x10702a` are the 38-byte P1/P2 player records; `0x107076` is
  the pointer to the *current* player record; `+0x10..+0x21` of a record is
  the 18-entry hole-score array (summed here by 0x73b28/0x73b46), `+1` the
  golfer id, `+3` a sub-index used with ball_lie_surface == 0xb (not a proven
  club byte; semantic-review-p1-74db8).
- `0x10ebe4` (accuracy_result, 0 perfect / 1, 2 = miss sides) and
  `0x106fe6` (shot_power) are the swing engine's outputs; full-swing
  shot_power comes from power_meter_actor 0x72286 and the putt meter 0x77ecc
  (semantic-review-p1-7732c).  On the scorecard screen accuracy_result is
  reused as a release byte that actor_grow_to_target 0x737f8 compares with
  each panel actor's +0x11 (semantic-review-p1-76560).
- The range spawns actors through the shared coroutine spawner 0x4c6, draws
  through 0x73912/0x71e/0x728 (sprite list) and 0xf08/0xf2e (actor draw), and
  blits tile maps through the 0xd0f6/0xd2ce sprite-map blitters.

## Subsystem A - actor/state framework helpers (0x73746-0x73b66)

| addr | role |
|---|---|
| 0x7376e | `actor_init_from_record`: reads a 12-byte record {u32 frame, u16 x, u16 y, u16 sprite_attr, u16 palette_bank_id} from a5 - the one constructor used by 13 call sites |
| 0x73760 | entry into it with a5 preloaded (used by vs_centre_frame_a and 0x74d28) |
| 0x73786 | `actor_field_reset`: clears the flip byte +0x38 and expires the delay timer +0x34 |
| 0x73764 | install actor_get_params_and_insert as a permanent state ("draw and stay") |
| 0x73792/0x73796 | find the first roster row whose status byte has a given bit set (1-based index in d7) |
| 0x737de | draw-insert through the *non*-priority list (0x71e) |
| 0x73b56 / 0x73b66 | a 20x16 sprite-map blit wrapper / vram_reset_all plus the 0xd0f6 sprite-area reset (0x73b66 blits nothing; semantic-review-p1-76560) |
| 0x73b28 / 0x73b46 | 18-hole and 9-hole score sums over a player record |

`actor_grow_to_target` (0x737f8, 282 bytes) is the richest state here: it
copies the position to the "home" pair +0x56/+0x58, derives per-axis velocity
`|delta| >> 5` and an 8-bit fraction `(|delta| << 3) & 0xff`, and then steps
`+0x808` per frame with `add.b`/`addx.w`/`subx.w` so the fraction carries into
the position - 32 frames to cross a 256-unit delta.  On arrival it holds 32
frames (the same 0x808 counter, carry = expired), snaps to the target and
picks a reaction from `(0x11,a6)` vs the shared byte `0x10ebe4`: the reaction
states are 0x7392c (zoom-in walk + immediate insert) and 0x73a20 (walk back to
the home pair, then leave via jmp $54a).

## Subsystem B - round results / best-score table (0x73b92-0x745f2)

Entered when the round is over: block `0000ed08.s` spawns the span at 0x73b72
at 0xeea6 when `hole_number` 0x10d4bc > 0x12; that span installs 0x73b92
(`move.l #$73b92,(a6)` at 0x73b8c) after blitting the course map.
*Corrected (semantic-review-p1-73f80):* the 0x100004 table is the backup-RAM
best-score (top-5) table with three initials and a course, not a golfer
leaderboard.

- 0x73b92 `round_results_actor`: 30-frame wait, chooses the current player by
  comparing the 18-hole sums, spawns the best-score handler 0x73d3c, exits to
  0x73d38 in attract mode.
- 0x73d7c `course_map_draw`: course-map image selected by `course_index`
  0x10d4b8 (tables 0x74570/0x74580, plus the hardcoded 0x239798 image for
  course 3).
- 0x73dc4 `leaderboard_insert_actor`: `score_to_par = sum(18 holes) - 0x48`
  (0x48 = 72 = par), inserted into the sorted 5-entry table at 0x100004 with
  the lower entries shifted down, blank initials 0xe6 and the course index;
  the record address is kept in +0x42 for the initials entry, driven by the
  input words 0x10d52b/0x10d52c.
- 0x73f80 `name_entry_letter_up`: the letter-up step of that initials entry
  (fall-through from 0x73dc4), with the cursor blink 0x73fb4 and the finish
  0x74014; not a typewriter.
- 0x740a0/0x740c8 and 0x74120: a sprite pair of the best-score screen and
  the table itself: heading "RANK/NAME/COURSE", the 5 rows formatted each
  frame by 0x7442c, course names from the 9-byte ASCII records at 0x745ce
  ("G.CANYON", "WOODY", "FUJIYAMA", "SEASIDE") indexed by the course byte at
  0x100008+5n.
- 0x743a6 `leaderboard_init` (backup-RAM init only, caller 0xe3ae) stamps the
  row heads at 0x10001e+0x24n and copies the 25-byte default table at
  0x745b5 into 0x100004.

### Data structures decoded here

```
best-score table (0x100004, five 5-byte records, sorted by score_to_par)
  +0 score_to_par (sum of the 18 hole scores - 72)
  +1..+3 three initials (0xe6 = blank, written at insert)
  +4 course_index (0x10d4b9)
row buffers (0x10001e + 0x24n, five 36-byte cells)
  formatted each frame by 0x7442c; 0x743a6 only stamps the rank digit head
player record (0x107004 P1 / 0x10702a P2, 38 bytes)
  +0x01 golfer id     +0x03 sub-index (lie 0xb)    +0x10..0x21 18 hole scores
leaderboard_rows (0x10eda2, 32 8-byte tournament rows; see config/ram)
  +4 score to par, +5 rank before the last hole, +6 holes played,
  +7 flags (bit 1 P1, bit 2 P2, bit 3 human)
```

## Subsystem C - results/standings sequence (0x745f2-0x75058)

- 0x745f2 `results_standings_actor`: in stroke play walks leaderboard_rows
  with a competition rank and stops at a human row (+$42 = rank); only in
  match play does it compare holes won (0x77e56 counts the 1-bytes in each
  player record) to decide who is shown first (+0x43)
  (semantic-review-p1-73f80, semantic-review-p1-7732c).
- 0x7478c `results_rows_spawn` spawns score_pose_board_actor, then the rank
  pieces (tens 0x74eec, units 0x74e5c, the 4-frame piece 0x74db8, the fixed
  piece 0x74d32) and the icons 0x74f7a/0x74ff2, each picking its frame from
  the tables 0x75124 (10 longs), 0x7514c (4) and 0x7515c (6)
  (semantic-review-p1-74db8).
- 0x74946 `results_sequence_actor` is the sole-leader / match-play sequence
  (from 0x748e6); 0x74af0 divides its frame counter by 8; 0x74cdc plays the
  -1-terminated flipbook list at 0x75100.

## Subsystem D - putt meter and commentary (0x751ec, 0x77ecc-0x78030)

*Corrected (semantic-review-p1-7732c, semantic-review-p1-74db8).*

- 0x77ecc (not 0x77eb8; the bytes before it are 0x776b6's id tables) is the
  putt power meter, spawned by the putt setup 0x6daee: palette 0x1c, clears
  shot_power and swing_lock_flag (0x106fec), loads the golfer power class
  0x72eb8[id] and step 0x72eac[id], converts `target_distance` 0x10d4aa into
  the required power and on the A-button edge sets the lock and shot_power.
  Full-swing shot_power comes from power_meter_actor 0x72286 instead.
- 0x78030 is a draw subroutine called every frame by 0x77ecc: the meter frame
  from 0x78224[class] and a marker whose frame and duration tables are
  0x78242/0x78236 and 0x782b8/0x782ae (indexed by power class, not club).
- 0x751ec picks one of 25 sound-command ids from the table at 0x75348 using
  the bag slot, lie surface, power, the slot's club type, record +3 and the
  accuracy result 0x10ebe4; callers are the swing engine (0x80dd6) and the
  club swing script op at 0x6d83a.

## Subsystem E - pin map marker, water sparkles, flag (0x7537a-0x75842)

0x7538e places a green-map marker at the pin words (0x10d4c2, 0x10d4c4)
scaled 1:20 relative to its parent, pulled one cell toward hole object type
0; nothing pans (semantic-review-p1-74db8).
`water_sparkle_scan_actor` ($7545e) scans nine grid points for water cells
$90/$190. `water_sparkle_actor_init`/$7569c place a blue-white water sparkle
(script $32de, palette $1f); $75664 tests the terrain, and the actor
bails out via jmp $54a when the cell is **not** $90/$190. Inputs-only
ROUND_C2 snapshots 13275/13288 show the twinkles on river water
(semantic-settle-misc-p1).
0x75824/0x75842 pick the flag sprite frame from 0x102e55 bit 5.

## Subsystem F - scorecard screens (0x75898-0x7732c)

The largest subsystem.  Entry 0x75898 `scorecard_screen_init` (spawned at
0xecf0) sets the palette stream 0x77578 (not a tile set), resets the screen
and blits the course map (0x76a12/0x769e2), picks the layout (0x76a32),
spawns the panel actors 0x76f3c, 0x773ec, 0x76b3a and calls 0x7730c
(semantic-review-p1-74db8, semantic-review-p1-76560).

- 0x75afc `result_sound_ids_compose`, called once per player (d7 = 1/2), clears
  0x10ebea..0x10ebf1 and appends one ladder id from the 19-entry tables at
  0x7766a/0x77690 (by the player's climb: leaderboard_rows +5 - new rank) plus
  an optional $1199; word 3 stays 0 (semantic-review-p1-75afc); 0x75ae8 is its append primitive (49 calls); 0x75904 paces
  one player at a time with timers from those words and button edges (0x60268);
  0x75a8e plays the cues when the counters hit 0xa/0xf0/0x19a.
- 0x75fb0/0x75ffa/0x7600a build the on-screen scorecard: one digit actor per
  hole score (handler 0x76946, score in +0x45) and two 0x768ee slot actors for
  each row sum (tens/units via `divu #$a`); the second row is the back nine
  (holes 10-18) with the 18-hole total, and 2P draws both player records
  (semantic-review-p1-75afc).
- 0x76560 spawns the panel's sprite children (handlers inside the same span
  at 0x76802, 0x7682a, 0x76874, 0x768a4), compares the front-nine sums
  against 0x24 to send ids 0x1188/0x1189, and at the round end writes
  0x10ebe4 = 0 then 1 (a release byte for its children) before handing over
  to the standings actor 0x745f2 (semantic-review-p1-76560).
- 0x76b3a + 0x76c88/0x76c6e draw the leaderboard rows (not a live scorecard)
  on the fix layer; 0x76f3c is the panel strip and does not slide.
  0x770a0/0x77154/0x77260/0x772f2 belong to the course select (spawned by
  0x6b3e4 after the course commit), not to the scorecard.

## Subsystem G - match-play lead announcer (0x776b6-0x77e7e)

*Rewritten (semantic-review-p1-7732c).*  0x776b6 `match_lead_announce_actor` runs
only in match play (spawned by 0x6f828 when the match is not decided): it
counts the holes won by each player before and after the last hole
(0x77e56), spawns the lead sprite 0x77862 (frame by who led before the last
hole) and the margin sprites 0x77b1e (0x77e7c[|P1 - P2|]), and sends the
lead message from the P1-ahead / P2-ahead id tables 0x77ea4/0x77eb8.  With
BIOS_COUNTRY_CODE 0 an all-square result after the last hole takes a
separate path (frame 0x9f41e with a scale flip) and sets 0x10ebf6, which
shortens 0x6f828's wait.  0x77e24/0x77de2 are the pair and three-way
comparisons, 0x77b00 interpolates the sprite y from the a6+0x3b fraction.

## Data tables inside the range (classified by the p1-mid lane)

| range | what the lane's evidence shows |
|---|---|
| 0x72b96 + 0x72ba0 | swing protocol tables: meter step 0x72e4a, power table 0x72e64, accuracy windows 0x72ebe/0x72ecc, club words 0x72eac/0x72eb8 (read by 0x77eb8), landing surface table 0x7346 (16 bytes/surface, companion FINDINGS.md) |
| 0x7443a | leaderboard template 0x745b5, name records 0x745ce, course-map tables 0x74570/0x74580/0x74590/0x74596/0x745b1 |
| 0x74c02 | *contains two actor handlers* (0x74c18, 0x74c5e) - see the lane report |
| 0x750d0/0x750de | actor records 0x750d0/0x750dc/0x750f4/0x750e8, flipbook list 0x75100, frame tables 0x75124 (10 longs)/0x7514c (4)/0x7515c (6)/0x75174 |
| 0x75348 | 25-word sound-command id table (index 0..$18) |
| 0x75452 | the 12-byte pan-banner record 0x75452 |
| 0x77426 | per-course tables 0x77520/0x77540/0x77550/0x77560/0x77570/0x775e2/0x775f2/0x77602/0x77612/0x77622/0x77632/0x77642/0x7764a/0x7765a/0x77662, message tables 0x7766a/0x77690 |
| 0x77e70/0x77e7e | lead frame table 0x77e70, margin frame table 0x77e7c (10 longs, running into the 0x77e7e row) and the P1-ahead / P2-ahead message id tables 0x77ea4/0x77eb8 |

## Open questions

1. `0x106fd6` (ball_lie_surface) is compared with 0/1/2/6/0xb by the
   commentary select but its producer is outside the lane; semantics unproven.
2. `leaderboard_rows` (0x10eda2): fields +0, +1, +6 are never read in the lane;
   the row identity (player? CPU golfer?) is inferred from usage, not proven.
3. `0x100004`/`0x10001e`: *settled (semantic-review-p1-73f80):* this is the
   separate best-score table (initials + course), unrelated to the 32
   tournament rows of leaderboard_rows.
4. `0x10ebf2` bits 0-3 select message variants in 0x75afc.  *Answered
   (semantic-review-p1-75afc):* 0x743a6 clears it and message_word_append
   adds 7 per appended word, so the variants depend on the append history
   only.
5. `0x77eb8`: the 0x14-byte table at the head of the published function is
   part of 0x776b6's message id tables; the true function start is 0x77ecc,
   the putt meter (semantic-review-p1-7732c).
6. Two 2-byte spans (0x74438, 0x74bfc) sit between published extents with no
   reference into them; they are the only "no evidence at all" bytes here.
