-- Lane hole-play scenario runner: natural-input gameplay past the
-- hole-advance gate, with runtime evidence for the remaining unknowns.
--
-- Derived from tools/lane_round_run.lua (same tap model and output files),
-- with one deliberate difference: THIS RUNNER NEVER WRITES EMULATED MEMORY.
-- There is no RAM-poke step kind, and the shot controller (PLAY) acts only
-- through the joystick/coin/start ports.  Every game state it reaches is
-- one a player can reach.
--
-- Step kinds (tab separated, one per line; see tools/lane_holeplay_seq.py):
--   MARK name                     label the following frames
--   SLEEP n                       wait n frames
--   LIMIT n                       hard frame limit (also the run end)
--   PRESS tag field dur           press a port field for dur frames
--   HOLD tag field n              hold a port field for n frames (blocking)
--   WAIT addr mask value timeout  wait until (u8 & mask) == value
--   WAITW addr value timeout      16-bit equality
--   WAITL addr value timeout      32-bit equality
--   SAVE name / LOAD name         MAME save state (state_directory)
--   PLAY holes frames [opts]      autonomous player (see play_tick)
--   TAPS n m                      A-tap train: n taps every m frames
-- Declarations (not steps):
--   WATCH/WATCHW/WATCHL addr      per-change value log (watch.tsv)
--   WTAP addr                     write tap: every write to the byte at
--                                 addr is logged with its PC (writes.tsv)
--   LMARK addr                    landmark: counts executions of the
--                                 instruction at addr (CURPC == addr)
--
-- Outputs in $LH_OUT: watch.tsv, writes.tsv, landmarks.tsv, access.tsv,
-- play.log, summary.txt (and state.tsv per frame when LH_STATE=1).
-- Env: LH_OUT, LH_SEQ, LH_RANGES, LH_LIMIT, LH_PAIR_CAP, LH_MARK_CAP,
--      LH_WCAP (writes.tsv line cap), LH_STATE.
local OUT = os.getenv("LH_OUT") or "/tmp/lh"
os.execute("mkdir -p '" .. OUT .. "'")
local SEQ = os.getenv("LH_SEQ") or ""
local RANGES = os.getenv("LH_RANGES") or ""
local LIMIT = tonumber(os.getenv("LH_LIMIT") or "400000")
local PAIRCAP = tonumber(os.getenv("LH_PAIR_CAP") or "512")
local MARKCAP = tonumber(os.getenv("LH_MARK_CAP") or "6")
local WCAP = tonumber(os.getenv("LH_WCAP") or "60000")
local STATE = os.getenv("LH_STATE") == "1"

local cpu = manager.machine.devices[":maincpu"]
local mem = cpu.spaces["program"]
local curpc_state = cpu.state["CURPC"]
local n = 0

local function curpc()
  return (curpc_state and curpc_state.value) or 0
end
local function u8(a) return mem:read_u8(a) or 0 end
local function u16(a) return (u8(a) << 8) | u8(a + 1) end
local function u32(a)
  return (u8(a) << 24) | (u8(a + 1) << 16) | (u8(a + 2) << 8) | u8(a + 3)
end
local function s32(v)
  if v >= 0x80000000 then return v - 0x100000000 end
  return v
end
local function s16(v)
  if v >= 0x8000 then return v - 0x10000 end
  return v
end

local playlog = io.open(OUT .. "/play.log", "w")
local function plog(fmt, ...)
  local s = string.format(fmt, ...)
  playlog:write(string.format("%d\t%s\n", n, s))
  playlog:flush()
  print(string.format("LH %d %s", n, s))
end

-- ---- target windows (ROM read taps) -------------------------------------
local windows = {}
do
  local f = io.open(RANGES, "r")
  if not f then
    print("LH: no windows file: " .. RANGES)
    manager.machine:exit()
    return
  end
  for rawline in f:lines() do
    local s, e = rawline:match("^%s*([0-9a-fA-F]+)%s+([0-9a-fA-F]+)%s*$")
    if s then
      windows[#windows + 1] = { s = tonumber(s, 16), e = tonumber(e, 16) - 1,
                                seen = {}, distinct = 0, hits = 0 }
    end
  end
  f:close()
end

-- ---- scenario steps -----------------------------------------------------
local steps = {}
local watches = {}
local wtaps = {}
local lmarks = {}
do
  local f = io.open(SEQ, "r")
  if not f then
    print("LH: no seq file: " .. SEQ)
    manager.machine:exit()
    return
  end
  for rawline in f:lines() do
    local line = rawline:match("^%s*(.-)%s*$")
    if line ~= "" and line:sub(1, 1) ~= "#" then
      local p = {}
      for x in line:gmatch("[^\t]+") do p[#p + 1] = x end
      local k = p[1]
      local st = { kind = k }
      if k == "MARK" then st.name = p[2]
      elseif k == "SLEEP" or k == "LIMIT" or k == "PROBE" then st.n = tonumber(p[2])
      elseif k == "PRESS" then st.tag = p[2]; st.field = p[3]; st.dur = tonumber(p[4])
      elseif k == "HOLD" then st.tag = p[2]; st.field = p[3]; st.n = tonumber(p[4])
      elseif k == "WAIT" then
        st.addr = tonumber(p[2], 16); st.mask = tonumber(p[3], 16)
        st.value = tonumber(p[4], 16); st.timeout = tonumber(p[5])
      elseif k == "WAITW" or k == "WAITL" then
        st.addr = tonumber(p[2], 16); st.value = tonumber(p[3], 16)
        st.timeout = tonumber(p[4])
      elseif k == "SAVE" or k == "LOAD" then st.name = p[2]
      elseif k == "TAPS" then st.count = tonumber(p[2]); st.every = tonumber(p[3])
      elseif k == "PLAY" then
        st.holes = tonumber(p[2]); st.frames = tonumber(p[3])
        st.opts = {}
        for i = 4, #p do
          local kk, vv = p[i]:match("^([%w_]+)=(.*)$")
          if kk then st.opts[kk] = vv end
        end
      elseif k == "WATCH" or k == "WATCHW" or k == "WATCHL" then
        local sz = (k == "WATCH" and 1) or (k == "WATCHW" and 2) or 4
        watches[#watches + 1] = { addr = tonumber(p[2], 16), sz = sz }
        st = nil
      elseif k == "WTAP" then
        wtaps[#wtaps + 1] = tonumber(p[2], 16)
        st = nil
      elseif k == "LMARK" then
        lmarks[#lmarks + 1] = { addr = tonumber(p[2], 16), count = 0,
                                first = nil, last = nil, marks = {} }
        st = nil
      else
        print("LH: bad step " .. line)
        st = nil
      end
      if st then steps[#steps + 1] = st end
    end
  end
  f:close()
  print(string.format("LH: %d steps, %d watches, %d wtaps, %d landmarks, %d windows",
      #steps, #watches, #wtaps, #lmarks, #windows))
end

-- ---- ROM access recording -----------------------------------------------
local mark = "init"
local PCCAP = 8

local function note_access(w, offset)
  w.hits = w.hits + 1
  local ent = w.seen[offset]
  local pc = curpc()
  if ent then
    ent.count = ent.count + 1
    ent.last = n
    if ent.pcs[pc] == nil then
      if ent.npc < PCCAP then
        ent.pcs[pc] = true
        ent.npc = ent.npc + 1
      else
        ent.overflow = true
      end
    end
    if mark ~= ent.lastmark then
      ent.lastmark = mark
      if #ent.marks < MARKCAP then ent.marks[#ent.marks + 1] = mark end
    end
  elseif w.distinct < PAIRCAP then
    w.distinct = w.distinct + 1
    w.seen[offset] = { addr = offset, count = 1, first = n, last = n, npc = 1,
                       pcs = { [pc] = true }, overflow = false,
                       marks = { mark }, lastmark = mark }
  end
end

keep_taps = {}
for i, w in ipairs(windows) do
  keep_taps[#keep_taps + 1] = mem:install_read_tap(w.s, w.e, "lhwin" .. i,
      function(offset, data, mask) note_access(w, offset) end)
end

-- landmarks: the 68000 core prefetches, so the fetch of an instruction's
-- first word happens while the PREVIOUS instruction executes (and happens
-- even when that one branches away).  An instruction at X is executing
-- when a fetch at X..X+15 (its own extension words / the next prefetch)
-- is made with CURPC == X; that is the criterion counted here.
for i, lm in ipairs(lmarks) do
  keep_taps[#keep_taps + 1] = mem:install_read_tap(lm.addr, lm.addr + 0xf,
      "lhlm" .. i, function(offset, data, mask)
        if curpc() == lm.addr then
          if lm.lastframe ~= n then
            lm.lastframe = n
            lm.count = lm.count + 1
            if not lm.first then lm.first = n end
            lm.last = n
            if lm.marks[#lm.marks] ~= mark and #lm.marks < MARKCAP then
              lm.marks[#lm.marks + 1] = mark
            end
          end
        end
      end)
end

-- ---- RAM write taps (logging only; the callback returns nothing, so the
-- written data is never altered) --------------------------------------
local wlog = io.open(OUT .. "/writes.tsv", "w")
wlog:write("frame\tmark\tpc\taddr\told\tnew\n")
local wlines = 0
local wseen = {}   -- addr -> { [pc..":"..val] = count }
local wshadow = {}
for i, a in ipairs(wtaps) do
  wshadow[a] = u8(a)
  wseen[a] = {}
  local base = a & ~1
  local lane_mask = ((a & 1) == 0) and 0xff00 or 0x00ff
  keep_taps[#keep_taps + 1] = mem:install_write_tap(base, base + 1, "lhw" .. i,
      function(offset, data, mask)
        if (mask & lane_mask) == 0 then return end
        local v
        if lane_mask == 0xff00 then v = (data >> 8) & 0xff else v = data & 0xff end
        local old = wshadow[a]
        local key = string.format("%x:%x", curpc(), v)
        local c = (wseen[a][key] or 0) + 1
        wseen[a][key] = c
        -- log every value change, and the first 3 same-value writes per
        -- (pc, value) pair
        if (v ~= old or c <= 3) and wlines < WCAP then
          wlines = wlines + 1
          wlog:write(string.format("%d\t%s\t%x\t%x\t%x\t%x\n",
              n, mark, curpc(), a, old, v))
        end
        wshadow[a] = v
      end)
end

-- ---- input helpers ------------------------------------------------------
local releases = {}   -- { {tag, field, frame} }
local function port_set(tag, field, v)
  local port = manager.machine.ioport.ports[tag]
  if port and port.fields[field] then
    port.fields[field]:set_value(v)
    return true
  end
  print(string.format("LH: no field %s:%s", tag, field))
  return false
end
local function tap(tag, field, dur)
  if port_set(tag, field, 1) then
    releases[#releases + 1] = { tag = tag, field = field, frame = n + dur }
  end
end
local J1 = ":edge:joy:JOY1"

-- ---- step engine --------------------------------------------------------
local si = 0
local step = nil
local wait_left = 0
local wait_goal = nil
local play = nil
local probe = nil
local taps = nil
local step_limit = nil
local step_end = false
for _, s in ipairs(steps) do
  if s.kind == "LIMIT" then step_limit = s.n end
end

local play_init   -- forward

local function advance()
  si = si + 1
  step = steps[si]
  if not step then return end
  local k = step.kind
  if k == "MARK" then
    mark = step.name
    plog("MARK %s", mark)
    advance()
  elseif k == "SLEEP" then wait_left = step.n
  elseif k == "LIMIT" then wait_left = 0; step_end = true
  elseif k == "PRESS" then
    tap(step.tag, step.field, step.dur)
    wait_left = step.dur
  elseif k == "HOLD" then
    tap(step.tag, step.field, step.n)
    wait_left = step.n
  elseif k == "WAIT" or k == "WAITW" or k == "WAITL" then
    wait_goal = { elapsed = 0 }
  elseif k == "SAVE" then
    manager.machine:save(step.name)
    plog("SAVE %s", step.name)
    advance()
  elseif k == "LOAD" then
    manager.machine:load(step.name)
    if pr_rearm then pr_rearm() end   -- r6-tee: reload debugger probes
    plog("LOAD %s", step.name)
    advance()
  elseif k == "TAPS" then
    taps = { left = step.count, every = step.every, t = 0 }
  elseif k == "PLAY" then
    play = play_init(step)
  elseif k == "PROBE" then
    probe = { left = step.n }
    advance()
  end
end

-- ---- PLAY: autonomous natural-input player ------------------------------
-- RAM it READS (never writes): root actor state 0x100160, phase 0x10ebbe,
-- power byte 0x106fe7, accuracy meter word 0x106fe8, swing_lock 0x106fec,
-- club 0x106fde, ball x/y (16.16) 0x102c68/0x102c6c, pin x/y 0x10d4c6/
-- 0x10d4ca, target distance 0x10d4aa, strokes 0x107006, hole 0x10d4bc,
-- BIOS_PLAYER_MOD1 0x10fdb6, aim unit vector 0x102cbc/0x102cc0.
-- Inputs it uses: P1 A (power/accuracy locks, panel advance), P1 Left/
-- Right (aim), Coin 1 + 1 Player Start (continue), as configured.
local ROOT, PHASE = 0x100160, 0x10ebbe
local POWER_B, ACC_W, LOCK, CLUB = 0x106fe7, 0x106fe8, 0x106fec, 0x106fde
local BALLX, BALLY, PINX, PINY = 0x102c68, 0x102c6c, 0x10d4c6, 0x10d4ca
local TDIST, STROKES, HOLE, PMOD1 = 0x10d4aa, 0x107006, 0x10d4bc, 0x10fdb6
local AIMX, AIMY = 0x102cbc, 0x102cc0

local function ball_pin()
  local bx, by = s32(u32(BALLX)), s32(u32(BALLY))
  local px, py = s32(u32(PINX)), s32(u32(PINY))
  return bx, by, px, py
end
local function pin_dist()
  local bx, by, px, py = ball_pin()
  local dx, dy = px - bx, py - by
  return math.sqrt(dx * dx + dy * dy) / 65536.0
end

-- actor slots: 0x60-byte objects from 0x100100 up to the free-list head
-- 0x102080; the state PC is the long at +0
local function find_actor(lo, hi)
  for a = 0x100100, 0x102020, 0x60 do
    local pc = u32(a)
    if pc >= lo and pc < hi then return a, pc end
  end
  return nil
end
local POWER_ACTOR = { 0x77ecc, 0x78030 }   -- power meter states
local ACC_ACTOR = { 0x72638, 0x729e8 }     -- accuracy meter states

local function probe_tick(pr)
  pr.left = pr.left - 1
  local pa, ppc = find_actor(POWER_ACTOR[1], POWER_ACTOR[2])
  local aa, apc = find_actor(ACC_ACTOR[1], ACC_ACTOR[2])
  local s = string.format("probe fe6=%04x lock=%x", u16(0x106fe6), u8(LOCK))
  if pa then
    s = s .. string.format(" P[%x %x] pos=%04x step=%04x max=%04x req=%04x", pa, ppc,
        u16(pa + 0x40), u16(pa + 0x42), u16(pa + 0x46), u16(pa + 0x48))
  end
  if aa then
    s = s .. string.format(" A[%x %x] pos=%02x step=%04x lo=%02x hi=%02x max=%02x", aa, apc,
        u8(aa + 0x40), u16(aa + 0x44), u8(aa + 0x4a), u8(aa + 0x4b), u8(aa + 0x43))
  end
  plog("%s", s)
  local acts = {}
  for a = 0x100100, 0x102020, 0x60 do
    local pc = u32(a)
    if pc ~= 0 and pc < 0x100000 then acts[#acts + 1] = string.format("%x:%x", a & 0xffff, pc) end
  end
  local key = table.concat(acts, " ")
  if key ~= pr.lastacts then
    pr.lastacts = key
    plog("actors %s", key)
  end
  if pr.left <= 0 then return true end
  return false
end
play_init = function(st)
  local o = st.opts
  local pl = {
    holes = st.holes, frames = st.frames, t0 = n,
    k = {},                                   -- learned carry k[club]
    continue = (o.continue or "1") == "1",    -- coin + start on the prompt
    acc_target = tonumber(o.acc or "17"),     -- accuracy lock position
    acc_list = {},                            -- optional per-shot cycle
    power_scale = tonumber(o.power_scale or "1.0"),
    power = tonumber(o.power or "-1"),        -- exact power meter target (r6-tee)
    aim = tonumber(o.aim or "0"),             -- P1 Left(-)/Right(+) taps on the tee
    aim_list = {},                            -- optional per-shot aim cycle (r6-lr0)
    curve = tonumber(o.curve or "-1"),        -- target shot curve 0..8 (P1 B/C taps)
    bend = tonumber(o.bend or "0"),           -- 1/2: hold Right/Left at the acc lock
    putt_over = tonumber(o.putt_over or "1.0"),   -- putt overshoot (yd)
    approach_short = tonumber(o.approach_short or "6"),
    putt_bias = tonumber(o.putt_bias or "0"),
    learn_putt = (o.learn_putt or "0") == "1",
    max_shots = tonumber(o.max_shots or "0"),   -- r6-tee: stop PLAY after N settled shots
    max_continues = tonumber(o.max_continues or "99"),
    stop_root = o.stop_root and tonumber(o.stop_root, 16) or nil,
    stop_hole = tonumber(o.stop_hole) or nil,
    stop_putt = (o.stop_putt or "0") == "1",
    twop = (o.twop or "0") == "1",
    state = "wait", t = 0, holes_done = 0, shots = 0, whiffs = 0,
    last_hole = u16(HOLE), last_root = u32(ROOT), idle = 0,
    continues = 0, last_pm = u8(PMOD1),
    prepped = false, taps_done = 0, curve_steps = 0, aim_held = false,
  }
  for x in (o.acc_list or ""):gmatch("[^,]+") do pl.acc_list[#pl.acc_list + 1] = tonumber(x) end
  for x in (o.aim_list or ""):gmatch("[^,]+") do pl.aim_list[#pl.aim_list + 1] = tonumber(x) end
  plog("PLAY start: holes=%d frames=%d hole=%d", pl.holes, pl.frames, pl.last_hole)
  return pl
end

-- full-power carry of the current club from the golfer bag table 0x8318e
-- ({u16 dist_x4, u16 type} per club); a seed for the learned model only
local function club_full_carry()
  local rec = u32(0x107076)
  local gid = u8(rec + 1)
  local bag = u32(0x8318e + gid * 4)
  local club = u16(CLUB)
  if club > 13 then return 100.0, club end
  return u16(bag + club * 4) / 4.0, club
end

local METER_MAX = 0x70

-- the active player's joystick (2P: the record pointer 0x107076 is
-- 0x107004 for P1 and 0x10702a for P2)
local function pjoy()
  if u32(0x107076) == 0x10702a then return ":edge:joy:JOY2", "P2 A", 2 end
  return J1, "P1 A", 1
end

local pjoy_tag, pjoy_a = J1, "P1 A"
local function play_tick()
  local pl = play
  pl.t = pl.t + 1
  local root = u32(ROOT)
  local hole = u16(HOLE)
  if hole ~= pl.last_hole then
    pl.holes_done = pl.holes_done + 1
    plog("HOLE advance %d -> %d (root %x, P1 strokes byte %d)", pl.last_hole,
        hole, root, u8(STROKES))
    pl.last_hole = hole
  end
  if pl.stop_hole and hole == pl.stop_hole and not pl.stop_done then
    pl.stop_done = true
    plog("PLAY stop: hole %d reached", hole)
    play = nil
    advance()
    return
  end
  if root ~= pl.last_root then
    plog("root %x -> %x", pl.last_root, root)
    pl.last_root = root
  end
  local pm = u8(PMOD1)
  if pm ~= pl.last_pm then
    plog("PLAYER_MOD1 %d -> %d", pl.last_pm, pm)
    pl.last_pm = pm
  end
  if pl.holes_done >= pl.holes or n - pl.t0 >= pl.frames
      or (pl.max_shots > 0 and pl.shots >= pl.max_shots)
      or (pl.stop_root and root == pl.stop_root) then
    plog("PLAY end: holes_done=%d shots=%d whiffs=%d continues=%d",
        pl.holes_done, pl.shots, pl.whiffs, pl.continues)
    play = nil
    advance()
    return
  end
  local pm2 = u8(PMOD1 + 1)
  if pm2 ~= pl.last_pm2 then
    plog("PLAYER_MOD2 %d -> %d", pl.last_pm2 or -1, pm2)
    pl.last_pm2 = pm2
  end
  -- the continue countdown (BIOS_PLAYER_MOD1/2 == 2): coin + start
  if (pm == 2 or pm2 == 2) and pl.continue and pl.state ~= "cont"
      and pl.continues < pl.max_continues then
    pl.state = "cont"
    pl.cont_p = (pm == 2) and 1 or 2
    pl.t = 0
    plog("continue prompt P%d: coin + start", pl.cont_p)
  end
  if pl.state == "cont" then
    if pl.t == 40 then tap(":AUDIO_COIN", "Coin 1", 6) end
    if pl.t == 120 then
      tap(":edge:joy:START", pl.cont_p == 1 and "1 Player Start" or "2 Players Start", 6)
    end
    if pl.t == 240 then
      if u8(PMOD1 + pl.cont_p - 1) == 1 then pl.continues = pl.continues + 1 end
      plog("continue: PLAYER_MOD%d=%d", pl.cont_p, u8(PMOD1 + pl.cont_p - 1))
      pl.state = "wait"
      pl.t = 0
    end
    return
  end
  local live = (root == 0xebb2)
  pjoy_tag, pjoy_a = pjoy()
  local v = u16(0x106fe6)        -- power meter (live value until locked)
  local lock = u8(LOCK)
  if pl.state == "wait" then
    -- r6-tee tee setup: one aim hold (P1 Left/Right, aim = signed frames)
    -- and curve taps (P1 B/C) before the swing meter starts
    if not pl.prepped and live and lock == 0 then
      local ai = pl.aim or 0
      if #pl.aim_list > 0 then ai = pl.aim_list[((pl.shots + pl.whiffs) % #pl.aim_list) + 1] end
      local aim_done = (ai == 0) or pl.aim_held
      if not aim_done then
        tap(J1, (ai < 0) and "P1 Left" or "P1 Right", math.abs(ai))
        pl.aim_held = true
        aim_done = true
      end
      local cv = pl.curve or -1
      local curve_done = (cv < 0) or (cv == 4) or (pl.curve_steps >= math.abs(cv - 4))
      if not curve_done and aim_done and pl.t % 12 == 7 then
        tap(J1, (cv > 4) and "P1 C" or "P1 B", 4)
        pl.curve_steps = pl.curve_steps + 1
        curve_done = pl.curve_steps >= math.abs(cv - 4)
      end
      if aim_done and curve_done then
        pl.prepped = true
        plog("tee setup: aim hold %d curve steps %d", pl.aim or 0, pl.curve_steps)
      end
    end
    -- putt meter: the power-meter actor (0x77ecc family) in its running
    -- state 0x77faa, position word +0x40 rising from 0, required power
    -- +0x48 (the game's own distance -> power conversion)
    local pa, ppc = nil, nil
    if live and lock == 0 then pa, ppc = find_actor(POWER_ACTOR[1], POWER_ACTOR[2]) end
    if pa and ppc == 0x77faa and u16(pa + 0x40) > 0 and u16(pa + 0x40) < 0x400
        and s16(u16(pa + 0x42)) > 0 then
      local d = u16(TDIST) / 4.0
      local req = u16(pa + 0x48)
      local tgt = req + pl.putt_bias
      if pl.putt_k and pl.learn_putt then
        tgt = (d + pl.putt_over) / pl.putt_k - 1 + pl.putt_bias
      end
      if tgt < 0 then tgt = 0 end
      if tgt > 0x17 then tgt = 0x17 end
      pl.pa, pl.ptarget, pl.req, pl.d0 = pa, tgt, req, d
      pl.club = u16(CLUB)
      pl.state = "putt"
      pl.t = 0
      plog("shot %d: hole %d PUTT dist %.2f req %d -> meter %.2f ball=(%.0f,%.0f)",
          pl.shots + 1, hole, d, req, tgt,
          s32(u32(BALLX)) / 65536.0, s32(u32(BALLY)) / 65536.0)
    -- full-shot meter: 0x106fe6 rising from the bottom
    elseif live and lock == 0 and pl.prev_v and v > pl.prev_v and v < 0x18 then
      local d = u16(TDIST) / 4.0
      local full, club = club_full_carry()
      local k = pl.k[club] or (full / (METER_MAX * METER_MAX))
      local target = d - pl.approach_short
      if target > full * 1.2 then target = full * 1.2 end
      if target < 0.5 then target = 0.5 end
      local p = math.sqrt(target / k) * pl.power_scale
      if pl.power >= 0 then p = pl.power end       -- r6-tee exact override
      if p < 1 then p = 1 end
      if p > METER_MAX then p = METER_MAX end
      pl.ptarget = p
      pl.club, pl.d0, pl.full = club, d, full
      pl.state = "power"
      pl.t = 0
      if #pl.acc_list > 0 then
        pl.acc_target = pl.acc_list[((pl.shots + pl.whiffs) % #pl.acc_list) + 1]
      end
      plog("shot %d: hole %d club %d full %.0f dist %.2f k %.5f -> power %.1f ball=(%.0f,%.0f)",
          pl.shots + 1, hole, club, full, d, k, p,
          s32(u32(BALLX)) / 65536.0, s32(u32(BALLY)) / 65536.0)
    elseif not live or pl.t > 1500 then
      -- screens between holes / panels: A every 60 frames (on the course
      -- only after a long idle, so no swing is locked by accident)
      if pl.t % 60 == 0 then tap(J1, "P1 A", 4) end
      if pl.twop and pl.t % 60 == 30 then tap(":edge:joy:JOY2", "P2 A", 4) end
    end
    pl.prev_v = v
  elseif pl.state == "putt" then
    local pos = u16(pl.pa + 0x40)
    local step = s16(u16(pl.pa + 0x42))
    local goal = pl.ptarget * 256
    if (step > 0 and pos + 2 * step >= goal) or step < 0 or pl.t > 600 then
      tap(pjoy_tag, pjoy_a, 3)
      pl.state = "putt_wait"
      pl.t = 0
    end
  elseif pl.state == "putt_wait" then
    if lock ~= 0 then
      pl.locked = u16(pl.pa + 0x40) / 256.0
      pl.acc_pos, pl.acc_res = -1, -1
      pl.putt = true
      pl.state = "flight"
      pl.prepped, pl.taps_done, pl.curve_steps, pl.aim_held = false, 0, 0, false
      pl.t = 0
      pl.moved = false
      pl.stable = 0
      pl.lx = nil
    elseif pl.t > 30 then
      plog("putt lock missed")
      pl.state = "wait"
      pl.t = 0
    end
  elseif pl.state == "power" then
    local rate = v - (pl.prev_v or v)
    pl.prev_v = v
    -- press two frames ahead (the lock lands ~2 frames after the press)
    if (rate > 0 and v + 2 * rate >= pl.ptarget) or (rate < 0) or pl.t > 400 then
      tap(pjoy_tag, pjoy_a, 3)
      pl.state = "power_wait"
      pl.t = 0
    end
  elseif pl.state == "power_wait" then
    if lock == 1 then
      pl.putt = false
      pl.locked = v
      pl.state = "acc"
      pl.t = 0
      pl.aa = nil
    elseif pl.t > 30 then
      plog("power lock missed (lock %x)", lock)
      pl.state = "wait"
      pl.t = 0
    end
  elseif pl.state == "acc" then
    -- r6-tee bend: hold P1 Left (bend 2) / Right (bend 1) across the
    -- accuracy lock (input bits 0/1 read by the meter actor 0x72638)
    if pl.bend ~= 0 and pl.t < 40 then
      tap(pjoy_tag, pl.bend == 2 and "P1 Left" or "P1 Right", 30)
    end
    if not pl.aa then
      pl.aa = find_actor(ACC_ACTOR[1], ACC_ACTOR[2])
    end
    if pl.aa then
      local a = pl.aa
      local pos = u16(a + 0x40)
      local step = s16(u16(a + 0x44))
      local goal = pl.acc_target * 256 + 128
      local pred = pos + 2 * step
      if pl.t > 12 and ((step > 0 and pred >= goal and pos < goal)
          or (step < 0 and pred <= goal and pos > goal)) then
        tap(pjoy_tag, pjoy_a, 3)
        pl.state = "acc_wait"
        pl.t = 0
      end
    end
    if pl.t > 600 then
      tap(pjoy_tag, pjoy_a, 3)
      pl.state = "acc_wait"
      pl.t = 0
    end
  elseif pl.state == "acc_wait" then
    if lock == 0xff or lock == 0xf0 then
      pl.acc_pos = u16(0x106fe8)
      pl.acc_res = u8(0x10ebe4)
      pl.state = "flight"
      pl.prepped, pl.taps_done, pl.curve_steps, pl.aim_held = false, 0, 0, false
      pl.t = 0
      pl.moved = false
      pl.stable = 0
      pl.lx = nil
    elseif pl.t > 30 then
      plog("accuracy lock missed (lock %x)", lock)
      pl.state = "wait"
      pl.t = 0
    end
  elseif pl.state == "flight" then
    local bx, by = s32(u32(BALLX)), s32(u32(BALLY))
    if pl.lx and (bx ~= pl.lx or by ~= pl.ly) then
      if not pl.moved then pl.fx, pl.fy = pl.lx, pl.ly end
      pl.moved = true
      pl.stable = 0
    else
      pl.stable = pl.stable + 1
    end
    pl.lx, pl.ly = bx, by
    if pl.t > 400 and not pl.moved then
      pl.whiffs = pl.whiffs + 1
      plog("no launch (acc pos %d result %d lock %x)", pl.acc_pos, pl.acc_res, lock)
      pl.state = "wait"
      pl.t = 0
    elseif pl.moved and pl.stable >= 45 then
      pl.shots = pl.shots + 1
      local dx = bx - pl.fx
      local dy = by - pl.fy
      local travel = math.sqrt(dx * dx + dy * dy) / 65536.0
      local left = u16(TDIST) / 4.0
      if pl.putt then
        -- putts: travel per meter step (meter position + 1), learned
        local k = travel / (pl.locked + 1)
        if pl.putt_k then k = 0.5 * pl.putt_k + 0.5 * k end
        if travel > 0.3 then pl.putt_k = k end
      elseif pl.locked > 0 and travel > 0.3 then
        local k = travel / (pl.locked * pl.locked)
        local old = pl.k[pl.club]
        if old then k = 0.5 * old + 0.5 * k end
        pl.k[pl.club] = k
      end
      plog("settled: power %.2f acc %d/%d travel %.2f left %.2f strokes %d holed %x ball=(%.0f,%.0f)",
          pl.locked, pl.acc_pos, pl.acc_res, travel, left, u8(STROKES),
          u8(0x107026), bx / 65536.0, by / 65536.0)
      pl.state = "wait"
      pl.t = 0
    elseif pl.t > 4000 then
      plog("settle timeout")
      pl.state = "wait"
      pl.t = 0
    end
  end
end

-- ---- per-frame ----------------------------------------------------------
local watch_log = io.open(OUT .. "/watch.tsv", "w")
watch_log:write("frame\tmark\taddr\tval\n")
local st_log = nil
if STATE then
  st_log = io.open(OUT .. "/state.tsv", "w")
  st_log:write("frame\tmark\tpc")
  for _, w in ipairs(watches) do st_log:write(string.format("\t%x", w.addr)) end
  st_log:write("\n")
end

local function finish()
  watch_log:close()
  wlog:close()
  if st_log then st_log:close() end
  local lm = io.open(OUT .. "/landmarks.tsv", "w")
  lm:write("addr\tcount\tfirst\tlast\tmarks\n")
  for _, l in ipairs(lmarks) do
    lm:write(string.format("%x\t%d\t%s\t%s\t%s\n", l.addr, l.count,
        tostring(l.first or "-"), tostring(l.last or "-"),
        table.concat(l.marks, ",")))
  end
  lm:close()
  local acc = io.open(OUT .. "/access.tsv", "w")
  acc:write("window\taddr\tcount\tfirst\tlast\tpcs\tof\tmarks\n")
  local total, dtotal, winhit = 0, 0, 0
  for _, w in ipairs(windows) do
    for _, ent in pairs(w.seen) do
      local pcs = {}
      for pc in pairs(ent.pcs) do pcs[#pcs + 1] = string.format("%x", pc) end
      table.sort(pcs)
      acc:write(string.format("%06x-%06x\t%x\t%d\t%d\t%d\t%s\t%s\t%s\n",
          w.s, w.e + 1, ent.addr, ent.count, ent.first, ent.last,
          table.concat(pcs, ","), ent.overflow and "of" or "-",
          table.concat(ent.marks, ",")))
    end
    total = total + w.hits
    dtotal = dtotal + w.distinct
    if w.hits > 0 then winhit = winhit + 1 end
  end
  acc:close()
  local sum = io.open(OUT .. "/summary.txt", "w")
  sum:write(string.format("frames=%d mark=%s distinct_addrs=%d total_hits=%d windows_touched=%d/%d writes_logged=%d\n",
      n, mark, dtotal, total, winhit, #windows, wlines))
  sum:close()
  plog("DONE frames=%d hits=%d windows=%d/%d", n, total, winhit, #windows)
  playlog:close()
  manager.machine:exit()
end

advance()

keep_frame = emu.add_machine_frame_notifier(function()
  n = n + 1
  -- input releases
  local kept = {}
  for _, r in ipairs(releases) do
    if n >= r.frame then port_set(r.tag, r.field, 0) else kept[#kept + 1] = r end
  end
  releases = kept
  if probe and probe_tick(probe) then probe = nil end
  if play then play_tick() end
  if taps then
    taps.t = taps.t + 1
    if taps.t % taps.every == 1 then
      tap(J1, "P1 A", 4)
      taps.left = taps.left - 1
      if taps.left <= 0 then taps = nil; advance() end
    end
  end
  if step and wait_goal then
    local k = step.kind
    wait_goal.elapsed = wait_goal.elapsed + 1
    local v
    if k == "WAIT" then v = u8(step.addr) & step.mask
    elseif k == "WAITW" then v = u16(step.addr)
    else v = u32(step.addr) end
    if v == step.value then
      wait_goal = nil
      advance()
    elseif wait_goal.elapsed >= step.timeout then
      plog("WAIT %x timeout (saw %x)", step.addr, v)
      wait_goal = nil
      advance()
    end
  elseif step and (step.kind == "SLEEP" or step.kind == "PRESS"
      or step.kind == "HOLD") then
    if wait_left > 0 then
      wait_left = wait_left - 1
      if wait_left == 0 then advance() end
    end
  end
  for _, w in ipairs(watches) do
    local v
    if w.sz == 2 then v = u16(w.addr)
    elseif w.sz == 4 then v = u32(w.addr)
    else v = u8(w.addr) end
    if v ~= w.last then
      w.last = v
      watch_log:write(string.format("%d\t%s\t%x\t%x\n", n, mark, w.addr, v))
    end
  end
  if st_log then
    st_log:write(string.format("%d\t%s\t%x", n, mark, curpc()))
    for _, w in ipairs(watches) do st_log:write(string.format("\t%x", w.last or 0)) end
    st_log:write("\n")
  end
  local hard = LIMIT
  if step_limit and step_limit < hard then hard = step_limit end
  if n >= hard or step_end or (not step and not play and not taps) then
    finish()
  end
end)
