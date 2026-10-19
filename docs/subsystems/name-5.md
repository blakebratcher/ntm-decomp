# name-5 subsystem map: fixed P1 [0x6cef0, 0x72b96)


What the 146 named sources in this range do, how they connect, and which data
structures they share.  Names come from `config/names/name-5.tsv`; RAM from
`config/ram/name-5.tsv`.  Everything here is comment/label-level evidence: the
byte gate (`make check`) is unchanged by this lane.

The range is the **front-end and shot-presentation half of the game**: the
lie display, post-hole and ending actor chains, the putting-view camera,
strip and ball actors, the VS screen, the match-play results table, the
contest-record panels and in-round notices, the fix-layer writers, and the
power/accuracy meter (corrected by the 2026-10-14 semantic lanes cited below).  Gameplay physics (0x74c0 upward) and the round
sequencer (0x54xxx/0x58xxx) live outside; the range talks to them through the
shared globals below.

## 1. Actor runtime this lane is written against

Every actor is a coroutine on A6.  The runtime helpers are outside the lane
but define the shape of every source here:

| helper | meaning |
|---|---|
| `(A6)` long | resume PC - `move.l #$state,(a6)` installs the next state, `rts` yields |
| `0x4c6` | spawn: `lea $handler,a1; jsr $4c6` allocates a child, links it into the A6+8 sibling list, stores the handler at (child) |
| `0x54a` / `0x598` | yield until the children's +0x11 handshake bytes arrive (`0x54a` = "wait for children", `0x598` = "run children down") |
| `0x68600` / `0x68618` | install an actor bytecode script (A6+0x16) and run it; `trap #$f` is a fatal halt (vector 47 = 0xe416, which writes D0 to 0 and spins) |
| `0xf08` / `0xf2e` | draw the actor's frame (A6+0x2c) into one of the two sprite-node lists |
| `0x73912` | unpack the actor draw fields into a sprite node (`jmp 0x728`) |
| `0xb9a22` | allocate a hardware palette slot from D1 = bank id |
| `0xb9cb6` | process a **palette request list**: `{u16 slot, u16 bank}` pairs ended by 0xffff; slot <= 0x40, 0xff = free |
| `0x804de` | queue a message/voice id |
| `0x54c3c` | the shot sequencer's "your turn" spawner (spawns the swing start 0x7223e) |

### Actor record fields this lane uses

| offset | use |
|---|---|
| +0x0c | parent |
| +0x10 | child count (`0x4c6`), compared by `0x54a`/`0x598` |
| +0x11 | child handshake byte (0/0xff and staged values 2/3/4 in the frame-sequence players) |
| +0x12 | flags (bit 1 = draw) |
| +0x13 | step/counter, reused per state |
| +0x14 | frame timer |
| +0x16 / +0x1a / +0x1b / +0x1c | bytecode-script pointer and interpreter state |
| +0x20, +0x22, +0x24, +0x26, +0x28, +0x2a | allocated palette slots (`0xb9a22` writes +0x22+2k in call order) |
| +0x2c | frame pointer |
| +0x30 / +0x32 | screen x / y |
| +0x34 / +0x35 / +0x3a / +0x3b | slide accumulators (VS plates, results rows, title zoom) |
| +0x36 / +0x38 | step / velocity (title bounce, results fly-in) |
| +0x3a / +0x3b | zoom x / y (0xff = none) |
| +0x3c | priority (0xFFFF-based, e.g. -0x7ff6) |
| +0x3e | flip byte |
| +0x40 / +0x42 / +0x44 / +0x46 / +0x48 / +0x4a | per-actor state (list cursors, scores, counts, types) |

## 2. Player records and the shot state block

`0x107076` points at the active **player record** (`0x107004` P1, `0x10702a`
P2, 0x26 bytes; `0x10707a` is hole_record_ptr, not a P2 pointer:
*corrected, semantic pass wave 2*).  Layout as used here:

| offset | meaning |
|---|---|
| +0 | player type/index (0 = P1 side, 1 = P2 side) |
| +1 | golfer id (0..5, 0xff = none) - indexes voices `0x6f810`/`0x6f81c`, names `0x6f7aa`, meter speeds `0x72e32`/`0x72e3e` |
| +2 | number of the shot being played (0x58274 increments it each shot; the "TEE SHOT / 2ND SHOT" banner 0x79eee; *corrected, semantic pass wave 2*: not a select cursor) |
| +3 | variant index, used together with ball_lie_surface == 0xb (lie 0xb indexes the script array `0x6bdd0` with it; 0x72638 and 0x751ec read it too; semantic-review-p1-74db8) |
| +4 / +8 | ball x / y (position x4), published by `0x5aea0` |
| +0xC | ball z (x64) |
| +0x10..+0x21 | the 18 per-hole bytes (the scorecard: strokes, or in game_mode 2 the match result 1 won / 2 lost / 0 halved, which `0x5abec` scans; *corrected, semantic pass wave 3*) |
| +0x23 | per-golfer power-top byte (`0x80c0a` from table `0x80bf8` = 2,4,0,3,3,1): extends the power meter top (`0x72286`) and picks the meter bar frame (`0x72a3e`) |

The **shot state block** `0x106fd6..0x106fea` is written by the round code and
read by the meters here: `0x106fd6` ball lie surface (not a scene/course id; semantic-review-p1-6d55e), `0x106fde` club index
(0xd = putter), `0x106fe6` power, `0x106fe8` meter position, `0x106fea` the
signed spin/bend word, `0x106fec` the swing/accuracy lock byte.

`0x10d4b8` = course index (0..3), `0x10d4bc` = hole index (0..0x12); the
scorecard maths in `0x5a19c` uses `course*0x12 + hole - 1`, which is what
pins both names down.

## 3. The lie display, post-hole sequences and ending chain (0x6cef0-0x6d6fa)

*Corrected (semantic-review-p1-6b9dc, semantic-review-p1-6d55e):* none of
this is title or attract code; the "title"/"attract" names are historical.

```
shot-result actor 0x6bee6/0x6bf06 (outside; spawned when the ball stops)
  -> 0x6cef0 lie_display_record      lie-display record from the 12-byte table
        0x6bd34 {script, frame, palette id} indexed by ball_lie_copy 0x106fd8;
        lie 0xb uses the 4-entry script array 0x6bdd0 by player record +3
  -> 0x6d2a2 lie_display_actor (entry 12 bytes before its inventory org 0x6d2ae)
       -> 0x6d3b0/0x6d3e0 zoom ramps through the 0x6cecc scale curve (0x549bc)
       -> 0x6d44a announce (0x10ebc4 bit 7, sound-command table 0x6d482)
shot sequencer (outside)
  -> 0x6cff4 contest_result_sequence     post-hole contest sequence (lea 0x5860e,
        only when 0x10d4ed < 0): spawns 0x7b398, the contest-record panel
        0x70260/0x70258, then 0x7c24e for contest_record_holder (+1 stock)
  -> 0x6d4b0 hole_result_sequence  post-hole result (lea 0x585c6, unless
        shot_end_code 0x107085 < 0)
        -> 0x6d55e hole_end_stock_actor   shot_end_code 1: hole-end stock
              settlement (no cursor); hole_number >= 18 -> ending instead
              -> 0x6d126 ending_sequence_actor  (palette lists 0x6d228/0x6d232)
                    -> 0x6d168 ending_message_dispatch
                          table 0x6d188 -> 0x6d1c2 panel variant
                                         -> 0x6d198 "YOU HAVE FINISHED / THE FINAL ROUND."
                                         -> 0x6d1ae "Con esto finaliza. / la ultima vuelta."
                                         -> 0x6d1b8 "Terminada a ultima volta."
                              all -> 0x6d1ea ending_wait_teardown (180 frames)
        -> 0x6d686 match_hole_end_actor    shot_end_code 2: match-play hole
              end (0x6c93e result panel, 0x5abec standing, MATCH HOLE tallies);
              not the 2P scorecard panels
```

The ending text streams at 0x6d23c/0x6d261/0x6d287 decode directly as ASCII
with `_` = space, 0xfd = line break, 0xfe = end; the 4-entry table at 0x6d188
is selected by `0xf4a0` from the BIOS mode 0x10fd83 and the country byte
0x10fd93.

## 4. Shot scene, camera and markers (0x6daac-0x6f02e)

The green-shot setup inside the `0x58652` block (0x58680, entered when the
lie is 3, during a hole; *corrected, semantic pass wave 2*: not a "hole
transition") spawns `0x6daac putt_scene_setup_actor` (lea at 0x5871e), which
draws the fix overlay (`0x6e4a2`), installs the **raster split**, and spawns
the panel + marker + ball actors.

* **Camera.** `0x10d4c6/0x10d4ca` are the pin x/y; `0x10eba0/0x10eb9c` the
  view axes (unit vector ball -> target); `0x10d4f0/0x10d4f4` the shot origin;
  `0x10ebda` the ball-height term `(ball_z - flag_object_height)<<6 * 0xefb3`,
  not a projection scale (semantic-review-p1-6d55e).  `0x6dbac
  project_world_to_screen` runs the dot-product rotation (via `0x6dc44
  dot_product_16_16`) with the +0x101/+0x191 screen bias; `0x6e356` is a
  perspective projection, not a 16.16 sibling.
* **Raster split.** `0x6e4c6` zeroes the 64-long buffer `0x10d730`, programs
  the LSPC registers (0x3c0006 mode, 0x3c000e IRQ control, timer reload pair
  `0x10ebce`/`0x10ebd0`) and chains `0x6e81a -> 0x6e86e -> 0x6e8fe` through
  the vblank hook slot `0x102ea8`.  `0x6e86e` writes one word per scanline
  IRQ (the high half of each long) to SCB4 0x8401 (sprite 1 x); the buffer
  is accumulated by `aim_offset_delta` 0x106fea from the tables at 0x6e812/
  0x6e816 (negated when the delta is positive), not scaled by a spin word
  (semantic-review-p1-6d55e).
* **Strip and aim guide.** `0x6dc62 scroll_strip_build` builds the wrapping
  figure strip of the putting view: the stream (0x6de3e/0x6de12/0x6de6e) is
  chosen by attract index, match standing or leaderboard position, and the
  distance selects the frame class; it falls through into `0x6dd50
  scroll_strip_spawn`, so the strip is spawned twice.  `0x6df76` moves each
  figure with `aim_offset_delta` and draws only while x is inside
  (0xb8,0x148).  `0x6e032` publishes the x offset `0x10ebcc` (entry 0/3/6).
  `0x6e05e` draws the five-dot aim guide (semantic-review-p1-6d55e).
* **Ball.** `0x6e900 ball_actor` draws the ball (+0x16) and shadow (+0x2c) at
  the projected ball position; `0x6e97a` picks the rolling frame from the
  screen-space velocity and the held left/right bits; `0x6eb46` waits until
  the struck ball is near the pin (within 8/0xc, after swing_lock_flag goes
  negative), then switches to the near-pin view and draws the ball 0x8b814
  and its ground shadow 0x8ca3e; `0x6ecb4` picks the ball's roll frame (8
  directions, table at 0x40be), not a shadow angle; `0x6ed92` adds a fixed
  sprite at (0x101,0x188); `0x6edfe` runs the club swing script 0x6d746 and
  writes swing_lock_flag $f0; `0x6eee2` selects a backdrop map; `0x6efd2`
  places the pin marker on the green map (semantic-review-p1-6eb46).
* **Panels.** `0x6e43e` (the par panel, frame table 0x6e496;
  semantic-review-p1-6d55e), `0x6e4c6`, `0x6ee4c shot_scene_backdrop_draw` (a sprite
  backdrop: course band, the 0x6eee2 map four times and a 0x23b798 run, then
  SCB3/SCB4 of base+1), `0x6f02e club_swing_frame_select` (frames of the club
  swing sprite by the script's selector; table 0x6f08e for club type 0x16).

## 5. VS screen and match-play results table (0x6f154-0x6fce0)

*Corrected (semantic-review-p1-6eb46, semantic-review-p1-6f9ee):* there are no
name plates or score digits; the results table exists only in match play
(game_mode 2; 0xeca4 spawns 0x6f828 only then).

```
0x6f0e2 vs_screen_init
  -> 0x6f772/0x6f77c                fixed centre frames 0x9e3ea/0x9e43a
  -> 0x6f328/0x6f3ee/0x6f4b2/0x6f568 left/right backdrops, P1/P2 golfer poses:
                                    slide in from $1a0 at 10 px per frame
                                    (0x6f5b2 = the P2 pose state, signals once)
0x6f154 vs_screen_driver            push-apart oscillation of 0x10ebde, then
  -> 0x6f2c2 vs_announce_schedule   sound commands at frames 0x14/0x6e/0x122
  -> 0x6f668                        centre label 0x9e724 (times the screen)
  -> 0x6f6c8 / 0x6f6e6              golfer name labels (table 0x6f7aa) x $60/$e0
  -> 0x6f71a / 0x6f722              label backings 0x9e83e x $60/$e0 (y zoom-in)
0x6f828 hole_intro_driver
  -> 0x6f92e results_course_banner  banner frames 0x7764a by course index
  -> 0x6f9ee match_table_actor    table actor: four row labels 0x6fe28
        (+$45 = 1,1,$a,$a) plus the earlier holes' marks, zooms in, then
        *becomes* 0x6fb88 (its second state: the just-played hole's marks)
        -> 0x6fc82 / 0x6fce0        halved/won/lost marks (tables 0x6ff1a / 0x6ff26)
        -> 0x6fd30 match_table_fly_in     fly-in from the centre ($a0,$178), 32 frames
        -> 0x6fc42                  close state (x zoom shrink); 0x6fe54 fly-out
```

`0x6f632 golfer_vs_pose_lookup` (already named in `config/names.tsv`) indexes
the 6-entry tables 0x6f7c2/0x6f7da; an uninitialised 0xff golfer id reads past
them and fed an odd frame pointer into the sprite list - the title-transition
address error the companion workspace root-caused.  `0x6f772`/`0x6f6c8` are
the name-frame stubs (records at 0x6f792/0x6f79e, player 0x73760).

## 6. Fix-layer writers, contest records and in-round notices (0x70008-0x717b2)

*Corrected (semantic-review-p1-6f9ee, -70748, -71614):* fix VRAM is
$7000 + x*$20 + y, so the stream writers run horizontally; and none of these
screens is a scorecard, select screen or gauge (the ROM strings at
0x71a16/0x71aba/0x71bb8/0x71c76/0x71dbc name them).

1. **Fix blitters.** `0x70008 fix_write_pair`, `0x7001c fix_fill_rect_ff`
   (d5 rows down, +1; d6 columns across, +$20) and the stream writers
   `0x70044` (one tile per call)/`0x70060` (one line)/`0x7007c`/`0x7011a`
   (multi-line)/`0x700d6` (block of consecutive tiles, no stream)/`0x70162`
   (2x2 glyphs)/`0x701f2`/`0x7021c` (right-aligned fields).  Control codes:
   0xfe = end, 0xff = blank column, 0xfd = line break; writes are usually
   clipped to 0x7043..0x74bd.  `d5` carries the tile base ORed into every
   stream byte.
2. **Screen states.** The contest-record panels `0x70260` (longest drive)
   and `0x70258` (nearest pin), spawned by 0x6cff4/0x6cce2, with the banners
   `0x7034e`/`0x703dc` and the plate `0x70436`; the record lines: longest-
   drive yards `0x70540 drive_record_rollup` (PLAYER n nnnn YARDS, from
   0x10d4e8 up to 0x10d4e4) and the nearest-pin line in ft/in (USA)
   `0x7068c/0x70748/0x7079a/0x707d8` or m/cm `0x70844/0x70900/0x70952/
   0x709b0`; tiles 0x6331/0x6332 are the digits '1'/'2' of the record holder.
   The message window `0x70a98` (picture streams 0x70df5/0x70e95) with its
   slide-open halves `0x70b6e`/`0x70c80`; the in-round yes/no prompts
   `0x70fe4`/`0x70ff6`/`0x7101e` ("GIVE UP THIS HOLE ?", "WILL YOU TRY TO PLAY
   FROM HERE?"); the forced give-up notice `0x713fe/0x71414`; the
   sudden-death playoff notice `0x71510/0x7153a/0x715d4/0x71614`; the "FINAL
   MATCH HAS FINISHED" notice `0x71678/0x71692`; and `0x717b2`, a panel spawned
   by golfer_swing_actor 0x54f44 (not a select summary).

Country-dependent artwork is selected through `0xf4a0` (BIOS mode
`0x10fd83` + country byte `0x10fd93`) and indexes the text streams
0x71a16/0x71aba/0x71c76/0x71dbc and the prompt-box label tiles 0x7135a
(YES/NO per language), not cursor colours.

## 7. Frame-sequence players and the swing meters (0x71e40-0x72a3e)

```
0x71e40 frame_seq_driver
  -> 0x71ee4 seq_sprite_maps_place (bank pointers 0x241a08, 0x241508 -> 0x73b56)
  -> 0x71efa frame_seq_player_front   list 0x7211c, 4-frame hold, 6-frame idle
  -> 0x71fd6 frame_seq_player_back    list 0x721a8, same holds

0x54c3c (shot sequencer) -> 0x7223e swing_start_actor
  -> 0x72a3e swing_meter_frame_actor   meter graphics (bar, range and zone
                                      edges; same 0x72ebe row as the
                                      accuracy meter)
  -> 0x72286 power_meter_actor        speed 0x72e32[golfer] + 0x72e4a[0x106fd6]
        -> 0x7249c power_meter_draw   power meter draw: palette blink at
                                      power 100, palette cycle at the top;
                                      no sound
  -> (swing_start_actor's next state) 0x725da ranking_screen_actor
        -> 0x72638 accuracy_meter     speed 0x72e3e[golfer] + 0x72e4a[0x106fd6]
              -> 0x729de / 0x72a20    perfect-chance / miss sounds
        (0x72574 doubles the meter speed when 0x106fd6 = 6)
```

*Corrected (semantic pass, wave 2):* the earlier diagram called 0x72a3e an
animation of 0x1c-byte rows, 0x72e4a club steps, the 0x72e3e term a window,
0x725da a child of the power meter, reversed the 0x729de/0x72a20 sounds, and
gave 0x7249c a lock sound.

This matches the companion workspace's decoded swing protocol (power meter ->
A press locks power; accuracy meter -> A press stores 0x10ebe4 = 0/1/2 and
0x106fe8 = meter position), so the names above are anchored to behaviour that
was already validated against MAME, not just to the disassembly.

## 8. Data tables this lane consumes

| address | shape | consumer |
|---|---|---|
| 0x6bd34 | 12-byte lie-display records `{script, frame, palette}`, 12 entries (9,10 = -1) | `0x6cef0` |
| 0x6bdd0 | 4 long script pointers (lie 0xb, indexed by player record +3; semantic-review-p1-6b9dc) | `0x6cef0` |
| 0x6d188 | 4 long pointers (ending variants) | `0x6d168` |
| 0x6d228 / 0x6d232 | palette request lists (`{slot, bank}` .. 0xffff) | `0x6d126` -> `0xb9cb6` |
| 0x6d4ac | 4 bytes {1,0,4,0xb} cursor floor | `0x6d49c` |
| 0x6d482 | 13 words of message ids | `0x6d44a` |
| 0x6d482/0x6e2c6/0x6e496/0x6f08e/0x6f810/0x6f81c | frame/voice/type tables | `0x6e22c`, `0x6e43e`, `0x6f02e`, `0x6f2c2` |
| 0x6e81a (downwards) | the raster-split source longs | `0x6e4c6` |
| 0x6f7aa/0x6f792/0x6f79e | golfer name frame records | `0x6f6e6`, `0x6f772` |
| 0x70df5 / 0x70e95 | message-window picture rows (10 x 15 and 10 x 14 tile bytes; semantic-review-p1-70748) | `0x70a98`, `0x70b6e`, `0x70c80` |
| 0x70f2b | roll step periods (frames per step, 15..1) indexed by gap/10 (semantic-review-p1-6f9ee) | `0x7066a` |
| 0x7135a | prompt-box label first-tile bytes per language (semantic-review-p1-70748) | `0x7132e` |
| 0x7211c / 0x721a8 | frame-sequence pointer lists (region row 0x7211c) | `0x71f34`, `0x7201e` |
| 0x72e32/0x72e3e/0x72e4a | power speed / accuracy speed by golfer; speed term by 0x106fd6 | `0x72286`, `0x72638` |
| 0x7764a/0x7765a | course banner frames and palettes | `0x6f92e` |

## 9. Open questions

1. **0x6daac's screen identity.** Its spawn chain (portrait panel 0x6e43e,
  ball actors, raster split) looks like the address/shot view, but the caller
  is the hole-transition block 0x58652 and the portrait uses the P2 golfer id;
  whether it is the between-hole presentation or the address view itself is
  not settled from static evidence alone.  *Partly settled (semantic pass
  wave 2):* the spawn site is the green-shot setup 0x58680 (lie 3, during a
  hole), so it is the on-green shot screen, not the between-hole
  presentation.  *Settled (semantic-review-p1-6d55e):* 0x6daac is the
  putting (on-green) view; 0x6e43e is the par panel.
2. **0x6f328-0x6f5b2 (650 bytes).** The companion workspace places the VS
  backdrop slide-ins (trees/crowd/sky, priorities 0x100-0x102, +0x1a0 offset,
  10 px per vblank) at 0x6f328/0x6f3ee.  *Stale (semantic-review-p1-6eb46):*
  vs_screen_init spawns all four entries 0x6f328/0x6f3ee/0x6f4b2/0x6f568 (lea
  at 0x6f11c/0x6f126/0x6f130/0x6f13a); the trees/crowd/sky reading stays
  unverified.
3. **0x10fd8b** (cursor floor selector) and **0x10fd93** (country) are BIOS
  work-RAM bytes whose exact MAME names are not confirmed here; only the
  game-side use is evidenced.
4. **0x106fea** is named `aim_offset_delta` from two consumers (marker slide,
  raster buffer scaling); the workspace's launch decode calls the sidespin
  term separately, so the name may be too narrow.
