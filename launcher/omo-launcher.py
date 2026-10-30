#!/usr/bin/env python3
"""Omo Space: the one window a player sees. Install, sign in, play.

Deliberately thin: every action is a call into omo_setup.py (a subprocess
whose output lines drive the window), which does the real work and can be
run on its own. This file is the front door: the omo.sh look, honest
readiness, one obvious next step, and getting out of the way while the game
runs (the window steps aside when Minecraft's opens, and comes back when
the game quits).

Falls back to a terminal menu when there is no display or no GTK.
"""
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import omo_setup as core  # noqa: E402  (paths, the game log marker, running-game check)

SETUP = HERE / "omo_setup.py"
ASSETS = HERE / "assets"
PLAY_LOG = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "omo" / "play.log"
LAUNCHED = "launching: "            # omo_setup.py play prints this right before it starts the game
PRISM_PATIENCE = 120                # seconds a headless Prism may take before we show its window


# --------------------------------------------------------------------------- shared logic (tested without a display)
def stop_tree(proc):
    """End a child and everything it started: its process group on Linux and
    macOS, `taskkill /T` on Windows (no process groups there)."""
    if core.WIN:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    else:
        os.killpg(proc.pid, signal.SIGTERM)


def wants_gui(argv, env, os_name):
    """GTK window when there is a desktop for it; the terminal menu otherwise.
    Linux needs an X or Wayland display; macOS and Windows always have one
    (the window falls back to the menu if GTK is not installed)."""
    if "--tui" in argv:
        return False
    return os_name != "linux" or bool(env.get("DISPLAY") or env.get("WAYLAND_DISPLAY"))

def status():
    p = subprocess.run([sys.executable, str(SETUP), "status"], capture_output=True, text=True)
    try:
        return json.loads(p.stdout)
    except ValueError:
        return {"error": (p.stdout + p.stderr).strip()[-400:] or "status failed"}


def readiness(s):
    """Three honest lines (name, state, text), state ok | warn | missing, and
    whether Play can work. Omo counts as ready only when the bridge answers
    AND its session server is up (status reports that as bridge_problem None)."""
    game_ok = bool(s.get("installed"))
    problem = s.get("bridge_problem", "status unavailable")
    omo_ok = bool(s.get("omolife") and s.get("tmux") and not problem)
    h = s.get("hermes", {})
    runs = (f"Runs {h['model']}" + (f" via {h['provider']}" if h.get("provider") else "") if h.get("model")
            else "Installed; uses the model set in its own settings")
    lines = [
        ("Game", "ok" if game_ok else "missing",
         "Minecraft 26.2, Fabric, CC: Tweaked, MCEF and the Omo mod" if game_ok
         else "Not installed yet."),
        ("Omo", "ok" if omo_ok else ("warn" if s.get("omolife") else "missing"),
         "Bridge and session server are running" if omo_ok
         else (f"Not ready: {problem}. Repair starts it." if s.get("omolife") else "Installed with the game.")),
        ("Hermes", "ok" if h.get("installed") else "warn",
         runs if h.get("installed") else "Not found. Terminals work without it; Hermes Computers need it "
                                         "(hermes-agent.nousresearch.com)."),
    ]
    return lines, bool(game_ok and omo_ok)


def page_for(s):
    """Which page a status belongs on."""
    if "error" in s:
        return "home"
    if not s.get("installed"):
        return "welcome"
    if s.get("running"):
        return "playing"
    account = s.get("account", {})
    if account.get("mode") == "omo" and not account.get("signed_in"):
        return "signin"
    return "home"


PLAN = re.compile(r"^== plan: (.+)$")
STEP = re.compile(r"^== step (\d+)/(\d+): (.+)$")
PERCENT = re.compile(r"^\s*(\d+(?:\.\d+)?)%\s+(.*)$")
WORD = re.compile(r"^\s*(verified|ok|installed|reused|retry|BAD HASH)\s+(.*?)(?: \(/.*\))?$")
CODE = re.compile(r"^signin: code (\S+) (\S+)")
WORDS = {"verified": "Verified", "ok": "Already here", "installed": "Installed", "reused": "Reused",
         "retry": "Retrying", "BAD HASH": "Bad download, fetching again"}


def parse_line(line):
    """One line of omo_setup.py output, as an event for the window: the plan
    of steps, a step starting, a percentage, a file done, a sign-in code."""
    line = line.rstrip("\n")
    if m := PLAN.match(line):
        return "plan", [t.strip() for t in m.group(1).split("|")]
    if m := STEP.match(line):
        return "step", int(m.group(1)), int(m.group(2)), m.group(3)
    if m := PERCENT.match(line):
        return "percent", float(m.group(1)) / 100, m.group(2).strip()
    if m := CODE.match(line):
        return "code", m.group(1), m.group(2)
    if m := WORD.match(line):
        return "did", f"{WORDS[m.group(1)]}: {m.group(2).strip()}"
    return "info", line.strip()


def read_from(path, offset):
    try:
        with open(path, "rb") as fh:
            fh.seek(offset)
            return fh.read().decode(errors="replace")
    except OSError:
        return ""


class PlayWatch:
    """Follows one Play. poll() says where it is: ("preflight",), ("launched",),
    ("window",) once the game's own log says its window exists, ("over", exit
    code) when the game has quit, or ("failed", reason) if it never got going.

    `proc` is `omo_setup.py play` (which becomes Java or the private Prism);
    `running()` says whether the game's Java is up. Once the window has been
    seen, the game ending is what counts: a Prism left behind is the
    caller's to close. One step per call, so no state is ever skipped."""

    def __init__(self, proc, play_log, offset, game_log, running):
        self.proc, self.play_log, self.offset, self.game_log, self.running = proc, play_log, offset, game_log, running
        self.started, self.state = time.time(), ("preflight",)

    def poll(self):
        code, now = self.proc.poll(), self.state[0]
        if now == "preflight" and LAUNCHED in read_from(self.play_log, self.offset):
            self.state = ("launched",)
        elif now == "launched" and self.window_up():
            self.state = ("window",)
        elif now == "window" and not self.running():
            self.state = ("over", code or 0)
        elif now in ("preflight", "launched") and code is not None and not self.running():
            if now == "launched":
                self.state = ("over", 0) if code == 0 else \
                    ("failed", f"Minecraft stopped before its window opened (exit code {code}).")
            else:
                tail = [ln.strip() for ln in read_from(self.play_log, self.offset).splitlines() if ln.strip()]
                self.state = ("failed", tail[-1][:240] if tail else f"Play stopped (exit code {code}).")
        return self.state

    def window_up(self):
        try:
            return (self.game_log.stat().st_mtime >= self.started - 1
                    and core.WINDOW_UP in self.game_log.read_text(errors="replace"))
        except OSError:
            return False


def load_css(provider, css):
    """Gtk.CssProvider's loader changed shape across GTK 4 (load_from_string
    is 4.12+; load_from_data took bytes, then bytes + length, then a string in
    PyGObject's overrides), so try each until one takes it."""
    if hasattr(provider, "load_from_string"):
        return provider.load_from_string(css)
    for args in ((css,), (css.encode(),), (css, -1), (css.encode(), -1)):
        try:
            return provider.load_from_data(*args)
        except TypeError:
            continue
    raise TypeError("no Gtk.CssProvider loader accepted the stylesheet")


def register_fonts(fontmap, files):
    """The launcher's bundled fonts. Pango 1.56+ adds them to the font map
    directly; older Pango (Ubuntu 24.04 has 1.52) goes through fontconfig.
    Without either the window uses system fonts and still works."""
    if hasattr(fontmap, "add_font_file"):
        for f in files:
            fontmap.add_font_file(str(f))
        return
    try:
        import ctypes
        import ctypes.util
        fc = ctypes.CDLL(ctypes.util.find_library("fontconfig") or "libfontconfig.so.1")
        fc.FcConfigAppFontAddFile.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        for f in files:
            fc.FcConfigAppFontAddFile(None, str(f).encode())
        if hasattr(fontmap, "config_changed"):
            fontmap.config_changed()
    except (OSError, AttributeError):
        pass


TERMINALS = (                                   # (binary, args before the command)
    ("x-terminal-emulator", ["-e"]), ("ptyxis", ["--"]), ("kgx", ["--"]), ("gnome-terminal", ["--"]),
    ("konsole", ["-e"]), ("xfce4-terminal", ["-x"]), ("alacritty", ["-e"]), ("kitty", []), ("xterm", ["-e"]),
)


def terminal_command(argv, which=shutil.which):
    """A command that runs argv in a new terminal window, or None."""
    for name, pre in TERMINALS:
        exe = which(name)
        if exe:
            return [exe, *pre, *argv]
    return None


def fallback(reason, interactive=None):
    """The window can't open. In a terminal: the menu, right here. Started from
    the app menu there is no terminal, so the menu would be invisible and the
    app would seem to do nothing: open it in a terminal window, or say why."""
    print(f"(the Omo Space window can't open: {reason}; using the terminal menu)", flush=True)
    if interactive is None:
        interactive = sys.stdin.isatty()
    if interactive:
        return tui()
    cmd = terminal_command([sys.executable, str(Path(__file__).resolve()), "--tui"])
    if cmd:
        subprocess.Popen(cmd, start_new_session=True)
        return None
    note = shutil.which("notify-send")
    if note:
        subprocess.run([note, "Omo Space", f"The window can't open ({reason}). "
                        "Run omo-space in a terminal for the menu."], check=False)
    return None


# --------------------------------------------------------------------------- terminal
PRISM_SIGNIN = ("Choose 2) Play: a Microsoft sign-in window from Prism Launcher, the engine Omo "
                "uses to start Minecraft for now, opens once. Sign in there; Omo remembers it.")


def account_line(account):
    """(state, text) for the terminal menu's Account line. Until Omo has its own
    Microsoft client ID, sign-in happens in Prism's window on the first Play,
    so "4) Sign in" cannot help yet and the menu has to say what will."""
    if account.get("signed_in"):
        return "ok", f"Signed in as {account.get('name')}"
    if account.get("mode") == "prism":
        return "warn", "Not signed in yet. " + PRISM_SIGNIN
    return "warn", "Not signed in. Choose 4) Sign in."


def tui():
    while True:
        s = status()
        lines, _ = readiness(s)
        account = s.get("account", {})
        print("\n  Omo Space\n")
        for name, state, text in [*lines, ("Account", *account_line(account))]:
            print(f"  [{'ok' if state == 'ok' else '--'}] {name:7s} {text}")
        print("\n  1) Set up   2) Play   3) Repair   4) Sign in   5) Sign out   q) Quit")
        try:
            c = input("  > ").strip().lower()
        except EOFError:
            return
        if c in ("q", "quit"):
            return
        op = {"1": "setup", "2": "play", "3": "repair", "4": "signin", "5": "signout"}.get(c)
        if op == "signin" and account.get("mode") == "prism":
            print(f"\n  {PRISM_SIGNIN}")         # not a failure: there is nothing to run yet
            continue
        if op == "play":
            os.execvp(sys.executable, [sys.executable, str(SETUP), "play"])
        if op:
            code = subprocess.run([sys.executable, str(SETUP), op]).returncode
            print(f"\n  {op}: {'done' if code == 0 else f'FAILED (exit {code}), see the last line above'}")


# --------------------------------------------------------------------------- window
# The reel: short clips of the worlds, played behind everything. assets/reel/reel.json
# lists them ({"file", "title", "place"}); with no reel (or no GStreamer) the stage
# falls back to slow warm light, so the window never depends on the clips.
REEL = Path(os.environ.get("OMO_REEL") or ASSETS / "reel")
SHOT_FADE = 2.4                     # seconds two shots overlap
SHOT_SECONDS = 9.0                  # a shot's length unless reel.json says otherwise
INTRO = 5.2                         # the title sequence; the window opens once it and status are done
OPEN = 2.2                          # seconds for the focus pull from the titles into the world
FILM_IN = 0.8                       # the first shot starts under the titles, dark and out of focus
FRAME_MARGIN = 24                   # transparent room around the card for its shadow


# Four looks for the same window, picked in the dev panel (F12) and remembered in
# ~/.config/omo-space/launcher.json. Every look shows the same pages and buttons.
LOOKS = {
    "cinema":  "Full-bleed film, words and Play bottom-left",
    "marquee": "Widescreen bars, everything centred like a title card",
    "panel":   "A solid side panel with the film beside it",
    "paper":   "omo.sh paper: light card, the film framed like a photograph",
}
DEFAULT_LOOK = "cinema"
SETTINGS = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "omo-space" / "launcher.json"


def load_look(path=SETTINGS, env=os.environ):
    """OMO_LOOK wins, then the saved choice, then the default; unknown names fall back."""
    name = env.get("OMO_LOOK")
    if not name:
        try:
            name = json.loads(path.read_text()).get("look")
        except (OSError, ValueError, AttributeError):
            name = None
    return name if name in LOOKS else DEFAULT_LOOK


def save_look(name, path=SETTINGS):
    if name not in LOOKS:
        raise ValueError(f"unknown look {name!r}")
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    data["look"] = name
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(path)


# Fake setup output for the dev panel: the same lines omo_setup.py prints, so the real
# parser and progress code run (nothing is installed).
FAKE_SETUP = ["== plan: Session server | Java | Minecraft | Fabric | CC: Tweaked | MCEF | Omo mod"] + [
    line for i, (name, mb) in enumerate([("Session server", 4), ("Java", 48), ("Minecraft", 480), ("Fabric", 6),
                                          ("CC: Tweaked", 3), ("MCEF", 110), ("Omo mod", 2)], 1)
    for line in [f"== step {i}/7: {name}"] + [f"   {p:.1f}%  {name} ({mb * p / 100:.0f} of {mb} MB)"
                                               for p in (12.5, 37.5, 62.5, 87.5, 100.0)] + [f"  verified  {name}"]]


# -- launching, in the window: what Prism and the game print, as a few clean steps -----------
LAUNCH_STEPS = [                    # (pattern in play.log or the game log, progress, what the window says)
    (re.compile(r"bridge: .*starting services"), 0.06, "Waking Omo up"),
    (re.compile(r"^launching: "), 0.12, "Starting Minecraft"),
    (re.compile(r"account refresh|Refreshing account|Writing account list"), 0.20, "Signing you in"),
    (re.compile(r"downloading libraries|asset index|reconstructAssets"), 0.30, "Checking the game files"),
    (re.compile(r"Loading Minecraft \S+ with Fabric"), 0.45, "Starting Java"),
    (re.compile(r"Loading (\d+) mods"), 0.60, "Loading {0} mods"),
    (re.compile(r"Reloading ResourceManager|Backend library:"), 0.90, "Opening the window"),
]
PRISM_PREFIX = re.compile(r"^\s*\d+\.\d+ (?:Debug|Info|Warning|Critical|Fatal): ")
GAME_PREFIX = re.compile(r"^\[\d\d:\d\d:\d\d\] \[[^\]]+/\w+\]: ")


def clean_line(line):
    """A log line as a person would want to read it, or None for noise (classpaths, arg dumps)."""
    line = PRISM_PREFIX.sub("", line.strip())
    line = GAME_PREFIX.sub("", line).replace(" (unknown:0)", "").replace('"', "").strip()
    if not line or len(line) > 220 or ".jar:" in line or line.startswith(("-D", "-X", "Env:")):
        return None
    return line


def launch_progress(play_text, game_text=""):
    """(progress 0..1, step title, latest readable line) for one launch so far."""
    frac, title, last = 0.03, "Getting ready", ""
    for text in (play_text, game_text):
        for raw in text.splitlines():
            for pattern, f, words in LAUNCH_STEPS:
                m = pattern.search(raw)
                if m and f >= frac:
                    frac, title = f, words.format(*m.groups())
            line = clean_line(raw)
            if line:
                last = line
    return frac, title, last


SECRET = [
    (re.compile(r"(?i)\b(access_?token|refresh_?token|client_?secret|password|authorization|bearer|token)"
                r"(\W{1,3})([^\s\"',]+)"), r"\1\2<removed>"),
    (re.compile(r"eyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]{8,}"), "<removed>"),
    (re.compile(r"\b[A-Za-z0-9+/_-]{48,}={0,2}\b"), "<removed>"),
]


def redact(text):
    """Strip anything that looks like a credential before a report leaves the machine."""
    for pattern, sub in SECRET:
        text = pattern.sub(sub, text)
    return text


def tail_lines(text, n):
    lines = [ln for ln in text.splitlines() if ln.strip() and ".jar:" not in ln]
    return "\n".join(ln[:400] for ln in lines[-n:])


def error_report(what, reason, output="", game_log="", crash=None, when=None):
    """One block of text to paste to an agent: what failed, why, and the logs it needs."""
    parts = [
        "Omo Space: something went wrong. Please help me fix it.",
        f"what: {what}",
        f"reason: {reason}",
        f"launcher: {core.MANIFEST.get('version', '?')} on {core.OS}"
        f"   time: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(when or time.time()))}",
        f"game dir: {core.GAME}",
    ]
    if output.strip():
        parts += ["", "--- launcher / Prism output (last lines) ---", tail_lines(output, 60)]
    if game_log.strip():
        parts += ["", "--- game log, logs/latest.log (last lines) ---", tail_lines(game_log, 80)]
    if crash:
        path, text = crash
        parts += ["", f"--- crash report: {path} (first lines) ---", "\n".join(text.splitlines()[:70])]
    return redact("\n".join(parts)).rstrip() + "\n"


def newest_crash(since, game=None):
    """(path, text) of a crash report or JVM hs_err file written after `since`, else None."""
    game = Path(game or core.GAME)
    found = []
    for pattern in ("crash-reports/*.txt", "hs_err_pid*.log"):
        for f in game.glob(pattern):
            try:
                if f.stat().st_mtime >= since - 1:
                    found.append((f.stat().st_mtime, f))
            except OSError:
                pass
    if not found:
        return None
    f = max(found)[1]
    try:
        return str(f), f.read_text(errors="replace")
    except OSError:
        return None


# -- community and plugins -------------------------------------------------------------------
# The invite comes from the manifest ("discord"), or OMO_DISCORD for testing; until there is
# one the button says the invite is on its way instead of opening a wrong server.
DISCORD_URL = os.environ.get("OMO_DISCORD") or core.MANIFEST.get("discord", "")
CATALOGUE = Path(os.environ.get("OMO_PLUGINS") or ASSETS / "plugins.json")
CATALOGUE_SLOTS = 15                # how many places the coming-soon catalogue shows


def load_catalogue(path=CATALOGUE):
    """Plugin entries ({id, name, tagline, category, author, status}); [] until there are any."""
    try:
        items = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return []
    if not isinstance(items, list):
        return []
    return [it for it in items if isinstance(it, dict) and it.get("id") and it.get("name")]


SAMPLE_CATALOGUE = [               # dev panel only: how a filled catalogue will look (not real plugins)
    {"id": f"sample-{i}", "name": n, "tagline": t, "category": c, "author": "sample", "status": "soon"}
    for i, (n, t, c) in enumerate([
        ("Sample plugin", "What a plugin card looks like once the catalogue opens.", "World"),
        ("Another sample", "A one-line description sits here, two lines at most.", "Agents"),
        ("Third sample", "Cards keep the same size whatever the text.", "Tools"),
        ("Fourth sample", "Install is one click; remove is one click.", "Builds"),
        ("Fifth sample", "Categories filter the grid.", "Media"),
        ("Sixth sample", "Authors get credited on every card.", "World"),
    ], 1)]


def load_reel(folder=REEL):
    """The clips that exist, in reel.json order."""
    try:
        items = json.loads((folder / "reel.json").read_text())
    except (OSError, ValueError):
        return []
    return [dict(it, path=folder / it["file"]) for it in items if (folder / it.get("file", "")).is_file()]


def ease(x):
    """Ease in-out (cubic), clamped."""
    x = min(max(x, 0.0), 1.0)
    return 4 * x * x * x if x < 0.5 else 1 - (-2 * x + 2) ** 3 / 2


def span(t, start, length):
    """0..1 progress of t through [start, start+length]."""
    return min(max((t - start) / length, 0.0), 1.0)


# The omo.sh mark as strokes (from assets/omo-mark.svg), so the load-in can draw it on.
MARK_STROKES = [
    (8, "M20 20V180M20 20H100M20 180H180M100 20H130M140 20H160M180 20V60M180 72V110M180 120V150M180 160V180"),
    (3, "M32 32V168M32 32H100M32 168H168M105 32H125M138 32H155M168 32V65M168 75V105M168 115V140M168 150V168"),
    (7, "M62 100A30 30 0 0 1 100 70A30 30 0 0 1 100 90A30 30 0 0 1 125 108A30 30 0 0 1 138 130"
        "M100 90A30 30 0 0 0 62 100"),
]
MARK_BITS = [(130, 110, 8, 15, 3), (142, 100, 6, -10, 2), (148, 118, 7, 25, 2.5), (138, 128, 5, -20, 2),
             (152, 108, 5, 8, 1.5)]          # the fracturing squares: x, y, size, rotation, stroke


def square_path(x, y, s, deg):
    import math
    cx, cy, a = x + s / 2, y + s / 2, math.radians(deg)
    pts = [(cx + dx * math.cos(a) - dy * math.sin(a), cy + dx * math.sin(a) + dy * math.cos(a))
           for dx, dy in ((-s / 2, -s / 2), (s / 2, -s / 2), (s / 2, s / 2), (-s / 2, s / 2))]
    return "M" + "L".join(f"{px:.2f} {py:.2f}" for px, py in pts) + "Z"


CSS = """
/* omo.sh, after dark: the warm paper palette as light on film. Crimson Text for words,
   IBM Plex Mono for labels. One clean card with rounded corners and a soft shadow;
   the window around it is transparent so the shadow falls on the desktop. */
window.omo, window.omo.background { background: transparent; box-shadow: none; border: none; }
window.omo label { font-family: "Crimson Text", serif; color: #f5f0e8; }
window.omo .mono, window.omo button, window.omo button label, window.omo .o-kicker, window.omo .o-meta,
window.omo .o-code, window.omo .o-chip, window.omo .o-brand { font-family: "IBM Plex Mono", monospace; }

.o-frame { background-color: #0e0a07; border-radius: 18px;
           box-shadow: 0 22px 60px rgba(0,0,0,0.55), 0 0 0 1px rgba(245,240,232,0.09); }

.o-brand { font-size: 11px; font-weight: 500; letter-spacing: 3.5px; color: #f5f0e8; }
.o-title { font-weight: 600; font-size: 76px; letter-spacing: -1.5px; color: #fbf7f0;
           text-shadow: 0 2px 24px rgba(10,6,3,0.45); }
.o-tagline { font-style: italic; font-size: 22px; color: rgba(245,240,232,0.82);
             text-shadow: 0 1px 12px rgba(10,6,3,0.6); }
.o-heading { font-weight: 600; font-size: 30px; letter-spacing: -0.4px; }
.o-body { font-size: 18px; color: rgba(245,240,232,0.86); text-shadow: 0 1px 10px rgba(10,6,3,0.6); }
.o-note { font-style: italic; font-size: 15px; color: rgba(245,240,232,0.62); }
.o-kicker { font-size: 10.5px; font-weight: 500; letter-spacing: 3px; color: rgba(245,240,232,0.65); }
.o-kicker.warm { color: #e8c48f; }
.o-meta { font-size: 11.5px; color: rgba(245,240,232,0.65); }
.o-percent { font-weight: 600; font-size: 88px; letter-spacing: -3px; color: #fbf7f0; }
.o-code { font-size: 40px; font-weight: 500; letter-spacing: 9px; color: #fbf7f0; }
.o-place { font-style: italic; font-size: 21px; color: #fbf7f0; text-shadow: 0 1px 10px rgba(10,6,3,0.7); }
.o-chip { font-size: 10.5px; font-weight: 500; letter-spacing: 2px; color: rgba(245,240,232,0.78); }
.o-chip.dim, label.dim { color: rgba(245,240,232,0.42); }
.o-ws-name { font-size: 17px; font-weight: 600; }
.o-row-text { font-size: 16px; color: rgba(245,240,232,0.82); }

.o-glass { background-color: rgba(16,11,7,0.86); border: 1px solid rgba(245,240,232,0.10); border-radius: 14px; }
.o-glass.pad { padding: 18px 22px; }
.o-row { padding: 11px 18px; }
.o-row + .o-row { border-top: 1px solid rgba(245,240,232,0.07); }
.o-problem { background-color: rgba(110,40,24,0.88); border: 1px solid rgba(226,184,162,0.30);
             border-radius: 12px; padding: 12px 16px; }
.o-problem label { color: #fbeee6; }

/* one button system: primary (cream), secondary (outline), link, pill. Same height for
   primary and secondary; never stretched; quiet caps in Plex Mono. */
button.o-play, button.o-ghost { min-height: 0; min-width: 0; border-radius: 10px; padding: 11px 26px;
                                font-size: 12px; font-weight: 600; letter-spacing: 2.4px; box-shadow: none;
                                transition: background-color 160ms ease, border-color 160ms ease, box-shadow 160ms ease; }
button.o-play { background: #f5f0e8; color: #1a1008; border: 1px solid #f5f0e8;
                box-shadow: 0 6px 20px rgba(0,0,0,0.28); }
button.o-play label { color: #1a1008; }
button.o-play:hover { background: #ffffff; border-color: #ffffff; box-shadow: 0 8px 26px rgba(232,196,143,0.30); }
button.o-play:active { background: #e9e1d4; }
button.o-play:focus-visible, button.o-ghost:focus-visible { outline: 2px solid #e8c48f; outline-offset: 2px; }
button.o-play:disabled { background: rgba(245,240,232,0.18); border-color: transparent; box-shadow: none; }
button.o-play:disabled label { color: rgba(245,240,232,0.55); }
button.o-play.hero { padding: 13px 46px; font-size: 13px; letter-spacing: 3.5px; }
button.o-ghost { background: rgba(16,11,7,0.40); color: #f5f0e8; border: 1px solid rgba(245,240,232,0.30);
                 font-weight: 500; }
button.o-ghost label { color: #f5f0e8; }
button.o-ghost:hover { border-color: rgba(245,240,232,0.75); background: rgba(16,11,7,0.65); }
button.o-ghost:disabled { border-color: rgba(245,240,232,0.14); background: transparent; }
button.o-ghost:disabled label { color: rgba(245,240,232,0.40); }
button.o-ghost.small, button.o-play.small { padding: 5px 12px; border-radius: 8px; font-size: 10px;
                                            letter-spacing: 1.8px; box-shadow: none; }
button.o-link { background: none; border: none; box-shadow: none; padding: 4px 6px; min-height: 0;
                font-size: 10.5px; letter-spacing: 2px; }
button.o-link label { color: rgba(245,240,232,0.55); }
button.o-link:hover label { color: #f5f0e8; }
button.o-win { background: none; border: none; box-shadow: none; min-height: 0; min-width: 0; padding: 6px 10px;
               border-radius: 999px; }
button.o-win label { font-family: "IBM Plex Mono", monospace; font-size: 13px; color: rgba(245,240,232,0.6); }
button.o-win:hover { background: rgba(245,240,232,0.12); }
button.o-win:hover label { color: #f5f0e8; }

.o-dot { min-width: 7px; min-height: 7px; border-radius: 999px; background-color: rgba(245,240,232,0.3);
         transition: background-color 500ms ease; }
.o-dot.ok, .o-dot.live { background-color: #8fd19e; }
.o-dot.warn { background-color: #e8c48f; }
.o-dot.missing, .o-dot.closed { background-color: rgba(245,240,232,0.3); }
.o-dot.interrupted { background-color: #e07a6c; }
.o-dot.now { background-color: #f5f0e8; }
.o-tick { min-height: 2px; min-width: 18px; border-radius: 2px; background-color: rgba(245,240,232,0.25);
          transition: all 600ms ease; }
.o-tick.on { background-color: #f5f0e8; min-width: 36px; }

progressbar trough { background-color: rgba(245,240,232,0.16); border-radius: 2px; min-height: 3px; border: none; }
progressbar progress { background-color: #f5f0e8; border-radius: 2px; min-height: 3px; border: none; }
.o-log, .o-log text { background-color: transparent; color: #e6dccb; font-family: "IBM Plex Mono", monospace;
                      font-size: 11px; }
window.omo toast { background-color: rgba(16,11,7,0.92); color: #f5f0e8; border-radius: 12px; margin-bottom: 26px;
                   border: 1px solid rgba(245,240,232,0.15); }
window.omo toast label { font-family: "IBM Plex Mono", monospace; font-size: 12px; }

/* panel look: a solid ink side panel */
.o-panelbg { background-color: #0e0a07; }
window.look-panel .o-title { font-size: 58px; }
window.look-panel .o-tagline { font-size: 19px; }

/* marquee look: a title card */
window.look-marquee .o-title { font-size: 84px; letter-spacing: -2px; }

/* paper look: omo.sh warm paper, ink type, the film framed like a photograph */
window.look-paper .o-frame { background-color: #f5f0e8; }
window.look-paper label { color: #1a1008; text-shadow: none; }
window.look-paper .o-title { font-size: 64px; color: #1a1008; }
window.look-paper .o-tagline, window.look-paper .o-body, window.look-paper .o-row-text { color: #4a3b2f; }
window.look-paper .o-note, window.look-paper .o-meta { color: #8a7865; }
window.look-paper .o-kicker, window.look-paper .o-chip { color: #8a7865; }
window.look-paper .o-kicker.warm { color: #9a6a2a; }
window.look-paper .o-brand { color: #1a1008; }
window.look-paper .o-place { color: #1a1008; text-shadow: none; font-size: 19px; }
window.look-paper button.o-play { background: #1a1008; box-shadow: 0 6px 18px rgba(26,16,8,0.18); }
window.look-paper button.o-play label { color: #f5f0e8; }
window.look-paper button.o-play:hover { background: #2c2015; }
window.look-paper button.o-ghost { background: transparent; border-color: #d4cdc0; }
window.look-paper button.o-ghost label { color: #1a1008; }
window.look-paper button.o-ghost:hover { border-color: #1a1008; }
window.look-paper button.o-link label, window.look-paper button.o-win label { color: #8a7865; }
window.look-paper button.o-win:hover { background: rgba(26,16,8,0.06); }
window.look-paper .o-glass { background-color: #faf7f2; border-color: #d4cdc0; }
window.look-paper .o-tick { background-color: rgba(26,16,8,0.18); }
window.look-paper .o-tick.on { background-color: #1a1008; }
window.look-paper progressbar trough { background-color: #e4ddd1; }
window.look-paper progressbar progress { background-color: #1a1008; }
window.look-paper .o-log, window.look-paper .o-log text { color: #1a1008; }

/* pages with more to say (install, sign-in, first run) get a smaller title, in every look */
window.omo.compact .o-title { font-size: 54px; letter-spacing: -1px; }
window.omo.compact .o-tagline { font-size: 18px; }

.o-kicker.bad { color: #f0a08f; }

/* top-bar pills: plugins and the community */
button.o-pill { background: rgba(16,11,7,0.40); border: 1px solid rgba(245,240,232,0.22); border-radius: 999px;
                padding: 4px 13px; min-height: 0; min-width: 0; box-shadow: none; transition: all 160ms ease; }
button.o-pill label { font-family: "IBM Plex Mono", monospace; font-size: 10px; font-weight: 500;
                      letter-spacing: 2px; color: #f5f0e8; }
button.o-pill:hover, button.o-pill.on { background: rgba(245,240,232,0.14); border-color: rgba(245,240,232,0.6); }
button.o-pill.accent { background: rgba(232,196,143,0.16); border-color: rgba(232,196,143,0.55); }
button.o-pill.accent label { color: #f3d7a8; }
button.o-pill.accent:hover { background: rgba(232,196,143,0.28); border-color: #e8c48f; }
window.look-paper button.o-pill { background: transparent; border-color: #d4cdc0; }
window.look-paper button.o-pill label { color: #1a1008; }
window.look-paper button.o-pill.accent { background: #1a1008; border-color: #1a1008; }
window.look-paper button.o-pill.accent label { color: #f5f0e8; }

/* the plugin catalogue */
.o-plugins { background-color: rgba(11,7,4,0.90); padding: 96px 56px 48px 64px; }
window.look-paper .o-plugins { background-color: #f5f0e8; }
.o-title.cat { font-size: 64px; }
window.omo label.o-soon { font-family: "IBM Plex Mono", monospace; font-size: 10px; font-weight: 500; letter-spacing: 2.5px;
          color: #1a1008; background-color: #e8c48f; border-radius: 999px; padding: 4px 12px; margin-top: 6px; }
.o-cat-scroll, .o-cat-scroll > viewport { background: transparent; }
.o-slot { border: 1px dashed rgba(245,240,232,0.16); border-radius: 14px; padding: 16px 18px;
          background-color: rgba(245,240,232,0.025);
          animation: breathe 3.2s ease-in-out infinite alternate; }
.o-slot.d1 { animation-delay: 0.4s; } .o-slot.d2 { animation-delay: 0.8s; } .o-slot.d3 { animation-delay: 1.2s; }
.o-slot.d4 { animation-delay: 1.6s; }
@keyframes breathe { from { opacity: 0.45; } to { opacity: 1.0; } }
.o-slot-num { font-family: "Crimson Text", serif; font-size: 30px; font-weight: 600; color: rgba(245,240,232,0.28); }
.o-skel { min-height: 6px; border-radius: 3px; background-color: rgba(245,240,232,0.10); }
.o-skel.long { margin-right: 20px; } .o-skel.short { margin-right: 90px; }
window.look-paper .o-slot { border-color: rgba(26,16,8,0.18); background-color: rgba(26,16,8,0.02); }
window.look-paper .o-slot-num { color: rgba(26,16,8,0.25); }
window.look-paper .o-skel { background-color: rgba(26,16,8,0.08); }
.o-plugin { border: 1px solid rgba(245,240,232,0.12); border-radius: 14px; padding: 16px 18px;
            background-color: rgba(245,240,232,0.05); transition: all 180ms ease; }
.o-plugin:hover { border-color: rgba(232,196,143,0.5); background-color: rgba(245,240,232,0.08); }
.o-card-name { font-family: "Crimson Text", serif; font-size: 21px; font-weight: 600; }
.o-card-text { font-family: "Crimson Text", serif; font-size: 15px; color: rgba(245,240,232,0.7); }
window.look-paper .o-plugin { border-color: #d4cdc0; background-color: #faf7f2; }
window.look-paper .o-card-text { color: #4a3b2f; }
window.look-paper .o-kicker.bad { color: #a8432e; }
.mono { font-family: "IBM Plex Mono", monospace; }

/* the dev panel */
window.omo-dev { background-color: #15100b; }
window.omo-dev label { color: #e6dccb; }
.d-body { background-color: #15100b; padding: 4px 18px 18px 18px; }
window.omo-dev .d-head { font-family: "IBM Plex Mono", monospace; font-size: 10.5px; font-weight: 500; letter-spacing: 3px;
          color: #e8c48f; }
window.omo-dev .d-note { font-family: "Crimson Text", serif; font-style: italic; font-size: 14px; color: #a89886; }
window.omo-dev .d-stats { font-family: "IBM Plex Mono", monospace; font-size: 11px; color: #e6dccb; }
button.d-btn { background: #241a12; color: #f5f0e8; border: 1px solid #3a2c20; border-radius: 8px; padding: 6px 8px;
               box-shadow: none; }
button.d-btn label { font-family: "IBM Plex Mono", monospace; font-size: 11px; color: #f5f0e8; }
button.d-btn:hover { border-color: #e8c48f; }
button.d-btn:checked { background: #f5f0e8; }
button.d-btn:checked label { color: #1a1008; }
"""


def gui():
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    gi.require_version("Gsk", "4.0")
    gi.require_version("Graphene", "1.0")
    gi.require_version("PangoCairo", "1.0")
    from gi.repository import Adw, Gdk, GLib, Graphene, Gsk, Gtk, Pango, PangoCairo
    try:
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst
        Gst.init(None)
        if not Gst.ElementFactory.find("gtk4paintablesink"):
            Gst = None
    except (ImportError, ValueError):
        Gst = None

    # Older GTK/libadwaita/Pango (Ubuntu 24.04: libadwaita 1.5, Pango 1.52;
    # 22.04: libadwaita 1.1, GTK 4.6) lack some calls this window uses. Each
    # has a fallback here, so the window opens on them instead of crashing.
    register_fonts(PangoCairo.FontMap.get_default(), sorted((ASSETS / "fonts").glob("*.ttf")))

    def spinner(size):
        s = Adw.Spinner() if hasattr(Adw, "Spinner") else Gtk.Spinner(spinning=True)   # Adw.Spinner: 1.6+
        s.set_size_request(size, size)
        return s

    def open_uri(window, uri):
        if hasattr(Gtk, "UriLauncher"):                # GTK 4.10+
            Gtk.UriLauncher.new(uri).launch(window, None, None, None)
        else:
            Gtk.show_uri(window, uri, Gdk.CURRENT_TIME)

    def clear(container):
        while child := container.get_first_child():
            container.remove(child)

    def ui_soon(fn, *args):
        """Run on the GTK thread. Default priority, not idle: the stage redraws every frame and
        would starve idle callbacks (status, setup output) on a slow renderer."""
        return GLib.idle_add(fn, *args, priority=GLib.PRIORITY_DEFAULT)

    def rgba(spec, alpha=None):
        c = Gdk.RGBA()
        c.parse(spec)
        if alpha is not None:
            c.alpha = alpha
        return c

    def stops(*pairs):
        out = []
        for offset, color in pairs:
            s = Gsk.ColorStop()
            s.offset, s.color = offset, color
            out.append(s)
        return out

    def rect(x, y, w, h):
        return Graphene.Rect().init(x, y, w, h)

    def point(x, y):
        return Graphene.Point().init(x, y)

    INK, CREAM = rgba("#0e0a07"), rgba("#f5f0e8")

    # -- the reel: two pipelines, so one shot can dissolve into the next ------------------
    class Deck:
        """One playbin drawing into a GdkPaintable, muted (the clips have no sound)."""

        def __init__(self, on_frame):
            self.sink = Gst.ElementFactory.make("gtk4paintablesink", None)
            self.bin = Gst.ElementFactory.make("playbin", None)
            self.bin.set_property("video-sink", self.sink)
            self.bin.set_property("volume", 0.0)
            self.paintable = self.sink.get_property("paintable")
            self.paintable.connect("invalidate-contents", lambda *_: on_frame())
            self.clip = None

        def load(self, clip):
            self.bin.set_state(Gst.State.NULL)
            self.clip = clip
            self.bin.set_property("uri", Gst.filename_to_uri(str(clip["path"])))
            self.bin.set_state(Gst.State.PAUSED)          # preroll: the first frame is ready before it shows

        def play(self):
            self.bin.set_state(Gst.State.PLAYING)

        def pause(self):
            if self.clip:
                self.bin.set_state(Gst.State.PAUSED)

        def stop(self):
            self.bin.set_state(Gst.State.NULL)

    class Stage(Gtk.Widget):
        """Everything behind the controls: the reel, the grade, the edge fade into the
        desktop, and the load-in (the mark drawing itself, then the world opening up)."""

        def __init__(self, reel, on_shot):
            super().__init__(hexpand=True, vexpand=True)
            self.reel, self.on_shot = reel, on_shot
            self.t0 = None                       # first frame time (s)
            self.now = 0.0
            self.intro_done_at = None            # when the curtain started to lift
            self.ready_to_open = False           # status has arrived
            self.intro_skipped = False
            self.decks, self.front, self.shot, self.shot_t = [], 0, -1, 0.0
            self.look = DEFAULT_LOOK
            self.frames, self.fps = [], 0.0          # for the dev panel
            self.mix = 1.0                       # how much of the front deck shows (0..1 during a dissolve)
            if Gst and reel:
                try:
                    self.decks = [Deck(self.queue_draw), Deck(self.queue_draw)]
                    self.decks[0].load(reel[0])
                except (TypeError, AttributeError, GLib.Error):
                    self.decks = []
            self.mark = self.build_mark()
            self.add_tick_callback(self.tick)

        # paths for the mark; None if this GTK has no GskPath (then the intro fades the word in alone)
        def build_mark(self):
            try:
                paths = [(w, Gsk.Path.parse(d)) for w, d in MARK_STROKES]
                paths += [(sw, Gsk.Path.parse(square_path(x, y, s, deg))) for x, y, s, deg, sw in MARK_BITS]
                return [(w, p, Gsk.PathMeasure.new(p)) for w, p in paths]
            except (AttributeError, TypeError, GLib.Error):
                return None

        # -- time ----------------------------------------------------------------
        def tick(self, _w, clock):
            now = clock.get_frame_time() / 1e6
            if self.t0 is None:
                self.t0 = now
            self.now = now - self.t0
            self.frames = [f for f in self.frames if now - f < 1.0] + [now]
            self.fps = len(self.frames)
            if self.shot < 0 and (self.intro_skipped or self.now >= FILM_IN):
                self.start_reel()
            if self.intro_done_at is None and (self.intro_skipped or (self.now >= INTRO and self.ready_to_open)):
                self.intro_done_at = self.now
            self.advance_reel()
            self.queue_draw()
            return GLib.SOURCE_CONTINUE

        def opened(self):
            """0..1, how far the curtain has lifted."""
            return 0.0 if self.intro_done_at is None else ease(span(self.now, self.intro_done_at, OPEN))

        def start_reel(self):
            if self.decks:
                self.shot, self.shot_t, self.front, self.mix = 0, self.now, 0, 1.0
                self.decks[0].play()
                self.on_shot(0)
                if len(self.reel) > 1:
                    self.decks[1].load(self.reel[1 % len(self.reel)])

        def shot_length(self, i):
            return float(self.reel[i % len(self.reel)].get("seconds", SHOT_SECONDS))

        def advance_reel(self):
            if not self.decks or self.shot < 0 or len(self.reel) < 2:
                return
            into = self.now - self.shot_t
            length = self.shot_length(self.shot)
            if into >= length - SHOT_FADE and self.mix == 1.0:
                self.mix = 0.999                                     # the dissolve starts: the back deck rolls
                self.decks[1 - self.front].play()
                self.on_shot((self.shot + 1) % len(self.reel))
            if self.mix < 1.0:
                self.mix = 1.0 - ease(span(into, length - SHOT_FADE, SHOT_FADE))
                if into >= length:                                   # the back deck is now the front
                    old = self.front
                    self.front, self.mix = 1 - old, 1.0
                    self.shot, self.shot_t = (self.shot + 1) % len(self.reel), self.now - (into - length) - SHOT_FADE
                    self.decks[old].load(self.reel[(self.shot + 1) % len(self.reel)])

        def set_paused(self, paused):
            for i, d in enumerate(self.decks):
                if paused:
                    d.pause()
                elif self.shot >= 0 and (i == self.front or self.mix < 1.0):
                    d.play()

        # -- drawing ----------------------------------------------------------------
        def do_snapshot(self, s):
            w, h = self.get_width(), self.get_height()
            if w <= 0 or h <= 0:
                return
            self.draw_world(s, w, h)
            self.draw_bars(s, w, h)
            opened = self.opened()
            if opened < 1:
                s.push_opacity(1 - ease(span(opened, 0.15, 0.85)))
                try:
                    self.draw_title_layer(s, w, h, opened)
                finally:
                    s.pop()

        PANEL_W = 560

        def film_rect(self, w, h):
            """Where the film plays, per look: (x, y, width, height, corner radius)."""
            if self.look == "panel":
                return self.PANEL_W - 40, 0, w - self.PANEL_W + 40, h, 0
            if self.look == "paper":
                fw = w * 0.47
                fh = fw * 9 / 16
                return w - fw - 56, (h - fh) / 2 - 12, fw, fh, 14
            return 0, 0, w, h, 0

        def draw_world(self, s, w, h):
            s.append_color(rgba("#f5f0e8") if self.look == "paper" else INK, rect(0, 0, w, h))
            fx, fy, fw, fh, radius = self.film_rect(w, h)
            s.save()
            if radius:
                rr = Gsk.RoundedRect()
                rr.init_from_rect(rect(fx, fy, fw, fh), radius)
                s.push_rounded_clip(rr)
            else:
                s.push_clip(rect(fx, fy, fw, fh))
            s.translate(point(fx, fy))
            self.draw_film(s, fw, fh)
            s.pop()
            s.restore()
            if self.look == "paper":
                return
            if self.look == "panel":                 # the film melts into the panel's edge
                x0 = self.PANEL_W - 40
                s.append_linear_gradient(rect(x0, 0, 200, h), point(x0, 0), point(x0 + 200, 0), stops(
                    (0, rgba("#0e0a07", 1)), (0.25, rgba("#0e0a07", 0.75)), (1, rgba("#0e0a07", 0))))
                s.append_linear_gradient(rect(0, 0, w, h), point(0, h), point(0, h * 0.6), stops(
                    (0, rgba("#0b0704", 0.6)), (1, rgba("#0b0704", 0))))
                return
            if self.look == "marquee":               # even and central: a soft centre pool of shadow
                s.append_radial_gradient(rect(0, 0, w, h), point(w / 2, h * 0.56), w * 0.42, h * 0.42, 0.0, 1.0, stops(
                    (0, rgba("#0b0704", 0.55)), (1, rgba("#0b0704", 0))))
                s.append_radial_gradient(rect(0, 0, w, h), point(w / 2, h / 2), w * 0.8, h * 0.9, 0.0, 1.0, stops(
                    (0.6, rgba("#000", 0)), (1, rgba("#000", 0.35))))
                return
            # cinema: a clean floor of shadow under the words, a soft top for the bar, a light vignette
            s.append_linear_gradient(rect(0, 0, w, h), point(0, h), point(0, h * 0.30), stops(
                (0, rgba("#0b0704", 0.86)), (0.5, rgba("#0b0704", 0.42)), (1, rgba("#0b0704", 0))))
            s.append_linear_gradient(rect(0, 0, w, h), point(0, 0), point(w * 0.58, 0), stops(
                (0, rgba("#0b0704", 0.50)), (1, rgba("#0b0704", 0))))
            s.append_linear_gradient(rect(0, 0, w, 120), point(0, 0), point(0, 120), stops(
                (0, rgba("#0b0704", 0.45)), (1, rgba("#0b0704", 0))))
            s.append_radial_gradient(rect(0, 0, w, h), point(w / 2, h / 2), w * 0.8, h * 0.9, 0.0, 1.0, stops(
                (0.6, rgba("#000", 0)), (1, rgba("#000", 0.30))))

        def draw_film(self, s, w, h):
            opened = self.opened()
            if self.decks and self.shot >= 0:
                front, back = self.decks[self.front], self.decks[1 - self.front]
                if self.mix < 1.0:
                    self.draw_deck(s, back, w, h, 1.0, self.now - (self.shot_t + self.shot_length(self.shot) - SHOT_FADE),
                                   self.shot + 1)
                self.draw_deck(s, front, w, h, self.mix, self.now - self.shot_t, self.shot, push=1.0 - opened)
            else:
                s.append_color(INK, rect(0, 0, w, h))
                self.draw_light(s, w, h)

        def bar_height(self, h, w):
            """The marquee look keeps a 2.39:1 frame once the titles are done."""
            rest = max((h - w / 2.39) / 2, 0) if self.look == "marquee" else 0.0
            return rest * self.opened()

        def draw_bars(self, s, w, h):
            b = self.bar_height(h, w)
            if b <= 0.5:
                return
            s.append_color(INK, rect(0, 0, w, b))
            s.append_color(INK, rect(0, h - b, w, b))

        def draw_deck(self, s, deck, w, h, alpha, into, index, push=0.0):
            p = deck.paintable
            iw, ih = p.get_intrinsic_width(), p.get_intrinsic_height()
            if iw <= 0 or ih <= 0 or alpha <= 0:
                return
            # cover the stage, then a slow push-in and drift (alternating directions per shot)
            base = max(w / iw, h / ih)
            k = min(max(into, 0) / (SHOT_SECONDS + SHOT_FADE), 1.0)
            zoom = 1.05 + 0.06 * k + 0.12 * ease(push)
            dw, dh = iw * base * zoom, ih * base * zoom
            drift = (1 if index % 2 else -1) * 0.022 * w * (k - 0.5)
            s.push_opacity(alpha)
            s.save()
            s.translate(point((w - dw) / 2 + drift, (h - dh) / 2))
            p.snapshot(s, dw, dh)
            s.restore()
            s.pop()

        def draw_light(self, s, w, h):
            """No reel: two warm lights drifting over ink."""
            import math
            t = self.now
            for i, (col, a) in enumerate((("#e8a35a", 0.30), ("#7a5236", 0.40))):
                cx = w * (0.35 + 0.25 * math.sin(t * 0.09 + i * 2.1))
                cy = h * (0.45 + 0.2 * math.cos(t * 0.07 + i * 1.3))
                s.append_radial_gradient(rect(0, 0, w, h), point(cx, cy), w * 0.55, h * 0.75, 0.0, 1.0, stops(
                    (0, rgba(col, a)), (1, rgba(col, 0))))

        def draw_title_layer(self, s, w, h, opened):
            """The title sequence, over the first shot (dark, out of focus, in a 2.39:1 frame):
            0.3  OMO RESEARCH, small and tracked, then it gives way
            0.8  the first shot fades up under a veil, deeply out of focus
            1.6  the mark draws itself
            2.3  Omo Space resolves letter by letter, each one coming out of blur
            3.5  a glint of light passes across the title
            3.7  a hairline grows under it (it finishes when the launcher is ready), the tagline
            then the focus pulls into the world: blur and veil fall away, the frame opens,
            the titles lift and dissolve."""
            t = self.now
            pull = ease(opened)
            s.append_color(INK, rect(0, 0, w, h))
            film = ease(span(t, FILM_IN, 2.0))
            if film > 0:
                radius = 30.0 * (1 - pull)
                s.push_opacity(film)
                if radius > 0.5:
                    s.push_blur(radius)
                self.draw_film(s, w, h)
                if radius > 0.5:
                    s.pop()
                s.pop()
                veil = 0.72 * (1 - pull)
                s.append_color(rgba("#0b0704", veil), rect(0, 0, w, h))
                s.append_radial_gradient(rect(0, 0, w, h), point(w / 2, h * 0.5), w * 0.55, h * 0.75, 0.0, 1.0, stops(
                    (0.35, rgba("#000", 0)), (1, rgba("#000", 0.6 * (1 - pull)))))
            bar = max((h - w / 2.39) / 2, 0) * (1 - pull)
            if bar > 0.5:
                s.append_color(INK, rect(0, 0, w, bar))
                s.append_color(INK, rect(0, h - bar, w, bar))

            lift = -22 * pull                                       # the titles rise as they leave
            s.save()
            s.translate(point(0, lift))

            def layout(text, font, spacing=0.0):
                lay = self.create_pango_layout(text)
                lay.set_font_description(Pango.FontDescription.from_string(font))
                if spacing:
                    attrs = Pango.AttrList()
                    attrs.insert(Pango.attr_letter_spacing_new(int(spacing * Pango.SCALE)))
                    lay.set_attributes(attrs)
                return lay

            def centred(lay, y, color, alpha, spacing=0.0):
                tw, th = lay.get_pixel_size()
                if alpha > 0:
                    s.save()
                    s.translate(point((w - tw) / 2 + spacing / 2, y))
                    s.append_layout(lay, rgba(color, alpha))
                    s.restore()
                return tw, th

            # 1. the company line
            card = ease(span(t, 0.3, 0.7)) * (1 - ease(span(t, 1.5, 0.6)))
            centred(layout("OMO RESEARCH", "IBM Plex Mono Medium 9.5", 8), h / 2 - 8, "#f5f0e8", 0.75 * card, 8)

            # 2. the mark, drawn on
            size = 64.0
            ox, oy = (w - size) / 2, h * 0.5 - 150
            if self.mark:
                s.save()
                s.translate(point(ox, oy))
                s.scale(size / 200.0, size / 200.0)
                for i, (width, path, measure) in enumerate(self.mark):
                    grow = ease(span(t, 1.6 + i * 0.1, 1.1 if i < 3 else 0.35))
                    if grow <= 0:
                        continue
                    stroke = Gsk.Stroke.new(width)
                    if grow < 1:                         # draw-on: one dash as long as the path, slid into view
                        length = measure.get_length() + 1
                        stroke.set_dash([length, length])
                        stroke.set_dash_offset(length * (1 - grow))
                    s.append_stroke(path, stroke, rgba("#f5f0e8", 0.92))
                s.append_color(rgba("#e8c48f", ease(span(t, 2.5, 0.4))), rect(95, 95, 10, 10))
                s.restore()

            # 3. the title, letter by letter out of blur
            word = "Omo Space"
            title = layout(word, "Crimson Text Semi-Bold 92", -1.5)
            tw, th = title.get_pixel_size()
            tx, ty = (w - tw) / 2, h * 0.5 - 70
            for i, ch in enumerate(word):
                if ch == " ":
                    continue
                k = ease(span(t, 2.3 + i * 0.075, 1.0))
                if k <= 0:
                    continue
                pos = title.index_to_pos(i)
                one = layout(ch, "Crimson Text Semi-Bold 92")
                blur = 10.0 * (1 - k)
                s.save()
                s.translate(point(tx + pos.x / Pango.SCALE, ty + 14 * (1 - k)))
                if blur > 0.3:
                    s.push_blur(blur)
                s.append_layout(one, rgba("#fbf7f0", k))
                if blur > 0.3:
                    s.pop()
                s.restore()
            # the glint: a warm band of light crossing the finished title once
            g = span(t, 3.5, 1.1)
            if 0 < g < 1 and hasattr(s, "push_mask"):           # GTK 4.10+; older ones skip the glint
                band = tx - 120 + (tw + 240) * ease(g)
                s.push_mask(Gsk.MaskMode.ALPHA)
                s.append_linear_gradient(rect(tx - 140, ty, tw + 280, th), point(band - 90, ty), point(band + 90, ty),
                                         stops((0, rgba("#000", 0)), (0.5, rgba("#000", 0.85)), (1, rgba("#000", 0))))
                s.pop()
                s.save()
                s.translate(point(tx, ty))
                s.append_layout(title, rgba("#ffe7b8"))
                s.restore()
                s.pop()

            # 4. the hairline (it completes once the launcher is ready) and the tagline
            line_y = ty + th + 18
            grow = ease(span(t, 3.7, 0.9)) * (1.0 if self.ready_to_open else 0.85)
            lw = 220.0 * grow
            if lw > 0:
                s.append_linear_gradient(rect((w - lw) / 2, line_y, lw, 1), point((w - lw) / 2, 0), point((w + lw) / 2, 0),
                                         stops((0, rgba("#e8c48f", 0)), (0.5, rgba("#e8c48f", 0.9)), (1, rgba("#e8c48f", 0))))
            centred(layout("THE NEXT INTERFACE ISN'T A CHAT WINDOW", "IBM Plex Mono 9", 4.5), line_y + 22, "#f5f0e8",
                    0.6 * ease(span(t, 4.0, 1.0)), 4.5)
            s.restore()

    class Mark(Gtk.Widget):
        """The omo.sh mark in cream, for dark backgrounds (the SVG asset is ink-coloured)."""

        def __init__(self, size):
            super().__init__(valign=Gtk.Align.CENTER)
            self.size, self.ink = size, False
            self.set_size_request(size, size)
            try:
                self.paths = [(w, Gsk.Path.parse(d)) for w, d in MARK_STROKES]
                self.paths += [(sw, Gsk.Path.parse(square_path(x, y, s, deg))) for x, y, s, deg, sw in MARK_BITS]
            except (AttributeError, TypeError, GLib.Error):
                self.paths = []

        def do_snapshot(self, s):
            k = self.size / 200.0
            s.save()
            s.scale(k, k)
            for width, path in self.paths:
                s.append_stroke(path, Gsk.Stroke.new(width), rgba("#1a1008") if self.ink else rgba("#f5f0e8", 0.95))
            s.append_color(rgba("#1a1008") if self.ink else rgba("#e8c48f"), rect(95, 95, 10, 10))
            s.restore()

    # -- widgets ---------------------------------------------------------------------------
    def label(text="", *classes, wrap=True, xalign=0.0, chars=60, **kw):
        w = Gtk.Label(label=text, xalign=xalign, wrap=wrap, **kw)
        if wrap:
            w.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            w.set_max_width_chars(chars)
        for c in classes:
            w.add_css_class(c)
        return w

    def button(text, style, callback):
        b = Gtk.Button(label=text)
        b.add_css_class(style)
        b.connect("clicked", lambda *_: callback())
        return b

    def box(vertical=True, spacing=0, *children, css=(), **kw):
        b = Gtk.Box(orientation=Gtk.Orientation.VERTICAL if vertical else Gtk.Orientation.HORIZONTAL,
                    spacing=spacing, **kw)
        for c in children:
            b.append(c)
        for c in css:
            b.add_css_class(c)
        return b

    def dot(state=None):
        return box(False, css=["o-dot", state] if state else ["o-dot"], valign=Gtk.Align.CENTER)

    def set_state(widget, state, states=("ok", "warn", "missing", "now", "live", "closed", "interrupted")):
        for c in states:
            widget.remove_css_class(c)
        if state:
            widget.add_css_class(state)

    class Window(Adw.ApplicationWindow):
        def __init__(self, app, intro=True):
            super().__init__(application=app, title="Omo Space")
            self.set_default_size(1280 + 2 * FRAME_MARGIN, 720 + 2 * FRAME_MARGIN)
            self.set_resizable(False)
            self.set_decorated(False)
            self.add_css_class("omo")
            self.app, self.status, self.proc, self.busy = app, {}, None, None
            self.hidden_for_game = self.cancelled = False
            self.step_total = None
            self.reel = load_reel()

            self.stage = Stage(self.reel, self.on_shot)
            if not intro:
                self.stage.intro_skipped = True

            # -- top bar: mark and name on the left; the player and the window buttons on the right
            self.account_label = label("", "o-chip", wrap=False)
            self.account_box = box(False, 10, dot("ok"), self.account_label,
                                   button("SIGN OUT", "o-link", self.sign_out), valign=Gtk.Align.CENTER)
            version = label(core.MANIFEST["version"], "o-chip", "dim", wrap=False)
            self.mark = Mark(22)
            self.plugins_btn = button("PLUGINS", "o-pill", self.show_plugins)
            self.discord_btn = button("JOIN DISCORD", "o-pill", self.open_discord)
            self.discord_btn.add_css_class("accent")
            top = self.top = box(False, 12,
                      self.mark, label("OMO SPACE", "o-brand", wrap=False), version,
                      box(False, hexpand=True),
                      box(False, 8, self.plugins_btn, self.discord_btn, valign=Gtk.Align.CENTER, margin_end=10),
                      self.account_box,
                      box(False, 2, button("—", "o-win", self.minimize), button("✕", "o-win", self.close),
                          margin_start=14),
                      valign=Gtk.Align.START, margin_start=64, margin_end=40, margin_top=26)

            # -- bottom left: the words, the page, the buttons, and readiness under them
            self.title = label("Omo Space", "o-title", wrap=False)
            self.tagline = label("The next interface isn't a chat window.", "o-tagline", wrap=False)
            self.problem_text = label("", "o-body", chars=52)
            self.problem = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_UP, child=box(
                True, 4, label("SOMETHING NEEDS A LOOK", "o-kicker"), self.problem_text, css=["o-problem"],
                margin_top=14))
            self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=320,
                                   vhomogeneous=False, interpolate_size=True)
            self.bar_stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=300,
                                       hhomogeneous=False, vhomogeneous=False)
            for name, (page, buttons) in (("loading", (box(), [])), ("welcome", self.welcome_page()),
                                          ("install", self.install_page()), ("signin", self.signin_page()),
                                          ("home", self.home_page()), ("starting", self.starting_page()),
                                          ("playing", self.playing_page()), ("failed", self.failed_page())):
                self.stack.add_named(page, name)
                self.bar_stack.add_named(box(False, 12, *buttons), name)
            self.chips = []
            chips = box(False, 20, valign=Gtk.Align.CENTER)
            for _ in range(3):
                d, n = dot(), label("", "o-chip", wrap=False)
                chip = box(False, 8, d, n)
                chips.append(chip)
                self.chips.append((chip, d, n))
            details = button("DETAILS", "o-link", self.toggle_details)
            details.set_valign(Gtk.Align.CENTER)
            self.chips_box = box(False, 20, chips, label("·", "o-chip", "dim", wrap=False))
            self.status_row = box(False, 20, self.chips_box, details, margin_top=22)
            self.left = box(True, 0, self.title, self.tagline, self.problem,
                            box(True, 0, self.stack, margin_top=20),
                            box(False, 14, self.bar_stack, margin_top=24),
                            self.status_row)
            # the clamp keeps the words to a readable width whatever the look
            self.left_clamp = Adw.Clamp(child=self.left, maximum_size=600, tightening_threshold=600)

            # -- right: workspaces up top, the reel's caption at the bottom
            self.ws = self.workspaces()
            self.caption = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=900,
                                     hhomogeneous=False, vhomogeneous=False)
            self.caption_flip = 0
            self.ticks = box(False, 6, halign=Gtk.Align.END)
            for _ in self.reel:
                self.ticks.append(box(False, css=["o-tick"], valign=Gtk.Align.CENTER))
            self.reel_box = box(True, 10, self.caption, self.ticks, halign=Gtk.Align.END, valign=Gtk.Align.END,
                                margin_end=56, margin_bottom=60, visible=bool(self.reel))

            # -- details: the raw output, on a solid card, when asked
            self.log = Gtk.TextView(editable=False, monospace=True, cursor_visible=False)
            self.log.add_css_class("o-log")
            self.log.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
            buf = self.log.get_buffer()
            self.log_end = buf.create_mark("end", buf.get_end_iter(), False)
            logs = Gtk.ScrolledWindow(child=self.log, hscrollbar_policy=Gtk.PolicyType.NEVER)
            logs.set_size_request(470, 300)
            self.log_rev = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.CROSSFADE, transition_duration=250,
                                        child=box(True, 10, label("DETAILS", "o-kicker"), logs, css=["o-glass", "pad"]),
                                        halign=Gtk.Align.END, valign=Gtk.Align.END, margin_end=56, margin_bottom=56)

            # the panel look's solid side panel (hidden in the other looks)
            self.panel_bg = box(True, 0, css=["o-panelbg"], halign=Gtk.Align.START, visible=False)
            self.panel_bg.set_size_request(Stage.PANEL_W - 40, -1)

            self.ui = Gtk.Overlay(child=box(True, 0))
            self.plugins_view = self.build_plugins()
            for w in (self.left_clamp, self.ws[0], self.reel_box, self.log_rev, self.plugins_view, top):
                self.ui.add_overlay(w)
            self.ui.set_opacity(0.0)
            self.ui.set_can_target(False)
            self.toasts = Adw.ToastOverlay(child=self.ui)

            # the card: the stage with everything on it, rounded, with a soft shadow into the desktop
            card = Gtk.Overlay(child=self.stage)
            card.add_overlay(self.panel_bg)
            card.add_overlay(self.toasts)
            card.add_css_class("o-frame")
            card.set_overflow(Gtk.Overflow.HIDDEN)
            for side in ("top", "bottom", "start", "end"):
                getattr(card, f"set_margin_{side}")(FRAME_MARGIN)
            self.set_content(Gtk.WindowHandle(child=card))

            keys = Gtk.EventControllerKey()
            keys.connect("key-pressed", self.on_key)
            self.add_controller(keys)
            click = Gtk.GestureClick()
            click.connect("pressed", lambda *_: self.skip_intro())
            self.add_controller(click)
            self.connect("notify::visible", lambda *_: self.stage.set_paused(not self.get_visible()))
            self.connect("notify::is-active", lambda *_: None)
            self.dev = None
            self.left_bottom = 56
            self.apply_look(load_look())
            self.show_page("loading")
            self.refresh()
            GLib.timeout_add_seconds(5, self.tick)
            GLib.timeout_add(50, self.watch_intro)
            GLib.timeout_add(33, self.ease_launch_bar)
            if "--dev" in sys.argv or os.environ.get("OMO_DEV"):
                GLib.timeout_add(300, lambda: self.toggle_dev() and False)

        # -- looks -------------------------------------------------------------------------
        def apply_look(self, name):
            """Place the same widgets for one of the four looks (see LOOKS)."""
            self.look = name
            for n in LOOKS:
                self.remove_css_class(f"look-{n}")
            self.add_css_class(f"look-{name}")
            self.stage.look = name
            self.stage.queue_draw()
            self.mark.ink = name == "paper"
            self.mark.queue_draw()
            self.panel_bg.set_visible(name == "panel")
            A = Gtk.Align
            center = name == "marquee"

            def place(w, h, v, start=0, end=0, top=0, bottom=0):
                w.set_halign(h)
                w.set_valign(v)
                w.set_margin_start(start)
                w.set_margin_end(end)
                w.set_margin_top(top)
                w.set_margin_bottom(bottom)

            clamp = self.left_clamp
            if name == "cinema":
                place(clamp, A.START, A.END, start=64, bottom=56)
                place(self.reel_box, A.END, A.END, end=56, bottom=60)
                place(self.ws[0], A.END, A.START, end=56, top=78)
                width = 580
            elif name == "marquee":
                place(clamp, A.CENTER, A.CENTER, top=40)
                place(self.reel_box, A.CENTER, A.END, bottom=30)
                width = 660
            elif name == "panel":
                place(clamp, A.START, A.CENTER, start=56, top=30)
                place(self.reel_box, A.END, A.END, end=48, bottom=52)
                place(self.ws[0], A.END, A.START, end=48, top=78)
                width = Stage.PANEL_W - 110
            else:                                                   # paper: the film framed on the right
                fx, fy, fw, fh, _ = self.stage.film_rect(1280, 720)
                place(clamp, A.START, A.CENTER, start=64, top=20)
                place(self.reel_box, A.END, A.START, end=56, top=int(fy + fh + 18))
                width = int(fx - 64 - 48)
            clamp.set_maximum_size(width)
            clamp.set_tightening_threshold(width)
            self.left_bottom = clamp.get_margin_bottom()
            self.center_text(self.left, center)
            for w in (self.bar_stack.get_parent(), self.status_row):
                w.set_halign(A.CENTER if center else A.START)
            self.chips_box.set_visible(name in ("cinema", "marquee"))     # the others list readiness in full
            self.ticks.set_halign(A.CENTER if center else A.END)
            self.show_page(self.stack.get_visible_child_name() or "loading")
            if self.status:
                self.apply_status(self.status, look_only=True)

        def center_text(self, widget, on):
            """Centre (or left-align) every label under widget, for the title-card look."""
            child = widget.get_first_child()
            while child is not None:
                if isinstance(child, Gtk.Label):
                    child.set_xalign(0.5 if on else 0.0)
                    child.set_justify(Gtk.Justification.CENTER if on else Gtk.Justification.LEFT)
                elif not isinstance(child, (Gtk.Button, Gtk.TextView)):
                    self.center_text(child, on)
                child = child.get_next_sibling()

        # -- the dev panel (F12): looks, pages, fake progress, film controls, live numbers ------
        def toggle_dev(self):
            if self.dev is None:
                self.dev = DevPanel(self)
            self.dev.set_visible(not self.dev.get_visible())
            if self.dev.get_visible():
                self.dev.present()

        def dev_page(self, name):
            self.busy = "dev"
            if name == "problem":
                self.show_page("home")
                self.show_problem("Example: the session server (omo-tmux) is not running. Repair starts it.")
            elif name == "failed":
                reason = "cannot play: the session server (omo-tmux) is not running"
                self.show_failure("Minecraft didn't start", reason,
                                  self.play_report("Play failed before the game window opened (dev panel example)",
                                                   reason), self.dev_live)
            elif name == "starting":
                self.show_page("starting")
                self.launch_target = 0.0
                self.launch_bar.set_fraction(0.0)
                fake = ["launching: Minecraft (headless Prism)",
                        "     4.812 Debug: Writing account list to \"accounts.json\" (unknown:0)",
                        "     4.921 Debug: \"Omo Space\" : downloading libraries (unknown:0)",
                        "[06:13:53] [main/INFO]: Loading Minecraft 26.2 with Fabric Loader 0.19.5",
                        "[06:13:53] [main/INFO]: Loading 90 mods:"]

                def feed(k=[0]):
                    if self.stack.get_visible_child_name() != "starting" or k[0] >= len(fake):
                        return False
                    k[0] += 1
                    self.update_launch(launch_progress("\n".join(fake[:k[0]])))
                    return True
                GLib.timeout_add(1300, feed)
            elif name == "signin":
                self.show_page("signin")
                self.on_event(("code", "OMO-7K2Q", "https://www.microsoft.com/link"))
            else:
                self.show_problem("")
                self.show_page(name)

        def dev_live(self):
            self.busy = None
            self.show_problem("")
            self.show_page("loading")
            self.code_rev.set_reveal_child(False)
            self.signin_btn.set_visible(True)
            self.signin_how.set_visible(True)
            self.cancel_btn.set_visible(False)
            self.refresh()

        def dev_fake_setup(self):
            """Feed the real progress code the lines omo_setup.py prints; nothing is installed."""
            self.run_fake = list(FAKE_SETUP)
            self.busy, self.step_total = "setup", None
            self.show_problem("")
            self.show_plan([])
            self.step_kicker.set_label("LOOKING AT WHAT IS ALREADY HERE")
            self.percent.set_label("0%")
            self.bar.set_fraction(0)
            self.install_note.set_label("Dev panel: a pretend install. Nothing is downloaded.")
            self.show_page("install")

            def feed():
                if not self.run_fake:
                    self.finished(0, ["done"])
                    return False
                line = self.run_fake.pop(0)
                self.append(line + "\n")
                self.on_event(parse_line(line))
                return True
            GLib.timeout_add(140, feed)

        def replay_intro(self):
            st = self.stage
            for d in st.decks:
                d.stop()
            if st.decks:
                st.decks[0].load(self.reel[0])
            st.t0, st.intro_done_at, st.intro_skipped = None, None, False
            st.front, st.shot, st.mix = 0, -1, 1.0
            st.frames = []
            self.ui.set_opacity(0.0)
            self.ui.set_can_target(False)
            GLib.timeout_add(50, self.watch_intro)

        def next_shot(self):
            st = self.stage
            if st.shot >= 0 and st.mix == 1.0 and len(self.reel) > 1:
                st.shot_t = st.now - (st.shot_length(st.shot) - SHOT_FADE)

        # -- the load-in ends: the controls rise into place ---------------------------------
        def skip_intro(self):
            if self.stage.intro_done_at is None:
                self.stage.intro_skipped = True

        def watch_intro(self):
            if self.stage.intro_done_at is None:
                return True
            self.ui.set_can_target(True)
            target = Adw.CallbackAnimationTarget.new(self.rise)
            self.rise_anim = Adw.TimedAnimation.new(self.ui, 0.0, 1.0, 1800, target)
            self.rise_anim.set_easing(Adw.Easing.EASE_OUT_CUBIC)
            GLib.timeout_add(int(OPEN * 650), lambda: self.rise_anim.play() and False)
            return False

        def rise(self, v):
            self.ui.set_opacity(v)
            self.left_clamp.set_margin_bottom(int(self.left_bottom - 24 * (1 - v)))
            self.title.set_opacity(min(1.0, v * 1.4))

        def on_shot(self, i):
            clip = self.reel[i]
            flip = f"c{self.caption_flip}"
            self.caption_flip ^= 1
            old = self.caption.get_child_by_name(flip)
            if old:
                self.caption.remove(old)
            self.caption.add_named(box(True, 2, label(f"{i + 1:02d}  —  {len(self.reel):02d}", "o-kicker", "warm",
                                                      wrap=False, xalign=1.0),
                                       label(clip.get("title", ""), "o-place", wrap=False, xalign=1.0),
                                       label(clip.get("place", "").upper(), "o-chip", wrap=False, xalign=1.0)), flip)
            self.caption.set_visible_child_name(flip)
            for n, t in enumerate(self.ticks):
                (t.add_css_class if n == i else t.remove_css_class)("on")

        # -- pages: (content, buttons) ---------------------------------------------------------
        def welcome_page(self):
            self.install_btn = button("INSTALL", "o-play", lambda: self.run("setup"))
            self.size_text = label("", "o-body", chars=52)
            self.worlds_text = label("", "o-row-text")
            self.worlds_card = box(True, 4, label("YOUR WORLDS COME WITH YOU", "o-kicker"), self.worlds_text,
                                   label("Copied from Prism. The originals stay exactly where they are.", "o-note"),
                                   css=["o-glass", "pad"])
            page = box(True, 14, label("FIRST RUN", "o-kicker", "warm"), self.size_text, self.worlds_card,
                       label("Minecraft is a trademark of Mojang. Omo is not affiliated with Mojang or Microsoft; "
                             "you play with your own Minecraft account.", "o-note", chars=60))
            return page, [self.install_btn]

        def install_page(self):
            self.step_kicker = label("", "o-kicker", "warm", wrap=False)
            self.step_kicker.set_ellipsize(Pango.EllipsizeMode.END)
            self.percent = label("", "o-percent", wrap=False)
            self.bar = Gtk.ProgressBar()
            self.step_detail = label("", "o-meta", wrap=False)
            self.step_detail.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            self.plan = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=4,
                                    column_spacing=18, row_spacing=6, margin_top=10)
            self.plan_rows = []
            self.install_note = label("", "o-note")
            page = box(True, 8, self.step_kicker, self.percent, self.bar, self.step_detail, self.plan,
                       self.install_note)
            return page, []

        def show_plan(self, titles):
            """The setup steps, from the plan setup prints first."""
            clear(self.plan)
            self.plan_rows = []
            self.plan.set_visible(bool(titles))
            for title in titles:
                d, meta = dot(), label("", "o-chip", wrap=False)
                self.plan.append(box(False, 8, d, label(title.upper(), "o-chip", wrap=False), meta))
                self.plan_rows.append((d, meta))

        def mark_steps(self, current, meta=""):
            for i, (d, label_) in enumerate(self.plan_rows, 1):
                set_state(d, "ok" if i < current else "now" if i == current else None)
                label_.set_label("" if i != current else meta)

        def signin_page(self):
            self.signin_btn = button("SIGN IN WITH MICROSOFT", "o-play", self.sign_in)
            self.cancel_btn = button("CANCEL", "o-ghost", self.stop_proc)
            self.cancel_btn.set_visible(False)
            self.code = label("", "o-code", xalign=0.0, wrap=False)
            self.code_uri = "https://www.microsoft.com/link"
            spin = spinner(12)
            card = box(True, 14,
                       label("GO TO MICROSOFT.COM/LINK AND ENTER", "o-kicker", "warm"), self.code,
                       box(False, 10, button("OPEN MICROSOFT.COM/LINK", "o-ghost", self.open_link),
                           button("COPY CODE", "o-ghost", self.copy_code)),
                       box(False, 10, spin, label("Waiting for you on the Microsoft page", "o-meta", wrap=False)),
                       css=["o-glass", "pad"])
            self.code_rev = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.CROSSFADE, child=card)
            self.signin_how = label("You get a short code to enter at microsoft.com/link, in any browser, even on "
                                    "your phone. Omo never sees your password.", "o-note")
            page = box(True, 12, label("SIGN IN", "o-kicker", "warm"),
                       label("Minecraft needs your Microsoft account. Omo keeps the sign-in on this computer "
                             "and renews it for you.", "o-body", chars=52), self.signin_how, self.code_rev)
            return page, [self.signin_btn, self.cancel_btn]

        def home_page(self):
            self.home_kicker = label("", "o-kicker", "warm")
            self.cards = []
            rows = box(True, 6)
            for _ in range(3):
                d, name, text = dot(), label("", "o-chip", wrap=False), label("", "o-row-text", chars=64)
                name.set_size_request(76, -1)
                name.set_valign(Gtk.Align.START)
                d.set_valign(Gtk.Align.START)
                d.set_margin_top(6)
                text.set_hexpand(True)
                rows.append(box(False, 12, d, name, text))
                self.cards.append((d, name, text))
            self.home_rows = rows
            self.prism_note = box(True, 4, label("SIGNING IN THE FIRST TIME", "o-kicker"), label(
                "When you press Play, a Microsoft sign-in window opens. It belongs to Prism Launcher, the "
                "open-source engine Omo uses to start Minecraft for now. Sign in there once; after that Omo "
                "starts the game straight away.", "o-note", chars=56), css=["o-glass", "pad"])
            self.play_btn = button("PLAY", "o-play", self.play)
            self.play_btn.add_css_class("hero")
            page = box(True, 12, self.home_kicker, rows, self.prism_note)
            self.repair_btn = button("REPAIR", "o-play", lambda: self.run("repair"))
            self.repair_btn.add_css_class("hero")
            return page, [self.play_btn, self.repair_btn]

        def workspaces(self):
            """(section, kicker, rows) for the host workspaces the bridge reports."""
            kicker, rows = label("", "o-kicker"), box(True, 0, css=["o-glass"])
            section = box(True, 10, kicker, rows, visible=False, halign=Gtk.Align.END, valign=Gtk.Align.START,
                          margin_top=78, margin_end=56)
            section.set_size_request(340, -1)
            section.has_items = False
            return section, kicker, rows

        def starting_page(self):
            """Launching, inside the window: one step title, a smooth bar, the latest log line."""
            self.launch_title = label("Getting ready", "o-heading", wrap=False)
            self.launch_bar = Gtk.ProgressBar()
            self.launch_bar.set_size_request(380, -1)
            self.launch_line = label("", "o-meta", "mono", wrap=False)
            self.launch_line.set_ellipsize(Pango.EllipsizeMode.END)
            self.launch_line.set_max_width_chars(58)
            self.starting_text = label("", "o-note", chars=52)
            self.launch_target = 0.0
            page = box(True, 10, label("STARTING MINECRAFT", "o-kicker", "warm", wrap=False), self.launch_title,
                       box(True, 0, self.launch_bar, margin_top=6), self.launch_line, self.starting_text)
            return page, []

        def failed_page(self):
            """Something failed: what, in one line, and a report ready to paste to an agent."""
            self.fail_title = label("", "o-heading", chars=40)
            self.fail_reason = label("", "o-body", chars=56)
            self.fail_reason.set_selectable(True)
            self.report = ""
            self.retry = None
            self.copy_btn = button("COPY ERROR REPORT", "o-play", self.copy_report)
            again = button("TRY AGAIN", "o-ghost", lambda: self.retry and self.retry())
            page = box(True, 10, label("SOMETHING WENT WRONG", "o-kicker", "bad", wrap=False), self.fail_title,
                       self.fail_reason,
                       label("Copy the report and paste it to your agent (Hermes or Claude Code): it has the error "
                             "and the logs it needs. Sign-in tokens are taken out.", "o-note", chars=56))
            return page, [self.copy_btn, again, button("SHOW LOG", "o-link", self.toggle_details)]

        def show_failure(self, title, reason, report, retry):
            self.fail_title.set_label(title)
            self.fail_reason.set_label(reason)
            self.report, self.retry = report, retry
            self.copy_btn.set_label("COPY ERROR REPORT")
            self.show_problem("")
            self.show_page("failed")

        def copy_report(self):
            self.get_clipboard().set(self.report)
            self.copy_btn.set_label("COPIED  ✓")
            self.toasts.add_toast(Adw.Toast(timeout=4, title="Copied. Paste it to your agent."))
            GLib.timeout_add(2500, lambda: self.copy_btn.set_label("COPY ERROR REPORT") and False)

        def update_launch(self, progress):
            frac, title, line = progress
            self.launch_target = max(self.launch_target, frac)
            self.launch_title.set_label(title)
            if line:
                self.launch_line.set_label(line)
            return False

        def ease_launch_bar(self):
            """Glide the bar toward the latest step, and creep a little while a step takes time."""
            if self.stack.get_visible_child_name() != "starting":
                return True
            cur = self.launch_bar.get_fraction()
            goal = min(self.launch_target + 0.08, 0.97)
            step = (self.launch_target - cur) * 0.12 if cur < self.launch_target else (goal - cur) * 0.004
            self.launch_bar.set_fraction(min(max(cur + step, 0.0), 0.99))
            return True

        def playing_page(self):
            page = box(True, 12, label("PLAYING", "o-kicker", "warm"),
                       label("Hermes and your terminals are live in the world. This window comes back when you "
                             "quit the game.", "o-body", chars=52))
            return page, []

        def show_page(self, name):
            self.stack.set_visible_child_name(name)
            self.bar_stack.set_visible_child_name(name)
            self.account_box.set_visible(name in ("home", "playing")
                                         and bool(self.status.get("account", {}).get("signed_in")))
            self.ws[0].set_visible(False)
            self.status_row.set_visible(False)          # readiness shows only when something is wrong
            (self.remove_css_class if name in ("home", "playing", "loading") else self.add_css_class)("compact")

        # -- status ------------------------------------------------------------------------
        def refresh(self):
            def work():
                s = status()
                ui_soon(self.apply_status, s)
            threading.Thread(target=work, daemon=True).start()

        def apply_status(self, s, look_only=False):
            self.status = s
            self.stage.ready_to_open = True
            lines, ready = readiness(s)
            for (d, name, text), (chip, cd, cn), (n, state, t) in zip(self.cards, self.chips, lines):
                set_state(d, state)
                set_state(cd, state)
                name.set_label(n.upper())
                cn.set_label(n.upper())
                chip.set_tooltip_text(t)
                text.set_label(t)
            self.home_rows.set_visible(not ready)
            account = s.get("account", {})
            self.account_label.set_label((account.get("name") or "").upper())
            self.prism_note.set_visible(account.get("mode") == "prism" and not account.get("signed_in"))
            worlds = s.get("worlds_to_copy") or []
            self.worlds_card.set_visible(bool(worlds))
            self.worlds_text.set_label("  ·  ".join(worlds))
            self.size_text.set_label(
                "Omo installs its own Minecraft 26.2 with Fabric, CC: Tweaked, MCEF and the Omo mod, and connects "
                f"it to Hermes and the terminals on this computer. About {s.get('download_mb', 650)} MB, each "
                "file checked as it lands. Stop any time; it carries on where it left off.")
            self.play_btn.set_sensitive(ready)
            self.play_btn.set_visible(ready)
            self.repair_btn.set_visible(not ready)
            self.home_kicker.set_label(("READY  ·  MINECRAFT 26.2  ·  HERMES INSIDE" if ready else "NOT QUITE READY"))
            if look_only:
                return False
            if "error" in s:
                self.show_problem(s["error"])
            if not self.busy and self.stack.get_visible_child_name() != "failed":
                self.show_page(page_for(s))
            self.load_workspaces()
            return False

        def tick(self):
            if not self.busy and self.get_visible():
                self.refresh()
            return True

        def load_workspaces(self):
            def work():
                board = core.bridge_get("/board.json")
                ui_soon(self.fill_workspaces, board)
            threading.Thread(target=work, daemon=True).start()

        def fill_workspaces(self, board):
            items = (board or {}).get("workspaces") or []
            section, kicker, rows = self.ws
            section.has_items = bool(items)
            section.set_visible(False)
            while child := rows.get_first_child():
                rows.remove(child)
            live = sum(1 for w in items if w["state"] == "live")
            kicker.set_label(f"WORKSPACES  ·  {live} LIVE")
            for w in items[:4]:
                tabs, busy = len(w["tabs"]), sum(1 for t in w["tabs"] if t.get("busy"))
                meta = f"{tabs} TAB{'S' * (tabs != 1)}" + (f" · {busy} BUSY" if busy else "")
                name = label(w["name"], "o-ws-name", wrap=False, hexpand=True)
                name.set_ellipsize(Pango.EllipsizeMode.END)
                state = w["state"] if w["state"] in ("live", "interrupted") else "closed"
                rows.append(box(False, 12, dot(state), name, label(meta, "o-chip", wrap=False), css=["o-row"]))
            if len(items) > 4:
                rows.append(label(f"+ {len(items) - 4} more at 127.0.0.1:{core.PORT}/board", "o-chip", "o-row"))
            return False

        # -- actions ---------------------------------------------------------------------
        def on_key(self, _c, keyval, _code, state):
            ctrl_shift = Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK
            if keyval == Gdk.KEY_F12 or (keyval in (Gdk.KEY_D, Gdk.KEY_d) and state & ctrl_shift == ctrl_shift):
                self.toggle_dev()
                return True
            if self.stage.intro_done_at is None:
                self.skip_intro()
                return True
            if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and not self.busy:
                page = self.stack.get_visible_child_name()
                home = self.play_btn if self.play_btn.get_visible() else self.repair_btn
                target = {"welcome": self.install_btn, "home": home, "signin": self.signin_btn}.get(page)
                if target and target.get_sensitive():
                    target.emit("clicked")
                    return True
            if keyval == Gdk.KEY_Escape:
                if self.plugins_view.get_reveal_child():
                    self.hide_plugins()
                    return True
                self.show_details(False)
                if self.stack.get_visible_child_name() == "failed" and not self.busy:
                    self.show_page(page_for(self.status))
                return True
            return False

        def show_details(self, on):
            self.log_rev.set_reveal_child(on)
            self.reel_box.set_visible(bool(self.reel) and not on)

        # -- plugins and community -------------------------------------------------------------
        def build_plugins(self):
            """The plugin catalogue, over the film. Empty for now: a coming-soon state that
            already has the shape of the real thing (15 places, categories, one-click cards)."""
            self.catalogue = load_catalogue()
            self.cat_grid = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=3,
                                        min_children_per_line=3, column_spacing=14, row_spacing=14,
                                        homogeneous=True, valign=Gtk.Align.START)
            scroller = Gtk.ScrolledWindow(child=self.cat_grid, hscrollbar_policy=Gtk.PolicyType.NEVER,
                                          vexpand=True, hexpand=True)
            scroller.add_css_class("o-cat-scroll")
            self.cat_count = label("", "o-chip", "dim", wrap=False)
            self.cat_status = label("COMING SOON", "o-soon", wrap=False, halign=Gtk.Align.START)
            self.cat_body = label("", "o-body", chars=34)
            discord = button("BUILD THEM WITH US ON DISCORD", "o-play", self.open_discord)
            discord.set_halign(Gtk.Align.START)
            back = button("←  BACK", "o-link", self.hide_plugins)
            back.set_halign(Gtk.Align.START)
            side = box(True, 0,
                       box(True, 0, back, margin_bottom=14),
                       label("PLUGINS", "o-kicker", "warm", wrap=False),
                       label("Plugins", "o-title", "cat", wrap=False),
                       self.cat_status,
                       box(True, 0, self.cat_body, margin_top=18),
                       label("New rooms, agents, tools and worlds for Omo Space. Pick one, press install, "
                             "and it is in your world next time you play.", "o-note", chars=34, margin_top=12),
                       box(True, 0, discord, margin_top=26),
                       box(True, 0, vexpand=True),
                       self.cat_count,
                       hexpand=False)
            side.set_size_request(330, -1)
            panel = box(False, 48, side, scroller, css=["o-plugins"])
            view = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.CROSSFADE, transition_duration=380,
                                child=panel, reveal_child=False, can_target=False)
            view.set_visible(False)
            self.fill_catalogue(self.catalogue)
            return view

        def fill_catalogue(self, items):
            clear(self.cat_grid)
            if items:
                for it in items:
                    self.cat_grid.append(self.plugin_card(it))
                for i in range(len(items), CATALOGUE_SLOTS):            # the places still to fill
                    self.cat_grid.append(self.plugin_slot(i))
                soon = all(it.get("status", "soon") == "soon" for it in items)
                self.cat_status.set_visible(soon)
                n = len(items)
                self.cat_body.set_label(f"{n} plugin{'s' * (n != 1)} on the way, each one a single click to "
                                        "install and a single click to remove." if soon else
                                        f"{n} plugin{'s' * (n != 1)} ready to install.")
                self.cat_count.set_label(f"{n} OF {max(n, CATALOGUE_SLOTS)}  ·  MORE ON THE WAY")
            else:
                for i in range(CATALOGUE_SLOTS):
                    self.cat_grid.append(self.plugin_slot(i))
                self.cat_status.set_visible(True)
                self.cat_body.set_label("The catalogue is being built. The first plugins land here soon, each one "
                                        "a single click to install.")
                self.cat_count.set_label(f"0 OF {CATALOGUE_SLOTS} PLACES FILLED")

        def plugin_slot(self, i):
            """An empty place in the coming-soon grid: a number and two soft lines, breathing."""
            bars = box(True, 8, box(False, css=["o-skel", "long"]), box(False, css=["o-skel", "short"]))
            card = box(True, 14, label(f"{i + 1:02d}", "o-slot-num", wrap=False), bars,
                       box(True, 0, vexpand=True), label("SOON", "o-chip", "dim", wrap=False),
                       css=["o-slot", f"d{i % 5}"])
            card.set_size_request(-1, 150)
            return card

        def plugin_card(self, it):
            install = button("SOON" if it.get("status", "soon") == "soon" else "INSTALL", "o-ghost",
                             lambda: None)
            install.add_css_class("small")
            install.set_sensitive(it.get("status") == "ready")
            card = box(True, 6,
                       label((it.get("category") or "PLUGIN").upper(), "o-kicker", "warm", wrap=False),
                       label(it["name"], "o-card-name", wrap=False),
                       label(it.get("tagline", ""), "o-card-text", chars=26, lines=2,
                             ellipsize=Pango.EllipsizeMode.END),
                       box(True, 0, vexpand=True),
                       box(False, 8, label(f"by {it.get('author', '?')}", "o-chip", "dim", wrap=False, hexpand=True),
                           install),
                       css=["o-plugin"])
            card.set_size_request(-1, 150)
            return card

        def show_plugins(self):
            self.plugins_view.set_visible(True)
            self.plugins_view.set_can_target(True)
            self.plugins_view.set_reveal_child(True)
            self.left_clamp.set_visible(False)
            self.reel_box.set_visible(False)
            self.plugins_btn.add_css_class("on")

        def hide_plugins(self):
            self.plugins_view.set_reveal_child(False)
            self.plugins_view.set_can_target(False)
            self.left_clamp.set_visible(True)
            self.reel_box.set_visible(bool(self.reel))
            self.plugins_btn.remove_css_class("on")
            GLib.timeout_add(400, lambda: (self.plugins_view.set_visible(self.plugins_view.get_reveal_child()), False)[1])

        def open_discord(self):
            if DISCORD_URL:
                open_uri(self, DISCORD_URL)
            else:
                self.toasts.add_toast(Adw.Toast(timeout=4, title="The Discord invite is on its way."))

        def toggle_details(self):
            self.show_details(not self.log_rev.get_reveal_child())

        def show_problem(self, text):
            self.problem_text.set_label(text)
            self.problem.set_reveal_child(bool(text))
            return False

        def append(self, text):
            buf = self.log.get_buffer()
            buf.insert(buf.get_end_iter(), text)
            self.log.scroll_mark_onscreen(self.log_end)
            return False

        def spawn(self, op, *args, **kw):
            self.append(f"$ omo_setup.py {' '.join((op, *args))}\n")
            return subprocess.Popen([sys.executable, str(SETUP), op, *args], stdin=subprocess.DEVNULL,
                                    start_new_session=not core.WIN, **kw)

        def stop_proc(self):
            self.cancelled = True
            if self.proc and self.proc.poll() is None:
                stop_tree(self.proc)

        def follow(self, op, show_output=True):
            """Run setup / repair / signin, turning its output into window events."""
            self.proc = self.spawn(op, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)

            def work(proc):
                lines = []
                for line in proc.stdout:
                    lines.append(line)
                    if show_output:
                        ui_soon(self.append, line)
                    ui_soon(self.on_event, parse_line(line))
                proc.wait()
                ui_soon(self.finished, proc.returncode, lines)
            threading.Thread(target=work, args=(self.proc,), daemon=True).start()

        def run(self, op):
            self.busy, self.step_total = op, None
            self.show_problem("")
            self.show_plan([])
            self.step_kicker.set_label("LOOKING AT WHAT IS ALREADY HERE")
            self.percent.set_label("0%")
            self.bar.set_fraction(0)
            self.step_detail.set_label("")
            self.install_note.set_label("Safe to close. Next time it carries on where it stopped." if op == "setup"
                                        else "Saves, sign-in, workspaces and Hermes settings are never touched.")
            self.show_page("install")
            self.follow(op)

        def on_event(self, ev):
            kind = ev[0]
            if kind == "plan":
                self.show_plan(ev[1])
            elif kind == "step":
                word = "REPAIR" if self.busy == "repair" else "STEP"
                self.step_kicker.set_label(f"{word} {ev[1]} OF {ev[2]}  ·  {ev[3].upper()}")
                self.step_total = ev[1:3]
                self.set_progress(0.0)
                self.mark_steps(ev[1])
            elif kind == "percent" and self.step_total:
                self.set_progress(ev[1])
                self.step_detail.set_label(ev[2])
                self.mark_steps(self.step_total[0], f"{ev[1] * 100:.0f}%")
            elif kind == "did":
                self.step_detail.set_label(ev[1])
            elif kind == "code":
                self.code.set_label(ev[1])
                self.code_uri = ev[2]
                self.code_rev.set_reveal_child(True)
                self.signin_btn.set_visible(False)
                self.signin_how.set_visible(False)
                self.cancel_btn.set_visible(True)
            return False

        def set_progress(self, within):
            """Overall progress: finished steps plus how far into this one (never backwards)."""
            i, n = self.step_total
            overall = max((i - 1 + within) / n, self.bar.get_fraction())
            self.bar.set_fraction(overall)
            self.percent.set_label(f"{overall * 100:.0f}%")

        def finished(self, code, lines):
            op, self.busy, self.proc = self.busy, None, None
            last = next((ln.strip() for ln in reversed(lines) if ln.strip()), "It stopped.")
            if code != 0 and not self.cancelled:
                what = {"setup": "Install", "repair": "Repair", "signin": "Sign-in"}.get(op, op)
                retry = (lambda o=op: self.run(o)) if op in ("setup", "repair") else (lambda: self.sign_in())
                self.finished_ui()
                self.show_failure(f"{what} didn't finish", last[:300],
                                  error_report(f"{what} failed (omo_setup.py {op}, exit code {code})", last[:300],
                                               output="" if op == "signin" else "".join(lines)), retry)
                return False
            elif code == 0:
                self.toasts.add_toast(Adw.Toast(timeout=5, title={
                    "setup": "Installed. Every file checks out.", "repair": "Repaired. Every file checks out.",
                    "signin": last.replace("signin: s", "S")}[op]))
            self.finished_ui()
            self.refresh()
            return False

        def finished_ui(self):
            self.cancelled = False
            self.code_rev.set_reveal_child(False)
            self.signin_btn.set_visible(True)
            self.signin_how.set_visible(True)
            self.cancel_btn.set_visible(False)

        def sign_in(self):
            self.busy = "signin"
            self.show_problem("")
            self.follow("signin", show_output=False)       # the code is on screen, not in the log

        def sign_out(self):
            subprocess.run([sys.executable, str(SETUP), "signout"], capture_output=True)
            self.refresh()

        def open_link(self):
            open_uri(self, self.code_uri)

        def copy_code(self):
            self.get_clipboard().set(self.code.get_label())

        def play(self, show=False):
            self.busy = "play"
            self.show_problem("")
            account = self.status.get("account", {})
            prism_signin = account.get("mode") == "prism" and (show or not account.get("signed_in"))
            self.starting_text.set_label(
                "A Microsoft sign-in window opens next. Sign in there once, and Omo carries on by itself."
                if prism_signin else "This window steps aside when the game opens, and comes back when you quit.")
            self.launch_target = 0.0
            self.launch_bar.set_fraction(0.0)
            self.launch_title.set_label("Getting ready")
            self.launch_line.set_label("")
            self.show_page("starting")
            PLAY_LOG.parent.mkdir(parents=True, exist_ok=True)
            offset = PLAY_LOG.stat().st_size if PLAY_LOG.exists() else 0
            # Output goes to a file, never a pipe: a pipe nobody reads would
            # eventually block the game.
            with open(PLAY_LOG, "ab") as out:
                self.proc = self.spawn("play", *(["--show"] if show else []), stdout=out, stderr=subprocess.STDOUT)
            watch = PlayWatch(self.proc, PLAY_LOG, offset, core.GAME / "logs/latest.log",
                              lambda: bool(core.instance_pids()))
            headless = account.get("mode") == "prism" and not prism_signin
            threading.Thread(target=self.watch_play, args=(watch, offset, headless), daemon=True).start()

        def watch_play(self, watch, offset, headless):
            seen, said = None, None
            self.play_watch = watch
            while True:
                state = watch.poll()
                if state[0] in ("preflight", "launched"):
                    progress = launch_progress(read_from(PLAY_LOG, offset), self.game_log_since(watch.started))
                    if progress != said:
                        said = progress
                        ui_soon(self.update_launch, progress)
                if state != seen:
                    seen = state
                    ui_soon(self.on_play, state)
                if state[0] in ("over", "failed"):
                    break
                if (headless and state[0] == "launched" and time.time() - watch.started > PRISM_PATIENCE
                        and not core.instance_pids()):
                    # A headless Prism that has not started the game is waiting on a dialog
                    # nobody can see (an expired sign-in): start again, with its window.
                    stop_tree(watch.proc)
                    watch.proc.wait()
                    ui_soon(self.play, True)
                    return
                time.sleep(0.25)
            ui_soon(self.append, read_from(PLAY_LOG, offset)[-3000:])

        def game_log_since(self, started):
            """The game's log, only if this launch wrote it."""
            log = core.GAME / "logs/latest.log"
            try:
                return log.read_text(errors="replace") if log.stat().st_mtime >= started - 1 else ""
            except OSError:
                return ""

        def play_report(self, what, reason):
            watch = getattr(self, "play_watch", None)
            started = watch.started if watch else time.time() - 600
            offset = watch.offset if watch else 0
            return error_report(what, reason, read_from(PLAY_LOG, offset), self.game_log_since(started),
                                newest_crash(started), when=started)

        def on_play(self, state):
            if state[0] == "window":
                self.update_launch((1.0, "Opening the window", ""))
                self.show_page("playing")               # what the window shows if opened mid-game
                self.hidden_for_game = True
                self.app.hold()
                self.set_visible(False)                 # the reel pauses with it: no decoding during the game
            elif state[0] in ("over", "failed"):
                if self.proc and self.proc.poll() is None:
                    stop_tree(self.proc)     # a private Prism that outlived its game
                self.busy, self.proc = None, None
                if state[0] == "failed":
                    self.show_failure("Minecraft didn't start", state[1],
                                      self.play_report("Play failed before the game window opened", state[1]),
                                      self.play)
                elif state[1]:
                    reason = f"Minecraft closed with exit code {state[1]}."
                    self.show_failure("Minecraft closed unexpectedly", reason,
                                      self.play_report("The game crashed or quit with an error", reason), self.play)
                if self.hidden_for_game:
                    self.hidden_for_game = False
                    self.set_visible(True)
                    self.app.release()
                self.present()
                self.refresh()
            return False

    class DevPanel(Adw.Window):
        """F12 / Ctrl+Shift+D / --dev: try the looks, walk every page, run a pretend
        install, drive the film, and watch the live numbers. Nothing here installs,
        signs in or starts the game."""

        def __init__(self, win):
            super().__init__(title="Omo Space · dev", transient_for=win, default_width=360, default_height=640)
            self.add_css_class("omo-dev")
            self.win = win

            def section(title, *rows):
                return box(True, 8, label(title, "d-head", wrap=False), *rows, margin_top=14)

            def btn(text, cb):
                b = Gtk.Button(label=text)
                b.add_css_class("d-btn")
                b.connect("clicked", lambda *_: cb())
                return b

            def flow(*children, per=3):
                f = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=per,
                                min_children_per_line=per, column_spacing=6, row_spacing=6, homogeneous=True)
                for c in children:
                    f.append(c)
                return f

            self.look_btns = {}
            group = None
            looks = []
            for name in LOOKS:
                t = Gtk.ToggleButton(label=name.capitalize(), group=group)
                t.add_css_class("d-btn")
                t.set_tooltip_text(LOOKS[name])
                t.connect("toggled", lambda b, n=name: b.get_active() and self.win.apply_look(n))
                group = group or t
                self.look_btns[name] = t
                looks.append(t)
            self.look_btns[win.look].set_active(True)
            self.saved = label("", "d-note", wrap=False)
            looks_box = section("LOOK", flow(*looks, per=2), self.look_desc_row(),
                                box(False, 8, btn("Make this the default", self.save), self.saved))

            pages = section("PAGE", flow(*(btn(n.capitalize(), lambda n=n: win.dev_page(n))
                                           for n in ("welcome", "install", "signin", "home", "starting",
                                                     "playing", "problem", "failed"))),
                            btn("Back to live status", win.dev_live))
            sim = section("SIMULATE", flow(btn("Pretend install", win.dev_fake_setup),
                                           btn("Details log", win.toggle_details), per=2))
            plug = section("PLUGINS", flow(btn("Open catalogue", win.show_plugins),
                                           btn("Sample cards", lambda: (win.fill_catalogue(SAMPLE_CATALOGUE),
                                                                        win.show_plugins())),
                                           btn("Empty (live)", lambda: win.fill_catalogue(win.catalogue)), per=3))
            film = section("FILM", flow(btn("Replay intro", win.replay_intro), btn("Next shot", win.next_shot),
                                        btn("Pause / play", self.pause), per=3))
            self.stats = label("", "d-stats", wrap=False)
            body = box(True, 0, looks_box, pages, sim, plug, film, section("LIVE", self.stats), css=["d-body"])
            self.set_content(Gtk.ScrolledWindow(child=body, hscrollbar_policy=Gtk.PolicyType.NEVER))
            self.paused = False
            GLib.timeout_add(500, self.update)

        def look_desc_row(self):
            self.desc = label("", "d-note", chars=40)
            return self.desc

        def save(self):
            try:
                save_look(self.win.look)
                self.saved.set_label(f"saved: {self.win.look}")
            except OSError as exc:
                self.saved.set_label(f"could not save ({exc.strerror})")

        def pause(self):
            self.paused = not self.paused
            self.win.stage.set_paused(self.paused)

        def update(self):
            w, st = self.win, self.win.stage
            self.desc.set_label(LOOKS[w.look])
            clip = w.reel[st.shot % len(w.reel)]["title"] if st.shot >= 0 and w.reel else "-"
            try:
                rss = int(open("/proc/self/statm").read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 2**20
            except (OSError, ValueError, AttributeError):
                rss = 0
            decks = " ".join(d.bin.get_state(0)[1].value_nick if hasattr(d, "bin") else "?" for d in st.decks) \
                if st.decks else "no video (fallback light)"
            lines = [
                f"look     {w.look}",
                f"page     {w.stack.get_visible_child_name()}   busy={w.busy}",
                f"fps      {st.fps:.0f}",
                f"shot     {st.shot + 1 if st.shot >= 0 else '-'} / {len(w.reel)}   {clip}",
                f"mix      {st.mix:.2f}   t={st.now:.1f}s",
                f"decks    {decks}",
                f"intro    {'done' if st.intro_done_at is not None else 'playing'}   ready={st.ready_to_open}",
                f"memory   {rss:.0f} MB (this window)",
                f"progress {w.bar.get_fraction() * 100:.0f}%   {w.step_kicker.get_label()[:34]}",
            ]
            self.stats.set_label("\n".join(lines))
            return self.get_visible() or True

    class App(Adw.Application):
        """One Omo window: opening the app again brings the existing one back."""

        def __init__(self):
            super().__init__(application_id=core.APP_ID)
            self.win = None
            self.failed = None                          # an exception that kept the window from opening
            self.connect("activate", self.on_activate)

        def on_activate(self, app):
            if self.win:
                self.win.set_visible(True)
                self.win.present()
                return
            try:
                self.open_window(app)
            except Exception as exc:                    # raised inside GTK's loop, it would leave no window
                self.failed = exc
                self.quit()

        def open_window(self, app):
            provider = Gtk.CssProvider()
            load_css(provider, CSS)
            Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider,
                                                      Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
            core.write_desktop_entry()                  # the app menu entry and the icon, kept current
            Gtk.Window.set_default_icon_name(core.APP_ID)
            self.win = Window(app)
            self.win.present()

    GLib.set_application_name("Omo Space")
    app = App()
    app.run([])
    if app.failed:
        raise app.failed


if __name__ == "__main__":
    if not wants_gui(sys.argv, os.environ, core.OS):
        tui()
    else:
        try:
            gui()
        except Exception as exc:                        # no GTK, or one too old for the window
            fallback(f"{type(exc).__name__}: {exc}")
