#!/usr/bin/env python3
"""The PRD's in-game acceptance checks (section 7), run against OmoTest.

Everything runs against the ISOLATED stack from tests/testenv.py: bridge on
127.0.0.1:8794, tmux socket "omo-test", its own data dir and token. The live
bridge, its shells and its token are never used or restarted.

  tests/launch-test.sh &        # test stack + OmoTest on Xvfb :98, driver on 8793
  tests/acceptance.py           # PASS/FAIL/SKIP per check with evidence; exit 1 on any FAIL

Env:
  OMO_TEST_NO_HERMES=1          skip the steps that spend paid inference (Hermes replies)
  OMO_TEST_GAME_RESTART=1       also quit and relaunch the game (slow): the shell and
                                the web page binding must survive it
  OMO_TEST_P95_MS=50            frame-time budget for 8c (p95, software GL)
  OMO_TEST_KEEP=1               keep the test workspaces, bridge and session server

The world OmoFlat is opened here. At the end every workspace this run created
is deleted through the bridge, and the test bridge and session server stop.
"""
import gzip
import http.server
import os
import re
import subprocess
import sys
import threading
import time
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import testenv  # noqa: E402
from gamectl import call  # noqa: E402

HERMES = not os.environ.get("OMO_TEST_NO_HERMES")
GAME_RESTART = bool(os.environ.get("OMO_TEST_GAME_RESTART"))
P95_BUDGET_MS = float(os.environ.get("OMO_TEST_P95_MS", "50"))
PRISM_DATA = Path(os.environ.get("OMO_PRISM_DATA", Path.home() / ".local/share/omo-test-prism"))
INSTANCE = PRISM_DATA / "instances/OmoTest"
SAVE = INSTANCE / ".minecraft/saves/OmoFlat"
SHOTS = testenv.DATA / "shots"
HEALTH_URL = f"http://127.0.0.1:{testenv.PORT}/health"
RESULTS = []

# Blocks used below (all at y=-60/-59, z=0, facing south; the player stands at z=3.5).
HERMES_PC, TERMINAL, MOVED = (0, -60, 0), (2, -60, 0), (4, -60, 0)
WEB_A, WEB_B = (-3, -59, 0), (-1, -59, 0)
SHIFT, CTRL, KEY_T, KEY_W, KEY_TAB, KEY_LCTRL = 1, 2, 84, 87, 258, 341   # GLFW modifier / key codes
ALT, KEY_O, KEY_1, KEY_2, KEY_ENTER, KEY_DOWN = 4, 79, 49, 50, 257, 264
KEY_C, KEY_F, KEY_V, KEY_END, KEY_LSHIFT = 67, 70, 86, 269, 340
KEY_LBRACKET, KEY_ESCAPE = 91, 256
PRESET = (0, -60, -4)                 # a station placed with an OmoPreset (CONTRACT P)
TEST_DISPLAY = os.environ.get("OMO_TEST_DISPLAY", ":98")


# --------------------------------------------------------------------------- helpers
def check(name, ok, evidence=""):
    RESULTS.append((name, bool(ok)))
    print(("PASS " if ok else "FAIL ") + name + (f"  -- {evidence}" if evidence else ""), flush=True)
    return bool(ok)


def skip(name, why):
    print(f"SKIP {name}  -- {why}", flush=True)


def wait_for(pred, timeout=30, step=0.5):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        last = pred()
        if last:
            return last
        time.sleep(step)
    return last


# What the station status bar says while keys reach a live shell.
LIVE = "LIVE"


def term_text():
    return "\n".join(call("term").get("lines", []))


def term_lines():
    return [line.strip() for line in call("term").get("lines", [])]


def screen_match(pattern, timeout=20, step=0.5):
    """First regex match on the open computer screen within `timeout`."""
    rx = re.compile(pattern, re.I)
    return wait_for(lambda: rx.search(term_text()), timeout, step)


def type_line(text):
    """Type like a very fast human. The driver delivers a whole `type` call in
    one game tick, about three CC events per key, and CC:T drops events past
    its 256-event queue; so long lines go in bursts. The last burst still
    carries its text and Enter together (the glued-Enter regression)."""
    line = text + "\n"
    for i in range(0, len(line), 24):
        call("type", {"text": line[i:i + 24]})
        time.sleep(0.12)


def be(pos):
    x, y, z = pos
    return call("be", {"x": x, "y": y, "z": z})


def setblock(pos, block):
    x, y, z = pos
    call("cmd", {"command": f"setblock {x} {y} {z} {block}"})


def open_block(pos, face="south"):
    x, y, z = pos
    call("use", {"x": x, "y": y, "z": z, "face": face})
    time.sleep(0.6)


def open_connected(pos, timeout=40):
    open_block(pos)
    return wait_for(lambda: LIVE in term_text(), timeout, 1)


def widgets():
    return {w["text"]: w for w in call("widgets")["widgets"]}


def click_widget(label):
    w = widgets().get(label)
    if not w:
        return False
    call("click", {"x": w["x"] + w["w"] / 2, "y": w["y"] + w["h"] / 2})
    time.sleep(0.4)
    return True


def open_settings(pos, face="south"):
    """Shift+right-click: a monitor's settings (a plain right-click takes control of the wall)."""
    call("sneak", {"on": True})
    time.sleep(0.4)                                  # the server sees the crouch a few ticks later
    open_block(pos, face)
    call("sneak", {"on": False})


def pin_page(pos, url):
    open_settings(pos)
    click_widget("Web page")
    time.sleep(0.3)
    box = widgets().get("Address")
    if box:
        call("click", {"x": box["x"] + 5, "y": box["y"] + box["h"] / 2})
        call("key", {"key": 65, "mods": CTRL})       # replace whatever address is there
        call("key", {"key": 259})
        call("type", {"text": url})
        click_widget("Pin page")
    time.sleep(0.8)
    call("close")


def shot(name):
    call("screenshot", {"path": str(SHOTS / f"{name}.png")})
    time.sleep(0.5)


def panel_state():
    return call("panel").get("debug") or {}


def stack(slot):
    """(workspace, station) an inventory slot's item carries, as the client sees it."""
    custom = call("stack", {"slot": slot}).get("custom", "")
    ws, st = re.search(r'Workspace:"(\w*)"', custom), re.search(r'Station:"([\w-]*)"', custom)
    return (ws.group(1) if ws else "", st.group(1) if st else "")


def tabs_of(ws_id):
    return (call("panel").get("debug") or {}).get("ws", {}).get("tabs", []) if ws_id else []


def views_of(ws_id):
    names = testenv.tmux("list-sessions", "-F", "#{session_name}").stdout.split()
    return [n for n in names if n.startswith(f"omo-{ws_id}-v")]


def view_size(ws_id):
    views = views_of(ws_id)
    if not views:
        return ""
    return testenv.tmux("display", "-p", "-t", f"={views[0]}:", "#{window_width}x#{window_height}").stdout.strip()


def pane_pids():
    return set(testenv.tmux("list-panes", "-a", "-F", "#{pane_pid}").stdout.split())


def proc_alive(pid, cmdline):
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")[:-1] == cmdline.encode().split()
    except OSError:
        return False


def parent_of(pid):
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[1]
    except (OSError, IndexError):
        return ""


def world_blobs(save):
    """Yield (path, decompressed bytes) for everything in a world save."""
    for root, _, files in os.walk(save):
        for f in files:
            pth = os.path.join(root, f)
            try:
                data = open(pth, "rb").read()
            except OSError:
                continue
            if f.endswith(".mca") and len(data) >= 8192:
                for i in range(1024):
                    off = int.from_bytes(data[i * 4:i * 4 + 3], "big") * 4096
                    if off == 0 or off + 5 > len(data):
                        continue
                    length = int.from_bytes(data[off:off + 4], "big")
                    kind, payload = data[off + 4], data[off + 5:off + 4 + length]
                    try:
                        yield pth, zlib.decompress(payload) if kind == 2 else (
                            gzip.decompress(payload) if kind == 1 else payload)
                    except Exception:
                        yield pth, payload
            elif f.endswith((".dat", ".dat_old")):
                try:
                    yield pth, gzip.decompress(data)
                except Exception:
                    yield pth, data
            else:
                yield pth, data


def enter_world():
    if not call("status")["in_world"]:
        call("openworld", {"name": "OmoFlat"})
        wait_for(lambda: call("status")["in_world"], 120, 2)
        time.sleep(6)
    call("cmd", {"command": "tp @s 0.5 -60 3.5"})
    call("look", {"yaw": 180, "pitch": 20})


def clear_bench():
    """Everything within web-wall render distance (64 blocks) goes: monitors,
    stations or rooms left by earlier runs or other test builds would join
    new walls, hold pages open and skew every check. Only at the start of a
    run: a game restart mid-run must find what it left. Slices keep each
    /fill under its 32768-block limit."""
    for x in range(-64, 64, 32):
        for z in range(-64, 64, 32):
            call("cmd", {"command": f"fill {x} -60 {z} {x + 31} -41 {z + 31} air"})
    time.sleep(1.5)


def go_far_and_back(probe, far_z):
    """Teleport out of range, prove the client dropped the chunk (no block
    entity at `probe`), come back. Returns (unloaded, pages_while_far)."""
    call("cmd", {"command": f"tp @s 0.5 -60 {far_z}.5"})
    unloaded = wait_for(lambda: be(probe).get("type") is None, 30, 1)
    time.sleep(4)                                   # let the server drop its ticket too
    pages_far = call("pages")["count"]
    call("cmd", {"command": "tp @s 0.5 -60 3.5"})
    call("look", {"yaw": 180, "pitch": 20})

    wait_for(lambda: be(probe).get("type") is not None, 30, 1)
    time.sleep(2)
    return bool(unloaded), pages_far
    wait_for(lambda: be(probe).get("type") is not None, 30, 1)
    time.sleep(2)
    return bool(unloaded), pages_far


def restart_game():
    """Quit OmoTest and start it again through launch-test.sh (same test stack)."""
    try:
        call("quit")
    except OSError:
        pass                                        # the driver may go down mid-reply
    wait_for(lambda: not _driver_up(), 90, 2)
    wait_for(lambda: not _game_pids(), 60, 2)
    subprocess.Popen([str(HERE / "launch-test.sh")], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)
    if not wait_for(_driver_up, 300, 3):
        return False
    enter_world()
    return True


def _driver_up():
    try:
        return bool(call("status", timeout=5))
    except OSError:
        return False


def _game_pids():
    return testenv.omo_setup.instance_pids(INSTANCE)


# --------------------------------------------------------------------------- the terminal
def chord(key, mods):
    """A key with its modifiers held down first, as on a keyboard: the screen
    client tracks Ctrl and Shift from their own key events."""
    held = ([KEY_LCTRL] if mods & CTRL else []) + ([KEY_LSHIFT] if mods & SHIFT else [])
    for k in held:
        call("down", {"key": k, "mods": mods})
    call("key", {"key": key, "mods": mods})
    for k in reversed(held):
        call("up", {"key": k})
    time.sleep(0.4)


def clip(text=None, want=None):
    """The test display's clipboard (the one the test bridge and game use)."""
    cmd = ["xclip", "-display", TEST_DISPLAY, "-selection", "clipboard"]
    if text is not None:
        subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True).communicate(text.encode(), timeout=5)
        want = text
    got = None
    for _ in range(30):
        got = subprocess.run(cmd + ["-o"], capture_output=True, text=True, timeout=5).stdout
        if want is None or got == want:
            break
        time.sleep(0.2)
    return got


def cell_px(cx, cy):
    """GUI pixel at the centre of a 1-based terminal cell (6x9 px cells, 2 px margin)."""
    t = widgets()["Computer terminal"]
    return t["x"] + 2 + (cx - 0.5) * 6, t["y"] + 2 + (cy - 0.5) * 9


def row_of(text):
    """1-based terminal row showing text (the lowest), or None."""
    lines = call("term").get("lines", [])
    return next((i + 1 for i in range(len(lines) - 1, -1, -1) if text in lines[i]), None)


def queue(pos, event, *args, settle=0.3):
    x, y, z = pos
    call("queue", {"x": x, "y": y, "z": z, "event": event, "args": list(args)})
    time.sleep(settle)


def clients(ws_id):
    """{view session: "WxH"} for the screens attached to a workspace, and the window's size."""
    out = testenv.tmux("list-clients", "-F", "#{client_session} #{client_width}x#{client_height}").stdout.split("\n")
    views = dict(line.split() for line in out if line.startswith(f"omo-{ws_id}-v"))
    win = testenv.tmux("display", "-p", "-t", f"=omo-{ws_id}:", "#{window_width}x#{window_height}").stdout.strip()
    return views, win


def terminal_checks(ws):
    """Section 11: the station is a good terminal, and a wall is a second view."""
    # ---- selection: a real drag copies to the host clipboard on release
    open_connected(MOVED)
    type_line("clear; echo omo-drag-select; echo alpha /tmp/omo-word.txt beta")
    wait_for(lambda: row_of("alpha /tmp"), 15)
    y = row_of("omo-drag-select")
    clip("nothing yet")
    if y:
        x1, y1 = cell_px(1, y)
        x2, y2 = cell_px(15, y)
        call("drag", {"x1": x1, "y1": y1, "x2": x2, "y2": y2})
    got = clip(want="omo-drag-select")
    check("11a drag-select copies to the host clipboard on release", got == "omo-drag-select", repr(got))
    shot("t11-selection")
    y = row_of("alpha /tmp")
    for _ in range(2):                                # a double click, as CC reports it
        queue(MOVED, "mouse_click", 1, 12, y, settle=0)
        queue(MOVED, "mouse_up", 1, 12, y, settle=0)
    got = clip(want="/tmp/omo-word.txt")
    clip("something else")
    chord(KEY_C, CTRL | SHIFT)
    again = clip(want="/tmp/omo-word.txt")
    check("11b a double-click copies the word (a path whole); Ctrl+Shift+C copies it again",
          got == again == "/tmp/omo-word.txt", f"{got!r} {again!r}")

    # ---- paste: Ctrl+Shift+V and CC's Ctrl+V, multi-line, run only on Enter
    clip("echo pasted-$((20+22))\necho line-$((1+1))")
    type_line("clear")
    chord(KEY_V, CTRL | SHIFT)
    time.sleep(1.0)
    held = not screen_match(r"(?m)^pasted-42$", 1) and "line-$((1+1))" in term_text()
    type_line("")
    ran = screen_match(r"(?m)^line-2$", 10) and screen_match(r"(?m)^pasted-42$", 1)
    clip("echo ctrlv-$((3*3))\necho second-$((2*2))")
    call("key", {"key": KEY_V, "mods": CTRL})
    time.sleep(1.0)
    type_line("")
    ctrl_v = screen_match(r"(?m)^second-4$", 10) and screen_match(r"(?m)^ctrlv-9$", 1)
    check("11c paste is one bracketed block (runs on Enter); Ctrl+V pastes the whole clipboard too",
          held and ran and ctrl_v, f"held={held} ran={bool(ran)} ctrl_v={bool(ctrl_v)}")

    # ---- OSC 52 from an app lands on the host clipboard
    type_line("printf '\\e]52;c;%s\\a' $(printf osc-in-game | base64)")
    got = clip(want="osc-in-game")
    check("11d an app's OSC 52 copy reaches the host clipboard", got == "osc-in-game", repr(got))

    # ---- find across the scrollback
    type_line("clear; echo omo-find-$((40+2)); seq 1 150")
    wait_for(lambda: row_of("150"), 15)
    chord(KEY_F, CTRL | SHIFT)
    call("type", {"text": "omo-find-42"})
    found = wait_for(lambda: "1/1" in term_text() and row_of("omo-find-42") and row_of("FIND"), 10)
    shot("t11-find")
    chord(KEY_F, CTRL | SHIFT)
    scrolled = wait_for(lambda: "SCROLLBACK" in term_text(), 5)
    call("key", {"key": KEY_END, "mods": 0})
    live = wait_for(lambda: LIVE in term_text() and "SCROLLBACK" not in term_text(), 5)
    check("11e Ctrl+Shift+F finds in the scrollback and scrolls there; End returns to live",
          found and scrolled and live, f"found={bool(found)} scrolled={bool(scrolled)} live={bool(live)}")
    check("11f the station screen never shows CraftOS", "CraftOS" not in term_text())
    t = call("term")
    own = f"{t['cols']}x{t['rows'] - 1}"                 # the computer's view: its screen less the status bar
    call("close")

    # ---- CONTRACT B: a wall is a second view at its own size
    build_wall(range(5, 10), (-60, -59, -58))
    views = wait_for(lambda: (lambda v: v if len(v[0]) == 2 else None)(clients(ws)), 20) or clients(ws)
    sizes = sorted(views[0].values())
    call("look", {"yaw": 180, "pitch": 0})
    time.sleep(1.5)
    shot("t11-wall")
    check("11g a 5x3 wall is its own view at its own size beside the computer's",
          sizes == sorted(["100x37", own]), f"clients {views[0]} window {views[1]}")
    open_connected(MOVED)
    type_line("echo on-the-wall-$((6*7))")
    call("close")
    wall = wait_for(lambda: (lambda b: b if any("on-the-wall-42" in ln for ln in b.get("term_lines", [])) else None)(
        be((5, -60, 0))), 15) or be((5, -60, 0))
    shot("t11-wall-typed")
    if "term_lines" in wall:
        check("11g' the wall shows the terminal: what was typed on the computer, and its LIVE bar",
              any("on-the-wall-42" in ln for ln in wall["term_lines"]) and any(LIVE in ln for ln in wall["term_lines"]),
              f"{wall.get('term_size')} {[ln for ln in wall['term_lines'] if ln.strip()][-4:]}")
    else:
        skip("11g' wall content", "this driver cannot read a wall's text; see shots/t11-wall-typed.png")
    queue(MOVED, "omo_focus", "monitor")
    wall_drives = wait_for(lambda: clients(ws)[1] == "100x37", 5)
    queue(MOVED, "omo_focus", "computer")
    computer_drives = wait_for(lambda: clients(ws)[1] == own, 5)
    check("11h the surface in use drives the PTY size (omo_focus)", wall_drives and computer_drives,
          f"window {clients(ws)[1]}")
    call("cmd", {"command": "fill 5 -58 0 9 -58 0 air destroy"})     # as a player breaks them
    refit = wait_for(lambda: "100x23" in clients(ws)[0].values(), 15)
    check("11i a wall made smaller refits live (5x2 -> 100x23)", refit, str(clients(ws)))
    open_connected(MOVED)
    chord(KEY_T, CTRL | SHIFT)
    time.sleep(2.5)

    def shown():
        return {testenv.tmux("display", "-p", "-t", f"={n}:", "#{@omo_tab}").stdout.strip() for n in clients(ws)[0]}
    steered = wait_for(lambda: len(tabs_of(ws)) > 1 and shown() == {tabs_of(ws)[-1]["id"]}, 10)
    check("11j a new tab from the panel moves both views", steered, f"shown={shown()} tabs={tabs_of(ws)}")
    call("key", {"key": KEY_TAB, "mods": CTRL})
    call("close")
    call("cmd", {"command": "fill 6 -60 0 9 -59 0 air destroy"})
    card = wait_for(lambda: len(clients(ws)[0]) == 1, 15)
    check("11k a wall too small to use becomes a status card (one view left)", card, str(clients(ws)))
    call("cmd", {"command": "fill 5 -60 -1 9 -58 0 air"})
    call("cmd", {"command": "kill @e[type=item]"})
    call("cmd", {"command": "tp @s 0.5 -60 3.5 180 20"})
    time.sleep(1)


# --------------------------------------------------------------------------- the run
def run(state):
    enter_world()
    call("cmd", {"command": "advancement revoke @s from omolife:root"})     # 14h: earned again during this run
    clear_bench()
    SHOTS.mkdir(parents=True, exist_ok=True)

    # ---------------- 1. Place and use
    for pos in (HERMES_PC, TERMINAL, MOVED):
        setblock(pos, "air")
    time.sleep(0.5)
    setblock(HERMES_PC, "omolife:hermes_computer[facing=south]")
    setblock(TERMINAL, "omolife:terminal[facing=south]")
    time.sleep(1.0)
    open_block(HERMES_PC)
    b = wait_for(lambda: (lambda d: d if d.get("workspace") else None)(be(HERMES_PC)), 20) or {}
    ws1 = state["ws1"] = b.get("workspace", "")
    check("1a Hermes Computer binds a workspace on open", ws1, f"ws={ws1} name={b.get('name')}")
    known = {w["id"] for w in testenv.api("list")["items"]}
    check("1a' the game talks to the isolated test bridge", ws1 in known,
          f"test bridge :{testenv.PORT} knows {sorted(known)}")
    hermes_up = wait_for(lambda: ("Available Tools" in term_text() or "Hermes Agent" in term_text())
                         and LIVE in term_text(), 60, 1)
    check("1b Hermes TUI visible on the computer screen", hermes_up)
    if HERMES:
        # (i) regression: text and Enter arrive in ONE burst; the Enter used to
        # be swallowed by the TUI. The answer (42) is not in the prompt, so it
        # can only come from a real reply.
        type_line("Reply with the product of 6 and 7 followed by the word omo, nothing else.")
        m = screen_match(r"\b42\s+omo\b", 120, 2)
        check("1c line+Enter in one burst submits; a real Hermes reply lands (42 omo)", m,
              m.group(0) if m else term_text()[-300:])
    else:
        skip("1c Hermes reply", "OMO_TEST_NO_HERMES set")
    shot("t1-hermes")
    call("close")
    time.sleep(0.5)

    open_block(TERMINAL)
    b = wait_for(lambda: (lambda d: d if d.get("workspace") else None)(be(TERMINAL)), 20) or {}
    ws2 = state["ws2"] = b.get("workspace", "")
    check("1d Terminal binds its own distinct workspace", ws2 and ws2 != ws1, f"ws={ws2}")
    prompt = wait_for(lambda: ":~$" in term_text() and LIVE in term_text(), 30, 1)
    check("1e Terminal opens a plain shell (no agent)", prompt and "Hermes Agent" not in term_text())
    type_line("echo omo-shell-$((6*7))")
    check("1f typing reaches the host shell", screen_match(r"(?m)^omo-shell-42$", 15))
    shot("t1-terminal")

    # ---------------- 3a. Continuity across a bridge restart
    type_line("export OMO_MARK=alpha; cd /var/tmp; sleep 9001 & echo PID=$$ JOB=$!")
    m = screen_match(r"PID=(\d+) JOB=(\d+)", 15)
    pid, job = (m.group(1), m.group(2)) if m else ("", "")
    state["job"] = job
    real = bool(m) and pid in pane_pids() and proc_alive(job, "sleep 9001") and parent_of(job) == pid
    check("3a' shell is a real pane of the test session server with a real background job", real,
          f"shell pid={pid} job={job} ppid={parent_of(job)}")
    call("close")
    time.sleep(0.5)
    testenv.restart_bridge()
    open_connected(TERMINAL)
    type_line("echo AFTER1 mark=$OMO_MARK pid=$$ pwd=$PWD")
    m = screen_match(r"AFTER1 mark=(\S*) pid=(\d+) pwd=(\S+)", 20)
    got = m.groups() if m else ()
    check("3a bridge restart: same shell, marker, cwd; background job still running",
          got == ("alpha", pid, "/var/tmp") and proc_alive(job, "sleep 9001"),
          f"{got} job {job} alive={proc_alive(job, 'sleep 9001')}")
    call("close")

    # ---------------- 4. Tabs
    open_connected(TERMINAL, 30)
    call("key", {"key": KEY_T, "mods": CTRL | SHIFT})   # Ctrl+Shift+T: new shell tab (Ctrl+T is bash's)
    time.sleep(2.5)
    tabs = tabs_of(ws2)
    check("4a Ctrl+Shift+T adds a second tab", len(tabs) == 2, f"tabs={[t['name'] for t in tabs]}")
    wait_for(lambda: ":~$" in term_text(), 20)
    type_line("echo in-tab-two; sleep 600 &")
    check("4b second tab is its own shell", screen_match(r"(?m)^in-tab-two$", 15))
    # (ii) Ctrl+W belongs to the shell (delete word), not to the panel.
    call("type", {"text": "echo keep-tab one two"})
    # Hold left Ctrl like a keyboard does: the screen client tracks Ctrl from
    # its own key events, not from a modifier flag.
    call("down", {"key": KEY_LCTRL, "mods": CTRL})
    call("key", {"key": KEY_W, "mods": CTRL})
    call("up", {"key": KEY_LCTRL})
    type_line("")
    m = screen_match(r"(?m)^keep-tab one$", 15)
    tabs = tabs_of(ws2)
    check("4d Ctrl+W at a bash prompt deletes a word and keeps the tab", m and len(tabs) == 2,
          f"tabs={len(tabs)} output={'keep-tab one' if m else term_lines()[-4:]}")
    call("key", {"key": KEY_TAB, "mods": CTRL})     # Ctrl+Tab: back to the first tab
    time.sleep(2)
    type_line("echo back-on-one mark=$OMO_MARK")
    check("4c switching tabs returns to the first shell with its state",
          screen_match(r"(?m)^back-on-one mark=alpha$", 15))
    shot("t4-tabs")

    # ---------------- 9. The header: tab keys and the workspace switcher
    tabs = tabs_of(ws2)
    call("key", {"key": KEY_2, "mods": ALT})
    time.sleep(1.5)
    check("9a Alt+2 jumps to the second tab", len(tabs) == 2 and panel_state().get("currentTab") == tabs[1]["id"],
          f"current={panel_state().get('currentTab')} tabs={[t['id'] for t in tabs]}")
    call("key", {"key": KEY_1, "mods": ALT})
    time.sleep(1.5)
    type_line("echo alt-one mark=$OMO_MARK")
    check("9a' Alt+1 jumps back to the first tab and its shell",
          panel_state().get("currentTab") == tabs[0]["id"] and screen_match(r"(?m)^alt-one mark=alpha$", 15))
    call("key", {"key": KEY_O, "mods": CTRL | SHIFT})
    listed = wait_for(lambda: {w["id"] for w in panel_state().get("workspaces", [])}, 10)
    host = {w["id"] for w in testenv.api("list")["items"]}
    check("9b Ctrl+Shift+O opens the switcher with every workspace on the host",
          panel_state().get("mode") == "WORKSPACES" and listed == host, f"listed {len(listed or ())} of {len(host)}")
    ws2_name = be(TERMINAL).get("name", "")
    name = f"omo-switch-{int(time.time()) % 100000}"
    call("type", {"text": name})
    shot("t9-switcher")
    call("key", {"key": KEY_DOWN})                   # "New terminal workspace '<name>'"
    call("key", {"key": KEY_ENTER})
    b = wait_for(lambda: (lambda d: d if d.get("name") == name else None)(be(TERMINAL)), 20) or {}
    ws3 = state["ws3"] = b.get("workspace", "")
    fresh = wait_for(lambda: ":~$" in term_text() and LIVE in term_text() and name in term_text(), 30, 1)
    type_line("echo new-ws-$((3*3)) mark=[$OMO_MARK]")
    check("9c a new workspace from the switcher: the station shows its fresh shell",
          ws3 and ws3 not in (ws1, ws2) and fresh and screen_match(r"(?m)^new-ws-9 mark=\[\]$", 15), f"ws={ws3}")
    check("9c' switching killed nothing: the old shell and its job still run",
          pid in pane_pids() and proc_alive(job, "sleep 9001"), f"shell {pid} job {job}")
    call("key", {"key": KEY_O, "mods": CTRL | SHIFT})
    time.sleep(1)
    call("type", {"text": ws2_name})
    call("key", {"key": KEY_ENTER})
    back = wait_for(lambda: be(TERMINAL).get("workspace") == ws2, 20)
    # LIVE is already on screen from the workspace being left: wait until the
    # screen shows the one switched back to, or the keys land in the old one.
    wait_for(lambda: LIVE in term_text() and ws2_name in term_text() and name not in term_text(), 30, 1)
    type_line("echo BACK mark=$OMO_MARK pid=$$")
    m = screen_match(r"BACK mark=(\S*) pid=(\d+)", 20)
    alive = ws3 in {w["id"]: w for w in testenv.api("list")["items"] if w["state"] == "live"}
    check("9d switching back from the switcher: the same shell, and the new workspace keeps running",
          back and m and m.groups() == ("alpha", pid) and alive, f"{m.groups() if m else ()} ws3 live={alive}")
    call("close")

    # ---------------- 2. Real screens: the computer always shows the terminal;
    # a wall big enough mirrors it, a small monitor shows a status card.
    mon_small = (3, -60, 0)
    setblock(mon_small, "omolife:monitor[facing=south]")
    time.sleep(4)
    size = view_size(ws2)
    open_connected(TERMINAL, 20)
    type_line("echo screen-kept-$((2+3))")
    kept = screen_match(r"(?m)^screen-kept-5$", 15)
    call("close")
    check("2a a small monitor never shrinks the station: 80x29, still typing on the computer",
          size == "80x29" and bool(kept), f"view {size}")
    setblock(mon_small, "air")
    # A 5x3 wall, placed as a player so CC:T joins it, mirrors the full screen.
    call("cmd", {"command": "fill 3 -60 -1 7 -58 -1 smooth_quartz"})
    call("cmd", {"command": "item replace entity @s weapon.mainhand with omolife:monitor 64"})
    call("cmd", {"command": "tp @s 5.5 -60 3.5 180 0"})
    time.sleep(0.5)
    for y in (-60, -59, -58):
        for x in range(3, 8):
            call("use", {"x": x, "y": y, "z": -1, "face": "south"})
            time.sleep(0.15)
    call("cmd", {"command": "item replace entity @s weapon.mainhand with air"})
    time.sleep(4)
    wall = be((3, -60, 0))
    size = view_size(ws2)
    shot("t2-mirror")
    check("2a' a 5x3 wall mirrors the station at full size (80x29)",
          (wall.get("width"), wall.get("height")) == (5, 3) and size == "80x29",
          f"wall {wall.get('width')}x{wall.get('height')} view {size}")
    call("cmd", {"command": "fill 3 -60 -1 7 -58 0 air"})
    call("cmd", {"command": "tp @s 0.5 -60 3.5 180 20"})
    time.sleep(3)
    size = view_size(ws2)
    check("2b breaking the wall leaves the computer's screen as it was (80x29)", size == "80x29", f"view {size}")

    # ---------------- 3b. Continuity across a chunk unload
    unloaded, _ = go_far_and_back(TERMINAL, 600)
    open_connected(TERMINAL)
    type_line("echo AFTER2 mark=$OMO_MARK pid=$$ pwd=$PWD")
    m = screen_match(r"AFTER2 mark=(\S*) pid=(\d+) pwd=(\S+)", 20)
    got = m.groups() if m else ()
    check("3b chunk really unloaded, then the same shell, marker, cwd and job",
          unloaded and got == ("alpha", pid, "/var/tmp") and proc_alive(job, "sleep 9001"),
          f"unloaded={unloaded} {got}")
    call("close")

    # ---------------- 5. Move and recover
    call("cmd", {"command": "clone 2 -60 0 2 -60 0 4 -60 0 replace move"})
    time.sleep(1.5)
    moved = be(MOVED)
    check("5a moved station keeps its workspace id", moved.get("workspace") == ws2, f"{moved.get('workspace')} vs {ws2}")
    open_connected(MOVED)
    type_line("echo MOVED mark=$OMO_MARK pid=$$ pwd=$PWD")
    m = screen_match(r"MOVED mark=(\S*) pid=(\d+) pwd=(\S+)", 20)
    check("5b moved station reattaches to the same shell", m and m.groups() == ("alpha", pid, "/var/tmp"),
          str(m.groups() if m else ()))
    call("close")
    # A powered station keeps exactly one view (its screen program runs with
    # the GUI closed, like any CC:T computer). Two means the old block leaked.
    views = views_of(ws2)
    check("5c exactly one view for the moved station", len(views) == 1, f"views={views}")

    terminal_checks(ws2)                         # section 11

    # ---------------- 6. Honest recovery: the whole session server restarts
    if HERMES:
        open_connected(HERMES_PC, 30)
        type_line("Remember the code word PINEAPPLE. Reply with the product of 3 and 4 "
                  "followed by the word omo, nothing else.")
        m = screen_match(r"\b12\s+omo\b", 120, 2)
        check("6' Hermes answered before the restart (12 omo)", m, m.group(0) if m else term_text()[-300:])
        call("close")
    testenv.restart_tmux()                           # like a reboot: every shell ends
    time.sleep(1.5)
    mine = [w for w in testenv.api("list")["items"] if w["id"] in (ws1, ws2)]
    states = {w["id"]: (w["state"], [t["state"] for t in w["tabs"]]) for w in mine}
    check("6a session server restart: every workspace and tab reports interrupted",
          len(mine) == 2 and all(s == "interrupted" and set(ts) == {"interrupted"} for s, ts in states.values()),
          str(states))
    check("6a' the background job ended with it (nothing claims it still runs)",
          not proc_alive(job, "sleep 9001"), f"job {job}")
    open_block(MOVED)
    shown = wait_for(lambda: re.search("interrupted", term_text(), re.I), 30, 1)
    panel = (call("panel").get("debug") or {}).get("ws", {})
    check("6b the station shows INTERRUPTED and its panel agrees", shown and panel.get("alive") is False,
          f"panel alive={panel.get('alive')} state={panel.get('state')}")
    clicked = click_widget("Revive shell")
    revived = wait_for(lambda: ":~$" in term_text() and LIVE in term_text(), 40, 1)
    type_line("echo REVIVED mark=[$OMO_MARK] pid=$$")
    m = screen_match(r"REVIVED mark=\[(\S*)\] pid=(\d+)", 20)
    state["revived_pid"] = m.group(2) if m else ""
    check("6c Revive shell starts a fresh shell, explicitly, re-running nothing",
          clicked and revived and m and m.group(1) == "", str(m.groups() if m else ()))
    shot("t6-revived")
    call("close")
    if HERMES:
        open_block(HERMES_PC)
        wait_for(lambda: re.search("interrupted", term_text(), re.I), 30, 1)
        clicked = click_widget("Resume Hermes")
        # A resumed chat shows its transcript, not the banner; Hermes' own
        # status bar ("ready ... omo:<ws>:<tab>") says it is taking input.
        up = wait_for(lambda: re.search(r"ready.*omo:[0-9a-f]{12}:", term_text()), 90, 2)
        type_line("What was the code word? Reply with just the word in lowercase.")
        ans = wait_for(lambda: "pineapple" in term_text().split("What was the code word?")[-1].lower(), 120, 2)
        check("6d Resume Hermes reopens the saved conversation (remembers PINEAPPLE)", clicked and up and ans)
        shot("t6-hermes-resumed")
        call("close")
    else:
        skip("6d Resume Hermes", "OMO_TEST_NO_HERMES set")

    # ---------------- 8. Web monitors
    # Stand at the bench first: earlier sections may leave the player out of
    # reach of these panels, and a use from too far away is silently ignored.
    call("cmd", {"command": "tp @s 0.5 -60 3.5 180 20"})
    time.sleep(1)
    for x in (-7, -5, -3, -1):
        setblock((x, -60, 0), "air")
        setblock((x, -59, 0), "air")
    time.sleep(0.5)
    setblock(WEB_A, "omolife:monitor[facing=south]")
    setblock(WEB_B, "omolife:monitor[facing=south]")
    time.sleep(1.5)
    open_settings(WEB_A)
    time.sleep(0.2)
    check("8a Shift+right-click opens monitor controls", call("status")["screen"].endswith("MonitorScreen"))
    call("close")
    pin_page(WEB_A, HEALTH_URL)
    pin_page(WEB_B, "https://example.com")
    call("look", {"yaw": 145, "pitch": 5})           # face the monitor wall
    b1 = wait_for(lambda: (lambda d: d if d.get("page_ready") else None)(be(WEB_A)), 120, 2) or {}
    b2 = wait_for(lambda: (lambda d: d if d.get("page_ready") else None)(be(WEB_B)), 120, 2) or {}
    state["web_url"] = b1.get("url")
    check("8b two monitors render two different live pages",
          b1 and b2 and b1.get("url") != b2.get("url"),
          f"{b1.get('page_url')} | {b2.get('page_url')} pages={call('pages')['count']}")
    shot("t8-webmonitors")
    for x, url in ((-5, "https://example.org"), (-7, HEALTH_URL)):
        setblock((x, -59, 0), "omolife:monitor[facing=south]")
        time.sleep(1.0)
        pin_page((x, -59, 0), url)
    call("look", {"yaw": 130, "pitch": 5})
    wait_for(lambda: call("pages")["count"] >= 4, 60, 2)
    call("perf", {"seconds": 4})                     # warm-up
    perf = call("perf", {"seconds": 6})
    check(f"8c frame time with 4 web monitors within budget (p95 < {P95_BUDGET_MS:g} ms, software GL)",
          perf.get("frame_ms_p95", 1e9) < P95_BUDGET_MS,
          f"fps={perf.get('fps')} p50={perf.get('frame_ms_p50', 0):.1f}ms p95={perf.get('frame_ms_p95', 0):.1f}ms "
          f"heap={perf.get('java_heap_mb')}MB")
    unloaded, pages_far = go_far_and_back(WEB_A, 400)
    call("look", {"yaw": 145, "pitch": 5})
    back = wait_for(lambda: (lambda d: d if d.get("page") else None)(be(WEB_A)), 30, 1) or be(WEB_A)
    check("8d chunk really unloaded; page binding restored on return",
          unloaded and back.get("url") == b1.get("url") and back.get("page") is True,
          f"unloaded={unloaded} pages far={pages_far} back={call('pages')['count']}")
    pages = call("pages")["count"]
    check("8e no page accumulation after reconnect", pages <= 4, f"pages={pages}")

    remote_walls(state)

    # ---------------- 3c/8f. Full game restart (optional, slow)
    if GAME_RESTART:
        ok = restart_game()
        check("3c' the game quits and comes back on the same test stack", ok)
        if ok:
            call("look", {"yaw": 145, "pitch": 5})
            w = wait_for(lambda: (lambda d: d if d.get("page_ready") else None)(be(WEB_A)), 180, 3) or be(WEB_A)
            check("8f web page binding survives a full game restart", w.get("url") == state.get("web_url"),
                  f"{w.get('url')} ready={w.get('page_ready')}")
            call("look", {"yaw": 180, "pitch": 20})
            open_connected(MOVED)
            type_line("echo AFTER3 pid=$$")
            m = screen_match(r"AFTER3 pid=(\d+)", 20)
            check("3c game restart: same shell process", m and m.group(1) == state.get("revived_pid"),
                  f"{m.group(1) if m else None} vs {state.get('revived_pid')}")
            call("close")
    else:
        skip("3c/8f full game restart", "set OMO_TEST_GAME_RESTART=1")

    # ---------------- 10. The Omo Pocket: a whole station in your hand
    call("cmd", {"command": "tp 0.5 -60 3.5"})
    call("look", {"yaw": 180, "pitch": 20})
    for slot in (0, 1, 2, 5):
        call("cmd", {"command": f"item replace entity @s hotbar.{slot} with air"})
    call("cmd", {"command": "item replace entity @s hotbar.0 with omolife:pocket_terminal"})
    call("cmd", {"command": "item replace entity @s hotbar.1 with omolife:pocket_terminal"})
    call("slot", {"slot": 0})
    time.sleep(0.5)
    call("useitem")
    live = wait_for(lambda: ":~$" in term_text() and LIVE in term_text(), 40, 1)
    p1 = panel_state()
    s1 = wait_for(lambda: (lambda s: s if s[0] else None)(stack(0)), 10) or ("", "")
    names = widgets()
    check("10a an Omo Pocket opens the station screen, header and all, on its own new workspace",
          live and p1.get("target") == "pocket" and p1.get("workspace") and s1 == (p1["workspace"], p1["station"])
          and {"More", "New tab"} <= names.keys(), f"panel ws={p1.get('workspace')} item={s1}")
    t = call("term")
    type_line("export POCKET_MARK=beta; echo POCKET-$((6*7)) PID=$$")
    m = screen_match(r"POCKET-42 PID=(\d+)", 15)
    ppid = m.group(1) if m else ""
    check("10b the pocket is a full 80-column station and typing reaches its shell",
          (t.get("cols"), t.get("rows")) == (80, 30) and m, f"{t.get('cols')}x{t.get('rows')}")
    shot("t10-pocket")
    call("close")
    time.sleep(1)
    call("useitem")
    wait_for(lambda: LIVE in term_text(), 30, 1)
    type_line("echo AGAIN mark=$POCKET_MARK pid=$$")
    m = screen_match(r"AGAIN mark=(\S*) pid=(\d+)", 15)
    check("10c reopening the pocket shows the same shell", m and m.groups() == ("beta", ppid), str(m.groups() if m else ()))
    call("close")
    call("slot", {"slot": 1})
    time.sleep(0.5)
    call("useitem")
    wait_for(lambda: LIVE in term_text(), 40, 1)
    p2 = panel_state()
    check("10d a second pocket is a second workspace and station",
          p2.get("workspace") and p2.get("workspace") != p1.get("workspace") and p2.get("station") != p1.get("station"),
          f"{p1.get('workspace')} / {p2.get('workspace')}")
    call("close")
    call("cmd", {"command": "item replace entity @s hotbar.2 from entity @s hotbar.0"})
    copied = wait_for(lambda: (lambda c: c if c[1] and c[1] != s1[1] else None)(stack(2)), 10) or stack(2)
    check("10e a copy of a running pocket keeps the workspace but never its station id",
          copied[0] == s1[0] and copied[1] and copied[1] != s1[1] and stack(0) == s1, f"original {s1} copy {copied}")
    call("cmd", {"command": "item replace entity @s hotbar.5 from entity @s hotbar.0"})
    call("cmd", {"command": "item replace entity @s hotbar.0 with air"})
    call("slot", {"slot": 5})
    time.sleep(1)
    call("useitem")
    wait_for(lambda: LIVE in term_text(), 40, 1)
    type_line("echo MOVED-POCKET mark=$POCKET_MARK")
    check("10f a pocket moved to another slot keeps its workspace (same shell)",
          screen_match(r"(?m)^MOVED-POCKET mark=beta$", 15))
    call("close")
    for slot in (1, 2, 5):
        call("cmd", {"command": f"item replace entity @s hotbar.{slot} with air"})
    call("slot", {"slot": 0})
    cc_pockets()
    escape_keys()
    presets()
    links()
    builders(state)
    room_kits()

    # ---------------- 7. Safety: the save carries no bridge token, ticket or browser data
    call("cmd", {"command": "save-all flush"})
    time.sleep(3)
    token = testenv.token().encode()
    hits, url_seen, ticket_seen = [], False, False
    for pth, blob in world_blobs(SAVE):
        if token in blob or b"Cookies" in blob or b"Local Storage" in blob:
            hits.append(pth)
        if b"OmoUrl" in blob and b"example.com" in blob:
            url_seen = True
        if re.search(rb"[A-Za-z0-9_-]{12}:\d{10}:[0-9a-f]{40}", blob):
            ticket_seen = True
    check("7a world save contains no bridge token, ticket or browser data", not hits and not ticket_seen,
          f"hits={hits[:3]} ticket={ticket_seen}")
    check("7b world save does carry the non-secret page binding (URL only)", url_seen)


# --------------------------------------------------------------------------- 10g-12. pockets, keys, presets
def cc_pockets():
    """Every pocket computer a player uses is a station, CC:T's own included."""
    ids = []
    for slot, item in ((3, "pocket_computer_advanced"), (4, "pocket_computer_normal")):
        call("cmd", {"command": f"item replace entity @s hotbar.{slot} with computercraft:{item}"})
        call("slot", {"slot": slot})
        time.sleep(0.5)
        call("useitem")
        live = wait_for(lambda: ":~$" in term_text() and LIVE in term_text(), 40, 1)
        p, t = panel_state(), call("term")
        kind = (p.get("ws") or {}).get("kind")
        check(f"10g a CC:T {item} opens as a station: its own terminal workspace, 80x30, the header",
              live and p.get("target") == "pocket" and p.get("workspace") and kind == "terminal"
              and (t.get("cols"), t.get("rows")) == (80, 30) and {"More", "New tab"} <= widgets().keys(),
              f"ws={p.get('workspace')} kind={kind} {t.get('cols')}x{t.get('rows')}")
        ids.append(p.get("workspace"))
        if slot == 3:
            type_line("export CC_MARK=gamma; echo CC-POCKET-$((6*7))")
            check("10h typing in a CC:T pocket reaches its shell", screen_match(r"(?m)^CC-POCKET-42$", 15))
            shot("t10-cc-pocket")
        call("close")
        time.sleep(1)
    call("slot", {"slot": 3})
    call("useitem")
    wait_for(lambda: LIVE in term_text(), 30, 1)
    type_line("echo CC-AGAIN mark=$CC_MARK")
    check("10i reopening a CC:T pocket shows the same shell; two pockets are two workspaces",
          screen_match(r"(?m)^CC-AGAIN mark=gamma$", 15) and panel_state().get("workspace") == ids[0]
          and all(ids) and len(set(ids)) == 2, str(ids))
    call("close")
    for slot in (3, 4):
        call("cmd", {"command": f"item replace entity @s hotbar.{slot} with air"})
    call("slot", {"slot": 0})


def current_tab():
    p = panel_state()
    return next((t for t in (p.get("ws") or {}).get("tabs", []) if t["id"] == p.get("currentTab")), {})


def escape_keys():
    """Esc always closes a station screen; Ctrl+[ is how a running program gets its Escape."""
    open_connected(MOVED)
    type_line("clear; cat -v")
    busy = wait_for(lambda: current_tab().get("busy"), 15, 1)
    shot("t12-program-running")                       # the footer says "Ctrl+[ sends Esc"
    chord(KEY_LBRACKET, CTRL)
    call("key", {"key": KEY_ENTER})
    check("12a Ctrl+[ reaches a running program as ESC (cat -v shows ^[)",
          busy and screen_match(r"(?m)^\^\[$", 10), f"busy={busy}")
    running = current_tab().get("busy")
    call("key", {"key": KEY_ESCAPE})
    time.sleep(0.5)
    check("12b a single Esc closes the station screen, even while a program runs",
          running and call("status")["screen"] == "", f"running={running}")
    open_connected(MOVED)
    chord(KEY_C, CTRL)                                # end cat
    call("close")


def presets():
    """CONTRACT P: an authored world's station makes its first workspace as its OmoPreset says, once."""
    call("cmd", {"command": "tp @s 0.5 -60 -1.5 180 20"})
    setblock(PRESET, "air")
    setblock(PRESET, 'omolife:hermes_computer[facing=south]{OmoPreset:{Name:"Preset · Dev",Kind:"terminal",'
             'Profile:"no-such-profile",Tabs:[{Name:"Main",Kind:"shell"},{Name:"Logs",Kind:"shell"}]}}')
    time.sleep(1)
    waiting = be(PRESET).get("preset")
    open_block(PRESET)
    b = wait_for(lambda: (lambda d: d if d.get("workspace") else None)(be(PRESET)), 20) or {}
    d = testenv.api("get", ws=b["workspace"]) if b else {}
    note = wait_for(lambda: "No Hermes profile" in panel_state().get("message", ""), 10, 0.5)
    tabs = [(t["name"], t["kind"]) for t in d.get("tabs", [])]
    check("13a a preset station's first workspace has the preset's name, kind and tabs; "
          "an unknown profile falls back to the default, with a toast",
          waiting and (d.get("name"), d.get("kind"), d.get("profile")) == ("Preset · Dev", "terminal", "")
          and tabs == [("Main", "shell"), ("Logs", "shell")] and note,
          f"preset before={waiting} ws={d.get('name')!r} kind={d.get('kind')} tabs={tabs} toast={bool(note)}")
    shot("t13-preset")
    call("close")
    after = be(PRESET)
    check("13b the preset stays as the station's recipe (a copy of the map elsewhere is built from it)",
          after.get("preset") is True and "Preset · Dev" in after.get("preset_tag", ""), after.get("preset_tag"))
    setblock(PRESET, "air")
    call("cmd", {"command": "tp @s 0.5 -60 3.5 180 20"})


# --------------------------------------------------------------------------- 15. linking and setting up
LINK_ST = (22, -60, 6)                # a Terminal well away from its wall
LINK_WALL = (14, -60, 0)              # origin of a 5x3 wall at x 14..18, y -60..-58, facing south: an 80x30 screen whole
FRESH = (20, -60, 6)                  # an unbound station, set up in game
STRANGER = (18, -60, 6)               # a station from "another PC": a workspace id this host never had


def wall_text(pos=LINK_WALL):
    return "\n".join(be(pos).get("term_lines", []))


def setup_of(pos):
    """Shift+right-click a station with an empty hand: its Station Setup."""
    call("sneak", {"on": True})
    time.sleep(0.4)
    open_block(pos, "north")
    call("sneak", {"on": False})
    return wait_for(lambda: call("status")["screen"].endswith("StationSetupScreen"), 10, 0.5)


def fill_box(label, text):
    box = widgets().get(label)
    if box:
        call("click", {"x": box["x"] + 5, "y": box["y"] + box["h"] / 2})
        call("key", {"key": 65, "mods": CTRL})
        call("key", {"key": 259})
        call("type", {"text": text})
    return bool(box)


def links():
    """The Omo Linker ties screens to a station anywhere nearby; stations are set up in game."""
    for pos in (LINK_ST, FRESH, STRANGER):
        setblock(pos, "air")
    call("cmd", {"command": "fill 14 -60 -1 18 -58 0 air"})
    setblock(LINK_ST, "omolife:terminal[facing=north]")
    build_wall(range(14, 19), (-60, -59, -58), stand="tp @s 16.5 -60 3.5 180 0")
    call("cmd", {"command": "item replace entity @s hotbar.8 with omolife:linker"})
    call("slot", {"slot": 8})
    call("cmd", {"command": "tp @s 22.5 -60 9.5 180 20"})
    time.sleep(0.5)
    call("use", {"x": LINK_ST[0], "y": LINK_ST[1], "z": LINK_ST[2], "face": "south"})
    time.sleep(0.5)
    chosen = call("stack", {"slot": 8}).get("custom", "")
    check("15a the Linker chooses a station (right-click)", "LinkTo" in chosen and "X:22" in chosen, chosen)
    call("cmd", {"command": "tp @s 16.5 -60 3.5 180 10"})
    time.sleep(0.5)
    call("use", {"x": 16, "y": -59, "z": 0, "face": "south"})
    time.sleep(1)
    panels = [be((x, y, 0)).get("linked") for x in range(14, 19) for y in (-60, -59, -58)]
    check("15b right-click links the whole wall, 9 blocks from its station",
          panels == ["22, -60, 6"] * 15, str(panels))
    shot("t15-linker-held")
    ws = wait_for(lambda: be(LINK_ST).get("workspace"), 30, 1)
    mirrored = wait_for(lambda: LIVE in wall_text() and ":~$" in wall_text(), 60, 1)
    w = be(LINK_WALL)
    check("15c the linked wall shows the station's own screen (prompt and LIVE bar), no view of its own",
          mirrored and len(views_of(ws)) == 1, f"{w.get('term_size')} views={views_of(ws)}")

    # Remote control of a linked wall types into the station's shell.
    call("slot", {"slot": 0})
    call("cmd", {"command": "item replace entity @s hotbar.0 with air"})
    call("cmd", {"command": "tp @s 16.5 -60 12.5 180 0"})
    time.sleep(1)
    call("aim", {"x": 16.5, "y": -58.5, "z": 1.0})
    time.sleep(0.5)
    right_click()
    r = call("remote")
    raw_line("echo linked-remote-$((6*7))\n")
    typed = wait_for(lambda: re.search(r"(?m)^linked-remote-42$", pane_text(ws)), 20, 1)
    shown = wait_for(lambda: "linked-remote-42" in wall_text(), 20, 1)
    shot("t15-linked-remote")
    call("rawkey", {"key": 256})
    time.sleep(0.5)
    check("15d right-click on a linked wall takes control; typing reaches the station's shell and shows on the wall",
          r.get("active") and not r.get("web") and typed and shown, f"{r} typed={bool(typed)} shown={bool(shown)}")

    # The Station Setup edits a bound station: renamed now, tabs and lock kept in its recipe.
    call("cmd", {"command": "tp @s 22.5 -60 9.5 180 20"})
    time.sleep(0.5)
    opened = setup_of(LINK_ST)
    shot("t15-setup-bound")
    fill_box("Name", "Linked · Desk")
    fill_box("Tab name", "Logs")
    click_widget("Shell tab")
    click_widget("Unlocked")
    click_widget("Save")
    renamed = wait_for(lambda: testenv.api("get", ws=ws).get("name") == "Linked · Desk", 10, 0.5)
    b = be(LINK_ST)
    check("15e the Station Setup renames a bound station now and keeps its recipe and lock",
          opened and renamed and b.get("locked") and "Logs" in b.get("preset_tag", ""),
          f"name={testenv.api('get', ws=ws).get('name')} locked={b.get('locked')} preset={b.get('preset_tag')}")
    call("cmd", {"command": "gamemode survival"})
    time.sleep(0.5)
    refused = not setup_of(LINK_ST)
    call("cmd", {"command": "gamemode creative"})
    time.sleep(0.5)
    check("15f a locked station refuses its setup outside creative mode", refused)

    # The Station Setup edits an unbound station's preset; its workspace is made from it.
    setblock(FRESH, "omolife:terminal[facing=north]")
    time.sleep(1)
    call("cmd", {"command": "tp @s 20.5 -60 9.5 180 20"})
    time.sleep(0.5)
    opened = setup_of(FRESH)
    shot("t15-setup-fresh")
    fill_box("Name", "Fresh · Kit")
    click_widget("Hermes")
    fill_box("Tab name", "Build")
    click_widget("Shell tab")
    click_widget("Save")
    time.sleep(1)
    preset = be(FRESH).get("preset_tag", "")
    open_block(FRESH, "north")
    fresh = wait_for(lambda: be(FRESH).get("workspace"), 30, 1)
    d = testenv.api("get", ws=fresh) if fresh else {}
    call("close")
    check("15g the setup writes an unbound station's preset, and its workspace is made from it",
          opened and 'Name:"Fresh · Kit"' in preset and (d.get("name"), d.get("kind")) == ("Fresh · Kit", "hermes")
          and [t["name"] for t in d.get("tabs", [])][-1:] == ["Build"],
          f"preset={preset} ws={d.get('name')} kind={d.get('kind')} tabs={[t['name'] for t in d.get('tabs', [])]}")

    # Unlink: Shift+right-click with the Linker.
    call("slot", {"slot": 8})
    call("cmd", {"command": "tp @s 16.5 -60 3.5 180 10"})
    time.sleep(0.5)
    call("sneak", {"on": True})
    time.sleep(0.4)
    call("use", {"x": 14, "y": -60, "z": 0, "face": "south"})
    call("sneak", {"on": False})
    time.sleep(1.5)
    check("15h Shift+right-click with the Linker unlinks the wall, and its glass is blank again",
          be(LINK_WALL).get("linked") == "" and "LIVE" not in wall_text(), wall_text()[:80])

    # Links and presets survive /clone: the copy's wall follows the copy's station.
    call("use", {"x": LINK_ST[0], "y": LINK_ST[1], "z": LINK_ST[2], "face": "south"})
    call("use", {"x": 16, "y": -59, "z": 0, "face": "south"})
    time.sleep(1)
    call("cmd", {"command": "clone 14 -60 -1 22 -58 6 14 -60 -21"})
    time.sleep(2)
    copy = wait_for(lambda: (lambda d: d if d.get("linked") else None)(be((14, -60, -20))), 10, 1) or {}
    call("cmd", {"command": "tp @s 16.5 -60 -16.5 180 10"})
    live = wait_for(lambda: LIVE in wall_text((14, -60, -20)), 60, 1)
    shot("t15-cloned")
    check("15i /clone keeps links relative: the copied wall mirrors the copied station",
          copy.get("linked") == "22, -60, -14" and live and be((22, -60, -14)).get("preset"),
          f"linked={copy.get('linked')} live={bool(live)}")
    call("cmd", {"command": "fill 14 -60 -21 22 -58 -14 air"})

    # ... and a structure template placed turned a quarter: the link turns with the room.
    setblock((13, -60, -1), 'minecraft:structure_block{mode:"SAVE",name:"omolife:linked_room",'
                            'posX:1,posY:0,posZ:0,sizeX:9,sizeY:3,sizeZ:8}')
    setblock((12, -60, -1), "minecraft:redstone_block")
    time.sleep(1)
    call("cmd", {"command": "tp @s 36.5 -60 -28.5 180 10"})
    time.sleep(2)
    call("cmd", {"command": "place template omolife:linked_room 40 -60 -40 clockwise_90"})
    turned = wait_for(lambda: (lambda d: d if d.get("linked") else None)(be((39, -60, -40))), 10, 1) or {}
    corner = next((p for p in ((39, y, z) for y in (-60, -58) for z in (-40, -36))
                   if (be(p).get("xIndex"), be(p).get("yIndex")) == (0, 0)), (39, -58, -40))
    turned_live = wait_for(lambda: LIVE in wall_text(corner), 60, 1)
    shot("t15-turned")
    check("15j a structure template turned a quarter keeps the link: the turned wall mirrors the turned station",
          turned.get("linked") == "33, -60, -32" and be((33, -60, -32)).get("type") == "StationBlockEntity"
          and turned_live, f"linked={turned.get('linked')} live={bool(turned_live)}")
    call("cmd", {"command": "fill 32 -60 -41 41 -58 -31 air"})
    call("cmd", {"command": "fill 12 -60 -1 13 -60 -1 air"})

    # A station from a map made on another PC: its workspace id is unknown here.
    call("server", {"command": "setblock 18 -60 6 omolife:terminal[facing=north]{Workspace:\"feedfacecafe\","
                    "WorkspaceName:\"Their Desk\",OmoPreset:{Name:\"Their Desk\",Kind:\"terminal\","
                    "Tabs:[{Name:\"Main\",Kind:\"shell\"},{Name:\"Notes\",Kind:\"shell\"}]}}"})   # > 256 chars
    time.sleep(1)
    call("slot", {"slot": 0})                        # an empty hand opens it (the Linker would choose it)
    call("cmd", {"command": "tp @s 18.5 -60 9.5 180 20"})
    time.sleep(0.5)
    open_block(STRANGER, "north")
    new = wait_for(lambda: (lambda w: w if w and w != "feedfacecafe" else None)(be(STRANGER).get("workspace")), 30, 1)
    d = testenv.api("get", ws=new) if new else {}
    up = wait_for(lambda: LIVE in term_text(), 30, 1)
    shot("t15-stranger")
    call("close")
    check("15k a station whose workspace this host never had makes a fresh one from its preset, no error",
          new and d.get("name") == "Their Desk" and [t["name"] for t in d.get("tabs", [])] == ["Main", "Notes"] and up,
          f"ws={new} name={d.get('name')} tabs={[t['name'] for t in d.get('tabs', [])]} live={bool(up)}")
    for pos in (LINK_ST, FRESH, STRANGER):
        setblock(pos, "air")
    call("cmd", {"command": "fill 14 -60 -1 18 -58 0 air"})
    call("cmd", {"command": "item replace entity @s hotbar.8 with air"})
    call("slot", {"slot": 0})
    call("cmd", {"command": "tp @s 0.5 -60 3.5 180 20"})


# --------------------------------------------------------------------------- 14. redstone, room kits, sharing
# Over 100 blocks from the bench at 0,0: kits hold web walls, which must not load pages there.
BENCH = (140, -60, 10)                # a Terminal with a comparator (and a lamp) in front of it
KITS = {"hermes_desk": (130, 20), "terminal_nook": (140, 20), "web_gallery": (150, 20), "meeting_room": (162, 20),
        "pocket_stand": (174, 20)}
KEY_Q = 81


def signal():
    x, y, z = BENCH
    return call("redstone", {"x": x, "y": y, "z": z + 1}).get("comparator")


def lamp_lit():
    x, y, z = BENCH
    return "lit=true" in call("redstone", {"x": x + 1, "y": y, "z": z}).get("block", "")


def omo_look(pos, command):
    """Run an /omo command looking at the block at pos, from right in front of it."""
    x, y, z = pos
    call("cmd", {"command": f"tp @s {x + 0.5} -60 {z + 2.5} 180 20"})
    time.sleep(0.5)
    call("aim", {"x": x + 0.5, "y": y + 0.5, "z": z + 0.5})
    time.sleep(0.6)
    call("cmd", {"command": command})
    time.sleep(0.8)


def advancements(names):
    """Which of these advancements the player has, asked of the server: each one it has
    sets a marker block (the saved progress file lags behind)."""
    x, y, z = BENCH
    done = set()
    for i, name in enumerate(names):
        pos = (x - 2 + i, y + 3, z - 2)
        setblock(pos, "air")
        call("cmd", {"command": f"execute if entity @s[advancements={{{name}=true}}] run setblock "
                                f"{pos[0]} {pos[1]} {pos[2]} gold_block"})
        time.sleep(0.2)
        if "gold_block" in call("redstone", {"x": pos[0], "y": pos[1], "z": pos[2]}).get("block", ""):
            done.add(name)
        setblock(pos, "air")
    return done


def room_kits():
    """Redstone by workspace state, room kits, presets, advancements, sharing a map."""
    x, y, z = BENCH
    # A known state first: no screen open, no wall under remote control, not
    # crouching — any of them swallows the placing click below.
    call("close")
    if call("remote").get("active"):
        call("rawkey", {"key": 256})                     # Esc lets go of the wall
    call("sneak", {"on": False})
    call("cmd", {"command": f"tp @s {x + 0.5} -60 {z + 3.5} 180 20"})
    time.sleep(3)                                       # far from the bench: let the chunks load
    call("cmd", {"command": f"fill {x - 2} {y} {z - 2} {x + 3} {y + 3} {z + 3} air"})
    call("cmd", {"command": "item replace entity @s weapon.mainhand with omolife:terminal"})
    time.sleep(0.5)
    for _ in range(3):                                   # placed by the player, as a builder would
        call("use", {"x": x, "y": y - 1, "z": z, "face": "up"})
        if wait_for(lambda: be(BENCH).get("type") == "StationBlockEntity", 3):
            break
    call("cmd", {"command": "item replace entity @s weapon.mainhand with air"})
    setblock((x, y, z + 1), "comparator[facing=north]")
    setblock((x + 1, y, z), "redstone_lamp")
    time.sleep(3)
    unbound = signal()
    open_connected(BENCH)
    call("close")
    idle = wait_for(lambda: signal() == 3, 10)
    open_block(BENCH)
    type_line("sleep 12")
    call("close")
    busy = wait_for(lambda: (signal() or 0) >= 8, 8)
    lamp_off_by_default = not lamp_lit()
    back = wait_for(lambda: signal() == 3, 20)
    check("14a a comparator reads a station: 0 no workspace, 3 idle, >=8 while a command runs, 3 when it ends",
          unbound == 0 and idle and busy and back, f"unbound={unbound} idle={idle} busy={busy} back={back}")
    shot("t14-redstone")

    omo_look(BENCH, "omo signal on")
    open_block(BENCH)
    type_line("sleep 8")
    call("close")
    lit = wait_for(lamp_lit, 8)
    dark = wait_for(lambda: not lamp_lit(), 15)
    check("14b with /omo signal on, a lamp beside the station lights while it works (off by default)",
          lamp_off_by_default and lit and dark, f"default_off={lamp_off_by_default} lit={lit} dark_after={dark}")

    open_block(BENCH)
    chord(KEY_Q, CTRL | SHIFT)                          # leave the screen client for CraftOS
    call("type", {"text": "redstone set left true\n"})
    call("type", {"text": "redstone set right true\n"})
    time.sleep(1)
    lua_on = lamp_lit()
    call("type", {"text": "redstone set left false\n"})
    call("type", {"text": "redstone set right false\n"})
    time.sleep(1)
    lua_off = not lamp_lit()
    call("type", {"text": "station\n"})
    time.sleep(1)
    call("close")
    check("14c CC:T's redstone API still drives the station's sides from Lua", lua_on and lua_off,
          f"on={lua_on} off={lua_off}")

    # room kits: /place template, and /omo kit where you look
    call("cmd", {"command": "tp @s 155.5 -60 12.5 180 10"})
    time.sleep(1)
    for name, (kx, kz) in KITS.items():
        call("cmd", {"command": f"fill {kx - 1} -60 {kz - 1} {kx + 10} -54 {kz + 9} air"})
        call("cmd", {"command": f"place template omolife:{name} {kx} -60 {kz}"})
    time.sleep(2)
    desk, wall = be((132, -59, 22)), be((132, -58, 21))
    gallery = be((152, -59, 21))
    nook, room = be((142, -59, 21)), be((166, -59, 21))
    check("14d room kits place with presets, no workspaces, and joined walls (web walls keep their page)",
          all(s.get("preset") and not s.get("workspace") for s in (desk, nook, room))
          and (wall.get("width"), wall.get("height")) == (3, 2) and gallery.get("source") == "web",
          f"desk={desk.get('preset')}/{desk.get('workspace')!r} wall={wall.get('width')}x{wall.get('height')} "
          f"gallery={gallery.get('source')} nook={nook.get('preset')} room={room.get('preset')}")
    call("cmd", {"command": "tp @s 132.5 -60 25.5 180 10"})
    time.sleep(0.5)
    open_connected((132, -59, 22))
    call("close")
    shown = wait_for(lambda: be((132, -58, 21)).get("term_size"), 15)
    check("14e a kit's station makes its preset workspace and drives the kit's wall",
          be((132, -59, 22)).get("name") == "Hermes Desk" and shown, f"wall term={shown}")
    call("cmd", {"command": "fill 130 -60 30 136 -56 38 air"})
    call("cmd", {"command": "tp @s 133.5 -60 40.5 180 0"})
    time.sleep(0.5)
    call("aim", {"x": 133.5, "y": -60.9, "z": 35.5})
    time.sleep(0.6)
    call("cmd", {"command": "omo kit hermes_desk"})
    time.sleep(1.5)
    placed = [p for p in ((133, -59, zz) for zz in range(32, 38)) if be(p).get("type") == "StationBlockEntity"]
    check("14f /omo kit places a kit where you look, facing you", bool(placed), f"station at {placed}")
    call("cmd", {"command": "tp @s 133.5 -58 44.5 180 15"})
    shot("t14-kits")

    # a builder's preset on a station that already has a workspace
    omo_look(BENCH, 'omo preset "Front Desk" hermes')
    b = be(BENCH)
    check("14g /omo preset turns the looked-at station into a preset (its workspace keeps running)",
          b.get("preset") and not b.get("workspace"), str(b))

    want = ["omolife:root", "omolife:place_station", "omolife:open_hermes", "omolife:get_pocket",
            "omolife:big_screen", "omolife:pin_page", "omolife:control_afar", "omolife:link_screen"]
    have = advancements(want)
    check("14h the Omo Space advancements were earned along the way", set(want) <= have,
          f"missing={sorted(set(want) - have)}")

    # sharing a map: the save must not bind anything to this PC's workspaces
    call("cmd", {"command": "omo export-clean"})
    bound = re.compile(rb"\x08\x00\x09Workspace\x00\x0c[0-9a-f]{12}")

    def clean():
        call("cmd", {"command": "save-all flush"})
        time.sleep(2)
        return not [p for p, blob in world_blobs(SAVE) if bound.search(blob)]
    ok = wait_for(clean, 40, 5)
    left = [os.path.relpath(p, SAVE) for p, blob in world_blobs(SAVE) if bound.search(blob)]
    check("14i /omo export-clean leaves no workspace binding anywhere in the save", ok, f"left={left[:4]}")
    for kx, kz in KITS.values():                        # leave no web walls behind for the next run
        call("cmd", {"command": f"fill {kx - 1} -60 {kz - 1} {kx + 10} -54 {kz + 9} air"})
    call("cmd", {"command": "fill 130 -60 30 136 -56 38 air"})
    call("cmd", {"command": f"fill {x - 2} {y} {z - 2} {x + 3} {y + 3} {z + 3} air"})

# --------------------------------------------------------------------------- 20. builders
def wall_of(pos, w, h, timeout=10):
    """The monitor at pos once it is part of a w x h wall (else as it is)."""
    return wait_for(lambda: (lambda d: d if (d.get("width"), d.get("height")) == (w, h) else None)(be(pos)),
                    timeout, 0.5) or be(pos)


def builders(state):
    """Round 3: walls that form however they are placed, the builder blocks, recipes."""
    call("cmd", {"command": "fill 9 -60 -13 22 -54 -9 air"})
    call("cmd", {"command": "fill 9 -60 -10 18 -54 -10 smooth_quartz"})
    call("cmd", {"command": "tp @s 15.5 -60 -1.5 180 10"})
    time.sleep(1)

    # 20a-c: /fill, a structure template (and one turned a quarter), never across kinds
    call("cmd", {"command": "fill 10 -60 -9 13 -58 -9 omolife:monitor[facing=south]"})
    mid = wall_of((11, -59, -9), 4, 3)
    check("20a /fill of a 4x3 area of Omo Monitors forms one 4x3 wall, drawn joined",
          (mid.get("width"), mid.get("height")) == (4, 3) and "state=lrud" in mid.get("block_state", ""),
          f"{mid.get('width')}x{mid.get('height')} {mid.get('block_state')}")
    setblock((10, -60, -12), 'minecraft:structure_block{mode:"SAVE",name:"omolife:test_wall",'
                             'posX:0,posY:0,posZ:3,sizeX:4,sizeY:3,sizeZ:1}')
    setblock((10, -60, -13), "minecraft:redstone_block")
    time.sleep(1)
    call("cmd", {"command": "place template omolife:test_wall 15 -60 -9"})
    call("cmd", {"command": "place template omolife:test_wall 20 -60 -12 clockwise_90"})
    placed, turned = wall_of((16, -59, -9), 4, 3), wall_of((20, -59, -10), 4, 3)
    check("20b a structure-block template of the wall places as a 4x3 wall, turned or not",
          (placed.get("width"), placed.get("height"), turned.get("width"), turned.get("height")) == (4, 3, 4, 3),
          f"placed {placed.get('width')}x{placed.get('height')}, turned {turned.get('width')}x{turned.get('height')}")
    call("cmd", {"command": "fill 10 -56 -9 12 -55 -9 omolife:slim_screen[facing=south]"})
    setblock((13, -56, -9), "omolife:monitor[facing=south]")
    slim, lone = wall_of((11, -56, -9), 3, 2), be((13, -56, -9))
    check("20c Slim Screens form their own 3x2 wall; a monitor beside them stays apart",
          (slim.get("width"), slim.get("height")) == (3, 2) and (lone.get("width"), lone.get("height")) == (1, 1),
          f"slim {slim.get('width')}x{slim.get('height')}, monitor {lone.get('width')}x{lone.get('height')}")
    call("cmd", {"command": "tp @s 15.5 -58.5 3.5 180 0"})
    time.sleep(2)
    shot("t20-walls")

    # 20d: a dye refinishes the whole Slim Screen wall
    call("cmd", {"command": "item replace entity @s weapon.mainhand with yellow_dye 4"})
    call("cmd", {"command": "tp @s 11.5 -56 -6.5 180 0"})
    time.sleep(0.5)
    call("use", {"x": 12, "y": -55, "z": -9, "face": "south"})
    gold = wait_for(lambda: all("finish=gold" in be((x, y, -9)).get("block_state", "")
                                for x in (10, 11, 12) for y in (-56, -55)), 5, 0.5)
    check("20d yellow dye on one Slim Screen turns its whole wall gold", gold, be((10, -56, -9)).get("block_state"))
    call("cmd", {"command": "item replace entity @s weapon.mainhand with air"})
    call("cmd", {"command": "tp @s 11.5 -60 -2.5 180 -28"})
    time.sleep(2)
    shot("t20-gold-screens")

    # 20e-f: a Status Lamp and a Name Plate follow the nearest station (the moved Terminal)
    call("cmd", {"command": "tp @s 4.5 -60 5.5 180 15"})
    setblock((3, -60, 2), "omolife:status_lamp")
    setblock((5, -60, 2), "omolife:name_plate[face=floor,facing=north]")
    ws2 = state["ws2"]
    name = testenv.api("get", ws=ws2).get("name")
    idle = wait_for(lambda: "glow=idle" in be((3, -60, 2)).get("block_state", "")
                    and be((5, -60, 2)).get("name") == name, 15, 1)
    check("20e the lamp turns green and the plate shows the workspace name of the nearest station", idle,
          f"lamp={be((3, -60, 2)).get('glow')} plate={be((5, -60, 2)).get('name')!r} ws={name!r}")
    shot("t20-lamp-idle")
    open_connected(MOVED)
    type_line("sleep 30")
    working = wait_for(lambda: be((3, -60, 2)).get("glow") == "working"
                       and be((5, -60, 2)).get("glow") == "working", 15, 1)
    chord(KEY_C, CTRL)
    call("close")
    check("20f while a command runs they turn working (gold)", working, str(be((3, -60, 2)).get("glow")))
    back = wait_for(lambda: be((3, -60, 2)).get("glow") == "idle", 15, 1)
    check("20f' and back to idle when it ends", back, str(be((3, -60, 2)).get("glow")))

    # 20g: recipes are loaded and unlock with their key ingredient
    call("cmd", {"command": "clear @s"})
    for name in ("hermes_computer", "slim_screen"):
        call("cmd", {"command": f"advancement revoke @s only omolife:recipes/redstone/{name}"})
        call("cmd", {"command": f"recipe take @s omolife:{name}"})
    time.sleep(0.5)
    before = call("recipes")
    call("cmd", {"command": "give @s computercraft:computer_advanced"})
    after = wait_for(lambda: (lambda r: r if {"hermes_computer", "slim_screen"} <= set(r["known"]) else None)(
        call("recipes")), 10, 0.5) or call("recipes")
    expected = {"hermes_computer", "terminal", "monitor", "slim_screen", "pocket_hermes", "pocket_terminal",
                "name_plate", "status_lamp"}
    check("20g every Omo block has a recipe; picking up an advanced computer unlocks the station and screen ones",
          expected <= set(after["all"]) and "hermes_computer" not in before["known"]
          and {"hermes_computer", "slim_screen"} <= set(after["known"]),
          f"all={sorted(after['all'])} known={sorted(after['known'])}")
    call("cmd", {"command": "clear @s"})

    # 20i: every Omo item says what it is; a Slim Screen also names its frame
    items = ["hermes_computer", "terminal", "monitor", 'slim_screen[block_state={finish:"gold"}]', "pocket_hermes",
             "pocket_terminal", "name_plate", "status_lamp"]
    for slot, item in enumerate(items):
        call("cmd", {"command": f"item replace entity @s hotbar.{slot} with omolife:{item}"})
    time.sleep(0.5)
    tips = [call("stack", {"slot": slot}).get("tooltip", []) for slot in range(len(items))]
    check("20i every Omo item has a tooltip line; a gold Slim Screen says 'Frame: Gold'",
          all(len(t) >= 2 for t in tips) and "Frame: Gold" in tips[3], str([t[1:] for t in tips]))
    call("cmd", {"command": "clear @s"})

    # 20h: 24 lamps and 24 plates cost nothing to draw
    call("cmd", {"command": "fill -10 -60 6 -3 -60 8 omolife:status_lamp"})
    call("cmd", {"command": "fill -10 -60 9 -3 -60 11 omolife:name_plate[face=floor,facing=north]"})
    call("cmd", {"command": "tp @s -6.5 -59 14.5 180 25"})
    time.sleep(3)
    call("perf", {"seconds": 3})
    perf = call("perf", {"seconds": 5})
    check(f"20h frame time with 24 Status Lamps and 24 Name Plates in view within budget (p95 < {P95_BUDGET_MS:g} ms)",
          perf.get("frame_ms_p95", 1e9) < P95_BUDGET_MS,
          f"fps={perf.get('fps')} p95={perf.get('frame_ms_p95', 0):.1f}ms")
    shot("t20-lamps")
    call("cmd", {"command": "fill -10 -60 6 -3 -60 11 air"})
    call("cmd", {"command": "tp @s 0.5 -60 3.5 180 20"})


# --------------------------------------------------------------------------- 9. walls from where you stand
TEST_PAGES = {
    # a page that is one big link: a click anywhere on it navigates to /typed
    "/click": "<body style='margin:0'><a href='/typed' style='display:block;height:100vh;background:#2b221c;"
              "color:#f3efe7;font:bold 25vh sans-serif'>CLICK</a></body>",
    # typing lands in the URL (#t=...), and so does scrolling (#y=...)
    "/typed": "<body style='margin:0;height:400vh;background:#fbf7ee'><input autofocus style='font-size:12vh;"
              "width:90%' oninput=\"history.replaceState(null,'','#t='+this.value)\"><script>onscroll=()=>"
              "history.replaceState(null,'','#y='+Math.round(scrollY))</script></body>",
    # no background of its own, stamped with its load time (#L...): a reload shows as a new stamp
    "/glass": "<body style='background:transparent;font:10vh sans-serif'>glass<script>"
              "history.replaceState(null,'','#L'+Date.now())</script></body>",
}


def serve_test_pages():
    """The pages above on a loopback port of their own; returns the base URL."""
    class Pages(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = TEST_PAGES.get(self.path.split("#")[0], "").encode()
            self.send_response(200 if body else 404)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Pages)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}"


def build_wall(xs, ys, backing="smooth_quartz", stand=None):
    """Monitors at z=0 facing south, placed as a player against a backing at z=-1 so CC:T joins them."""
    call("cmd", {"command": f"fill {min(xs)} {min(ys)} -1 {max(xs)} {max(ys)} -1 {backing}"})
    call("cmd", {"command": "item replace entity @s weapon.mainhand with omolife:monitor 64"})
    call("cmd", {"command": stand or f"tp @s {(min(xs) + max(xs)) / 2 + 0.5} -60 3.5 180 0"})
    time.sleep(0.5)
    for y in ys:
        for x in xs:
            call("use", {"x": x, "y": y, "z": -1, "face": "south"})
            time.sleep(0.15)
    call("cmd", {"command": "item replace entity @s weapon.mainhand with air"})
    time.sleep(3)


def right_click():
    call("rawbutton", {"button": 1, "action": 1})
    time.sleep(0.1)
    call("rawbutton", {"button": 1, "action": 0})
    time.sleep(0.6)


def left_click():
    call("rawbutton", {"button": 0, "action": 1})
    time.sleep(0.1)
    call("rawbutton", {"button": 0, "action": 0})


def raw_line(text):
    """Typing at a wall, in bursts like type_line: CC:T's event queue holds 256 events."""
    for i in range(0, len(text), 16):
        call("rawtype", {"text": text[i:i + 16]})
        time.sleep(0.15)


def pane_text(ws_id):
    panes = testenv.tmux("list-panes", "-s", "-t", f"=omo-{ws_id}", "-F", "#{pane_id}").stdout.split()
    return "\n".join(testenv.tmux("capture-pane", "-p", "-t", pane).stdout for pane in panes)


def game_log():
    try:
        return (INSTANCE / ".minecraft/logs/latest.log").read_text(errors="replace")
    except OSError:
        return ""


def pixels(name):
    """RGB pixels of a screenshot, or None without Pillow."""
    try:
        from PIL import Image
    except ImportError:
        return None
    path = SHOTS / f"{name}.png"
    wait_for(path.exists, 10, 0.5)
    time.sleep(0.5)
    return Image.open(path).convert("RGB")


def box_stats(img, fx0, fy0, fx1, fy1):
    """(mean luminance, share of lime pixels) over a box given in fractions of the image."""
    w, h = img.size
    px = [img.getpixel((x, y)) for x in range(int(w * fx0), int(w * fx1), 4)
          for y in range(int(h * fy0), int(h * fy1), 4)]
    lum = sum(0.3 * r + 0.59 * g + 0.11 * b for r, g, b in px) / len(px)
    lime = sum(1 for r, g, b in px if g > 140 and b < 90 and g > r + 40) / len(px)
    return lum, lime


def remote_walls(state):
    ws2 = state["ws2"]
    base = serve_test_pages()

    # ---- 9a-d a terminal wall, from across the room
    build_wall(range(5, 9), (-60, -59, -58))
    origin = be((5, -60, 0))
    check("9 a 4x3 terminal wall stands beside the station", (origin.get("width"), origin.get("height")) == (4, 3),
          f"{origin.get('width')}x{origin.get('height')} source={origin.get('source')}")
    call("cmd", {"command": "tp @s 6.5 -60 14.5 0 0"})    # facing away, so the hint is fresh
    time.sleep(1)
    call("aim", {"x": 7.0, "y": -58.5, "z": 1.0})
    time.sleep(0.5)
    r = call("remote")
    check("9a'' the crosshair finds the wall 13 blocks away (the hint shows)", r.get("wall") == "5, -60, 0", str(r))
    shot("t9-hint")
    right_click()
    r = call("remote")
    check("9a right-click from 13 blocks takes control; no screen opens",
          r.get("active") and not r.get("web") and r.get("screen") == "", str(r))
    shot("t9-controlling")
    before = call("status")
    raw_line("echo wasd-remote-$((6*7))\n")
    typed = wait_for(lambda: re.search(r"(?m)^wasd-remote-42$", pane_text(ws2)), 20, 1)
    after, r = call("status"), call("remote")
    moved = abs(after["x"] - before["x"]) + abs(after["z"] - before["z"])
    check("9b typing at the wall reaches the station's shell; W/A/S/D do not walk",
          typed and moved < 0.01 and not r.get("forward_down"), f"moved={moved:.3f} typed={bool(typed)}")
    call("rawscroll", {"dy": 1})
    left_click()
    time.sleep(0.5)
    shot("t9-typed")
    call("rawkey", {"key": 256})
    time.sleep(0.5)
    r = call("remote")
    check("9c Esc lets go (and opens no pause menu)", not r.get("active") and r.get("screen") == "", str(r))
    m = wait_for(lambda: re.findall(r"let go of monitor wall 5, -60, 0 \(keys (\d+), chars (\d+), mouse (\d+)\)",
                                    game_log()), 10, 1)
    keys, chars, mouse = map(int, m[-1]) if m else (0, 0, 0)
    check("9c' the station got omo_focus, key/char and omo_mouse (scroll, click, up), then omo_release",
          m and chars >= 20 and keys >= 40 and mouse >= 3 and "controls monitor wall 5, -60, 0" in game_log(),
          f"keys={keys} chars={chars} mouse={mouse}")
    call("sneak", {"on": True})
    time.sleep(0.4)
    right_click()
    call("sneak", {"on": False})
    check("9d Shift+right-click from afar opens the settings instead",
          call("status")["screen"].endswith("MonitorScreen"))
    call("close")

    # ---- 9e-f a web wall, from across the room (the spare page from 8c; 7b still wants example.com)
    web = (-7, -59, 0)
    call("cmd", {"command": "tp @s -6.5 -60 3.5 180 20"})
    time.sleep(0.5)
    pin_page(web, base + "/click")
    wait_for(lambda: be(web).get("page_ready") and "/click" in be(web).get("page_url", ""), 60, 1)
    call("cmd", {"command": "tp @s -6.5 -60 12.5 180 0"})
    time.sleep(1)
    call("aim", {"x": -6.5, "y": -58.5, "z": 1.0})
    time.sleep(0.5)
    right_click()
    r = call("remote")
    web_ok = r.get("active") and r.get("web")
    left_click()
    clicked = wait_for(lambda: "/typed" in be(web).get("page_url", ""), 15, 0.5)
    time.sleep(1.5)
    raw_line("omo")
    typed = wait_for(lambda: be(web).get("page_url", "").endswith("#t=omo"), 10, 0.5)
    shot("t9-web-control")
    check("9e right-click takes a web wall; a click follows a link and typing fills its input",
          web_ok and clicked and typed, f"{r} url={be(web).get('page_url')}")
    call("rawkey", {"key": 256})
    time.sleep(0.4)
    call("aim", {"x": -6.5, "y": -58.5, "z": 1.0})
    time.sleep(0.4)
    for _ in range(3):
        call("rawscroll", {"dy": -1})
        time.sleep(0.2)
    scrolled = wait_for(lambda: re.search(r"#y=[1-9]", be(web).get("page_url", "")), 10, 0.5)
    check("9f without taking control, the wheel scrolls the page under the crosshair",
          scrolled and not call("remote").get("active"), be(web).get("page_url"))

    # ---- 9g-i a web wall is opaque glass, day and night, and hidden behind blocks
    glass_ys = (-60, -59)
    build_wall((-12, -11), glass_ys, backing="lime_concrete")
    pin_page((-12, -60, 0), base + "/glass")
    wait_for(lambda: be((-12, -60, 0)).get("page_ready"), 60, 1)
    time.sleep(2)
    call("cmd", {"command": "tp @s -11 -60 3.5 180 0"})
    call("aim", {"x": -11.0, "y": -59.0, "z": 1.0})
    time.sleep(1.5)
    shot("t9-glass-day")
    day = pixels("t9-glass-day")
    call("cmd", {"command": "time set midnight"})
    time.sleep(2)
    shot("t9-glass-night")
    night = pixels("t9-glass-night")
    call("cmd", {"command": "fill -12 -60 2 -11 -59 2 stone"})
    time.sleep(1.5)
    shot("t9-glass-behind-stone")
    behind = pixels("t9-glass-behind-stone")
    call("cmd", {"command": "fill -12 -60 2 -11 -59 2 air"})
    call("cmd", {"command": "time set day"})
    if day is None:
        skip("9g-i opacity", "Pillow is not installed")
    else:
        box = (0.36, 0.3, 0.64, 0.45)                  # glass above the crosshair and the hint
        (dl, dlime), (nl, nlime), (sl, slime) = box_stats(day, *box), box_stats(night, *box), box_stats(behind, *box)
        check("9g a web wall's glass is opaque: nothing of the lime wall behind it shows",
              dlime < 0.01 and dl > 200, f"lime={dlime:.3f} luminance={dl:.0f}")
        check("9h at night the page stays lit and opaque", nlime < 0.01 and nl > 200,
              f"lime={nlime:.3f} luminance={nl:.0f}")
        check("9i behind a stone wall the page is hidden", sl < 160 and slime < 0.01, f"luminance={sl:.0f}")

    # ---- 9j the wall grows: the same page (same load stamp) moves and widens, never reloads
    first = be((-12, -60, 0))
    stamp = re.search(r"#L\d+", first.get("page_url", ""))
    build_wall((-13,), glass_ys, backing="lime_concrete", stand="tp @s -11 -60 3.5 180 0")
    grown = wait_for(lambda: (lambda d: d if d.get("page_ready") and d.get("width") == 3 else None)(be((-13, -60, 0))),
                     20, 1) or be((-13, -60, 0))
    check("9j a web wall that grows keeps its page: moved to the new origin, wider, not reloaded",
          stamp and stamp.group(0) in grown.get("page_url", "") and grown.get("page_w", 0) > first.get("page_w", 0)
          and grown.get("url") == first.get("url"),
          f"{first.get('page_url')} {first.get('page_w')}px -> {grown.get('page_url')} {grown.get('page_w')}px "
          f"pages={call('pages')['count']}")
    shot("t9-grown")
    pages = call("pages")["count"]
    check("9k still no page accumulation", pages <= 5, f"pages={pages}")
    call("cmd", {"command": "tp @s 0.5 -60 3.5 180 20"})


def cleanup(before):
    """(v) Delete every workspace this run created, then stop the test stack."""
    if os.environ.get("OMO_TEST_KEEP"):
        print("cleanup: kept (OMO_TEST_KEEP set)")
        return
    try:
        created = [w["id"] for w in testenv.api("list")["items"] if w["id"] not in before]
        for ws_id in created:
            testenv.api("delete", ws=ws_id, force="1")
        left = [w["id"] for w in testenv.api("list")["items"] if w["id"] not in before]
        print(f"cleanup: deleted {len(created) - len(left)}/{len(created)} test workspaces" + (f", left {left}" if left else ""))
    except OSError as exc:
        print(f"cleanup: bridge unreachable, workspaces not deleted ({exc})")
    testenv.down()


def main():
    testenv.up()
    # The isolated test bridge only: start from no workspaces, so leftovers of
    # earlier runs or manual testing can't hit its workspace cap.
    for w in testenv.api("list")["items"]:
        testenv.api("delete", ws=w["id"], force=1)
    before = set()
    print(f"test stack: bridge 127.0.0.1:{testenv.PORT}, tmux -L {testenv.SOCKET}, data {testenv.DATA}; "
          f"hermes steps {'ON' if HERMES else 'OFF'}", flush=True)
    try:
        run({})
    except Exception as exc:                        # a crash is a failure, not a silent stop
        check(f"run aborted: {exc!r}", False)
    finally:
        cleanup(before)
    failed = [name for name, ok in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed" + (f"; failed: {failed}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
