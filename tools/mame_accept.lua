-- Headless acceptance for the rebuilt turfmast set; driven by
-- tools/mame_check.sh. Self-contained: reads no companion-workspace files;
-- it mirrors that workspace's harness pattern only.
--
-- Milestones come from the decoded boot evidence (the working notes phase 3/5
-- status; companion FINDINGS sessions 4-7). Frames are script-relative and
-- the watched bytes hold BIOS garbage before the game starts, so each
-- milestone is gated on the previous phase and reacts to value *changes*:
--   game_entry  first PC inside the P-ROM window (the user vector JMP 0xE36E)
--   title_phase 0x10EBBE (the title/boot state byte) leaves its game-entry
--               value
--   attract     the demo's input-sampler gate 0x10EF3C leaves its game-entry
--               value (the attract demo owns the machine; polarity-proof)
--   shot_fired  first nonzero write to the ball velocity vx (0x102C40)
--               while the attract phase is running
--
-- Results stream to $ACCEPT_OUT/result.tsv; tools/mame_accept.py asserts them.
local OUT = os.getenv("ACCEPT_OUT") or "/tmp/ntm-mame-accept"
os.execute("mkdir -p '" .. OUT .. "'")
local LIMIT = tonumber(os.getenv("ACCEPT_FRAMES") or "12000")

local cpu = manager.machine.devices[":maincpu"]
local mem = cpu.spaces["program"]
local res = io.open(OUT .. "/result.tsv", "w")
res:write("milestone\tframe\n")

local marks = {}
local gate0 = nil
local ebbe0 = nil
local function mark(name, frame)
  if marks[name] then return end
  marks[name] = frame
  res:write(name .. "\t" .. frame .. "\n")
  res:flush()
  print(string.format("ACCEPT %s @frame %d", name, frame))
end

-- taps must be kept in a global or the Lua GC drops them
local function vx_written(offset, data, mask)
  if data ~= 0 and marks["attract"] then mark("shot_fired", n) end
end

n = 0
keep_notifier = emu.add_machine_frame_notifier(function()
  n = n + 1
  if not marks["game_entry"] then
    if cpu.state["CURPC"].value < 0x100000 then
      mark("game_entry", n)
      gate0 = mem:read_u8(0x10ef3c)
      ebbe0 = mem:read_u8(0x10ebbe)
      print(string.format("  (game-entry samples: ebbe=%02x gate=%02x)",
                          ebbe0, gate0))
    end
  else
    if not marks["title_phase"] and mem:read_u8(0x10ebbe) ~= ebbe0 then
      mark("title_phase", n)
    end
    if not marks["attract"] and mem:read_u8(0x10ef3c) ~= gate0 then
      mark("attract", n)
    end
  end
  if not vx_tap then
    vx_tap = mem:install_write_tap(0x102c40, 0x102c43, "vx", vx_written)
  end
  if n >= LIMIT then
    res:close()
    manager.machine:exit()
  end
end)
