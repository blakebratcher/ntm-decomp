local output = assert(os.getenv("LH_OUT"), "LH_OUT is required")
os.execute("mkdir -p '" .. output .. "'")
local cpu = manager.machine.devices[":maincpu"]
local memory = cpu.spaces["program"]
local log = assert(io.open(output .. "/match-registers.tsv", "w"))
log:write("pc\ta1\ta2\ta3\ta6\ta1_index\ta1_shot\ta2_done\n")
local taps = {}
local seen = {}
local count = 0
for _, address in ipairs({0x6cb30, 0x6cb66}) do
  taps[#taps + 1] = memory:install_read_tap(address, address + 15,
    "semantic_match_" .. address, function(offset, data, mask)
      if cpu.state["CURPC"].value ~= address or count >= 1000 then return end
      local first = cpu.state["A1"].value & 0xffffff
      local second = cpu.state["A2"].value & 0xffffff
      local third = cpu.state["A3"].value & 0xffffff
      local actor = cpu.state["A6"].value & 0xffffff
      local key = string.format("%x:%x:%x:%x:%x", address, first, second, third, actor)
      if seen[key] then return end
      seen[key] = true
      count = count + 1
      log:write(string.format("%06x\t%06x\t%06x\t%06x\t%06x\t%02x\t%02x\t%02x\n",
        address, first, second, third, actor,
        memory:read_u8(first), memory:read_u8(first + 2), memory:read_u8(second + 0x22)))
      log:flush()
    end)
end
semantic_match_taps = taps
semantic_match_stop = emu.add_machine_stop_notifier(function() log:close() end)
dofile(assert(os.getenv("SEMANTIC_GAME_RUNNER"), "SEMANTIC_GAME_RUNNER is required"))
