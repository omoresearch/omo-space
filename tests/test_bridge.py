#!/usr/bin/env python3
"""Bridge integration tests: a real bridge on a disposable tmux server.

Nothing here touches the live bridge, its token or its tmux socket. A stub
`hermes` on the test server's PATH records its arguments, so no inference is
spent and Hermes wiring (profiles, titles, autorun) is checked exactly.

    python3 tests/test_bridge.py          # prints PASS/FAIL per check, exit 1 on failure
"""
import asyncio
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import types
import urllib.error
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "bridge", "vendor"))
sys.path.insert(0, os.path.join(REPO, "bridge"))
import termcore  # noqa: E402
import websockets  # noqa: E402

TMUX = os.environ.get("OMO_TMUX") or os.path.expanduser("~/.local/share/omo/bin/tmux")
if not os.path.exists(TMUX):
    TMUX = shutil.which("tmux")
RESULTS = []


def check(name, ok, evidence=""):
    RESULTS.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  -- {evidence}" if evidence and not ok else ""), flush=True)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Env:
    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix="omo-bridge-test-")
        self.data = os.path.join(self.dir, "data")
        self.stub = os.path.join(self.dir, "stub")
        self.hermes_root = os.path.join(self.dir, "hermes")
        os.makedirs(self.data)
        os.makedirs(self.stub)
        os.makedirs(os.path.join(self.hermes_root, "profiles", "omo-dev"))
        self.calls = os.path.join(self.dir, "hermes-calls")
        # A stand-in for the Hermes TUI: its status bar and input line, a
        # clean /exit, "work" to look busy, "bg"/"fg" to own a child process.
        with open(os.path.join(self.stub, "hermes"), "w") as fh:
            fh.write(f'#!/bin/sh\necho "$*" >> {self.calls}\necho "stub hermes $*"\n'
                     'while :; do\n'
                     '  printf " ─ ready │ stub │ 0s\\n ❯ "\n'
                     '  IFS= read -r line || exit 0\n'
                     '  case "$line" in\n'
                     '    /exit) echo "stub bye"; exit 0 ;;\n'
                     '    work) printf " ─ (o_o) thinking… │ stub\\n"; sleep 3600 ;;\n'
                     '    bg) sleep 3600 & BG=$! ;;\n'
                     '    fg) kill $BG ;;\n'
                     '  esac\n'
                     'done\n')
        os.chmod(os.path.join(self.stub, "hermes"), 0o755)
        # A stand-in for Claude Code: records its arguments and, like the real
        # one, keeps a session file per --session-id so a resume can be told
        # from a fresh start. Codex is deliberately missing (OMO_CODEX_BIN
        # points nowhere): its tab must say so and stay a usable shell.
        self.claude_calls = os.path.join(self.dir, "claude-calls")
        self.claude_home = os.path.join(self.dir, "claude-home")
        with open(os.path.join(self.stub, "claude"), "w") as fh:
            fh.write(f'#!/bin/sh\necho "$*" >> {self.claude_calls}\necho "stub claude $* in $PWD"\n'
                     'if [ "$1" = "--session-id" ]; then\n'
                     '  mkdir -p "$CLAUDE_CONFIG_DIR/projects/-stub" && : > "$CLAUDE_CONFIG_DIR/projects/-stub/$2.jsonl"\n'
                     'fi\n'
                     'while :; do\n'
                     '  printf " > "\n'
                     '  IFS= read -r line || exit 0\n'
                     '  case "$line" in /exit) echo "stub claude bye"; exit 0 ;; esac\n'
                     'done\n')
        os.chmod(os.path.join(self.stub, "claude"), 0o755)
        self.opened = os.path.join(self.dir, "opened")
        with open(os.path.join(self.stub, "open-url"), "w") as fh:
            fh.write(f'#!/bin/sh\necho "$1" >> {self.opened}\n')
        os.chmod(os.path.join(self.stub, "open-url"), 0o755)
        self.sock = f"omotest{os.getpid()}"
        self.port = free_port()
        self.bridge = None
        self.xvfb, self.display = None, None

    def start_display(self):
        """A private X server for the clipboard, so no test touches the
        desktop's. Without Xvfb and xclip the clipboard checks are skipped."""
        if not (shutil.which("Xvfb") and shutil.which("xclip")):
            return False
        n = 300 + os.getpid() % 600
        while os.path.exists(f"/tmp/.X11-unix/X{n}"):
            n += 1
        self.display = f":{n}"
        self.xvfb = subprocess.Popen(["Xvfb", self.display, "-nolisten", "tcp"], stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
        for _ in range(50):
            if subprocess.run(["xdpyinfo", "-display", self.display], capture_output=True).returncode == 0:
                return True
            time.sleep(0.1)
        return False

    def clip(self, text=None, want=None):
        """Read the private display's clipboard (waiting a moment for `want`:
        xclip takes the selection in the background), or set it."""
        cmd = ["xclip", "-display", self.display, "-selection", "clipboard"]
        if text is not None:
            p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True)
            p.communicate(text.encode(), timeout=5)
            want = text
        for _ in range(20):
            got = subprocess.run(cmd + ["-o"], capture_output=True, text=True, timeout=5).stdout
            if want is None or got == want:
                break
            time.sleep(0.1)
        return got

    def tmux(self, *args):
        return subprocess.run([TMUX, "-L", self.sock, *args], capture_output=True, text=True)

    def start_server(self):
        env = {**os.environ, "PATH": f"{self.stub}:{os.environ['PATH']}", "CLAUDE_CONFIG_DIR": self.claude_home}
        subprocess.run([TMUX, "-L", self.sock, "-f", os.path.join(REPO, "config", "omo-tmux.conf"),
                        "start-server"], env=env, check=True)

    def start_bridge(self):
        env = {**os.environ, "OMO_DATA": self.data, "OMO_PORT": str(self.port), "OMO_TMUX": TMUX,
               "OMO_TMUX_SOCKET": self.sock, "OMO_HERMES_ROOT": self.hermes_root,
               "OMO_HERMES_BIN": os.path.join(self.stub, "hermes"), "OMO_OPEN": os.path.join(self.stub, "open-url"),
               "OMO_CLAUDE_BIN": os.path.join(self.stub, "claude"),
               "OMO_CODEX_BIN": os.path.join(self.dir, "no-such-dir", "codex"),
               # Sleep after 2s idle, scanned only when a test asks (/api/sleep_scan).
               "OMO_SLEEP_AFTER": "2", "OMO_SLEEP_SCAN": "3600",
               # Never the desktop's clipboard: the private display, or one that does not exist.
               "OMO_DISPLAY": self.display or ":65000", "DISPLAY": self.display or ":65000"}
        env.pop("WAYLAND_DISPLAY", None)
        self.bridge = subprocess.Popen([sys.executable, os.path.join(REPO, "bridge", "omobridge.py")], env=env,
                                       stdout=subprocess.DEVNULL, stderr=open(os.path.join(self.dir, "stderr"), "w"))
        for _ in range(50):
            try:
                self.http("/health")
                return
            except OSError:
                time.sleep(0.1)
        raise SystemExit("bridge did not start: " + open(os.path.join(self.dir, "stderr")).read())

    def stop_bridge(self):
        if self.bridge:
            self.bridge.terminate()
            self.bridge.wait(5)

    @property
    def token(self):
        return open(os.path.join(self.data, "token")).read().strip()

    def http(self, path, host=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}")
        if host:
            req.add_header("Host", host)
        with urllib.request.urlopen(req, timeout=10) as r:
            body = r.read().decode()
            return json.loads(body) if r.headers.get("Content-Type", "").startswith("application/json") else body

    def cleanup(self):
        self.stop_bridge()
        self.tmux("kill-server")
        if self.xvfb:
            self.xvfb.terminate()
        shutil.rmtree(self.dir, ignore_errors=True)


class Client:
    def __init__(self, env):
        self.env, self.inbox = env, []
        self.runs, self.frame = {}, {}      # the painted screen: row -> runs; the last frame

    def line(self, y):
        return "".join(r[1] for r in self.runs.get(y, []))

    def row_of(self, text):
        """1-based row showing text (the lowest one), or None."""
        return next((y for y in sorted(self.runs, reverse=True) if text in self.line(y)), None)

    def run_of(self, text):
        """The [count, text, fg, bg] run holding text, or None."""
        return next((r for y in sorted(self.runs, reverse=True) for r in self.runs[y] if text in r[1]), None)

    def _apply(self, m):
        if m.get("full"):
            self.runs = {}
        for y, runs in m["rows_changed"]:
            self.runs[y] = runs
        self.frame = m

    async def open(self, **hello):
        self.ws = await websockets.connect(f"ws://127.0.0.1:{self.env.port}/ws")
        await self.send(t="hello", **hello)
        return await self.expect(lambda m: m["t"] in ("welcome", "error"))

    async def send(self, **msg):
        await self.ws.send(json.dumps(msg))

    async def expect(self, pred, timeout=8):
        end = time.time() + timeout
        while time.time() < end:
            for m in self.inbox:
                if pred(m):
                    self.inbox.remove(m)
                    return m
            try:
                raw = await asyncio.wait_for(self.ws.recv(), max(0.05, end - time.time()))
                m = json.loads(raw)
                if m["t"] == "frame":
                    self._apply(m)
                self.inbox.append(m)
            except (asyncio.TimeoutError, TimeoutError):
                break
            except websockets.ConnectionClosed:
                return None
        return None

    async def screen_has(self, text, timeout=8):
        """Wait for a new frame that shows text."""
        end = time.time() + timeout
        while time.time() < end:
            m = await self.expect(lambda m: m["t"] in ("frame", "detached"), end - time.time())
            if not m:
                break
            if m["t"] == "detached":
                return m
            if self.row_of(text):
                return True
        return False

    async def settle(self, seconds=0.6):
        """Take in every frame for a moment (the screen is then current)."""
        await self.expect(lambda m: False, seconds)
        self.inbox = [m for m in self.inbox if m["t"] != "frame"]

    async def close(self):
        await self.ws.close()


def api(env, op, **q):
    qs = "&".join(f"{k}={urllib.request.quote(str(v))}" for k, v in {"token": env.token, **q}.items())
    return env.http(f"/api/{op}?{qs}")


def units():
    """Pure rendering and text helpers (no bridge)."""
    fold = termcore.fold_char
    check("CC glyphs: arrows and bullets use the font's own, Latin-1 stays, blocks invert",
          (fold("→"), fold("•"), fold("é"), fold("█"), fold("─")) ==
          (("\x1a", False), ("\x07", False), ("é", False), ("\x80", True), ("-", False)))
    check("truecolour maps onto the Omo palette", termcore.nearest("6fd08c") == "d" and termcore.nearest("dda744") == "1")
    check("a wrapped link opens whole",
          termcore.url_at(["see https://exa", "mple.com/x.    "], 2, 1, 15) == "https://example.com/x")
    check("double-click grows to the word, paths included",
          termcore.expand(["cat /tmp/a-b.txt now"], (7, 0, 7, 0), "word", 20) == (4, 0, 15, 0))
    check("a selection over a wrapped line copies it as one line",
          termcore.selection_text(["a" * 10, "bb        "], (0, 0, 9, 1), 10) == "a" * 10 + "bb")
    got = []
    fake = types.SimpleNamespace(_osc="", on_clipboard=got.append)
    out = termcore.PtyScreen._take_osc52(fake, "ab\x1b]5") + termcore.PtyScreen._take_osc52(fake, "2;c;aGk=\x07cd")
    termcore.PtyScreen._take_osc52(fake, "\x1b]52;c;?\x07")
    check("OSC 52 split across reads is cut out and copied; a query is ignored", out == "abcd" and got == ["hi"],
          f"{out!r} {got}")


async def clipboard(env, ui, y):
    """Copy, paste and OSC 52 against the private display's clipboard."""
    async def copied(want):
        await ui.send(t="copy")
        await ui.expect(lambda m: m["t"] == "toast", 3)
        got = env.clip(want=want)
        return got == want, repr(got)

    check("copy puts the selection on the host clipboard", *await copied("beta-gamma.txt"))
    await ui.send(t="sel", x1=3, y1=y, x2=3, y2=y, unit="line")
    check("a triple-click copies the whole line", *await copied("alpha beta-gamma.txt delta"))
    await ui.send(t="in", d="clear; printf 'W%.0s' $(seq 1 100); echo\r")
    await ui.screen_has("W" * 40)
    await ui.settle()
    y = ui.row_of("W" * 40) - 1
    await ui.send(t="sel", x1=0, y1=y - 1, x2=0, y2=y, unit="line")
    check("a line wrapped on screen copies as one line", *await copied("W" * 100))

    # A TUI redraws on its own (Hermes streaming): the highlight must not sit
    # on new text, and a later copy takes what was highlighted.
    await ui.send(t="in", d="clear; echo keep-this-$((3*3)); sleep 1.5; clear; echo other-text-$((2*2))\r")
    await ui.screen_has("keep-this-9")
    await ui.settle(0.3)
    y = ui.row_of("keep-this-9") - 1
    await ui.send(t="sel", x1=0, y1=y, x2=10, y2=y, unit="char")
    await ui.screen_has("other-text-4")
    await ui.settle(0.6)
    moved = ui.run_of("other-text-4")
    check("a redraw under a selection drops the highlight", moved and moved[3] != "3", str(moved))
    check("a copy after the redraw takes the text that was highlighted", *await copied("keep-this-9"))

    env.clip("echo pasted-$((40+2))\necho second-$((1+1))")
    await ui.send(t="in", d="clear\r")
    await ui.settle()
    await ui.send(t="paste")
    await ui.settle(1.0)
    held = not ui.row_of("pasted-42") and ui.row_of("second-$((1+1))")
    shown = [ui.line(y).rstrip() for y in sorted(ui.runs) if ui.line(y).strip()]
    await ui.send(t="in", d="\r")
    await ui.screen_has("second-2")
    check("a multi-line paste arrives as one bracketed block and runs only on Enter",
          held and ui.row_of("pasted-42"), f"held={held} {shown}")
    env.clip("echo full-$((2+3))\necho more-$((3+3))")
    await ui.send(t="paste", d="echo full-$((2+3))")
    await ui.settle(0.6)
    await ui.send(t="in", d="\r")
    check("CC's cut-down Ctrl+V pastes the whole host clipboard", await ui.screen_has("more-6"))
    await ui.send(t="paste", d="echo given-$((1+6))")
    await ui.settle(0.6)
    await ui.send(t="in", d="\r")
    check("a paste the host clipboard does not match is typed as given", await ui.screen_has("given-7"))

    await ui.send(t="in", d="printf '\\e]52;c;%s\\a' $(printf osc-copy-$((6*7)) | base64)\r")
    toast = await ui.expect(lambda m: m["t"] == "toast" and "copied by the app" in m["m"], 5)
    got = env.clip(want="osc-copy-42")
    await ui.send(t="in", d="printf '\\e]52;c;?\\a'; echo after-query\r")
    await ui.screen_has("after-query")
    await ui.settle()
    check("an app's OSC 52 copy reaches the host clipboard; a clipboard query is not answered",
          toast and got == "osc-copy-42" and not ui.row_of("b3NjLWNvcHktNDI"), f"{toast} {got!r}")


async def terminal_abilities(env, ui):
    """Colours, glyphs, selection and copy, paste, OSC 52, bell, find, wheel,
    links and clearing history, on a 60x12 view of a plain shell."""
    await ui.send(t="in", d="clear; printf '\\e[31mKR\\e[0m \\e[1;31mKB\\e[0m \\e[38;2;111;208;140mKT\\e[0m "
                             "\\e[7mKV\\e[0m \\e[1mKW\\e[0m \\342\\206\\222 \\n'\r")
    await ui.screen_has("KW")
    await ui.settle()
    runs = {k: (ui.run_of(k) or [0, "", "", ""])[2:] for k in ("KR", "KB", "KT", "KV", "KW")}
    check("ANSI colours land on the palette: red, bold = bright, truecolour, reverse, bold default",
          runs == {"KR": ["e", "f"], "KB": ["6", "f"], "KT": ["d", "f"], "KV": ["f", "8"], "KW": ["0", "f"]},
          str(runs))
    check("an arrow is drawn with CC's arrow glyph", "\x1a" in ui.line(ui.row_of("KR")), repr(ui.line(ui.row_of("KR"))))

    await ui.send(t="in", d="printf '\\a'\r")
    check("a bell reaches the screen", await ui.expect(lambda m: m["t"] == "bell", 4))

    await ui.send(t="in", d="clear; echo alpha beta-gamma.txt delta\r")
    await ui.screen_has("alpha beta")
    await ui.settle()
    y = ui.row_of("alpha beta") - 1
    await ui.send(t="sel", x1=8, y1=y, x2=8, y2=y, unit="word")
    await ui.settle(0.4)
    marked = ui.run_of("beta-gamma.txt")
    check("a double-click selects the word under it and highlights it", marked and marked[3] == "3", str(marked))
    if env.display:
        await clipboard(env, ui, y)
    else:
        print("SKIP clipboard checks  -- no Xvfb or xclip for a private display")

    await ui.send(t="in", d="clear; echo nee''dle-early; seq 1 200; echo nee''dle-late\r")
    await ui.screen_has("needle-late")
    await ui.send(t="find", q="needle", step=0)
    first = await ui.expect(lambda m: m["t"] == "found", 5)
    await ui.send(t="find", q="needle", step=1)
    older = await ui.expect(lambda m: m["t"] == "found", 5)
    await ui.settle(1.0)
    cur = ui.run_of("needle")
    check("find searches the whole history: newest first, then older, scrolled into view and marked",
          first and (first["i"], first["n"]) == (1, 2) and older and older["i"] == 2 and ui.frame["scroll"] > 0
          and cur and cur[1] == "needle" and cur[3] == "5" and ui.row_of("needle-early"),
          f"{first} {older} scroll={ui.frame.get('scroll')} run={cur}")
    await ui.send(t="find", q="needle", step=1)
    wrapped = await ui.expect(lambda m: m["t"] == "found", 5)
    await ui.send(t="find", q="")
    await ui.expect(lambda m: m["t"] == "found", 5)
    await ui.send(t="scroll", by=-100000)
    await ui.settle()
    check("find wraps around, and closing it leaves no marks", wrapped and wrapped["i"] == 1 and ui.frame["scroll"] == 0
          and ui.run_of("needle-late")[3] == "f", str(wrapped))

    await ui.send(t="wheel", by=1, x=5, y=5)
    await ui.settle()
    up = ui.frame["scroll"]
    await ui.send(t="wheel", by=-1, x=5, y=5)
    await ui.settle()
    check("the wheel scrolls history three lines a notch when the app has no mouse",
          up == 3 and ui.frame["scroll"] == 0, f"up={up} back={ui.frame['scroll']}")
    mouse_app = os.path.join(env.dir, "mouse.py")
    with open(mouse_app, "w") as fh:
        fh.write("import os, sys, tty\ntty.setraw(0)\nsys.stdout.write('\\x1b[?1000h\\x1b[?1006hMREADY\\r\\n')\n"
                 "sys.stdout.flush()\nd = os.read(0, 64)\n"
                 "sys.stdout.write('\\x1b[?1000l\\x1b[?1006lGOT ' + repr(d) + '\\r\\n')\nsys.stdout.flush()\n")
    await ui.send(t="in", d=f"clear; python3 {mouse_app}; stty sane\r")
    await ui.screen_has("MREADY")
    await ui.settle()
    await ui.send(t="wheel", by=1, x=4, y=2)
    check("the wheel goes to an app that asked for the mouse", await ui.screen_has("<64;5;3M"))

    await ui.send(t="in", d="clear; echo see https://example.com/omo?q=1.\r")
    await ui.screen_has("see https")
    await ui.settle()
    await ui.send(t="open", x=10, y=ui.row_of("see https") - 1)
    opened = await ui.expect(lambda m: m["t"] == "toast", 4)
    await ui.send(t="in", d=f"clear; echo http://127.0.0.1:9/x?t={env.token}\r")
    await ui.screen_has("http://127")
    await ui.settle()
    await ui.send(t="open", x=10, y=ui.row_of("http://127") - 1)
    refused = await ui.expect(lambda m: m["t"] == "toast", 4)
    await asyncio.sleep(0.5)
    urls = open(env.opened).read().split() if os.path.exists(env.opened) else []
    check("a link opens in the host browser; one carrying the bridge token never does",
          urls == ["https://example.com/omo?q=1"] and "opening" in opened["m"] and "not opened" in refused["m"],
          f"{urls} {opened} {refused}")

    await ui.send(t="in", d="seq 1 50\r")
    await ui.settle(1.2)
    had = ui.frame["max_scroll"]
    await ui.send(t="clear")
    await ui.expect(lambda m: m["t"] == "toast" and "cleared" in m["m"], 4)
    await ui.settle(1.0)
    check("Clear scrollback empties the history", had > 0 and ui.frame["max_scroll"] == 0,
          f"{had} -> {ui.frame['max_scroll']}")


async def two_surfaces(env, ui, term):
    """CONTRACT B: a computer and its wall are two views of one tab, each
    painting at its own size; the surface in use sizes the window."""
    def window():
        return env.tmux("display", "-p", "-t", f"=omo-{term['id']}:", "#{window_width}x#{window_height}").stdout.strip()

    views = []
    for cols, rows in ((80, 28), (100, 40)):
        c = Client(env)
        await c.open(ticket=env.http(f"/ticket?token={env.token}&station=st-2&ws={term['id']}"), station="st-2",
                     ws=term["id"])
        await c.send(t="attach", ws=term["id"], tab="", cols=cols, rows=rows)
        await c.expect(lambda m: m["t"] == "attached")
        views.append(c)
    computer, wall = views
    await asyncio.sleep(0.8)
    after_wall = window()
    await computer.send(t="focus")
    await asyncio.sleep(0.6)
    after_focus = window()
    await wall.send(t="focus")
    await asyncio.sleep(0.6)
    wall_focus = window()
    await computer.send(t="in", d="echo typed-on-the-computer\r")
    await asyncio.sleep(0.6)
    after_key = window()
    await computer.settle()
    await wall.settle()
    check("two views of one tab paint at their own sizes; focus and keys decide the window size",
          (after_wall, after_focus, wall_focus, after_key) == ("100x40", "80x28", "100x40", "80x28")
          and (computer.frame["cols"], computer.frame["rows"], wall.frame["cols"], wall.frame["rows"]) == (80, 28, 100, 40),
          f"{after_wall} {after_focus} {wall_focus} {after_key}")
    await wall.send(t="size", cols=60, rows=20)
    await asyncio.sleep(0.6)
    check("a wall that grows or shrinks refits live", window() == "60x20", window())
    await ui.send(t="tab_new", ws=term["id"], kind="shell", name="Other")
    other = (await ui.expect(lambda m: m["t"] == "tab_created"))["tab"]
    await ui.send(t="steer", station="st-2", ws=term["id"], tab=other)
    moved = [await c.expect(lambda m: m["t"] == "attached" and m["tab"] == other, 6) for c in views]
    check("a tab switch from the panel moves both views", all(moved), str(moved))
    for c in views:
        await c.close()
    await ui.send(t="tab_close", ws=term["id"], tab=other, force=True)
    await ui.expect(lambda m: m["t"] == "workspace")
    await ui.send(t="focus")
    await ui.settle()
    ui.inbox.clear()


async def far_screen_parks(env, term):
    """A screen no player is near keeps its last picture with no view open
    ("pause" parks it); "resume" brings the same tab back live."""
    def views():
        return [n for n in env.tmux("list-sessions", "-F", "#{session_name}").stdout.split()
                if n.startswith(f"omo-{term['id']}-v")]
    c = Client(env)
    await c.open(ticket=env.http(f"/ticket?token={env.token}&station=st-far&ws={term['id']}"), station="st-far",
                 ws=term["id"])
    before = len(views())
    await c.send(t="attach", ws=term["id"], tab="", cols=50, rows=10)
    await c.expect(lambda m: m["t"] == "attached")
    await c.send(t="pause")
    await asyncio.sleep(3.0)
    parked = len(views())
    detached = any(m.get("t") == "detached" for m in c.inbox)
    check("a paused screen keeps its frame and closes its view", parked == before and not detached
          and c.frame is not None, f"views {before}->{parked} detached={detached}")
    c.inbox.clear()
    await c.send(t="resume")
    back = await c.expect(lambda m: m["t"] == "attached", 6)
    await c.send(t="in", d="echo resumed-$((2*21))\r")
    check("resume re-attaches the same tab live", bool(back) and len(views()) == before + 1
          and await c.screen_has("resumed-42"), f"{back} views={len(views())}")
    await c.close()


async def auto_sleep(env):
    """Idle, unwatched Hermes tabs sleep with a clean /exit and wake, on the
    same conversation, when a screen opens them. Everything that means
    "someone may still need this" keeps a tab awake."""
    ui = Client(env)
    await ui.open(token=env.token, role="ui")
    ws = api(env, "create", name="Sleepy", kind="hermes")
    tab = ws["tabs"][0]["id"]
    title = f"-c omo:{ws['id']}:{tab} "
    await asyncio.sleep(1.5)

    async def two_scans():
        # Other idle Hermes tabs from earlier checks may sleep too: count only this one.
        try:
            api(env, "sleep_scan")          # first look: remembers the screen
            await asyncio.sleep(2.5)        # longer than OMO_SLEEP_AFTER
            return [k for k in api(env, "sleep_scan")["slept"] if k.startswith(ws["id"] + "/")]
        except OSError as exc:              # a /exit that never lands holds the scan past the HTTP timeout
            return [f"scan failed: {exc}"]

    def state():
        t = api(env, "get", ws=ws["id"])["tabs"][0]
        return t["command"], t["sleeping"]

    await ui.send(t="attach", ws=ws["id"], tab=tab, cols=60, rows=12)
    await ui.expect(lambda m: m["t"] == "attached")
    s = await two_scans()
    check("a tab a screen shows never sleeps", s == [] and state()[0] == "hermes", f"{s} {state()}")

    await ui.send(t="in", d="half a thought")
    await asyncio.sleep(0.4)
    await ui.send(t="detach")
    s = await two_scans()
    pane = env.tmux("capture-pane", "-p", "-t", f"=omo-{ws['id']}:").stdout
    # Sleeping types /exit: onto a draft it would send "half a thought/exit" to the agent.
    check("a tab with an unsent draft never sleeps, and nothing is typed into it",
          s == [] and state()[0] == "hermes" and "/exit" not in pane, f"{s} {pane.strip()[-120:]!r}")

    await ui.send(t="attach", ws=ws["id"], tab=tab, cols=60, rows=12)
    await ui.expect(lambda m: m["t"] == "attached")
    await ui.send(t="in", d="\r")
    await ui.send(t="in", d="bg\r")
    await asyncio.sleep(0.5)
    await ui.send(t="detach")
    check("a tab whose Hermes runs a process of its own never sleeps",
          await two_scans() == [] and state()[0] == "hermes")
    await ui.send(t="attach", ws=ws["id"], tab=tab, cols=60, rows=12)
    await ui.expect(lambda m: m["t"] == "attached")
    await ui.send(t="in", d="fg\r")
    await asyncio.sleep(0.5)
    await ui.send(t="detach")

    api(env, "keep_awake", ws=ws["id"], on="1")
    check("Keep awake is kept and reported", api(env, "get", ws=ws["id"])["keep_awake"] is True)
    check("a Keep-awake workspace never sleeps", await two_scans() == [] and state()[0] == "hermes")
    api(env, "keep_awake", ws=ws["id"], on="0")

    calls_before = open(env.calls).read().count(title)
    slept = await two_scans()
    check("an idle, unwatched Hermes tab sleeps", slept == [f"{ws['id']}/{tab}"], str(slept))
    pane = env.tmux("capture-pane", "-p", "-t", f"=omo-{ws['id']}:").stdout
    check("sleep is a clean /exit (Hermes saves its conversation)", "stub bye" in pane, pane.strip()[-200:])
    check("a sleeping tab reads 'sleeping', its shell still live", state() == ("bash", True), str(state()))
    board = env.http("/board.json")
    check("the board shows the tab sleeping",
          any(t["sleeping"] for w in board["workspaces"] if w["name"] == "Sleepy" for t in w["tabs"]))

    await ui.send(t="attach", ws=ws["id"], tab=tab, cols=60, rows=12)
    toast = await ui.expect(lambda m: m["t"] == "toast" and "waking" in m["m"])
    att = await ui.expect(lambda m: m["t"] == "attached")
    check("opening a screen on it wakes it at once", bool(toast) and att and att["state"] == "live", str(att))
    await asyncio.sleep(1.5)
    calls_after = open(env.calls).read().count(title)
    check("waking resumes the same conversation", calls_after == calls_before + 1, f"{calls_before}->{calls_after}")
    check("a woken tab reads awake and running Hermes", state() == ("hermes", False), str(state()))
    await ui.send(t="detach")
    check("a fresh wake does not sleep straight away",
          not [k for k in api(env, "sleep_scan")["slept"] if k.startswith(ws["id"] + "/")])
    await ui.close()
    api(env, "delete", ws=ws["id"], force=1)


async def agent_tabs(env, welcome):
    """The "+" menu's other agents: Claude Code and Codex tabs. Each runs its
    CLI in the tab's shell (so quitting it leaves a prompt), Claude keeps one
    session per tab across revives and restores, and a CLI that is not
    installed is said so in the tab, never a crash or a dead tab."""
    agents = welcome.get("agents") or {}
    check("the welcome says which agent CLIs this PC has",
          agents.get("claude", {}).get("found") is True and agents.get("codex", {}).get("found") is False
          and agents.get("hermes", {}).get("found") is True, str(agents))
    check("the welcome lists the tab kinds this bridge can open",
          welcome.get("tab_kinds") == ["shell", "hermes", "claude", "codex"], str(welcome.get("tab_kinds")))
    check("a missing CLI comes with an install hint",
          "npm" in agents.get("codex", {}).get("install", ""), str(agents.get("codex")))
    ui = Client(env)
    await ui.open(token=env.token, role="ui")
    await ui.send(t="agents")
    fresh = await ui.expect(lambda m: m["t"] == "agents")
    check("the agents op re-checks on demand", fresh and fresh["agents"]["claude"]["found"] is True, str(fresh))

    ws = api(env, "create", name="Agents", kind="terminal")
    await ui.send(t="tab_new", ws=ws["id"], kind="claude")
    claude = (await ui.expect(lambda m: m["t"] == "tab_created"))["tab"]
    await ui.send(t="attach", ws=ws["id"], tab=claude, cols=70, rows=12)
    await ui.expect(lambda m: m["t"] == "attached")
    check("a Claude tab starts Claude Code", await ui.screen_has("stub claude --session-id"))
    d = api(env, "get", ws=ws["id"])
    tab = next(t for t in d["tabs"] if t["id"] == claude)
    check("a Claude tab is named and kinded for its agent", (tab["name"], tab["kind"]) == ("Claude Code", "claude"),
          str(tab))
    check("running Claude reads as busy, by its own name", (tab["command"], tab["busy"]) == ("claude", True), str(tab))
    calls = open(env.claude_calls).read().split()
    session = calls[1] if len(calls) > 1 else ""
    check("the Claude session id is a stable UUID per tab",
          len(session) == 36 and session.count("-") == 4, str(calls))
    await ui.send(t="in", d="/exit\r")
    check("quitting Claude leaves a shell prompt in the tab", await ui.screen_has("stub claude bye"))
    await ui.send(t="in", d='echo "agent=[$OMO_TAB_KIND] auto=[$OMO_AUTORUN]"\r')
    check("the tab knows its kind; the autorun ran once", await ui.screen_has("agent=[claude] auto=[]"))

    # Revive (the same path a restore after a session-server restart takes):
    # Claude resumes the tab's own session instead of starting another.
    await ui.send(t="detach")
    win = next(t["window"] for t in api(env, "get", ws=ws["id"])["tabs"] if t["id"] == claude)
    env.tmux("kill-window", "-t", f"=omo-{ws['id']}:{win}")
    await asyncio.sleep(0.5)
    await ui.send(t="tab_revive", ws=ws["id"], tab=claude, mode="claude")
    await ui.expect(lambda m: m["t"] == "workspace")
    await asyncio.sleep(1.5)
    calls = open(env.claude_calls).read()
    check("a revived Claude tab resumes its own session", f"--resume {session}" in calls, calls)

    await ui.send(t="tab_new", ws=ws["id"], kind="claude")
    second = (await ui.expect(lambda m: m["t"] == "tab_created"))["tab"]
    await asyncio.sleep(1.5)
    d = api(env, "get", ws=ws["id"])
    names = {t["id"]: t["name"] for t in d["tabs"]}
    calls = open(env.claude_calls).read().splitlines()
    check("a second Claude tab is numbered and gets its own session",
          names.get(second) == "Claude Code 2" and len({c.split()[1] for c in calls if c.startswith("--")}) == 2,
          f"{names} {calls}")

    await ui.send(t="tab_new", ws=ws["id"], kind="codex")
    codex = (await ui.expect(lambda m: m["t"] == "tab_created"))["tab"]
    await ui.send(t="attach", ws=ws["id"], tab=codex, cols=70, rows=12)
    await ui.expect(lambda m: m["t"] == "attached")
    check("a missing Codex says so in its tab, with how to install it", await ui.screen_has("codex not found"))
    check("... and the install command", ui.row_of("npm install -g @openai/codex") is not None,
          str([ui.line(y) for y in sorted(ui.runs)]))
    await ui.send(t="in", d="echo still-a-shell-$((5*5))\r")
    check("a tab whose CLI is missing is still a working shell", await ui.screen_has("still-a-shell-25"))
    d = api(env, "get", ws=ws["id"])
    tab = next(t for t in d["tabs"] if t["id"] == codex)
    check("the Codex tab is named and kinded, and live", (tab["name"], tab["kind"], tab["state"]) ==
          ("Codex", "codex", "live"), str(tab))

    await ui.send(t="tab_new", ws=ws["id"], kind="rm -rf /")
    odd = (await ui.expect(lambda m: m["t"] == "tab_created"))["tab"]
    tab = next(t for t in api(env, "get", ws=ws["id"])["tabs"] if t["id"] == odd)
    check("an unknown tab kind opens a plain shell", tab["kind"] == "shell" and tab["name"].startswith("Shell"),
          str(tab))
    preset = api(env, "create", name="Agent preset", kind="terminal",
                 tabs=json.dumps([{"kind": "claude"}, {"name": "Review", "kind": "codex"}]))
    check("a preset workspace can hold agent tabs",
          [(t["name"], t["kind"]) for t in preset["tabs"]] == [("Claude Code", "claude"), ("Review", "codex")],
          str(preset["tabs"]))
    await ui.close()
    for w in (ws, preset):
        api(env, "delete", ws=w["id"], force=1)


async def stalled_tmux(env, ui):
    """A session server that stops answering for a while (load, swap) is
    waited out: the screen stays attached and keeps working. On 28 Oct
    every open screen detached at once when `tmux display` timed out."""
    pid = int(env.tmux("display", "-p", "#{pid}").stdout.strip())
    await ui.send(t="in", d="echo before-stall\r")
    await ui.screen_has("before-stall")
    os.kill(pid, signal.SIGSTOP)
    try:
        detached = await ui.expect(lambda m: m["t"] == "detached", 12)
    finally:
        os.kill(pid, signal.SIGCONT)
    check("a stalled session server does not detach the screen", not detached, str(detached))
    await ui.send(t="in", d="echo after-stall\r")
    check("the screen works again once tmux answers", await ui.screen_has("after-stall", 10) is True)


async def run(env):
    units()
    env.start_display()
    env.start_server()
    env.start_bridge()

    # -- plain HTTP ---------------------------------------------------------
    check("health answers", env.http("/health")["ok"] is True)
    check("board page is served", "Hermes" in env.http("/board"))
    try:
        env.http("/board.json", host="evil.example:80")
        check("foreign Host header refused", False)
    except urllib.error.HTTPError as e:
        check("foreign Host header refused", e.code == 403)
    try:
        env.http("/api/list?token=wrong")
        check("api without the token refused", False)
    except urllib.error.HTTPError as e:
        check("api without the token refused", e.code == 403)
    check("profiles are listed, default first", api(env, "profiles")["items"] == ["default", "omo-dev"])

    # -- ui: terminal workspace, typing, Enter split ------------------------
    ui = Client(env)
    welcome = await ui.open(token=env.token, role="ui")
    check("token hello is welcomed, with the Omo palette", welcome["t"] == "welcome" and len(welcome["palette"]) == 16,
          str(welcome))
    await ui.send(t="ws_create", name="Test Term", kind="terminal")
    term = (await ui.expect(lambda m: m["t"] == "workspace"))["ws"]
    check("terminal workspace starts live with one shell", term["state"] == "live" and term["tabs"][0]["state"] == "live")
    await ui.send(t="attach", ws=term["id"], tab="", cols=60, rows=12)
    await ui.expect(lambda m: m["t"] == "attached")
    await ui.send(t="in", d="echo omo-$((6*7)); echo नमस्ते q́ done\r")
    check("typed line with glued Enter runs in the shell", await ui.screen_has("omo-42"))
    await ui.send(t="size", cols=33, rows=11)
    await asyncio.sleep(0.8)
    sizes = env.tmux("list-clients", "-F", "#{client_width}x#{client_height}").stdout.split()
    check("a screen resize reaches the tmux client", "33x11" in sizes, str(sizes))
    await ui.send(t="size", cols=60, rows=12)
    await ui.send(t="in", d="echo still-painting\r")
    check("combining marks do not freeze the screen", await ui.screen_has("still-painting"))
    await stalled_tmux(env, ui)
    # A stand-in for Ink/prompt_toolkit TUIs: Enter only counts when a read
    # returns it alone. Keys arrive one message each, as the Lua client sends.
    tui = os.path.join(env.dir, "tui.py")
    with open(tui, "w") as fh:
        fh.write("import os, tty\ntty.setraw(0)\nprint('READY', end='\\r\\n', flush=True)\nb = b''\n"
                 "while True:\n    c = os.read(0, 64)\n    if c == b'\\r':\n"
                 "        print('SUBMIT', b.decode(), end='\\r\\n', flush=True)\n        break\n"
                 "    b += c.replace(b'\\r', b'')\n")
    await ui.send(t="in", d=f"python3 {tui}; stty sane\r")
    await ui.screen_has("READY")
    await asyncio.sleep(1.0)
    for ch in "hello\r":
        await ui.send(t="in", d=ch)
    check("Enter typed right after text reaches a TUI as its own keypress", await ui.screen_has("SUBMIT hello"))
    await terminal_abilities(env, ui)
    await two_surfaces(env, ui, term)
    await far_screen_parks(env, term)

    # -- Hermes workspace: autorun, profile, sibling tabs -------------------
    await ui.send(t="ws_create", name="Test Hermes", kind="hermes")
    herm = (await ui.expect(lambda m: m["t"] == "workspace"))["ws"]
    await asyncio.sleep(1.5)
    calls = open(env.calls).read() if os.path.exists(env.calls) else ""
    check("Hermes tab autoruns its titled conversation",
          f"chat --tui -c omo:{herm['id']}:{herm['tabs'][0]['id']} --create-if-missing" in calls, calls)
    d = api(env, "get", ws=herm["id"])
    check("running Hermes is named, not reported as bash", d["tabs"][0]["command"] == "hermes" and d["tabs"][0]["busy"],
          str(d["tabs"][0]))
    await ui.send(t="tab_new", ws=herm["id"], kind="shell")
    new_tab = (await ui.expect(lambda m: m["t"] == "tab_created"))["tab"]
    await ui.send(t="attach", ws=herm["id"], tab=new_tab, cols=60, rows=12)
    await ui.expect(lambda m: m["t"] == "attached")
    await ui.send(t="in", d='echo "auto=[$OMO_AUTORUN] title=[$OMO_HERMES_TITLE]"\r')
    check("a shell tab never inherits the Hermes autorun", await ui.screen_has("auto=[] title=[]"))
    try:
        api(env, "profile", ws=herm["id"], profile="nope")
        check("unknown profile rejected", False)
    except urllib.error.HTTPError as e:
        check("unknown profile rejected", e.code == 409)
    await ui.send(t="ws_profile", ws=herm["id"], profile="omo-dev")
    prof = await ui.expect(lambda m: m["t"] == "workspace" and m["ws"]["profile"] == "omo-dev")
    check("profile change is broadcast", prof and prof["ws"]["profile"] == "omo-dev")
    await ui.send(t="hermes", ws=herm["id"], tab=new_tab)
    await asyncio.sleep(1.5)
    calls = open(env.calls).read()
    check("Start Hermes uses the workspace profile", f"-p omo-dev chat --tui -c omo:{herm['id']}:{new_tab}" in calls, calls)
    await ui.send(t="tab_close", ws=herm["id"], tab=new_tab)
    confirm = await ui.expect(lambda m: m["t"] == "confirm", 4)
    check("closing a busy tab asks first", bool(confirm))
    await auto_sleep(env)
    await agent_tabs(env, welcome)

    # -- screen tickets are scoped --------------------------------------------
    ticket = env.http(f"/ticket?token={env.token}&station=st-1&ws={term['id']}")
    scr = Client(env)
    check("ticket for its own station/workspace is welcomed",
          (await scr.open(ticket=ticket, station="st-1", ws=term["id"]))["t"] == "welcome")
    await scr.send(t="attach", ws=herm["id"], tab="", cols=40, rows=10)
    err = await scr.expect(lambda m: m["t"] in ("error", "attached"))
    check("a screen cannot attach to another workspace", err and err["t"] == "error")
    await scr.send(t="tab_new", ws=term["id"], kind="shell")
    err = await scr.expect(lambda m: m["t"] in ("error", "tab_created"))
    check("a screen cannot manage tabs", err and err["t"] == "error")
    await scr.send(t="attach", ws=term["id"], tab="", cols=40, rows=10)
    ok = await scr.expect(lambda m: m["t"] == "attached")
    check("a screen can attach to its own workspace", ok and ok["state"] == "live")
    # A tab switch (steer from the panel) re-attaches the screen; keys typed in
    # that instant must land in the new tab, not vanish.
    await ui.send(t="tab_new", ws=term["id"], kind="shell", name="Switch")
    switch_tab = (await ui.expect(lambda m: m["t"] == "tab_created"))["tab"]
    await ui.send(t="steer", station="st-1", ws=term["id"], tab=switch_tab)
    for ch in "echo typed-in-switch\r":
        await scr.send(t="in", d=ch)
    await asyncio.sleep(2)
    pane = env.tmux("capture-pane", "-p", "-t", f"=omo-{term['id']}:").stdout
    windows = env.tmux("list-windows", "-t", f"=omo-{term['id']}", "-F", "#{@omo_tab} #{window_id}").stdout.split("\n")
    target = next((line.split()[1] for line in windows if line.startswith(switch_tab)), "")
    pane = env.tmux("capture-pane", "-p", "-t", f"=omo-{term['id']}:{target}").stdout if target else ""
    check("keys typed during a tab switch reach the new tab", "typed-in-switch" in pane.replace("echo typed-in-switch", ""),
          pane.strip()[-120:])
    await ui.send(t="tab_close", ws=term["id"], tab=switch_tab, force=True)
    await ui.expect(lambda m: m["t"] == "workspace", 3)
    again = Client(env)
    check("a ticket is single-use", (await again.open(ticket=ticket, station="st-1", ws=term["id"]))["t"] == "error")
    other = Client(env)
    t2 = env.http(f"/ticket?token={env.token}&station=st-1&ws={term['id']}")
    check("a ticket is bound to its workspace", (await other.open(ticket=t2, station="st-1", ws=herm["id"]))["t"] == "error")

    # -- honest endings ---------------------------------------------------------
    await ui.send(t="tab_new", ws=term["id"], kind="shell", name="Build")
    build = (await ui.expect(lambda m: m["t"] == "tab_created"))["tab"]
    await ui.send(t="attach", ws=term["id"], tab=build, cols=60, rows=12)
    await ui.expect(lambda m: m["t"] == "attached")
    await ui.send(t="in", d="exit\r")
    gone = await ui.expect(lambda m: m["t"] == "detached", 6)
    check("a screen whose tab exits is told, not moved to another tab", gone and gone["state"] == "exited", str(gone))
    d = api(env, "get", ws=term["id"])
    states = {t["name"]: t["state"] for t in d["tabs"]}
    check("the exited tab reads 'exited', its sibling stays live", states == {"Shell": "live", "Build": "exited"}, str(states))

    # -- session server restart -> interrupted; window ids are not trusted -----
    await ui.close()
    await scr.close()
    env.tmux("kill-server")
    time.sleep(0.5)
    d = api(env, "get", ws=term["id"])
    check("after the session server dies, the workspace is interrupted",
          d["state"] == "interrupted" and all(t["state"] == "interrupted" for t in d["tabs"]), str(d["state"]))
    env.start_server()
    ui = Client(env)
    await ui.open(token=env.token, role="ui")
    shell_tab = next(t["id"] for t in d["tabs"] if t["name"] == "Shell")
    await ui.send(t="tab_revive", ws=term["id"], tab=shell_tab, mode="shell")
    await ui.expect(lambda m: m["t"] == "workspace")
    await ui.send(t="tab_new", ws=term["id"], kind="shell", name="Fresh")
    await ui.expect(lambda m: m["t"] == "tab_created")
    d = api(env, "get", ws=term["id"])
    states = {t["name"]: t["state"] for t in d["tabs"]}
    check("revive is explicit and reused tmux ids do not resurrect other tabs",
          states == {"Shell": "live", "Build": "interrupted", "Fresh": "live"}, str(states))

    # -- bridge restart keeps shells --------------------------------------------
    await ui.send(t="attach", ws=term["id"], tab=shell_tab, cols=60, rows=12)
    await ui.expect(lambda m: m["t"] == "attached")
    await ui.send(t="in", d="export OMO_MARK=kept; cd /tmp\r")
    await asyncio.sleep(0.5)
    await ui.close()
    env.stop_bridge()
    env.start_bridge()
    ui = Client(env)
    await ui.open(token=env.token, role="ui")
    await ui.send(t="attach", ws=term["id"], tab=shell_tab, cols=60, rows=12)
    await ui.expect(lambda m: m["t"] == "attached")
    await ui.send(t="in", d='echo "mark=$OMO_MARK pwd=$PWD"\r')
    check("a bridge restart keeps the shell, its env and cwd", await ui.screen_has("mark=kept pwd=/tmp"))
    old = env.http(f"/ticket?token={env.token}&station=st-9&ws={term['id']}")
    env.stop_bridge()
    env.start_bridge()
    late = Client(env)
    check("tickets minted before a restart are refused", (await late.open(ticket=old, station="st-9", ws=term["id"]))["t"] == "error")

    # -- session server restart -> coming back to a screen restores the tabs --
    env.stop_bridge()
    env.tmux("kill-server")
    time.sleep(0.5)
    env.start_server()
    env.start_bridge()
    before = api(env, "get", ws=term["id"])
    open(env.calls, "w").close()
    ui = Client(env)
    await ui.open(token=env.token, role="ui")
    await ui.send(t="attach", ws=term["id"], tab=shell_tab, cols=60, rows=12)
    toast = await ui.expect(lambda m: m["t"] == "toast" and "restored" in m.get("m", ""), 6)
    att = await ui.expect(lambda m: m["t"] == "attached", 6)
    d = api(env, "get", ws=term["id"])
    check("attaching after a server restart restores every interrupted tab, same ids and order",
          toast and att and att["state"] == "live" and att["tab"] == shell_tab
          and [t["id"] for t in d["tabs"]] == [t["id"] for t in before["tabs"]]
          and all(t["state"] == "live" for t in d["tabs"]), str(d["tabs"]))
    await ui.send(t="in", d='echo "back pwd=$PWD"\r')
    check("a restored shell opens in its last directory", await ui.screen_has("back pwd=/tmp"))
    await ui.send(t="attach", ws=herm["id"], tab="", cols=60, rows=12)
    await ui.expect(lambda m: m["t"] == "attached", 6)
    await asyncio.sleep(1.5)
    calls = open(env.calls).read()
    check("a restored Hermes tab resumes its own conversation",
          f"-c omo:{herm['id']}:{herm['tabs'][0]['id']} --create-if-missing" in calls, calls)
    await ui.close()

    # -- an authored world's preset: name, kind, profile and tabs in one create --
    preset = api(env, "create", name="Website · Dev", kind="hermes", profile="omo-dev",
                 tabs=json.dumps([{"name": "Agent", "kind": "hermes"}, {"name": "Shell", "kind": "shell"},
                                  {"kind": "shell"}]))
    check("a preset workspace gets its name, profile and tabs in order",
          (preset["name"], preset["profile"]) == ("Website · Dev", "omo-dev")
          and [(t["name"], t["kind"]) for t in preset["tabs"]] == [("Agent", "hermes"), ("Shell", "shell"),
                                                                    ("Shell 2", "shell")], str(preset))
    try:
        api(env, "create", name="Bad", kind="hermes", profile="nope")
        check("a preset with an unknown profile is refused (the mod retries on default)", False)
    except urllib.error.HTTPError as e:
        check("a preset with an unknown profile is refused (the mod retries on default)", e.code == 409)

    # -- last tab ended (not interrupted) -------------------------------------
    for ws_id in (term["id"], herm["id"], preset["id"]):
        api(env, "delete", ws=ws_id, force=1)
    ws3 = api(env, "create", name="Solo", kind="terminal")
    ui = Client(env)
    await ui.open(token=env.token, role="ui")
    await ui.send(t="attach", ws=ws3["id"], tab="", cols=60, rows=12)
    await ui.expect(lambda m: m["t"] == "attached")
    await ui.send(t="in", d="exit\r")
    await ui.expect(lambda m: m["t"] == "detached", 6)
    d = api(env, "get", ws=ws3["id"])
    check("when every tab exits the workspace reads 'ended', not interrupted", d["state"] == "ended", d["state"])
    api(env, "delete", ws=ws3["id"], force=1)
    check("deleted workspaces are gone", api(env, "list")["items"] == [])


def main():
    env = Env()
    try:
        asyncio.run(run(env))
    finally:
        env.cleanup()
    print(f"\n{sum(RESULTS)}/{len(RESULTS)} bridge checks passed")
    return 0 if all(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
