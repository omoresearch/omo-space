--[[ Omo station autorun.

  Shipped inside the Omo Space mod jar (data/computercraft/lua/rom), so it
  runs on EVERY computer with nothing installed on the computer's own disk.
  It only does anything on an Omo station: the `omo` API exists there and
  nowhere else, so a plain CC:T computer is left completely alone.

  This is the single entry point. A computer must not also carry a /startup
  that launches the screen, or two clients fight over the display.

  CraftOS runs autoruns in name order, and this one never returns on a
  station on its own, so its name sorts first ("00-" before "00_", digits
  and letters).

  Security (review C1): what a station runs must not depend on world data.
  - The computer's own files (its /.settings, /startup, a floppy's startup)
    live in the save, so a shared map controls them. On a station they are
    never run: user and disk startup are switched off here, before CraftOS
    reaches them, and no /.settings key makes this autorun step aside (the
    old `omo.enabled` opt-out and its /craftos helper are gone).
  - The ROM itself (this file, station.lua, bios.lua) can be overridden by a
    world data pack; the mod refuses every ticket unless the ROM is byte for
    byte what the installed mods ship (RomCheck), so an override gets nothing.
  - Leaving the station screen (Ctrl+Shift+Q) is the owner's deliberate act
    at the keyboard; it drops to a CraftOS prompt, and 'station' reopens it.
]]
if not omo or not omo.station() then return end

-- In memory only (never saved): /startup never runs here, and a station
-- never boots from a floppy: a disk drive placed next to it must not run its
-- own startup with this computer's omo API (and its ticket).
settings.set("shell.allow_startup", false)
settings.set("shell.allow_disk_startup", false)

shell.setAlias("station", "/rom/programs/omo/station.lua")

-- A station is its terminal, never a CraftOS prompt. If the screen client
-- stops on an error it says so on an Omo card and comes straight back; only
-- a deliberate leave (Ctrl+Shift+Q, which ends the program cleanly) reaches
-- this shell. Terminate events (a held Ctrl+T) never end this loop.
local function snag(err)
  local w, h = term.getSize()
  local function line(y, text, fg)
    text = text:sub(1, w - 4)
    term.setCursorPos(3, y)
    term.blit(text, fg:rep(#text), ("f"):rep(#text))
  end
  term.setBackgroundColour(colours.black)
  term.clear()
  term.setCursorBlink(false)
  local y = math.max(1, math.floor(h / 2) - 2)
  line(y, "Omo Space", "1")
  line(y + 2, "The station screen hit a snag; it restarts in a moment.", "8")
  line(y + 3, tostring(err or "stopped"), "7")
  line(h, "Your shells keep running on the host.", "7")
end

while true do
  local client, loadErr = loadfile("/rom/programs/omo/station.lua", nil, _ENV)
  local ok, err = false, loadErr
  if client then ok, err = pcall(client) end
  if ok then break end
  pcall(snag, err)
  local timer = os.startTimer(3)
  repeat local _, id = os.pullEventRaw("timer") until id == timer
end
