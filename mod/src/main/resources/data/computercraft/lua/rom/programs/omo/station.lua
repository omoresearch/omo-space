--[[ Omo station screen (ComputerCraft client).

  CraftOS is only the glass. The shells, Hermes and everything else run on
  the host inside tmux; this program paints frames from the Omo bridge and
  forwards keys, mouse and resizes. Tabs, names, profiles and reconnecting
  are driven from the station panel above the terminal (or /omo); this file
  never decides which tab is shown - the bridge tells it.

  Surfaces. The computer's own screen always shows the terminal. An attached
  monitor wall is a second surface: its own socket and view of the same tab,
  at its own size (a wall too small to use shows a status card). The surface
  that last got input drives the PTY size: the computer's GUI, or the wall
  while a player controls it remotely (omo_focus / omo_mouse / omo_release).

  Security: the computer never sees the bridge token. omo.ticket() mints a
  single-use ticket for THIS station and its workspace, and the bridge only
  lets that ticket look at and type into that one workspace.

  Keys follow a desktop terminal (README has the full table). ComputerCraft
  fires no `char` event while Ctrl is held, so every control combination is
  resolved from `key` events. Ctrl+Shift+T/H/W/R/N/E/P and Ctrl+Tab belong to
  the panel above and never arrive here.
]]

local BRIDGE = omo.bridge()
local ID = os.getComputerID()

-- ---------------------------------------------------------------- palette and colours

-- The bridge maps every host colour onto CC's 16 slots and sends the RGB for
-- each slot (the Omo dark theme); the last palette seen is kept for the next
-- boot's splash. Roles below are slots of that palette (ANSI in comments).
local C = {
  bg = "f",       -- black: espresso, the terminal background
  text = "8",     -- white: cream text
  bright = "0",   -- bright white
  dim = "7",      -- bright black: the status bar
  gold = "1",     -- yellow: Omo gold
  hermes = "4",   -- bright yellow: Hermes gold
  live = "d",     -- green
  red = "e",      -- red
}

local function applyPalette(dev, pal)
  if type(pal) ~= "table" or not dev.setPaletteColour then return end
  for digit, hex in pairs(pal) do
    local slot, rgb = colours.fromBlit(digit), tonumber(hex, 16)
    if slot and rgb then dev.setPaletteColour(slot, rgb) end
  end
end

local function restorePalette(dev)
  if not dev.setPaletteColour then return end
  for i = 0, 15 do
    local slot = colours.fromBlit(("0123456789abcdef"):sub(i + 1, i + 1))
    dev.setPaletteColour(slot, term.nativePaletteColour(slot))
  end
end

local palette = settings.get("omo.palette")

-- Frame text and names arrive as JSON; CC glyphs above 127 come through as UTF-8.
local function glyphs(text)
  if not text:find("[\128-\255]") then return text end
  local ok, out = pcall(function()
    local bytes = {}
    for _, c in utf8.codes(text) do bytes[#bytes + 1] = string.char(c < 256 and c or 63) end
    return table.concat(bytes)
  end)
  return ok and out or text
end

-- ---------------------------------------------------------------- state

local info = omo.station()
local hermes, wsName = info.hermes, info.name
local wsId, tabId, tabName = "", "", ""
local ctrl, shift, alt = false, false, false
local focus = "computer"                      -- the surface keys go to and that sizes the PTY
local connSeq = 0
local tickets = {}                            -- surfaces waiting for the ticket worker

-- A surface: somewhere the terminal is shown, with its own socket and view.
local function newSurface(name, dev)
  return {
    name = name, dev = dev, w = 1, h = 1,
    state = "connecting", detail = "",        -- connecting | connected | reconnecting | offline | refused
    toast = "", toastUntil = 0, bellUntil = 0, --   | unbound | interrupted | exited | ended
    sock = nil, url = nil, pending = nil, hello = nil, welcomed = false, minting = false,
    attempt = 0, timer = nil,
    scroll = 0, maxScroll = 0, mouse = false,
    sel = nil, dragging = false, lastClick = nil,
    yield = false,                            -- give the PTY size back to the surface in use on the next frame
    find = nil, menu = nil,
  }
end

local computer = newSurface("computer", term.current())
local wall = nil                              -- the monitor surface, when a usable wall is attached
local mon = nil                               -- the attached monitor peripheral (a wall or a card)
local surfaces = { computer }

local function each(fn)
  for _, s in ipairs(surfaces) do fn(s) end
end

local function focused()
  return (focus == "monitor" and wall) or computer
end

local function resize(s)
  local sw, sh = s.dev.getSize()
  s.w, s.h = sw, sh - 1                       -- the last row is the status bar
end

-- ---------------------------------------------------------------- drawing

local function blitRow(s, y, t, f, b)
  if #t < s.w then
    local pad = s.w - #t
    t, f, b = t .. (" "):rep(pad), f .. C.text:rep(pad), b .. C.bg:rep(pad)
  end
  s.dev.setCursorPos(1, y)
  s.dev.blit(t:sub(1, s.w), f:sub(1, s.w), b:sub(1, s.w))
end

-- One row from coloured segments { text, fg, bg }, padded with `fill`.
local function segments(s, y, segs, fill)
  local t, f, b = {}, {}, {}
  for i, seg in ipairs(segs) do
    t[i], f[i], b[i] = seg[1], seg[2]:rep(#seg[1]), seg[3]:rep(#seg[1])
  end
  t, f, b = table.concat(t), table.concat(f), table.concat(b)
  local pad = math.max(0, s.w - #t)
  blitRow(s, y, t .. (" "):rep(pad), f .. C.text:rep(pad), b .. fill:rep(pad))
end

local BAR = {
  connected    = { "LIVE",         C.live },
  connecting   = { "CONNECTING",   C.gold },
  reconnecting = { "RECONNECTING", C.gold },
  unbound      = { "NO WORKSPACE", C.gold },
  exited       = { "TAB ENDED",    C.text },
  ended        = { "ENDED",        C.text },
  offline      = { "OFFLINE",      C.bright, C.red },
  refused      = { "REFUSED",      C.bright, C.red },
  interrupted  = { "INTERRUPTED",  C.bright, C.red },
}

local function stateText(s)
  local st = s.state
  if st == "connected" then
    if s.sel then return "selected \183 on the host clipboard" end
    return ""
  elseif st == "connecting" then return "to your workspace..."
  elseif st == "reconnecting" then return "keys are not reaching the host"
  elseif st == "offline" then return s.detail ~= "" and s.detail or "bridge not running: systemctl --user start omo-bridge"
  elseif st == "refused" then return "the bridge refused this screen" .. (s.detail ~= "" and (": " .. s.detail) or "")
  elseif st == "unbound" then return "the workspace is being created..."
  elseif st == "interrupted" then return "nothing is running; revive a tab above"
  elseif st == "exited" then return "revive it or pick another tab above"
  elseif st == "ended" then return "every tab has exited; revive one above"
  end
  return ""
end

local function fit(text, room)
  if #text <= room then return text end
  return text:sub(1, math.max(0, room - 2)) .. ".."
end

local function drawFindBar(s)
  local f = s.find
  local count = f.q == "" and "" or (f.n == 0 and "  no matches" or ("  %d/%d"):format(f.i, f.n))
  local help = "  Enter older \183 Shift+Enter newer \183 Esc"
  local room = s.w - 8 - #count
  if #f.q + 1 + #help > room then help = "" end
  local q = f.q:sub(-math.max(1, room - #help - 1))
  segments(s, s.h + 1, { { " FIND ", C.bg, C.gold }, { " " .. q .. " ", C.bright, C.dim },
                         { count, f.n == 0 and f.q ~= "" and C.red or C.hermes, C.dim }, { help, C.text, C.dim } }, C.dim)
  s.dev.setCursorPos(math.min(s.w, 8 + #q), s.h + 1)
  s.dev.setTextColour(colours.fromBlit(C.gold))
end

local function drawStatus(s)
  if s.find then return drawFindBar(s) end
  local cx, cy = s.dev.getCursorPos()
  local style = BAR[s.state] or BAR.connecting
  local label, lc, bg = style[1], style[2], style[3] or C.dim
  local text = stateText(s)
  if s.toast ~= "" and os.clock() < s.toastUntil then text = s.toast end
  if s.state == "connected" and s.scroll > 0 then
    label, lc, bg = ("SCROLLBACK %d/%d"):format(s.scroll, s.maxScroll), C.bg, C.gold
    if text == "" then text = "End returns to live" end
  end
  if os.clock() < s.bellUntil then bg, lc = C.hermes, C.bg end
  local ink = bg == C.dim and C.text or (bg == C.red and C.bright or C.bg)
  local name = wsName ~= "" and wsName or ("station " .. ID)
  local right = name .. (tabName ~= "" and (" / " .. glyphs(tabName)) or "") .. " "
  local left = 3 + #label + 1
  if left + #right + 4 > s.w then right = "" end
  text = fit(text, s.w - left - #right - 2)
  segments(s, s.h + 1, {
    { " \7 ", lc, bg }, { label, lc, bg }, { " " .. text, ink, bg },
    { (" "):rep(math.max(1, s.w - left - #text - #right)), C.text, bg },
    { right, bg == C.dim and (hermes and C.hermes or C.gold) or ink, bg },
  }, bg)
  s.dev.setCursorPos(cx, cy)
end

local function setToast(s, msg, secs)
  s.toast, s.toastUntil = msg, os.clock() + (secs or 3)
  os.startTimer((secs or 3) + 0.05)           -- the status bar redraws when it expires
  drawStatus(s)
end

-- The Omo wing, as 2x3 teletext sub-pixels (CC characters 128-159).
local WING = {
  "......................###.",
  "..................#######.",
  "..##############.#######..",
  "....############.######...",
  ".................#####....",
  ".....###########.####.....",
  ".......#########.###......",
  "..................##......",
  ".........#######.##.......",
  "...........#####.#........",
  "..........................",
  "..........................",
}

local function drawWing(s, x, y, fg, bg)
  local function px(row, col, r, c)
    return WING[row * 3 + r]:sub(col * 2 + c, col * 2 + c) == "#"
  end
  for row = 0, #WING / 3 - 1 do
    local t, f, b = "", "", ""
    for col = 0, #WING[1] / 2 - 1 do
      local bits = 0
      for _, p in ipairs({ { 1, 1, 1 }, { 1, 2, 2 }, { 2, 1, 4 }, { 2, 2, 8 }, { 3, 1, 16 } }) do
        if px(row, col, p[1], p[2]) then bits = bits + p[3] end
      end
      if px(row, col, 3, 2) then        -- the bottom-right sub-pixel is drawn by inverting
        t, f, b = t .. string.char(128 + 31 - bits), f .. bg, b .. fg
      else
        t, f, b = t .. string.char(128 + bits), f .. fg, b .. bg
      end
    end
    s.dev.setCursorPos(x, y + row)
    s.dev.blit(t, f, b)
  end
end

local function put(s, x, y, text, fg)
  if y < 1 or y > s.h or x > s.w then return end
  text = text:sub(1, s.w - x + 1)
  s.dev.setCursorPos(x, y)
  s.dev.blit(text, fg:rep(#text), C.bg:rep(#text))
end

local SPLASH = {
  connecting   = { { "Connecting to your workspace...", C.gold } },
  unbound      = { { "Creating this station's workspace...", C.gold },
                   { "It appears as soon as the Omo bridge answers.", C.dim } },
  offline      = { { "The Omo bridge is not reachable.", C.red },
                   { "Start it with: systemctl --user start omo-bridge", C.dim },
                   { "This screen retries on its own.", C.dim } },
  refused      = { { "The bridge refused this screen.", C.red },
                   { "Reopen the computer or press Reconnect above;", C.dim },
                   { "if it persists, update the mod and the bridge together.", C.dim } },
  interrupted  = { { "This workspace was interrupted:", C.red },
                   { "the session server or the host restarted.", C.dim },
                   { "Nothing is running and nothing re-runs by itself.", C.dim },
                   { "Revive a shell or resume Hermes from the panel above.", C.text } },
  exited       = { { "This tab's process has ended.", C.text },
                   { "Revive it or pick another tab in the panel above.", C.dim } },
  ended        = { { "Every tab in this workspace has exited.", C.text },
                   { "Revive one from the panel above to carry on.", C.dim } },
}

-- The splash: the wing, who this station is, why, and the keys.
local function splash(s)
  local lines = SPLASH[s.state] or SPLASH.connecting
  if s.detail ~= "" and (s.state == "offline" or s.state == "refused") then
    lines = { lines[1], { s.detail, C.text }, table.unpack(lines, 2) }
  end
  s.dev.setBackgroundColour(colours.fromBlit(C.bg))
  s.dev.clear()
  s.dev.setCursorBlink(false)
  local x, y = 3, 2
  local name = wsName ~= "" and wsName or "new workspace"
  if s.h >= 12 and s.w >= 36 then
    drawWing(s, x, y, hermes and C.hermes or C.gold, C.bg)
    put(s, x + 15, y, "Omo Space", C.gold)
    put(s, x + 15, y + 1, hermes and "Hermes Computer" or "Terminal", C.dim)
    put(s, x + 15, y + 2, name, C.bright)
    y = y + 5
  else
    put(s, x, y, "Omo Space - " .. name, C.gold)
    y = y + 2
  end
  for _, l in ipairs(lines) do
    put(s, x, y, l[1], l[2])
    y = y + 1
  end
  if s.h - 3 > y then
    put(s, x, s.h - 1, "Ctrl+Shift+C/V", C.text)
    put(s, x + 15, s.h - 1, "copy \183 paste", C.dim)
    put(s, x, s.h, "Ctrl+Shift+F", C.text)
    put(s, x + 15, s.h, "find, also in the scrollback", C.dim)
  end
  drawStatus(s)
end

-- A monitor too small to be a terminal shows who this is and how it is.
local function drawCard()
  if not mon or wall then return end
  local s = computer
  local style = BAR[s.state] or BAR.connecting
  pcall(function()
    mon.setTextScale(0.5)
    applyPalette(mon, palette)
    mon.setBackgroundColour(colours.fromBlit(C.bg))
    mon.clear()
    local mw, mh = mon.getSize()
    local lines = { { "Omo", C.gold }, { wsName ~= "" and wsName or ("station " .. ID), C.bright },
                    { style[1], style[3] or style[2] }, { "open the computer", C.dim } }
    local top = math.max(1, math.floor((mh - #lines) / 2) + 1)
    for i, l in ipairs(lines) do
      local text = l[1]:sub(1, mw)
      mon.setCursorPos(math.max(1, math.floor((mw - #text) / 2) + 1), top + i - 1)
      mon.setTextColour(colours.fromBlit(l[2]))
      mon.write(text)
    end
  end)
end

local function showState(s, st, why)
  s.state = st
  s.detail = why or ""
  s.find, s.menu, s.sel = nil, nil, nil
  if SPLASH[st] then splash(s) else drawStatus(s) end   -- reconnecting keeps the last frame under its bar
  if s == computer then drawCard() end
end

local function paint(s, f)
  s.scroll, s.maxScroll, s.mouse = f.scroll or 0, f.max_scroll or 0, f.mouse or false
  for _, entry in ipairs(f.rows_changed or {}) do
    local y, runs = entry[1], entry[2]
    if y >= 1 and y <= s.h then
      local t, fg, bg = {}, {}, {}
      for i, run in ipairs(runs) do
        t[i], fg[i], bg[i] = glyphs(run[2]), run[3]:rep(run[1]), run[4]:rep(run[1])
      end
      blitRow(s, y, table.concat(t), table.concat(fg), table.concat(bg))
    end
  end
  drawStatus(s)
  local menu = s.menu
  if menu then menu.draw() end
  if s.find then
    s.dev.setCursorBlink(true)
  elseif f.cursor and s.scroll == 0 and not f.cursor_hidden and not menu then
    s.dev.setCursorPos(math.min(f.cursor[1] + 1, s.w), math.min(f.cursor[2] + 1, s.h))
    s.dev.setTextColour(colours.fromBlit(C.bright))
    s.dev.setCursorBlink(true)
  else
    s.dev.setCursorBlink(false)
  end
end

-- ---------------------------------------------------------------- connection

-- What the bridge ends a selection for (typing, pasting, moving the view).
local ENDS_SELECTION = { ["in"] = true, paste = true, scroll = true, wheel = true, find = true, clear = true }

-- Send a JSON message; never let a dead socket raise.
local function send(s, tbl)
  if not (s and s.sock and s.welcomed) then return false end
  if ENDS_SELECTION[tbl.t] then s.sel = nil end
  return pcall(function() s.sock.send(textutils.serialiseJSON(tbl)) end)
end

local function dropSocket(s)
  if s.sock then pcall(function() s.sock.close() end) end
  s.sock, s.url, s.welcomed, s.pending, s.hello = nil, nil, false, nil, nil
end

-- Try again later. Backs off, never gives up quietly: the bar always says why.
local function retry(s, st, why)
  dropSocket(s)
  s.attempt = s.attempt + 1
  local delay
  if st == "refused" then delay = 10
  elseif st == "unbound" then delay = 2
  else delay = s.attempt < 4 and 1 or (s.attempt < 10 and 3 or 8) end
  if s.state ~= st or st == "offline" or st == "refused" then showState(s, st, why) else drawStatus(s) end
  s.timer = os.startTimer(delay)
end

-- Ask for a socket. The ticket is minted by a worker coroutine (omo.ticket
-- waits for its own event; nothing else is lost meanwhile) and the handshake
-- finishes in the event loop.
local function connect(s)
  if s.sock or s.pending or s.minting then return end
  local st = omo.station()
  hermes = st.hermes
  if st.name ~= "" then wsName = st.name end
  if not st.bound then
    omo.ensure()
    return retry(s, "unbound")
  end
  s.minting = true
  tickets[#tickets + 1] = s
  os.queueEvent("omo_ticket_wanted")
end

local function onTicket(s, ticket, wsOrWhy)
  s.minting = false
  if not ticket then
    if wsOrWhy == "unbound" then return retry(s, "unbound") end
    if tostring(wsOrWhy):find("refused") then return retry(s, "refused", wsOrWhy) end
    return retry(s, "offline", wsOrWhy == "bridge unreachable" and "" or tostring(wsOrWhy or ""))
  end
  connSeq = connSeq + 1
  wsId = wsOrWhy
  s.pending = ("%s?screen=%d-%s-%d"):format(BRIDGE, ID, s.name, connSeq)
  s.hello = { t = "hello", ticket = ticket, station = omo.station().station, ws = wsId, world = "mc" }
  local ok, err = pcall(http.websocketAsync, s.pending)
  if not ok then
    s.pending = nil
    retry(s, "offline", tostring(err))
  end
end

local function ticketWorker()
  while true do
    local s = table.remove(tickets, 1)
    if s then
      local ok, ticket, why = pcall(omo.ticket)
      os.queueEvent("omo_ticket_ready", s.name, ok and ticket or nil, ok and why or tostring(ticket))
    else
      os.pullEventRaw("omo_ticket_wanted")
    end
  end
end

local function reconnectAll()
  each(function(s)
    dropSocket(s)
    s.timer, s.attempt = nil, 0
    connect(s)
  end)
end

-- The surface in use sizes the PTY: its view becomes tmux's latest client.
local function claimSize()
  send(focused(), { t = "focus" })
end

local function setFocus(name)
  if name ~= focus then
    focus = name
    claimSize()
  end
  ctrl, shift, alt = false, false, false
end

-- ---------------------------------------------------------------- the wall

local SCALES = { 0.5, 1, 1.5, 2, 2.5, 3, 4, 5 }
local MIN_W, MIN_H = 20, 6                    -- smaller than this at 0.5 is a status card
local wallFit = nil                           -- { scale, w, h } last chosen, to tell our own resize events
local wallZoom = nil                          -- a scale picked with Ctrl+Shift+= / -, until the wall changes

-- The largest text at which the wall still holds the computer's whole
-- screen (so it shows everything while the computer drives); on a smaller
-- wall, the densest. Ctrl+Shift+= / - pick another scale until the wall is
-- built bigger or smaller.
local function fitWall()
  local cw, ch = computer.dev.getSize()
  local want = wallZoom
  local chosen = 1
  for i = #SCALES, 1, -1 do
    mon.setTextScale(SCALES[i])
    local mw, mh = mon.getSize()
    if SCALES[i] == want and mw >= MIN_W and mh >= MIN_H then chosen = i break end
    if not want and mw >= cw and mh >= ch then chosen = i break end
  end
  mon.setTextScale(SCALES[chosen])
  local mw, mh = mon.getSize()
  wallFit = { SCALES[chosen], mw, mh }
  return mw >= MIN_W and mh >= MIN_H
end

local function dropWall()
  if not wall then return end
  dropSocket(wall)
  for i, s in ipairs(surfaces) do
    if s == wall then table.remove(surfaces, i) end
  end
  wall = nil
  if focus == "monitor" then setFocus("computer") end
end

-- (Re)decide what the attached monitor is: a terminal surface or a card.
local function refitWall()
  local found = peripheral.find("monitor")
  if mon and found and peripheral.getName(found) ~= peripheral.getName(mon) then dropWall() end
  if mon and not found then
    pcall(function() restorePalette(mon) mon.setTextScale(1) mon.clear() end)
    dropWall()
  end
  mon = found
  if not mon then return end
  local usable = fitWall()
  if not usable then
    dropWall()
    drawCard()
    return
  end
  if not wall then
    wall = newSurface("monitor", mon)
    surfaces[#surfaces + 1] = wall
    applyPalette(mon, palette)
    resize(wall)
    splash(wall)
    connect(wall)
  else
    resize(wall)
    mon.setBackgroundColour(colours.fromBlit(C.bg))
    mon.clear()
    if wall.state == "connected" then
      send(wall, { t = "size", cols = wall.w, rows = wall.h })
      wall.yield = focus ~= "monitor"
    else
      splash(wall)
    end
  end
end

-- A wall that changes under us (re-forming, half broken) must never take the
-- station down: whatever goes wrong, it is looked at again on the next check.
local function checkWall(changed)
  if changed then wallZoom = nil end
  if not pcall(refitWall) then
    dropWall()
    mon, wallFit = nil, nil
  end
end

local function wallChanged()
  local ok, mw, mh = pcall(function() return mon.getSize() end)
  return not (ok and wallFit and mw == wallFit[2] and mh == wallFit[3])
end

local function zoomWall(delta)
  if not wall then return setToast(focused(), "zoom needs a monitor wall") end
  local cur = 1
  for i, sc in ipairs(SCALES) do
    if sc == (wallFit and wallFit[1]) then cur = i end
  end
  local want = SCALES[math.max(1, math.min(#SCALES, cur + delta))]
  local ok, fits = pcall(function()
    mon.setTextScale(want)
    local mw, mh = mon.getSize()
    return mw >= MIN_W and mh >= MIN_H
  end)
  if ok and fits then wallZoom = want end
  checkWall(false)
  setToast(focused(), wallZoom == want and ("wall text %sx"):format(want) or "the wall is too small for bigger text")
end

-- ---------------------------------------------------------------- selection, menu, find

local function sendSel(s)
  local a, b = s.sel.a, s.sel.b
  send(s, { t = "sel", x1 = a[1], y1 = a[2], x2 = b[1], y2 = b[2], unit = s.sel.unit })
end

local function selectAll(s)
  s.sel = { a = { 0, 0 }, b = { s.w - 1, s.h - 1 }, unit = "line" }
  sendSel(s)
  send(s, { t = "copy" })
end

local function openFind(s)
  s.find = { q = "", i = 0, n = 0 }
  drawStatus(s)
  s.dev.setCursorBlink(true)
end

local function closeFind(s)
  s.find = nil
  send(s, { t = "find", q = "" })
  drawStatus(s)
end

local function findStep(s, step)
  send(s, { t = "find", q = s.find.q, step = step })
  drawStatus(s)
end

local function findKey(s, k)
  if k == keys.enter or k == keys.numPadEnter or k == keys.up or k == keys.down then
    findStep(s, (shift or k == keys.down) and -1 or 1)
  elseif k == keys.backspace then
    s.find.q = s.find.q:sub(1, -2)
    findStep(s, 0)
  elseif k == keys.escape or (ctrl and (keys.getName(k) == "c" or keys.getName(k) == "g" or keys.getName(k) == "f")) then
    closeFind(s)
  end
end

-- The right-click menu, drawn over the terminal; the bridge repaints what it
-- covered when it closes.
local MENU = {
  { "Copy", function(s) send(s, { t = "copy" }) end, function(s) return s.sel ~= nil end },
  { "Paste", function(s) send(s, { t = "paste" }) end },
  { "Select all", selectAll },
  { "Find...", openFind },
  { "Clear scrollback", function(s) send(s, { t = "clear" }) end },
}

local function closeMenu(s)
  s.menu = nil
  send(s, { t = "redraw" })
end

local MENU_W = 20

local function openMenu(s, x, y)
  local width = MENU_W
  local menu = { x = math.max(1, math.min(x, s.w - width + 1)), y = math.max(1, math.min(y, s.h - #MENU + 1)),
                 pick = 1 }
  function menu.enabled(i)
    return not MENU[i][3] or MENU[i][3](s)
  end
  function menu.draw()
    for i, item in ipairs(MENU) do
      local label = (" " .. item[1]):sub(1, width)
      local fg = menu.enabled(i) and C.bg or C.dim
      local bg = i == menu.pick and menu.enabled(i) and C.gold or C.bright
      s.dev.setCursorPos(menu.x, menu.y + i - 1)
      s.dev.blit(label .. (" "):rep(width - #label), fg:rep(width), bg:rep(width))
    end
    s.dev.setCursorBlink(false)
  end
  function menu.run(i)
    closeMenu(s)
    if menu.enabled(i) then MENU[i][2](s) end
  end
  s.menu = menu
  menu.draw()
end

local function menuKey(s, k)
  local menu = s.menu
  if k == keys.up or k == keys.down then
    menu.pick = (menu.pick + (k == keys.up and -2 or 0)) % #MENU + 1
    menu.draw()
  elseif k == keys.enter or k == keys.numPadEnter then
    menu.run(menu.pick)
  else
    closeMenu(s)
  end
end

-- ---------------------------------------------------------------- mouse

-- CC mouse buttons (1 left, 2 right, 3 middle) to xterm SGR (0 left, 1 middle, 2 right).
local SGR_BUTTON = { [1] = 0, [2] = 2, [3] = 1 }
local DOUBLE_MS = 400

local function report(s, code, x, y, up)
  send(s, { t = "in", d = ("\27[<%d;%d;%d%s"):format(code, x, y, up and "m" or "M") })
end

-- Mouse events on a surface, in its cells (1-based). kind: click | drag | up | scroll.
local function mouse(s, kind, button, x, y)
  if s.state ~= "connected" then return end
  x, y = math.max(1, math.min(x, s.w)), math.max(1, y)
  local menu = s.menu
  if menu then
    if kind == "click" then
      local i = y - menu.y + 1
      if x >= menu.x and x < menu.x + MENU_W and MENU[i] then menu.run(i) else closeMenu(s) end
    end
    return
  end
  if kind == "scroll" then
    if ctrl and s == wall then return zoomWall(button < 0 and 1 or -1) end
    return send(s, { t = "wheel", by = button < 0 and (shift and 5 or 1) or (shift and -5 or -1), x = x - 1,
                     y = math.min(y, s.h) - 1 })
  end
  -- An app that asked for the mouse (Hermes, vim, htop) still gets clicks and
  -- the wheel, but a left-button DRAG selects text, as in the plain shell: the
  -- press is held back until we know whether it is a click or a drag. Shift
  -- still reaches past the app for Shift+click / Shift+drag selection. A
  -- double or triple click selects a word or line here too, so what is
  -- highlighted is always ours and Ctrl+Shift+C / Ctrl+C copy it.
  if s.mouse and not shift then
    if y > s.h then return end
    local held = s.heldPress
    local last = s.lastClick
    local again = last and os.epoch("utc") - last.at < DOUBLE_MS and last.y == y - 1 and math.abs(last.x - (x - 1)) <= 1
    if kind == "click" and button == 1 and again then
      s.heldPress = nil                        -- a second/third click: falls through to word/line selection
    elseif kind == "click" and button == 1 then
      s.heldPress = { x = x, y = y }
      return
    elseif kind == "drag" and held then
      if held.x == x and held.y == y then return end
      s.heldPress = nil
      local a = { held.x - 1, held.y - 1 }
      s.sel = { a = a, b = { x - 1, y - 1 }, unit = "char" }
      s.dragging = true
      return sendSel(s)
    elseif kind == "up" and held then
      s.heldPress = nil                        -- no movement: a plain click for the app
      s.lastClick = { at = os.epoch("utc"), x = held.x - 1, y = held.y - 1, count = 1 }
      report(s, 0, held.x, held.y, false)
      return report(s, 0, held.x, held.y, true)
    elseif s.dragging and (kind == "drag" or kind == "up") then
      -- our own selection, started above; fall through to the selection code
    else
      local code = SGR_BUTTON[button] or 0
      if kind == "drag" then code = code + 32 end
      return report(s, code, x, y, kind == "up")
    end
  end
  y = math.min(y, s.h)
  local cell = { x - 1, y - 1 }
  if kind == "click" and button == 1 then
    if ctrl then return send(s, { t = "open", x = cell[1], y = cell[2] }) end
    local now, last = os.epoch("utc"), s.lastClick
    local count = 1
    if last and now - last.at < DOUBLE_MS and last.y == cell[2] and math.abs(last.x - cell[1]) <= 1 then
      count = last.count % 3 + 1
    end
    s.lastClick = { at = now, x = cell[1], y = cell[2], count = count }
    local unit = ({ "char", "word", "line" })[count]
    if shift and s.sel then
      s.sel.b = cell
    else
      s.sel = { a = cell, b = cell, unit = unit }
    end
    s.dragging = true
    if unit ~= "char" or shift then sendSel(s) else send(s, { t = "sel" }) end
  elseif kind == "click" and button == 2 then
    openMenu(s, x, y)
  elseif kind == "click" and button == 3 then
    send(s, { t = "paste" })
  elseif kind == "drag" and s.dragging and s.sel then
    s.sel.b = cell
    sendSel(s)
  elseif kind == "up" and s.dragging then
    s.dragging = false
    local sel = s.sel
    if sel and (sel.unit ~= "char" or sel.a[1] ~= sel.b[1] or sel.a[2] ~= sel.b[2]) then
      send(s, { t = "copy" })                 -- copy on select, like a desktop terminal
    else
      s.sel = nil
    end
    drawStatus(s)
  end
end

-- ---------------------------------------------------------------- keys

-- xterm encodings; `m` is the modifier parameter (1 + Shift 1, Alt 2, Ctrl 4).
local CSI = { [keys.up] = "A", [keys.down] = "B", [keys.right] = "C", [keys.left] = "D",
              [keys.home] = "H", [keys["end"]] = "F" }
local SS3 = { [keys.f1] = "P", [keys.f2] = "Q", [keys.f3] = "R", [keys.f4] = "S" }
local TILDE = { [keys.insert] = 2, [keys.delete] = 3, [keys.pageUp] = 5, [keys.pageDown] = 6,
                [keys.f5] = 15, [keys.f6] = 17, [keys.f7] = 18, [keys.f8] = 19, [keys.f9] = 20,
                [keys.f10] = 21, [keys.f11] = 23, [keys.f12] = 24 }
-- US layout for the keys CC names by word: { plain, shifted }.
local US = { one = { "1", "!" }, two = { "2", "@" }, three = { "3", "#" }, four = { "4", "$" },
             five = { "5", "%" }, six = { "6", "^" }, seven = { "7", "&" }, eight = { "8", "*" },
             nine = { "9", "(" }, zero = { "0", ")" }, minus = { "-", "_" }, equals = { "=", "+" },
             leftBracket = { "[", "{" }, rightBracket = { "]", "}" }, backslash = { "\\", "|" },
             semicolon = { ";", ":" }, apostrophe = { "'", '"' }, grave = { "`", "~" },
             comma = { ",", "<" }, period = { ".", ">" }, slash = { "/", "?" }, space = { " ", " " } }
local CTRL_PUNCT = { space = "\0", two = "\0", leftBracket = "\27", backslash = "\28", rightBracket = "\29",
                     six = "\30", minus = "\31", slash = "\31" }

local function printable(k)
  local n = keys.getName(k)
  if not n then return nil end
  if #n == 1 and n:match("%a") then return shift and n:upper() or n end
  return US[n] and US[n][shift and 2 or 1]
end

-- The bytes a key sends to the app, or nil.
local function keyBytes(k)
  local m = 1 + (shift and 1 or 0) + (alt and 2 or 0) + (ctrl and 4 or 0)
  local meta = alt and "\27" or ""
  if CSI[k] then return m > 1 and ("\27[1;%d%s"):format(m, CSI[k]) or "\27[" .. CSI[k] end
  if SS3[k] then return m > 1 and ("\27[1;%d%s"):format(m, SS3[k]) or "\27O" .. SS3[k] end
  if TILDE[k] then return m > 1 and ("\27[%d;%d~"):format(TILDE[k], m) or ("\27[%d~"):format(TILDE[k]) end
  if k == keys.enter or k == keys.numPadEnter then return meta .. "\r" end
  if k == keys.tab then return shift and "\27[Z" or meta .. "\t" end
  if k == keys.backspace then return ctrl and "\23" or meta .. "\127" end
  if k == keys.escape then return "\27" end
  local name = keys.getName(k)
  if ctrl then
    if name and #name == 1 and name:match("%a") then return meta .. string.char(name:byte() - 96) end
    return CTRL_PUNCT[name] and meta .. CTRL_PUNCT[name]
  end
  if alt then
    local ch = printable(k)
    return ch and "\27" .. ch
  end
end

-- Ctrl+Shift actions on the terminal (the panel's own never reach here).
local ACTIONS = {
  c = function(s) send(s, { t = "copy" }) end,
  v = function(s) send(s, { t = "paste" }) end,
  a = selectAll,
  f = function(s) if s.find then closeFind(s) else openFind(s) end end,
  equals = function() zoomWall(1) end,
  minus = function() zoomWall(-1) end,
  up = function(s) send(s, { t = "scroll", by = 1 }) end,
  down = function(s) send(s, { t = "scroll", by = -1 }) end,
  home = function(s) send(s, { t = "scroll", by = 100000 }) end,
  ["end"] = function(s) send(s, { t = "scroll", by = -100000 }) end,
}

local running = true

local function onKey(s, k)
  if k == keys.leftCtrl or k == keys.rightCtrl then ctrl = true return end
  if k == keys.leftShift or k == keys.rightShift then shift = true return end
  if k == keys.leftAlt then alt = true return end
  if s.menu then return menuKey(s, k) end
  if s.find and not (ctrl and shift) then return findKey(s, k) end
  local name = keys.getName(k)
  if ctrl and shift and name == "q" then running = false return end
  if ctrl and shift and ACTIONS[name] then return ACTIONS[name](s) end
  if s.state ~= "connected" then return end
  -- Ctrl+C with text highlighted copies it (like Windows Terminal / VS Code);
  -- with nothing highlighted it is the usual interrupt.
  if ctrl and not shift and not alt and name == "c" and s.sel then
    send(s, { t = "copy" })
    s.sel = nil
    send(s, { t = "sel" })
    return drawStatus(s)
  end
  if shift and not ctrl and (k == keys.pageUp or k == keys.pageDown) then
    return send(s, { t = "scroll", by = (k == keys.pageUp and 1 or -1) * math.max(1, s.h - 2) })
  end
  if k == keys["end"] and s.scroll > 0 and not (ctrl or shift or alt) then
    return send(s, { t = "scroll", by = -100000 })
  end
  local bytes = keyBytes(k)
  if bytes then send(s, { t = "in", d = bytes }) end
end

local function onChar(s, ch)
  if s.find then
    s.find.q = s.find.q .. ch
    return findStep(s, 0)
  end
  if s.menu then closeMenu(s) end
  if not ctrl and not alt then send(s, { t = "in", d = ch }) end
end

-- CC's own Ctrl+V (and a remote paste) carries a cut-down copy of the host
-- clipboard; the bridge pastes the full text when it matches.
local function onPaste(s, text)
  if s.find then
    s.find.q = s.find.q .. text:match("^[^\n]*")
    return findStep(s, 0)
  end
  send(s, { t = "paste", d = text })
end

-- ---------------------------------------------------------------- bridge messages

local function surfaceNamed(name)
  for _, s in ipairs(surfaces) do
    if s.name == name then return s end
  end
end

local function surfaceFor(url)
  for _, s in ipairs(surfaces) do
    if url and (s.url == url or s.pending == url) then return s end
  end
end

local function attach(s)
  send(s, { t = "attach", ws = wsId, tab = tabId, cols = s.w, rows = s.h })
end

local function learnTabs(ws)
  if not ws then return end
  wsName = ws.name and glyphs(ws.name) or wsName   -- names ("Website · Dev") arrive as UTF-8
  for _, tb in ipairs(ws.tabs or {}) do
    if tb.id == tabId then tabName = tb.name end
  end
end

local function onMessage(s, msg)
  if not s.welcomed then
    if msg.t == "welcome" then
      s.welcomed, s.attempt = true, 0
      if msg.palette then
        local changed = type(palette) ~= "table"
        for digit, hex in pairs(msg.palette) do changed = changed or palette[digit] ~= hex end
        if changed then
          palette = msg.palette
          settings.set("omo.palette", palette)
          settings.save()
          each(function(o) applyPalette(o.dev, palette) end)
        end
      end
      attach(s)
    else
      retry(s, "refused", msg.m or "unexpected reply")
    end
    return
  end
  if msg.t == "frame" then
    -- A view that attached or resized has just become tmux's latest client;
    -- once it paints, the surface in use takes the size back.
    if s.yield then
      s.yield = false
      claimSize()
    end
    if s.state ~= "connected" then
      s.state = "connected"
      s.dev.setBackgroundColour(colours.fromBlit(C.bg))
      s.dev.clear()
    end
    paint(s, msg)
  elseif msg.t == "attached" then
    tabId = msg.tab or ""
    learnTabs(msg.ws)
    if msg.state == "live" then
      s.state = "connected"
      s.find, s.menu, s.sel = nil, nil, nil
      s.dev.setBackgroundColour(colours.fromBlit(C.bg))
      s.dev.clear()
      send(s, { t = "size", cols = s.w, rows = s.h })
      s.yield = s ~= focused()
      drawStatus(s)
    elseif msg.state == "unbound" then
      showState(s, "unbound")
    elseif msg.state == "exited" then
      showState(s, msg.ws and msg.ws.state == "ended" and "ended" or "exited")
    else
      showState(s, "interrupted")
    end
    if s == computer then drawCard() end
  elseif msg.t == "workspace" then
    learnTabs(msg.ws)
    -- The user just revived or opened a tab from the panel: look at a live
    -- tab again so they see it. Never re-runs anything by itself.
    local ws = msg.ws
    if ws and s.state ~= "connected" and ws.tabs then
      local pick = nil
      for _, tb in ipairs(ws.tabs) do
        if tb.state == "live" and (pick == nil or tb.name == tabName) then pick = tb end
      end
      if pick then
        s.state, tabId = "connecting", pick.id
        attach(s)
      elseif ws.state == "ended" and s.state == "exited" then
        showState(s, "ended")
      end
    end
    drawStatus(s)
  elseif msg.t == "detached" then
    showState(s, msg.state == "interrupted" and "interrupted" or "exited")
  elseif msg.t == "found" then
    if s.find and msg.q == s.find.q then
      s.find.i, s.find.n = msg.i or 0, msg.n or 0
      drawStatus(s)
    end
  elseif msg.t == "bell" then
    s.bellUntil = os.clock() + 0.3
    drawStatus(s)
    os.startTimer(0.35)
  elseif msg.t == "toast" then
    setToast(s, tostring(msg.m))
  elseif msg.t == "error" then
    setToast(s, "bridge: " .. tostring(msg.m), 6)
  end
end

-- ---------------------------------------------------------------- main loop

local function handle(ev)
  local e = ev[1]
  if e == "websocket_success" then
    local s = surfaceFor(ev[2])
    if s and ev[2] == s.pending then
      s.sock, s.url, s.welcomed, s.pending = ev[3], ev[2], false, nil
      pcall(function() s.sock.send(textutils.serialiseJSON(s.hello)) end)
      s.hello = nil
    else
      pcall(function() ev[3].close() end)       -- a stale attempt
    end
  elseif e == "websocket_failure" then
    local s = surfaceFor(ev[2])
    if s and ev[2] == s.pending then
      s.pending = nil
      retry(s, "offline", "")
    end
  elseif e == "websocket_message" then
    local s = surfaceFor(ev[2])
    if s and ev[2] == s.url then
      local msg = textutils.unserialiseJSON(ev[3])
      if msg then onMessage(s, msg) end
    end
  elseif e == "websocket_closed" then
    local s = surfaceFor(ev[2])
    if s and ev[2] == s.url then
      local was = s.welcomed
      s.sock, s.url = nil, nil
      retry(s, was and "reconnecting" or "refused", not was and "closed during the handshake" or nil)
    end
  elseif e == "omo_ticket_ready" then
    local s = surfaceNamed(ev[2])
    if s then onTicket(s, ev[3], ev[4]) end
  elseif e == "timer" then
    each(function(s)
      if ev[2] == s.timer then
        s.timer = nil
        connect(s)
      elseif s.state == "connected" or s.state == "reconnecting" then
        drawStatus(s)                          -- a toast or a bell flash has run out
      end
    end)
  elseif e == "omo_bound" then
    -- The server created or rebound this station's workspace: (re)connect now.
    if ev[3] and ev[3] ~= "" then wsName = ev[3] end
    if ev[2] ~= wsId or not computer.sock then reconnectAll() end
  elseif e == "omo_focus" then
    setFocus(ev[2] == "monitor" and wall and "monitor" or "computer")
  elseif e == "omo_release" then
    setFocus("computer")
  elseif e == "key" then
    onKey(focused(), ev[2])
  elseif e == "key_up" then
    local k = ev[2]
    if k == keys.leftCtrl or k == keys.rightCtrl then ctrl = false end
    if k == keys.leftShift or k == keys.rightShift then shift = false end
    if k == keys.leftAlt then alt = false end
  elseif e == "char" then
    onChar(focused(), ev[2])
  elseif e == "paste" then
    onPaste(focused(), ev[2])
  elseif e == "mouse_click" or e == "mouse_drag" or e == "mouse_up" or e == "mouse_scroll" then
    setFocus("computer")                       -- only the computer's own GUI has a pointer
    mouse(computer, ({ mouse_click = "click", mouse_drag = "drag", mouse_up = "up", mouse_scroll = "scroll" })[e],
          ev[2], ev[3], ev[4])
  elseif e == "omo_mouse" then
    if wall then
      setFocus("monitor")
      mouse(wall, ev[2], ev[3], ev[4], ev[5])
    end
  elseif e == "monitor_touch" then
    if wall then                               -- a tap on the wall is a whole click
      mouse(wall, "click", 1, ev[3], ev[4])
      mouse(wall, "up", 1, ev[3], ev[4])
    end
  elseif e == "terminate" then
    send(focused(), { t = "in", d = "\3" })    -- CC's Ctrl+T hold: an interrupt, never an exit
  elseif e == "term_resize" then
    resize(computer)
    computer.dev.clear()
    if computer.state == "connected" then
      send(computer, { t = "size", cols = computer.w, rows = computer.h })
    else
      splash(computer)
    end
  elseif e == "monitor_resize" then
    if mon and wallChanged() then checkWall(true) end     -- not our own scale change: the wall was rebuilt
  elseif e == "peripheral" or e == "peripheral_detach" then
    checkWall(false)
  end
end

local function main()
  applyPalette(computer.dev, palette)
  resize(computer)
  showState(computer, "connecting")
  checkWall(false)
  connect(computer)
  -- The wall is re-checked every few seconds as well as on its events: an
  -- attach event can come before a fresh monitor offers its peripheral, and
  -- a missed resize must not leave the wall at its old size.
  local wallTimer = os.startTimer(3)
  while running do
    local ev = { os.pullEventRaw() }
    if ev[1] == "timer" and ev[2] == wallTimer then
      if (peripheral.find("monitor") ~= nil) ~= (mon ~= nil) or (mon and wallChanged()) then checkWall(true) end
      wallTimer = os.startTimer(3)
    else
      handle(ev)
    end
  end
end

parallel.waitForAny(main, ticketWorker)

-- Leaving is deliberate (Ctrl+Shift+Q): hand the glass back to CraftOS as found.
each(dropSocket)
if mon then pcall(function() restorePalette(mon) mon.setTextScale(1) mon.clear() end) end
restorePalette(computer.dev)
term.setBackgroundColour(colours.black)
term.setTextColour(colours.white)
term.clear()
term.setCursorPos(1, 1)
print("Station screen closed. Type 'station' to reopen it.")
