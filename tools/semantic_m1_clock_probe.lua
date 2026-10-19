local output = assert(os.getenv("M1_CLOCK_OUT"), "M1_CLOCK_OUT is required")
os.execute("mkdir -p '" .. output .. "'")
local audio = assert(manager.machine.devices[":audiocpu"], "audio CPU is required")
local program = audio.spaces["program"]
local ticks = assert(io.open(output .. "/irq.tsv", "w"))
ticks:write("seconds\tpc\tvalue\n")
ticks:flush()
semantic_m1_clock_tap = program:install_write_tap(0xf802, 0xf802,
  "semantic_m1_tick", function(offset, data, mask)
    local address = audio.state["CURPC"].value
    if address >= 0x1e7a and address < 0x1eb3 then
      ticks:write(string.format("%.12f\t%04x\t%02x\n",
        manager.machine.time:as_double(), address, data & 0xff))
      ticks:flush()
    end
  end)
local frames = 0
local limit = tonumber(os.getenv("M1_CLOCK_FRAMES") or "1200")
semantic_m1_clock_frame = emu.add_machine_frame_notifier(function()
  frames = frames + 1
  if frames >= limit then
    print("M1_CLOCK DONE frames=" .. frames)
    manager.machine:exit()
  end
end)
semantic_m1_clock_stop = emu.add_machine_stop_notifier(function() ticks:close() end)
