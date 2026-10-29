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
CSS = """
/* omo.sh "warmpaper": paper, ink and taupes; Crimson Text for words, IBM Plex Mono for labels. */
window.omo { background-color: #f5f0e8; color: #1a1008; }
window.omo headerbar { background: transparent; box-shadow: none; border: none; min-height: 0; }
window.omo headerbar windowcontrols button { color: #8a7865; }
window.omo label { font-family: "Crimson Text", serif; }
window.omo button, window.omo button label, window.omo .o-kicker, window.omo .o-meta, window.omo .o-code,
window.omo .o-row-name, window.omo .o-footer { font-family: "IBM Plex Mono", monospace; }

.o-title { font-weight: 600; font-size: 28px; letter-spacing: -0.2px; }
.o-tagline { font-style: italic; font-size: 16px; color: #4a3b2f; }
.o-heading { font-weight: 600; font-size: 26px; letter-spacing: -0.2px; }
.o-body { font-size: 17px; color: #4a3b2f; }
.o-note { font-style: italic; font-size: 15px; color: #8a7865; }
.o-kicker { font-size: 10.5px; font-weight: 500; letter-spacing: 1.6px; color: #8a7865; }
.o-meta { font-size: 11.5px; color: #8a7865; }
.o-footer { font-size: 10px; letter-spacing: 1.2px; color: #a89886; }
.o-percent { font-weight: 600; font-size: 44px; letter-spacing: -1px; }
.o-code { font-size: 34px; font-weight: 500; letter-spacing: 6px; color: #1a1008; }

.o-card { background-color: #faf7f2; border: 1px solid #d4cdc0; border-radius: 10px; }
.o-row { padding: 12px 18px; }
.o-code-card { padding: 26px 18px 22px 18px; }
.o-row + .o-row { border-top: 1px solid #e9e2d6; }
.o-row-name { font-size: 10.5px; font-weight: 500; letter-spacing: 1.4px; color: #1a1008; }
.o-row-text { font-size: 16px; color: #4a3b2f; }
.o-ws-name { font-size: 16.5px; font-weight: 600; }
.o-problem { background-color: #fbeee6; border: 1px solid #e2b8a2; border-radius: 10px; padding: 12px 16px; }
.o-problem label { color: #7a2e1c; }
.o-bar { border-top: 1px solid #e3dccf; padding: 14px 40px 12px 40px; }

button.o-dark { background: #1a1008; color: #f5f0e8; border: 1px solid #1a1008; border-radius: 6px;
                padding: 9px 26px; font-size: 13px; font-weight: 500; letter-spacing: 0.3px; box-shadow: none;
                transition: background-color 140ms ease, border-color 140ms ease; }
button.o-dark:hover { background: #2c2015; border-color: #2c2015; }
button.o-dark:active { background: #000000; }
button.o-dark:disabled { background: #d4cdc0; border-color: #d4cdc0; color: #faf7f2; }
button.o-outline { background: transparent; color: #1a1008; border: 1px solid #d4cdc0; border-radius: 6px;
                   padding: 9px 20px; font-size: 13px; font-weight: 500; box-shadow: none;
                   transition: background-color 140ms ease, border-color 140ms ease; }
button.o-outline:hover { border-color: #1a1008; background: rgba(26,16,8,0.04); }
button.o-outline:disabled { color: #b5aca0; border-color: #e6dfd3; }
button.o-link { background: none; border: none; box-shadow: none; color: #8a7865; padding: 6px 8px;
                font-size: 11.5px; min-height: 0; }
button.o-link:hover { color: #1a1008; background: none; }

.o-dot { min-width: 8px; min-height: 8px; border-radius: 999px; background-color: #d4cdc0;
         transition: background-color 400ms ease; }
.o-dot.ok, .o-dot.live { background-color: #4d8b5a; }
.o-dot.warn { background-color: #c9932a; }
.o-dot.missing, .o-dot.closed { background-color: #b5aca0; }
.o-dot.interrupted { background-color: #c0564b; }
.o-dot.now { background-color: #1a1008; }
.o-step { padding: 8px 18px; }

progressbar trough { background-color: #e4ddd1; border-radius: 2px; min-height: 4px; border: none; }
progressbar progress { background-color: #1a1008; border-radius: 2px; min-height: 4px; border: none; }
.o-log { background-color: #1a1008; color: #e6dccb; border-radius: 10px; padding: 10px;
         font-family: "IBM Plex Mono", monospace; font-size: 11px; }
.o-log text { background-color: #1a1008; color: #e6dccb; }
window.omo toast { background-color: #1a1008; color: #f5f0e8; border-radius: 6px; margin-bottom: 18px; }
window.omo toast label { font-family: "IBM Plex Mono", monospace; font-size: 12px; }
"""


def gui():
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    gi.require_version("PangoCairo", "1.0")
    from gi.repository import Adw, Gdk, GLib, Gtk, Pango, PangoCairo

    # Older GTK/libadwaita/Pango (Ubuntu 24.04: libadwaita 1.5, Pango 1.52;
    # 22.04: libadwaita 1.1, GTK 4.6) lack some calls this window uses. Each
    # has a fallback here, so the window opens on them instead of crashing.
    register_fonts(PangoCairo.FontMap.get_default(), sorted((ASSETS / "fonts").glob("*.ttf")))

    def spinner(size):
        s = Adw.Spinner() if hasattr(Adw, "Spinner") else Gtk.Spinner(spinning=True)   # Adw.Spinner: 1.6+
        s.set_size_request(size, size)
        return s

    def header_bar():
        bar = Adw.HeaderBar()
        if hasattr(bar.props, "show_title"):          # libadwaita 1.4+
            bar.set_show_title(False)
        else:
            bar.set_title_widget(Gtk.Label())          # the same: no title text
        return bar

    class Frame(Gtk.Box):
        """The three Adw.ToolbarView calls this window makes, on a plain box (libadwaita < 1.4)."""

        def __init__(self):
            super().__init__(orientation=Gtk.Orientation.VERTICAL)

        def add_top_bar(self, widget):
            self.prepend(widget)

        def set_content(self, widget):
            widget.set_vexpand(True)
            self.append(widget)

        def add_bottom_bar(self, widget):
            self.append(widget)

    def toolbar_view():
        return Adw.ToolbarView() if hasattr(Adw, "ToolbarView") else Frame()

    def label(text="", *classes, wrap=True, xalign=0.0):
        w = Gtk.Label(label=text, xalign=xalign, wrap=wrap)
        if wrap:                            # wrap at the width it is given, not at a guess
            w.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            w.set_max_width_chars(90)
            w.set_hexpand(True)
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
        d = box(False, css=["o-dot", state] if state else ["o-dot"], valign=Gtk.Align.CENTER)
        return d

    def mark(size):
        img = Gtk.Image.new_from_file(str(ASSETS / "omo-mark.svg"))
        img.set_pixel_size(size)
        return img

    class Window(Adw.ApplicationWindow):
        def __init__(self, app):
            super().__init__(application=app, title="Omo Space")
            self.set_default_size(780, 740)
            self.add_css_class("omo")
            self.app, self.status, self.proc, self.busy = app, {}, None, None
            self.hidden_for_game = self.cancelled = False
            self.step_total = None

            view = toolbar_view()
            view.add_top_bar(header_bar())
            self.set_content(view)

            # -- brand, with the signed-in player on the right
            self.account_label = label("", "o-meta", wrap=False)
            self.account_box = box(False, 8, dot("ok"), self.account_label,
                                   button("Sign out", "o-link", self.sign_out), valign=Gtk.Align.CENTER)
            words = box(True, 0, label("Omo Space", "o-title"),
                        label("The next interface isn't a chat window.", "o-tagline"),
                        valign=Gtk.Align.CENTER, hexpand=True)
            brand = box(False, 18, mark(64), words, self.account_box, margin_start=40, margin_end=40)

            # -- a problem, when there is one
            self.problem_text = label("", "o-body")
            self.problem = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN, child=box(
                True, 4, label("SOMETHING NEEDS A LOOK", "o-kicker"), self.problem_text, css=["o-problem"],
                margin_start=40, margin_end=40, margin_top=16))

            # -- pages, each with its own buttons in the bar at the bottom
            self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=200,
                                   vhomogeneous=False)
            self.bar_stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=200,
                                       hhomogeneous=False, hexpand=True)
            for name, (page, buttons) in (("loading", (box(), [])), ("welcome", self.welcome_page()),
                                          ("install", self.install_page()), ("signin", self.signin_page()),
                                          ("home", self.home_page()), ("starting", self.starting_page()),
                                          ("playing", self.playing_page())):
                self.stack.add_named(page, name)
                self.bar_stack.add_named(box(False, 10, *buttons), name)

            # -- details (hidden until asked)
            self.log = Gtk.TextView(editable=False, monospace=True, cursor_visible=False)
            self.log.add_css_class("o-log")
            self.log.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
            buf = self.log.get_buffer()
            self.log_end = buf.create_mark("end", buf.get_end_iter(), False)
            logs = Gtk.ScrolledWindow(min_content_height=150, child=self.log)
            self.log_rev = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN,
                                        child=box(True, 0, logs, margin_top=16))

            body = box(True, 0, self.stack, self.log_rev, margin_start=40, margin_end=40, margin_top=22,
                       margin_bottom=22)
            scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True, child=body)
            self.toasts = Adw.ToastOverlay(child=box(True, 0, brand, self.problem, scroller))
            view.set_content(self.toasts)

            details = button("Details", "o-link", self.toggle_details)
            foot = label(f"OMO.SH  ·  {core.MANIFEST['version']}", "o-footer", wrap=False)
            view.add_bottom_bar(box(False, 10, self.bar_stack, details, foot, css=["o-bar"]))

            keys = Gtk.EventControllerKey()
            keys.connect("key-pressed", self.on_key)
            self.add_controller(keys)
            self.show_page("loading")
            self.refresh()
            GLib.timeout_add_seconds(5, self.tick)

        # -- pages: (content, buttons for the bottom bar) -----------------------
        def welcome_page(self):
            self.install_btn = button("Install", "o-dark", lambda: self.run("setup"))
            self.size_text = label("", "o-body")
            self.worlds_text = label("", "o-row-text")
            self.worlds_card = box(True, 4, label("YOUR WORLDS COME WITH YOU", "o-row-name"), self.worlds_text,
                                   label("Copied from Prism. The originals stay exactly where they are.", "o-note"),
                                   css=["o-card", "o-row"])
            page = box(True, 14, label("FIRST RUN", "o-kicker"), label("Set up Omo Space", "o-heading"),
                       self.size_text, self.worlds_card,
                       label("Minecraft is a trademark of Mojang. Omo is not affiliated with Mojang or Microsoft; "
                             "you play with your own Minecraft account.", "o-note"))
            return page, [self.install_btn]

        def install_page(self):
            self.step_kicker = label("", "o-kicker")
            self.percent = label("", "o-percent")
            self.bar = Gtk.ProgressBar()
            self.step_detail = label("", "o-meta", wrap=False)
            self.step_detail.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            self.plan = box(True, 0, css=["o-card"], margin_top=10)
            self.plan_rows = []
            self.install_note = label("", "o-note")
            page = box(True, 10, self.step_kicker, self.percent, self.bar, self.step_detail, self.plan,
                       self.install_note)
            return page, []

        def show_plan(self, titles):
            """The checklist of setup steps, from the plan setup prints first."""
            while child := self.plan.get_first_child():
                self.plan.remove(child)
            self.plan_rows = []
            self.plan.set_visible(bool(titles))
            for title in titles:
                d, meta = dot(), label("", "o-meta", wrap=False)
                name = label(title, "o-row-text", wrap=False)
                name.set_hexpand(True)
                self.plan.append(box(False, 12, d, name, meta, css=["o-row", "o-step"]))
                self.plan_rows.append((d, meta))

        def mark_steps(self, current, meta=""):
            for i, (d, label_) in enumerate(self.plan_rows, 1):
                for c in ("ok", "now"):
                    d.remove_css_class(c)
                if i < current:
                    d.add_css_class("ok")
                    label_.set_label("done")
                elif i == current:
                    d.add_css_class("now")
                    label_.set_label(meta)
                else:
                    label_.set_label("")

        def signin_page(self):
            self.signin_btn = button("Sign in with Microsoft", "o-dark", self.sign_in)
            self.cancel_btn = button("Cancel", "o-outline", self.stop_proc)
            self.cancel_btn.set_visible(False)
            self.code = label("", "o-code", xalign=0.5, wrap=False)
            self.code_uri = "https://www.microsoft.com/link"
            spin = spinner(14)
            card = box(True, 16,
                       label("GO TO MICROSOFT.COM/LINK AND ENTER", "o-kicker", xalign=0.5), self.code,
                       box(False, 10, button("Open microsoft.com/link", "o-dark", self.open_link),
                           button("Copy code", "o-outline", self.copy_code), halign=Gtk.Align.CENTER),
                       box(False, 10, spin, label("Waiting for you to finish on the Microsoft page",
                                                     "o-meta", wrap=False), halign=Gtk.Align.CENTER),
                       css=["o-card", "o-code-card"])
            self.code_rev = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.CROSSFADE, child=card)
            self.signin_how = label("You get a short code to enter at microsoft.com/link, in any browser, even on "
                                    "your phone. Omo never sees your password.", "o-note")
            page = box(True, 14, label("SIGN IN", "o-kicker"), label("Sign in with Microsoft", "o-heading"),
                       label("Minecraft needs your Microsoft account. Omo keeps the sign-in on this computer "
                             "and renews it for you.", "o-body"), self.signin_how, self.code_rev)
            return page, [self.signin_btn, self.cancel_btn]

        def home_page(self):
            self.cards = []
            rows = box(True, 0, css=["o-card"])
            for _ in range(3):
                d, name, text = dot(), label("", "o-row-name", wrap=False), label("", "o-row-text")
                name.set_size_request(70, -1)
                rows.append(box(False, 14, d, name, text, css=["o-row"]))
                self.cards.append((d, name, text))
            self.prism_note = box(True, 4, label("SIGNING IN THE FIRST TIME", "o-row-name"), label(
                "When you press Play, a Microsoft sign-in window opens. It belongs to Prism Launcher, the "
                "open-source engine Omo uses to start Minecraft for now. Sign in there once; after that Omo "
                "starts the game straight away.", "o-row-text"), css=["o-card", "o-row"])
            self.play_btn = button("Play", "o-dark", self.play)
            self.ws_home = self.workspaces()
            self.home_kicker = label("", "o-kicker")
            page = box(True, 14, self.home_kicker, rows, self.prism_note, self.ws_home[0])
            return page, [self.play_btn, button("Repair", "o-outline", lambda: self.run("repair"))]

        def workspaces(self):
            """(section, kicker, rows) for the host workspaces the bridge reports."""
            kicker, rows = label("", "o-kicker"), box(True, 0, css=["o-card"])
            section = box(True, 10, kicker, rows, margin_top=8, visible=False)
            return section, kicker, rows

        def starting_page(self):
            spin = spinner(36)
            self.starting_text = label("", "o-body", xalign=0.5)
            self.starting_text.set_justify(Gtk.Justification.CENTER)
            self.starting_text.set_max_width_chars(46)
            page = box(True, 18, spin, label("Starting Minecraft", "o-heading", xalign=0.5),
                       self.starting_text, margin_top=90, halign=Gtk.Align.CENTER)
            return page, []

        def playing_page(self):
            self.ws_play = self.workspaces()
            page = box(True, 14, label("PLAYING", "o-kicker"), label("Minecraft is running", "o-heading"),
                       label("Hermes and your terminals are live in the world. This window comes back when you "
                             "quit the game.", "o-body"), self.ws_play[0])
            return page, []

        def show_page(self, name):
            self.stack.set_visible_child_name(name)
            self.bar_stack.set_visible_child_name(name)
            self.account_box.set_visible(name in ("home", "playing")
                                         and bool(self.status.get("account", {}).get("signed_in")))

        # -- status ------------------------------------------------------------
        def refresh(self):
            def work():
                s = status()
                GLib.idle_add(self.apply_status, s)
            threading.Thread(target=work, daemon=True).start()

        def apply_status(self, s):
            self.status = s
            lines, ready = readiness(s)
            for (d, name, text), (n, state, t) in zip(self.cards, lines):
                for c in ("ok", "warn", "missing"):
                    d.remove_css_class(c)
                d.add_css_class(state)
                name.set_label(n.upper())
                text.set_label(t)
            account = s.get("account", {})
            self.account_label.set_label(account.get("name") or "")
            self.prism_note.set_visible(account.get("mode") == "prism" and not account.get("signed_in"))
            worlds = s.get("worlds_to_copy") or []
            self.worlds_card.set_visible(bool(worlds))
            self.worlds_text.set_label("  ·  ".join(worlds))
            self.size_text.set_label(
                "Omo installs its own Minecraft 26.2 with Fabric, CC: Tweaked, MCEF and the Omo mod, and connects "
                f"it to Hermes and the terminals on this computer. About {s.get('download_mb', 650)} MB, each "
                "file checked as it lands. Stop any time; it carries on where it left off.")
            self.play_btn.set_sensitive(ready)
            self.home_kicker.set_label("READY" if ready else "NOT QUITE READY")
            if "error" in s:
                self.show_problem(s["error"])
            if not self.busy:
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
                GLib.idle_add(self.fill_workspaces, board)
            threading.Thread(target=work, daemon=True).start()

        def fill_workspaces(self, board):
            items = (board or {}).get("workspaces") or []
            for section, kicker, rows in (self.ws_home, self.ws_play):
                section.set_visible(bool(items))
                while child := rows.get_first_child():
                    rows.remove(child)
                live = sum(1 for w in items if w["state"] == "live")
                kicker.set_label(f"WORKSPACES  ·  {live} LIVE")
                for w in items[:5]:
                    tabs, busy = len(w["tabs"]), sum(1 for t in w["tabs"] if t.get("busy"))
                    meta = f"{w['kind']}  ·  {tabs} tab{'s' * (tabs != 1)}" + (f"  ·  {busy} busy" if busy else "")
                    name = label(w["name"], "o-ws-name", wrap=False)
                    name.set_hexpand(True)
                    name.set_ellipsize(Pango.EllipsizeMode.END)
                    state = w["state"] if w["state"] in ("live", "interrupted") else "closed"
                    rows.append(box(False, 12, dot(state), name, label(meta, "o-meta", wrap=False), css=["o-row"]))
                if len(items) > 5:
                    rows.append(label(f"and {len(items) - 5} more on the board at 127.0.0.1:{core.PORT}/board",
                                      "o-meta", "o-row"))
            return False

        # -- actions -----------------------------------------------------------
        def on_key(self, _c, keyval, _code, _state):
            if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and not self.busy:
                page = self.stack.get_visible_child_name()
                target = {"welcome": self.install_btn, "home": self.play_btn, "signin": self.signin_btn}.get(page)
                if target and target.get_sensitive():
                    target.emit("clicked")
                    return True
            if keyval == Gdk.KEY_Escape:
                self.log_rev.set_reveal_child(False)
                return True
            return False

        def toggle_details(self):
            self.log_rev.set_reveal_child(not self.log_rev.get_reveal_child())

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
                        GLib.idle_add(self.append, line)
                    GLib.idle_add(self.on_event, parse_line(line))
                proc.wait()
                GLib.idle_add(self.finished, proc.returncode, lines)
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
                self.show_problem(last[:300] + "  Details has the whole story.")
            elif code == 0:
                self.toasts.add_toast(Adw.Toast(timeout=5, title={
                    "setup": "Installed. Every file checks out.", "repair": "Repaired. Every file checks out.",
                    "signin": last.replace("signin: s", "S")}[op]))
            self.cancelled = False
            self.code_rev.set_reveal_child(False)
            self.signin_btn.set_visible(True)
            self.signin_how.set_visible(True)
            self.cancel_btn.set_visible(False)
            self.refresh()
            return False

        def sign_in(self):
            self.busy = "signin"
            self.show_problem("")
            self.follow("signin", show_output=False)       # the code is on screen, not in the log

        def sign_out(self):
            subprocess.run([sys.executable, str(SETUP), "signout"], capture_output=True)
            self.refresh()

        def open_link(self):
            if hasattr(Gtk, "UriLauncher"):                # GTK 4.10+
                Gtk.UriLauncher.new(self.code_uri).launch(self, None, None, None)
            else:
                Gtk.show_uri(self, self.code_uri, Gdk.CURRENT_TIME)

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
            seen = None
            while True:
                state = watch.poll()
                if state != seen:
                    seen = state
                    GLib.idle_add(self.on_play, state)
                if state[0] in ("over", "failed"):
                    break
                if (headless and state[0] == "launched" and time.time() - watch.started > PRISM_PATIENCE
                        and not core.instance_pids()):
                    # A headless Prism that has not started the game is waiting on a dialog
                    # nobody can see (an expired sign-in): start again, with its window.
                    stop_tree(watch.proc)
                    watch.proc.wait()
                    GLib.idle_add(self.play, True)
                    return
                time.sleep(0.25)
            GLib.idle_add(self.append, read_from(PLAY_LOG, offset)[-3000:])

        def on_play(self, state):
            if state[0] == "window":
                self.show_page("playing")               # what the window shows if opened mid-game
                self.hidden_for_game = True
                self.app.hold()
                self.set_visible(False)
            elif state[0] in ("over", "failed"):
                if self.proc and self.proc.poll() is None:
                    stop_tree(self.proc)     # a private Prism that outlived its game
                self.busy, self.proc = None, None
                if state[0] == "failed":
                    self.show_problem(state[1] + "  Details has more.")
                elif state[1]:
                    self.show_problem(f"Minecraft closed with exit code {state[1]}. Its log is in "
                                      f"{core.GAME / 'logs'}.")
                if self.hidden_for_game:
                    self.hidden_for_game = False
                    self.set_visible(True)
                    self.app.release()
                self.present()
                self.refresh()
            return False

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
            Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
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
