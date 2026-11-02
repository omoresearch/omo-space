#!/usr/bin/env python3
"""Which surface sizes the PTY: the REAL station.lua, run in Lua 5.4 (lupa)
with small ComputerCraft stubs, against a real throwaway bridge and tmux
server. No game, no player input.

A station has two surfaces showing one tab: the computer's own screen and,
when one is attached, a monitor wall. Only the surface in use may size the
tab's tmux window, or a TUI is drawn for the wrong size (cropped, or tiny in
a corner of the wall). The computer's own screen is only visible while a
player has its GUI open (the mod sends ``omo_gui``); the rest of the time the
wall is the only thing anyone can see, so it must own the size without being
clicked first.

    python3 tests/test_station_sizing.py
    python3 -m unittest tests.test_station_sizing

Needs the ``lupa`` module (``pip install lupa``) and tmux; skipped otherwise.
Nothing here touches the live bridge, its token or its tmux socket.
"""
import heapq
import json
import os
import pwd
import queue
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATION_LUA = os.path.join(REPO, "mod", "src", "main", "resources", "data", "computercraft", "lua", "rom",
                           "programs", "omo", "station.lua")
sys.path.insert(0, os.path.join(REPO, "bridge", "vendor"))

try:
    from lupa.lua54 import LuaRuntime
except ImportError:                     # optional: only this test needs it
    LuaRuntime = None


def find_tmux():
    # Other tests in a discover run fake HOME, so the real home comes from pwd.
    home = pwd.getpwuid(os.getuid()).pw_dir
    for cand in (os.environ.get("OMO_TMUX"), os.path.join(home, ".local", "share", "omo", "bin", "tmux"),
                 shutil.which("tmux")):
        # (In a discover run another test sets OMO_TMUX=/bin/true: ask the binary.)
        try:
            if cand and subprocess.run([cand, "-V"], capture_output=True, text=True,
                                       timeout=5).stdout.startswith("tmux"):
                return cand
        except (OSError, subprocess.SubprocessError):
            continue
    return None


TMUX = find_tmux()

# The station's computer screen (80x30: the PTY gets 80x29, the last row is
# the status bar) and a wall that, at the scale station.lua picks, is larger.
COMPUTER_W, COMPUTER_H = 80, 30

CC_STUBS = r'''
local function noop() end
local function device(w, h)
  local d = { w = w, h = h, scale = 1 }
  d.getSize = function() return d.w, d.h end
  d.isColour = function() return true end
  d.getCursorPos = function() return 1, 1 end
  return setmetatable(d, { __index = function() return noop end })
end
COMPUTER = device(COMPUTER_W, COMPUTER_H)
MON = device(0, 0)
MON.setTextScale = function(s) MON.scale = s end
MON.getTextScale = function() return MON.scale end
MON.getSize = function() return math.floor(230 / MON.scale), math.floor(90 / MON.scale) end
term = setmetatable({ current = function() return COMPUTER end, nativePaletteColour = function() return 0 end },
                    { __index = function(_, k) return COMPUTER[k] end })
colours = setmetatable({ fromBlit = function(c) return 2 ^ tonumber(c, 16) end, black = 32768, white = 1 },
                       { __index = function() return 1 end })
colors = colours
keys = setmetatable({}, { __index = function() return -1 end })
local store = {}
settings = { get = function(k) return store[k] end, set = function(k, v) store[k] = v end, save = noop }
peripheral = {
  find = function(kind) if kind == "monitor" and WALL_ON then return MON end end,
  getName = function() return "monitor_0" end,
}
http = { websocketAsync = function(url) return py.ws_async(url) end }
omo = {
  bridge = function() return "ws://bridge/ws" end,
  station = function() return { hermes = false, name = "sizing", bound = true, station = "st-sizing" } end,
  ticket = function() return py.ticket() end,
  ensure = noop,
}
local function esc(s)
  return (s:gsub('[%c"\\]', function(c)
    if c == '"' then return '\\"' elseif c == "\\" then return "\\\\" end
    return string.format("\\u%04x", c:byte())
  end))
end
local function enc(v)
  local t = type(v)
  if t == "table" then
    if v[1] ~= nil or next(v) == nil then
      local out = {}
      for i, x in ipairs(v) do out[i] = enc(x) end
      return "[" .. table.concat(out, ",") .. "]"
    end
    local out = {}
    for k, x in pairs(v) do out[#out + 1] = '"' .. esc(tostring(k)) .. '":' .. enc(x) end
    return "{" .. table.concat(out, ",") .. "}"
  elseif t == "string" then return '"' .. esc(v) .. '"'
  elseif t == "number" then
    return (math.type(v) == "integer" or v ~= math.floor(v)) and tostring(v) or tostring(math.floor(v))
  elseif t == "boolean" then return tostring(v) end
  return "null"
end
textutils = { serialiseJSON = enc, serializeJSON = enc, unserialiseJSON = function(s) return py.unserialise(s) end }
textutils.unserializeJSON = textutils.unserialiseJSON
os.getComputerID = function() return 7 end
os.startTimer = function(d) return py.start_timer(d) end
os.cancelTimer = noop
os.queueEvent = function(...) py.queue_event(...) end
os.pullEventRaw = function(f) return coroutine.yield(f) end
os.pullEvent = os.pullEventRaw
os.clock = function() return py.clock() end
os.epoch = function() return py.epoch() end
parallel = {
  waitForAny = function(...)
    local cos, filters = {}, {}
    for i, f in ipairs({ ... }) do cos[i] = coroutine.create(f) end
    local ev = { n = 0 }
    while true do
      for i, co in ipairs(cos) do
        if filters[i] == nil or filters[i] == ev[1] then
          local ok, f = coroutine.resume(co, table.unpack(ev, 1, ev.n or #ev))
          if not ok then error(f, 0) end
          filters[i] = f
          if coroutine.status(co) == "dead" then return i end
        end
      end
      local e = py.next_event()
      ev = { n = #e }
      for k = 1, #e do ev[k] = e[k] end
    end
  end,
}
'''


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Stack:
    """A throwaway tmux server + bridge, and one terminal workspace."""

    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix="omo-station-sizing-")
        self.data = os.path.join(self.dir, "data")
        os.makedirs(self.data)
        self.sock = f"omosizing{os.getpid()}"
        self.port = free_port()
        subprocess.run([TMUX, "-L", self.sock, "-f", os.path.join(REPO, "config", "omo-tmux.conf"),
                        "start-server"], check=True)
        env = {**os.environ, "OMO_DATA": self.data, "OMO_PORT": str(self.port), "OMO_TMUX": TMUX,
               "OMO_TMUX_SOCKET": self.sock, "OMO_HERMES_ROOT": os.path.join(self.dir, "hermes"),
               "OMO_HERMES_BIN": "/bin/false", "OMO_DISPLAY": ":65000", "DISPLAY": ":65000",
               "OMO_SLEEP_SCAN": "3600"}
        env.pop("WAYLAND_DISPLAY", None)
        self.stderr = open(os.path.join(self.dir, "stderr"), "w")
        self.bridge = subprocess.Popen([sys.executable, os.path.join(REPO, "bridge", "omobridge.py")], env=env,
                                       stdout=subprocess.DEVNULL, stderr=self.stderr)
        for _ in range(100):
            try:
                self.http("/health")
                break
            except OSError:
                time.sleep(0.1)
        with open(os.path.join(self.data, "token")) as fh:
            self.token = fh.read().strip()
        self.ws = self.http(f"/api/create?token={self.token}&name=sizing&kind=terminal")["id"]

    def http(self, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=10) as r:
            body = r.read().decode()
            return json.loads(body) if r.headers.get("Content-Type", "").startswith("application/json") else body

    def window(self):
        """The tab's tmux window size, as a TUI in it sees it."""
        return subprocess.run([TMUX, "-L", self.sock, "display", "-p", "-t", f"=omo-{self.ws}:",
                               "#{window_width}x#{window_height}"], capture_output=True, text=True).stdout.strip()

    def close(self):
        self.bridge.terminate()
        try:
            self.bridge.wait(5)
        except subprocess.TimeoutExpired:
            self.bridge.kill()
        self.stderr.close()
        subprocess.run([TMUX, "-L", self.sock, "kill-server"], capture_output=True)
        shutil.rmtree(self.dir, ignore_errors=True)


class Station:
    """station.lua running on its own thread, fed CC events from Python.

    Lua objects are only ever made on the Lua thread (lupa deadlocks
    otherwise): other threads queue plain data and next_event converts it."""

    def __init__(self, stack, wall=True):
        self.stack = stack
        self.events = queue.Queue()
        self.timers = []
        self.timer_id = 0
        self.stop = False
        self.sockets = {}
        self.sent = []                  # (surface name, message) the program sent the bridge
        self.error = None
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        g = self.lua.globals()
        g.COMPUTER_W, g.COMPUTER_H, g.WALL_ON = COMPUTER_W, COMPUTER_H, wall
        g.py = self.lua.table_from({
            "next_event": self._next_event, "queue_event": self.queue, "start_timer": self._start_timer,
            "ws_async": self._websocket_async, "unserialise": lambda text: self._to_lua(json.loads(text)),
            "ticket": self._ticket, "clock": time.monotonic, "epoch": lambda *a: int(time.time() * 1000)})
        self.lua.execute(CC_STUBS)
        with open(STATION_LUA) as fh:
            self.program = self.lua.eval("function(src) return load(src, '@station.lua', 't', _G) end")(fh.read())
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    # -- CC stubs (Lua thread) -------------------------------------------
    def queue(self, *args):
        self.events.put(list(args))

    def _start_timer(self, delay):
        self.timer_id += 1
        heapq.heappush(self.timers, (time.monotonic() + float(delay), self.timer_id))
        return self.timer_id

    def _to_lua(self, obj):
        if isinstance(obj, tuple) and obj[0] == "__sock__":
            send, close = self.sockets[obj[1]]
            return self.lua.table_from({"send": send, "close": close})
        if isinstance(obj, dict):
            return self.lua.table_from({k: self._to_lua(v) for k, v in obj.items() if v is not None})
        if isinstance(obj, list):
            return self.lua.table_from([self._to_lua(v) for v in obj])
        return obj

    def _next_event(self):
        while True:
            if self.stop:
                raise SystemExit("stop")
            now = time.monotonic()
            if self.timers and self.timers[0][0] <= now:
                _, tid = heapq.heappop(self.timers)
                return self.lua.table_from(["timer", tid])
            wait = min(0.05, self.timers[0][0] - now) if self.timers else 0.05
            try:
                ev = self.events.get(timeout=max(0.001, wait))
            except queue.Empty:
                continue
            return self.lua.table_from([self._to_lua(x) for x in ev])

    def _ticket(self):
        st = self.stack
        return st.http(f"/ticket?token={st.token}&station=st-sizing&ws={st.ws}"), st.ws

    def _websocket_async(self, url):
        from websockets.sync.client import connect
        name = "monitor" if "monitor" in url else "computer"

        def run():
            try:
                conn = connect(f"ws://127.0.0.1:{self.stack.port}/ws", max_size=None)
            except Exception:
                self.queue("websocket_failure", url, "no bridge")
                return

            def send(text):
                self.sent.append((name, json.loads(text)))
                conn.send(text)

            def close():
                try:
                    conn.close()
                except Exception:
                    pass
            self.sockets[url] = (send, close)
            self.queue("websocket_success", url, ("__sock__", url))
            try:
                for msg in conn:
                    self.queue("websocket_message", url, msg)
            except Exception:
                pass
            self.queue("websocket_closed", url)
        threading.Thread(target=run, daemon=True).start()
        return True

    def _run(self):
        try:
            ok, *rest = self.lua.eval("function(f) return xpcall(f, debug.traceback) end")(self.program)
            if not ok and not self.stop:
                self.error = str(rest)
        except BaseException as exc:            # stop() ends it through SystemExit
            if not self.stop:
                self.error = repr(exc)

    # -- test side ---------------------------------------------------------
    def pty_size(self, surface):
        """The size a surface asked the bridge for (cols x rows)."""
        sizes = [m for n, m in self.sent if n == surface and m.get("t") in ("size", "attach")]
        return f"{sizes[-1]['cols']}x{sizes[-1]['rows']}" if sizes else None

    def close(self):
        self.stop = True
        self.queue("noop")
        self.thread.join(3)


@unittest.skipIf(LuaRuntime is None, "needs lupa (pip install lupa) to run station.lua")
@unittest.skipIf(TMUX is None, "needs tmux")
class StationSizing(unittest.TestCase):

    def setUp(self):
        self.stack = Stack()
        self.addCleanup(self.stack.close)

    def station(self, wall=True):
        st = Station(self.stack, wall)
        self.addCleanup(st.close)
        return st

    def settle(self, st, want, timeout=8.0):
        """Wait until the tab's window is `want` (a surface name); return what it is."""
        deadline = time.monotonic() + timeout
        got = None
        while time.monotonic() < deadline:
            self.assertIsNone(st.error, st.error)
            target = st.pty_size(want)
            got = self.stack.window()
            if target and got == target:
                time.sleep(0.6)                 # and it stays there (no late reclaim by the other view)
                got = self.stack.window()
                if got == target:
                    return got
            time.sleep(0.1)
        return got

    def test_wall_sizes_the_tab_while_no_gui_is_open(self):
        st = self.station(wall=True)
        wall = self.settle(st, "monitor")
        self.assertEqual(wall, st.pty_size("monitor"),
                         f"with only the wall in view the TUI should fill it; computer is {st.pty_size('computer')}")
        self.assertNotEqual(st.pty_size("monitor"), st.pty_size("computer"))

        st.queue("omo_gui", True)
        st.queue("omo_focus", "computer")       # what the mod sends when a player opens the computer
        self.assertEqual(self.settle(st, "computer"), st.pty_size("computer"), "GUI open: the computer sizes it")

        st.queue("omo_gui", False)
        self.assertEqual(self.settle(st, "monitor"), st.pty_size("monitor"), "GUI closed: back to the wall")

        st.queue("omo_focus", "monitor")        # a player steers the wall remotely ...
        st.queue("omo_release")                 # ... and lets go
        self.assertEqual(self.settle(st, "monitor"), st.pty_size("monitor"),
                         "letting go of the wall leaves it sized for the player looking at it")

    def test_computer_alone_sizes_the_tab(self):
        st = self.station(wall=False)
        self.assertEqual(self.settle(st, "computer"), f"{COMPUTER_W}x{COMPUTER_H - 1}")
        st.queue("omo_gui", True)
        st.queue("omo_gui", False)
        self.assertEqual(self.settle(st, "computer"), f"{COMPUTER_W}x{COMPUTER_H - 1}",
                         "with no wall the computer keeps the size whether or not its GUI is open")


if __name__ == "__main__":
    unittest.main(verbosity=2)
