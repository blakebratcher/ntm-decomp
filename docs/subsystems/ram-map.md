# Work-RAM map (0x100000-0x10FFFF)


Consolidated from the naming lanes' tables (`config/ram/*.tsv`, one row per
variable with its evidence) and the region write-ups
(`docs/subsystems/name-*.md`).  The lane `ram-names` added the rows that were
still missing (`config/ram/ram-names.tsv`) and the census tool
the lane tool (per-address read/write/size report, record
field map, JSON dump).

Work RAM is 64 KiB mirrored x16 at 0x100000-0x10FFFF.  43,327 bytes carry at
least one named row; the unnamed spans are listed at the end.

## Driver, task and screen state (0x100000-0x1000D3)

| Addr | What |
|---|---|
| 0x100000 | `dev_flags` (development flags; bit 0 = debug hole select), `dev_flags_lo`, `attract_demo_cycle`, `seq_free_run_counter` (not free-running: +1 per spawn of 0x7eb1a; semantic-review-p1-7e726) |
| 0x100004 | `leaderboard`: the best-score (top-5) table, 5 x 5-byte records `{score_to_par, initial 1, initial 2, initial 3, course_index}` (record n at 0x100004+5n; 0xe6 = blank initial; +4 is the course index 0x74120 uses for the course-name records; semantic-review-p1-73f80) |
| 0x10001e | `leaderboard_row_text`: 5 x 36-byte row text buffers formatted each frame by 0x7442c (0x10001e + 0x24n; the fields 0x100042/66/8a/ae are `leaderboard_row_text_2..5`) |
| 0x1000d2 | `sound_cmd_pending` (the COIN_SOUND pending word: set by 0x8044a, cleared by 0x803f6/0x802b4; semantic-review-p1-7fca6) |

## Boot task nodes and the actor pool (0x100100-0x1020E0)

| Addr | What |
|---|---|
| 0x100100 / 0x100160 / 0x1001c0 | two 0x60-byte boot task nodes and `start_actor_slot` (seeded from the $400 boot table by `boot_init_actor_pool` 0x428) |
| 0x100220 | `actor_spare_node`: the 0x60-byte scratch node `actor_spawn_child` 0x4c6 returns when the free list is empty |
| 0x100280 | `actor_pool`: 0x51 x 0x60-byte actor slots (0x100280-0x1020E0) linked into the free list 0x102080 |

Actor node layout (0x60 bytes, from 0x4c6/0x54a): +0 resume PC, +4 parent,
+8 next sibling, +0xC parent back-link, +0x10 level, +0x11 phase/id, +0x12
flag, +0x13..+0x14 params, +0x20..+0x2A frame words, +0x34/+0x35 flags,
+0x3A/+0x3B = -1 sentinels, +0x3E flag, +0x40.. effect state.

## Sprite/render lists (0x102080-0x102B84)

| Addr | What |
|---|---|
| 0x102080 | `actor_free_list` head |
| 0x102088 | `sprite_node_budget` (reloaded to 0x50 by `sprite_lists_reset` 0x6f2) |
| 0x10208A | `render_node_state` (18 B): the sprite-walker cursor `{+0 record ptr, +4 rows left, +5 row delta, +6 pos byte, +8 width, +A height, +C vram base}` (fields named by the ram-names lane) |
| 0x102098 | `sprite_range_half`, then `vram_queue_write_ptr`, `scb3_write_ptr`, `sprite_node_pool_ptr`/`_used` |
| 0x1020A2 | `sprite_list_head_prio` (18-byte head node) |
| 0x1020B4 | `sprite_node_pool`: 0x50 x 0x12-byte nodes (`{+0 next, +4 priority, +6 frame ptr, +A x, +C y, +E/+F zoom, +10 palette, +11 flags}`) |
| 0x102654 | `sprite_list_head_bg` (+ `sprite_list_head_bg_prio` at +4) |
| 0x102666 | `camera_x`, `camera_y` |
| 0x102670 / 0x1028F2 | `vram_queue_a` / `vram_queue_b` (642 B each, double-buffered SCB/VRAM upload) |
| 0x102B74 | SCB range words `scb_range_fg/_end`, `scb_range_bg/_end`; `hole_object_actor_slots/b` (180 B each) at 0x102B84 |

## Ball physics and shot state (0x102C38-0x102DB6)

The ball state block is contiguous and pointer-reachable from the pivot
0x108100 (`a5 = $108100`, e.g. `(-$5498,a5)` = 0x102C68 `ball_x`):

- flags/counters: `ball_state_flags` 0x102C38, `ball_bounce_count`,
  `shot_result_byte`, `ball_ground_contact_flag`;
- motion: `ball_speed` 0x102C3C, `ball_vel_x/y/z`, `ball_roll`, `ball_spin`,
  `ball_lift`, `ball_wind_*`, `ball_x/y/z`, `ball_prev_*`, `ball_unit_x/y`;
- launch/aim: `shot_launch_angle`, `shot_power_factor`, `aim_rot_sin/sin`,
  `aim_unit_x/y`, `ball_ground_height`, `sweep_step_count`;
- surface: `ball_surface_type/word`, `bounce_normal_*`, `ball_landing_coeff`;
- putting: `putt_state_start/current`, `putt_along_aim`, `putt_pin_distance`,
  `putt_lateral_offset`, `putt_depth_term*`, `putt_pull_*`, `putt_roll_term`,
  `putt_hole_flag`;
- collisions: `ball_bounce_dir`, `shot_bend`, `roll_transfer_amount`,
  `ball_obstacle_depth`, `obstacle_walk_sp`, `obstacle_list` (256 B at
  0x102D3A), `landing_sound_latch` 0x102DB6.

## Driver / HUD / game state (0x102E48-0x102EC2)

`sprite_area_start/split/end`, `map_col_fg/bg`, `vblank_gate_once`,
`vblank_counter` 0x102E55, `palette_upload_request`, `display_driver_gate`,
`challenger_request` 0x102E58, `frame_counter`, `vram_addr_shadow`,
`task_table` (64 B) + `task_cursor/head`, `round_pause_flag` (really the
per-frame sound_cmd_reply value; semantic-review-p1-7d4be), the start-block
bytes, `raster_hook` 0x102EA8, `lspc_mode_word`, `video_50hz_flag`,
`resume_hook`, `difficulty_level` 0x102EB8, `start_join_lock` 0x102EB9,
`game_mode` 0x102EBA/0x102EBB, the continue counters, `player_state_pending`,
`sound_2a_latch/c1`.

## Palette shadow (0x102EC2-0x106FD2)

`palette_free_ring` + cursors, `palette_slot_records` (3328 B, 0x102FC8),
the palette dirty/upload flags (0x104FC8-0x104FCF) and `palette_shadow`
(8160 B at 0x104FD2 - the RAM palette mirror `upload_palettes` walks).

## Ball/shot/club state (0x106FD2-0x107004)

`golfer_anchor_x/vel_*`, `loft_slot_putter_flag`, `ball_lie_surface` (+
`ball_lie_variant` 0x106FD7), `ball_lie_copy`, `prev_ball_lie`, `shot_lie_copy`,
`club_bag_index`, `wind_strength` 0x106FE0, `wind_direction_106fe2`,
`shot_curve` (+ byte 0x106FE5; *corrected, semantic pass wave 2*: the
hook/slice shot curve 0..8, 4 = straight, not a loft), `shot_power`,
`shot_accuracy`,
`aim_offset_delta`, `swing_lock_flag`, `club_changed_flag`, `digit_buffer`,
`wind_bias` 0x107000, `rng_index` 0x107002.

## Player records (0x107004-0x10707A)

`player_record_p1` 0x107004 and `player_record_p2` 0x10702A, 0x26 bytes each
(the copy in `player_state_save` 0x59F6A moves 0x26 bytes), plus
`player_record_snapshot` 0x107050:

| Offset | Field |
|---|---|
| +0x00 | player slot 0 = P1, 1 = P2 (round_player_init 0x59F26; compared with the snapshot by 0x59FC6 and 0x6C562) |
| +0x01 | `golfer_id_p1/p2` (0xff = not playing) |
| +0x02 | `player_rec_shot_p1/p2` - shot number / strokes on this hole (cleared by hole_load, +1 per shot at 0x58274; compared with par by 0x6CDA2/0x6CB30/0x6E428 and 0x83C24), not a score to par |
| +0x04 | `demo_tee_x` - the ball x of that player, published every frame by 0x5AEA0 (the 0xF2D6 demo tee table also writes it); not demo-only |
| +0x08 | `demo_tee_y` |
| +0x10..+0x21 | `scorecard_p1/p2`: the 18 per-hole result bytes (0xff unplayed) |
| +0x22 | `player_rec_hole_done_p1/p2` - hole-done byte: 1 holed out (0xBC78/0xBC98), $ff stroke limit (0x6CDCA) or conceded/given up (0x6C242); read by 0x58AEE, 0x58C00 |
| +0x24 | `player_rec_in_play_p1/p2` - tee honour: hole_result_record 0x58B64 writes it, active_player_pick_far 0x589F6 reads it; 0xE9E2 sets P1 = 1 / P2 = 0 at every round start (*corrected, semantic pass wave 3*: not "takes part in the round") |

Round pointers follow: `current_player_record` 0x107076 (0x107004 or
0x10702A), `hole_record_ptr` 0x10707A, `aim_target_angle/scale`,
`shot_kind`/`ball_holed_flag`, `shot_clock_expired`,
`shot_end_code`, `hole_tilemap_desc` 0x107086 and `hole_tilemap` 0x1070A2
(decoded hole cells, column-major; the largest map is 57x195 words =
$56D6 bytes; the RAM row now records that full extent, including interior
address aliases).

## Round / course / input state (0x10D4A2-0x10D630)

- ball screen telemetry: `ball_screen_vel_*`, `target_distance`,
  `shot_distance_word`, `type6_point_list` (at most 10 B: the type-6 object x/y pairs
  + $ffff, not pins; semantic-review-p1-54c3c);
- course/round: `course_index` 0x10D4B8 (word; the value lives in
  `course_index_byte` 0x10D4B9), `course_number_saved`, `hole_number`,
  `hole_number_lo` (really the low byte of hole_number; semantic-review-p1-79204),
  `course_obstacle_type` (really the per-course obstacle
  object type $a/5/5/4), `pin_x_word/y` (really the pin x/y in map
  units), `pin_x_16_16`, `tee_x_word`/`tee_y_word` (map units),
  `course_x_limit`/`height_cells` (really the x/y clamp limits
  (cells-2)*16-1; *corrected, semantic pass wave 3*),
  `results_*`, `shot_origin_*`, `aim_reference_*`;
- HUD/ball: `ball_y_published`, `ball_z64_published`, `ball_hold_timer`,
  `round_end_flag`, `ball_relocate_busy`;
- input: P1 triplet `input_held_p1` 0x10D514 (`input_pad_p1`,
  `input_dir_p1`), P2 triplet `p2_input_state` 0x10D51F (`input_edges_p2`,
  `input_dir_p2`), the merged active bytes `active_input_word` 0x10D52A,
  `input_word_b`, `active_input_word_b2`, `input_merge_bios` 0x10D52D;
- per-player round records: `player_status_p1` 0x10D52E and `player_status_p2`
  0x10D58E (96 B each); `hole_stock_p1/p2` (0x10D572 / 0x10D5D2) ARE
  their +0x44 bytes: the hole stock, seeded from the HOLE(1st)/HOLE(cont)
  soft DIPs by the PLAYER_START handler 0xE238, -1 per hole, + for under-par
  holes / hole in one / contest wins (*corrected, semantic pass wave 3*: not
  a shot counter, not credit displays);
- `screen_transition_flag` 0x10D5EE, the green marker/wipe block
  `green_map_flag`..`wipe_span_e` (0x10D5F0-0x10D606), the map/VRAM windows
  0x10D606-0x10D61E, then `wipe_rows` 0x10D62C (598 B) and
  `vram_stage_rows` 0x10D730 (each row 0x20 B; row N base word at
  0x10D730+0x20N, `vram_stage_row1..3_base` named).

## Green/cup working set (0x10D834-0x10D996)

`green_slope` 0x10D83A, the pin-blend flags, `cup_grid` 0x10D840 (52 x
6-byte entries `{x, y, flag}` + the 0x7FF0 sentinel; entry 0 fields at
0x10D842/44), the two 18-byte cup extents 0x10D972/0x10D982 with their cell
bytes, `cup_extent_scale` 0x10D992 (word pair; +2 = 0x10D994).

## Screen buffers and effect state (0x10D996-0x10EC00)

| Addr | What |
|---|---|
| 0x10D996 | `fix_row_buffer` (2016 B): 56 rows x 9 cells of `{u16 tile, u16 attr}` - the fix-layer map |
| 0x10E176 | `sprite_map_buffer` (2016 B): the same 56 x 9 cell layout (guard at 0x6418a; semantic-review-p1-61620) |
| 0x10E956 | strip cells, `hole_strip_state` (the `effect_list` row at 0x10E950 is not an allocation: 0x641ae walks hole_marker_list from 0x10E95C; semantic-review-p1-61620) |
| 0x10E95C | `hole_marker_list` (540 B): up to 45 12-byte marker records |
| 0x10EB78 | marker cursor/count, nearest-object/axis pair (`view_axis_x/y` 0x10EB9C/A0), cup frame offsets, `cell_band_y` |
| 0x10EBAE | ball-effect block: `ball_cell_terrain`, `ball_effect_active` 0x10EBB0, `trail_stream_buf` 0x10EBB2 (4 B) + `trail_stream_end` 0x10EBB6, `trail_effect_id` 0x10EBB7, `impact_shared_x` 0x10EBB8, `impact_cell_terrain` 0x10EBBA, `obstacle_hit_type`, `phase_byte` 0x10EBBE, `panel_fix_dest_addr` 0x10EBC0 |
| 0x10EBEA | `result_message_words` (three ids; +2/+4 named), `message_counters` (+ `message_counter_b` at +1), round result flags 0x10EBF6-0x10EBFD |
| 0x10EBFE | `ball_trail_history` (180 B: 30 x 6-byte samples; semantic-review-p1-7d4be) |
| 0x10ECB6 | `sound_cmd_queue` (32 B) + head/tail/last-reply bytes |
| 0x10ECE2 | overlay list block: `overlay_slot_count` 0x10ECE2, `overlay_node_free_ptr` 0x10ECE4 (+ low word 0x10ECE6), `overlay_list_head` 0x10ECE8, `overlay_head_node` 0x10ECF0 (+ key 0x10ECF4), `overlay_node_pool` 0x10ECF8 (8-byte nodes `{next, +4 y, +6 x}`) |

## Panel / roster / card (0x10ED98-0x10EF22)

`panel_header_ptr` 0x10ED98, the address expression `leaderboard_rows - 8`
used by addq-first walks (not a separate RAM record), `panel_vram_cursor`
0x10ED9E, `panel_sprite_base` 0x10EDA0, `leaderboard_rows` 0x10EDA2 (the 32-row
tournament leaderboard, 0x10EDA2-0x10EEA1; 8-byte rows `{+0 name ptr, +4
score to par, +5 standing, +6 holes played, +7 flags}`; *corrected, semantic
pass wave 3*: 32 rows, +5 is the standing), the
two player rows `roster_row_p2` 0x10EE92 / `roster_row_p1` 0x10EE9A (rows 30/31
inside it), then
`card_record` 0x10EEA2 (0x80 B memory-card record, BIOS card commands) with
its card_* fields.

## Input records, demo input, results (0x10EF22-0x10EF48)

| Addr | What |
|---|---|
| 0x10EF22 / 0x10EF28 | `input_state_p1/p2`, 8 B each: `{+0, +1 prev, +2 current, +3 newly pressed edge, +4 held/auto-repeat, +5 repeat countdown}` (filled by `input_sampler` 0x85086) |
| 0x10EF2E | `demo_input_bytes`: the two synthetic demo pad bytes (`demo_input_p2` at +1) |
| 0x10EF30 | `demo_stream_ptr`, play/write cursors, pending nibble |
| 0x10EF3A | `input_repeat_word` (delay 0x10 at +0, `input_repeat_period` at +1), `input_source` 0x10EF3C |
| 0x10EF3E | `result_prize`, prize divisor, holes, best score/rank, title service byte |

## BIOS work RAM (0x10F300-0x10FFFF, canonical names)

`config/ram/hardware.tsv` holds the ngdevkit BIOS names: system mode/country
0x10FD80+, the per-player input latches `BIOS_P1CURRENT`..`BIOS_P4TIMER`
(0x10FD94-0x10FDAB), stat/credit/player-mode bytes, the memory-card command
block 0x10FDC4+, RTC, devmode/frame counters.  0x10FD84-0x10FD93 is
BIOS_GAME_DIP, the BIOS-maintained soft-DIP block (labels from the cart's
tables at 0xDA2C/0xDCC8/0xDF64): TIME 0x10FD84, HOLE(1st) 0x10FD88, HOLE(cont)
0x10FD8A, HOLE PENALTY 0x10FD8B, MATCH HOLE 0x10FD8C, CONTINUE 0x10FD8D,
DIFFICULTY 0x10FD8E, page break 0x10FD8F, PLAY MANUAL 0x10FD90, DEMO SOUND
0x10FD91, CREDIT/LEVEL 0x10FD92, LANGUAGE 0x10FD93 (Europe only).  The lane
rows `soft_dip_holes_first/p2`, `soft_dip_hole_penalty`, `soft_dip_match_hole`,
`soft_dip_continue`, `soft_dip_demo_sound`, `soft_dip_credit_level` and
`soft_dip_language` name these soft-DIP bytes (*corrected, semantic pass wave
3*: they are not game-owned or coin counters).  `records_table` 0x10FE00 is
game-owned (mirrored to backup RAM 0xD00034).

## Unnamed spans (no row, no direct reference)

| Span | Size | Note |
|---|---|---|
| 0x1000D4-0x100100 | 44 B | between the sound byte and the task nodes |
| 0x1083E0-0x10D4A2 | 20,674 B | no direct operand and no pointer base in the covered corpus; the ball-state pivot 0x108100 reaches only the named 0x102C68/0x10D4C2/0x10EBBC targets, so this arena is either unused or reached only from uncovered code (open) |
| small BIOS-page holes | < 1 KB total | listed in the ram-names lane doc |

## Cross-references

- `the lane tool report` - every direct work-RAM operand with
  its access kind and size; `structs` - the record field maps; `json` - dump.
- `tools/symbolize.py` rewrites these names into the sources; after the
  ram-names pass 2136 equates are published and the raw absolute operands
  dropped from 834 to 677 (`abs-no-name`), with the byte gate still
  byte-identical on all 1325 sources.
