--[[ Omo station autorun.

  Shipped inside the Omo Space mod jar (data/computercraft/lua/rom), so it
  runs on EVERY computer with nothing installed on the computer's own disk.
  It only does anything on an Omo station: the `omo` API exists there and
  nowhere else, so a plain CC:T computer is left completely alone.

  This is the single entry point. A computer must not also carry a /startup
  that launches the screen, or two clients fight over the display.

  CraftOS runs autoruns in name order, and this one never returns on a
  station, so its name sorts first ("00-" before "00_", digits and letters):
  a world datapack's own autorun (such as the old mcterm terminal client in
  earlier worlds) must not take the station's screen first.
]]
if not omo or not omo.station() then return end

shell.setAlias("station", "/rom/programs/omo/station.lua")

-- Opt out per computer: `craftos` drops to plain CraftOS on this station
-- until `stationon` is run.
if not fs.exists("/craftos") then
  local f = fs.open("/craftos", "w")
  f.write('settings.set("omo.enabled", false)\nsettings.save()\n'
    .. 'print("Plain CraftOS on this station. Type \'station\' to reconnect,")\n'
    .. 'print("or run \'stationon\' to boot into the workspace again.")\n')
  f.close()
end
if not fs.exists("/stationon") then
  local f = fs.open("/stationon", "w")
  f.write('settings.set("omo.enabled", true)\nsettings.save()\n'
    .. 'print("This station will boot into its workspace again.")\n')
  f.close()
end

-- A stale /startup from the earlier terminal mod would launch a second
-- client and steal the screen. Remove it; this autorun is the entry point.
if fs.exists("/startup") then
  local h = fs.open("/startup", "r")
  local body = h.readAll() or ""
  h.close()
  if body:find("term%.lua") or body:find("mcterm_client") or body:find("station%.lua") then
    fs.delete("/startup")
  end
end

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

while settings.get("omo.enabled", true) do
  local client, loadErr = loadfile("/rom/programs/omo/station.lua", nil, _ENV)
  local ok, err = false, loadErr
  if client then ok, err = pcall(client) end
  if ok then break end
  pcall(snag, err)
  local timer = os.startTimer(3)
  repeat local _, id = os.pullEventRaw("timer") until id == timer
end
