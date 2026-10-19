# Subsystems of P1 fixed [0x0, 0xd54a) (lane name-1)


Everything below is read off the byte-exact sources in
the P1 corpus (names in `config/names/name-1.tsv`, RAM in
`config/ram/name-1.tsv`) plus the companion workspace `FINDINGS.md`.  The
range is the bottom of the fixed P-ROM: reset/boot code, the actor kernel,
the sprite/VRAM pipeline, the fixed-point library and the whole ball/putt
simulation, ending just before the round handler at $d54a.

## 1. Boot and the actor/task kernel ($122-$598)

The BIOS enters user mode through the cart vector `cart_vector_user` ($122 →
`user_entry` $e36e).  `boot_init_actor_pool` ($428) runs once and:

1. links the 0x51 0x60-byte actor slots `$100280..$1020e0` into the free
   list whose head is `$102080`;
2. seeds the **five boot tasks** from the 8-byte `{node, entry}` table at
   $400 — they are the whole game's coroutine roots:

| node | entry | role (from the code) |
|---|---|---|
| $100100 | $d54a | first fixed task (the main flow; just past this lane) |
| $10d52e | $60328 | round/screen task |
| $10d58e | $60328 | second instance of the same task |
| $100160 | $e558 | mid fixed task |
| $1001c0 | $d5f6 | last fixed task |

An **actor is a coroutine node**: `(A6)` is the resume PC, so a state machine
advances by storing the next entry (`move.l #state,(a6)`) and returning.  The
scheduler (outside this range) resumes nodes in list order; the kernel calls
in this range are:

| entry | name | what it does |
|---|---|---|
| $4c6 | `actor_spawn_child` | pop a node, `level = parent+1`, insert right after the parent; **681 call sites** — every character, HUD element, effect and ball actor is created here |
| $54a | `actor_wait_children_then_release` | re-arm itself while the next node is deeper (children run first); when no child is left it falls through into `actor_release` — the "run my effect, then disappear" tail (227 calls) |
| $562 | `actor_release` | unlink, hook `$ca6d8`, push back to the free list (or keep static nodes), restore the parent in A6 |
| $598 | `actor_release_children` | release every node deeper than A6; the "new screen, clear old actors" idiom (148 calls) |

Node layout (0x60 bytes) decoded from $428/$4c6/$562 and the renderers:

```
+$00 long  resume PC (script/state entry)
+$04 long  previous sibling (parent when the node is a child)
+$08 long  next sibling / free-list link
+$0c long  first child (set by actor_spawn_child: node->child = parent's old next)
+$10 byte  level = parent level + 1
+$12 byte  flags: bit 0 keep on release, bit 1 set by $7c20
+$16 long  animation script pointer
+$1a byte  wait/hold counter
+$1c long  second script pointer
+$20 word  palette slot (allocated by palette_slot_alloc $b9a22)
+$22/+$24/+$26/+$28/+$2a word  palette slot copies in allocation order
+$2c long  current frame pointer (frame bank base $88000)
+$30 word  x, +$32 word y (screen, SCB3-style)
+$34 byte  camera-relative flag, +$35 byte  hflip flag
+$3a byte  x zoom, +$3b byte y zoom ($ff = none)
+$3c word  priority (ascending; equal keeps draw order)
+$3e byte  flip bits (bit0 h, bit1 v)
+$40 long  owner-object back pointer (scenery/message actors)
+$44 long  owner list cursor
```

## 2. Sprite and VRAM pipeline ($5d8-$a00, $d0f6-$d3fc)

Data flow per frame:

```
state code (actors)
   -> sprite_list_insert ($71e, background list $102654)
   -> sprite_list_insert_prio ($728, priority list $1020a2, 0x12-byte nodes from
      the pool $10209e, budget $102088)
   -> sprite_list_build ($788, once per frame): picks queue buffer A/B
      ($102670 / $1028f2 via $10266a), computes both SCB half-ranges, calls
   -> sprite_node_render ($878) / sprite_walker ($a00): decode the frame record
      (u8 parts, u8 attr, per part s16 dx/dy, u8 cols, u8 rows, u16 tiles[],
      column-major) into (VRAM addr, data) word pairs at $10209a/$10209c
   -> vram_queue_dirty $10266b set
per vblank: vram_queue_flush ($5d8) -> REG_VRAMADDR/REG_VRAMRW ($3c0000),
      then sprite_list_clear_stale ($64c) hides the SCB3 slots the shorter list
      left stale in both ranges ($102b74/$102b78, $102b7c/$102b80)
```

The map blitters ($d0f6-$d3fc) are the fix/playfield tile writers: they stream
4-byte cells (tile word + attribute with the flip mask XOR'd) into SCB1, one
32-tile strip per column, row wrap at 64 words (`sprite_map_blit` $d1ee),
with SCB2/3/4 size/Y/X writes in `scb_write_shrink` ($d34a).

Boot VRAM state comes from `vram_reset_all` ($678: fills $8000.. with 0 and
0..$2fa0 with 1, poking the watchdog $300001 inside the loop) and
`sprite_lists_reset` ($6f2).

## 3. Fixed-point library ($277e-$2c48)

| entry | name | contract |
|---|---|---|
| $277e | `math_neg_cos` | 12-bit angle (0x1000 = 360°) → 16.16, -cos |
| $27c0 | `math_sin` | same, sin (phase +0x400) |
| $27c6 | `sqrt_newton` | 16.16 sqrt |
| $27f2 | `math_mul_32x32_64` | d0 = low, d1 = high |
| $2820 | `math_mul_int` | 16.16 x integer → 16.16 (low 32 bits) |
| $2844 | `math_mul_fixed` | 16.16 x 16.16 → 16.16 (bits 16-47; small-magnitude path truncates) |
| $28bc | `math_div_u32` | 32/32 → 32 (the tail at $28e0 is the signed wrapper) |
| $2908 | `div_fixed_unsigned` | 16.16 divide; 782-byte body **byte-identical in mslug2/mslugx** (the 809-byte equal run crosses the entry) |
| $2c20 | `div_fixed_signed` | signed wrapper |
| $2c48 | `math_atan2` | -> 12-bit angle via the $1f7c table |

The trig tables live just below: the quarter-sine at $f78 (0x401 longs) and
the arctangent ratio table at $1f7c (0x401 words).

## 4. Ball flight and the launch machine ($4460-$5060, $74c0-$8240)

`shot_launch` ($4cbe, called from the shot sequencer at $584f0/$5876e) is the
biggest routine in the range.  It reads the locked shot inputs
(`$106fde` bag slot 0..13, $d = putter; `$106fe6` power; `$106fe8` accuracy
meter; `$106fe4` hook/slice shot curve 0..8, 4 = straight; `$106fe0/2` wind),
pulls the per-golfer tables through
`club_angle_get` ($5304), `club_base_get` ($53f0), `power_factor_get` ($5578),
`accuracy_err_lo/hi` ($52f0/$52fa) and the launch-scale tables
(`launch_scale_lookup`/`spin_factor_a/b_get`: values <= 1.0 chosen by golfer and
(record+3 + surface $106fd6), entry by bag slot; factor a multiplies the
horizontal speed, b the vz chain; nothing ties them to spin), and writes the
ball record `$102c38+`: vx/vy/vz, roll, roll2, spin, lift, wind, per-shot
parameters; the unit vector is cleared to 0, not written.  FINDINGS.md
session 6 (outside the repo: the companion RE workspace (outside this repository)) has
the closed formula and its six-shot validation; the code here matches that
structure line for line.  *Corrected (semantic pass, wave 2):* "club",
"loft" and "spin tables" above were wrong names for the bag slot, the shot
curve and the launch-scale tables.

The per-frame simulation:

```
ball_integrate_xy ($7446)                              pos += v >> 4 for x, y and z
ball_ground_proximity ($7490)                          $102cee bit 7: z <= ground+20.0 or z < 0
vec2_normalize ($74c0)                                 |v| ($102c3c) + unit vector
airborne: airborne_velocity_update ($75aa)             wind/drag/spin/lift/gravity
flight step: $76a2 terrain byte query ($5933e); landing test/response at $76bc
             ball_motion_sweep ($7eba)                 16 sub-steps of v>>8
             ball_impact_spawn ($784c), bounce via $7c78 (reflection about
             n = (0.447 sin, -0.447 cos, 0.894) of the slope direction 0..$f)
             $800a: contact with the per-hole y band ($10d4d2/$10d4d4 from
             hole_band_lookup $5a19c), not a pin plane; $81c4 re-seats ball_z
             unless the ball is within 2.0 of the pin
roll: $95aa roll step -> ball_roll_friction ($964a); ball_roll_spin_transfer
      ($7a22) runs at landing; $9766 pushes the ball out to radius $6000
      and cannot fire from its only caller; ball_stop_and_zero ($974a)
surface: ball_surface_classify ($93fa) -> $945e (surface $b lie-byte pick,
      not a sound), ball_hole_out_event ($bc78); $9544 sets the generic
      bit 1 ("motion over") of $102c38, not a sink flag
obstacles: obstacle_collision_check ($8200) with the response handlers
      $8388/$83b4/$85ea (record +$c = 0..2) from the $8656 table; the
      record list is picked by type through $8662; obstacle_list $102d3a
      is rebuilt each frame by $7d96 from the hole-object actor slots
      $102b84 (no divots involved)
```

The **surface table $7346** (12 x 16 B; [$5f96,$7346) before it is the 90
spin tables, [$7406,$7446) after it is zero pad) is read by landing and roll:
`{+0 landing/roll scale, +4 vertical restitution, +8 spin bite, +C roll
friction}`; the values reproduce the FINDINGS numbers (0.625 fairway, 0.5
rough, 0.25 sand, 0.71875, 0.6875 green).  *Disproved (semantic pass):* "0.9746
green friction" - $f980 = 0.9746 is the +$C of records 0 and 4, while
record 8 (the surface the landing code special-cases) has $fa00 = 0.9766;
which surface is the green is not settled.  Terrain sampling goes through the bank/fixed
helpers `$5929a` (height), `$59312` (attribute word), `$5933e` (cell) and
`$5b974` (surface type); `$460a` wraps the height at the ball position.

## 5. Putt engine and green reading ($b06a-$b986)

`putt_slope_force` ($b06a) turns the slope byte from `terrain_slope_at_ball`
($981a; $981c is mid-instruction) and the selector $9848 (the terrain
attribute's 0x1fff field plus the in-tile sub-position bits, $1800-byte
table at $9862-$b061) into a force of $2d4/$400/$653 (class 1..3, word
table $b062) along one of 16 directions, added to vx/vy.  The putt states
dispatch through the 4x4 $b1ae table ($b1f6/$b208/$b20c); `putt_roll_step`
($b6a0) is the below-pin-plane tick.  `compute_putt_pace_ratio` $b358 gives
$6000/(pace + distance) (`putt_pace_ratio` is the RAM $102d0a); the putt code
uses the ball's unit velocity, not an aim vector (`putt_along_heading` $b664
projects onto it; `putt_lateral_offset_calc` $b94a is the lateral offset, $b968 is a
mid-instruction phantom; `putt_radius_rescale` $b986 rescales pace and
distance to sum to $6000).  `putt_cup_edge_bounce` ($b378) is the bounce
off the $6000 edge (rescale, velocity reflection, sound once); the hole-out
itself is `ball_hole_out_event` $bc78, called from $b186.  The state key
`$102cf1` (with `$102cf0`) is 0..3 = (distance >= $6000 ? 0/3 : 1/2), the
low value when the along-aim offset $102cf2 < 0.

## 6. Scenery/camera actors, the shot sound-command actor, display drivers

`hole_object_actors_spawn` ($2d8a) + `hole_object_in_view` ($2e34)
form the hole-object actor system (*corrected, semantic pass wave 2*: there
is no camera record and no aim camera).  $10707a is hole_record_ptr; +6
points at the hole-object list (6-byte {type, flags, x, y} records, negative
type ends).  Every object whose box overlaps the window around the ball gets
an actor from the $2df8 table (types 3,4,5,7,9,10,12,14), tracked in the
45-long slot array $102b84; the per-shot task $2f0a re-walks the list each
frame, and each actor despawns and frees its slot when out of view ($2f9e).
Type 3 is the wind-animated pin flag at pin_x_word/y; its $3210 child
is the cup (frame $94eec, parent palette slot and xy). Type $e (14) is a
rock pillar in a water band on course 0 hole 2, course 2 hole 17 and course
3 holes 8/11 (semantic-settle-misc-p1);
types 4/5/10 also go into the overlay list (0x80b36).  The handlers draw
themselves through `actor_draw_alt`.

The "HUD/message actors" ($cad2-$d00a) are one shot sound-command actor
(semantic-review-p1-0cad2), spawned at $5ad32 by the ball actor the first
frame swing_lock_flag bit 7 is set; its paths on the stop surface, shot vs
par-2 and the distances queue one to three commands through `$804de`
(gated by the BIOS bytes $10fdaf/$10fd91), most then release, and the
state $ce9e never releases.  Nothing draws or positions an actor.
*Disproved (semantic pass):* the ids ($105d-$1177) are not fix-layer
text/score messages: `$804de` queues the 2-byte code in
the Z80 sound command ring $10ecb6 (drained to REG_SOUND by $803f6), so
they are sound commands.  The **display ring** ($102e60, 8 slots walked by
`display_driver_step` $d4c8 / `task_schedule` $d510) switches the LSPC
display setup once per message change.

`vblank_handler` ($d430, called from the IRQ entry $e450) is the per-frame
driver: LSPC IRQ ack $3c000c, watchdog poke $300001 while `$102e59 <= 0x14`,
the display driver, `palette_upload` ($ca892) and `vram_queue_flush`, then the
two display tasks $6a134/$6a2f6.  `screen_hook_install` ($d4ae) points the
extra vblank hook `$102ea8` at a no-op and clears `REG_LSPCMODE` ($3c0006).

## 7. Debug leftovers found in the fixed bank

- `ball_direction_octant` ($baa4) has an out-of-range path ($baf4) that draws
  the four ball values (unit y, speed, vx, vy) as fix-layer text at VRAM
  $7208..$720b through the hex printer `$56752` and writes the string
  **"V COS ERROR!"** from $bb5a at VRAM $71a7 with tile word $1300.  The
  printer family ($56752/$56762/$567c2) splits a long into 4-bit nibbles,
  maps them through the ASCII table at $567a0 and writes (VRAM addr, tile)
  pairs to $3c0000 with a +0x20 tile stride - the game's own fix-text
  emitter, used here only by the debug path.
- *Disproved (semantic pass):* the deflection at $925a is not unreferenced
  and has five responses, not four: it is called by jsr at $8394 (obstacle
  response handler 0 $8388, entry 0 of the $8656 dispatch) and picks +30
  degree rotate, reflect, reverse, swap or -30 degree rotate by the lateral
  pin offset, then halves vx/vy ($93c8).  "Cup lip" is unproven.

## 8. Open questions

1. ~~$5faa-$7446~~ resolved: [$5f96,$7346) is the bank of 90 spin tables
   (14 {word, word} entries each, read by $5dc2/$5dd4), then the surface
   table and its zero pad.
2. ~~$986a-$b06a~~ resolved: [$9862,$b062) is the terrain slope byte table
   read by terrain_slope_selector at $9852; [$b062,$b06a) is the 4-word
   slope force table read at $b07e.
3. **$184-$200**: a table-search routine (p1-lo) with no consumer found; the
   header vectors and the boot table do not point at it.
4. **The 6 scenery handler entries** ($2fb0/$3000/$30ac/$30da/$30e8/$30f6/
   $317e/$3244) are proven *entries* but their bodies are unclassified;
   publishing them needs the flow-recovery pass, not a decode.
   *Resolved (semantic pass, wave 2):* there are 8 entries (the hole-object
   types of section 6), and all bodies are covered and described
   (00002f9e.s, 00003000.s, 0000307a.s, 000030da.s, 000030e8.s, 00003100.s,
   00003244.s).
5. **$102ce8 / $102cf0/$102cf1**: mechanics now pinned (semantic pass):
   $102ce8 is the sweep's per-sub-step int(z), not an armed flag; $102cf1 =
   (distance >= $6000 ? 0/3 : 1/2) by the sign of $102cf2, and ($102cf0,
   $102cf1) index the 4x4 $b1ae transitions.  The gameplay meaning of the
   $6000/$c000 "cup" geometry is still unproven.
6. **$10702a P2 record and the 2P paths**: named but not exercised in the
   attract/demo evidence.
