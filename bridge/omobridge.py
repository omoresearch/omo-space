#!/usr/bin/env python3
"""Omo bridge — workspaces and terminal tabs for Minecraft stations.

Session ownership lives in a tmux server that runs as its OWN systemd unit
(omo-tmux.service). This process never owns a user shell: it only asks tmux to
create windows and attaches read/write "views" to them. Restarting, crashing
or upgrading the bridge therefore leaves every shell, working directory,
environment variable and running command exactly where it was.

Identity model
  workspace  = one tmux session   (omo-<id>)      named, renameable, durable id,
               optionally tied to a Hermes profile (hermes -p <profile>)
  tab        = one tmux window, tagged @omo_tab=<tab id> (tmux's own @n ids
               restart from @0 with the server, so they are never trusted alone)
  view       = one grouped tmux session per open screen; each view can look at
               a different tab of the same workspace and is torn down when the
               socket closes. Tearing down a view never touches a window.

Honesty rules
  * Workspaces and tabs are stamped with the session server's identity
    (pid:start_time). Gone under the same server = "exited" / "ended" (the
    process finished). Gone under a different server = "interrupted" (the
    host or the session server restarted). Nothing is re-run automatically.
  * A screen whose window disappears is told so; it never silently slides onto
    another tab and never keeps painting a stale frame.
  * Limits reject new work with a message. They never evict a live session.

Trust
  * The bridge token (0600 file) authenticates the mod's controls ("ui" role).
  * The Lua screen inside a computer only ever holds a single-use ticket bound
    to its station AND workspace ("screen" role): it can look at and type into
    that one workspace, nothing else.
  * Loopback only. Requests naming another Host are refused (DNS rebinding).

Protocol (JSON text frames over a loopback websocket; first frame is `hello`):
  -> hello {token, role:"ui"} | {ticket, station, ws}   <- welcome {version, limits, tab_kinds, agents}
  ui only:
  -> ws_list | ws_create {name, kind} | ws_rename {ws, name} | ws_delete {ws, force?}
  -> ws_profile {ws, profile} | profiles                <- workspace | profiles {items}
  -> tab_new {ws, kind: shell|hermes|claude|codex, name?} | tab_rename {ws, tab, name}
  -> agents                                             <- agents {agents: {kind: {found, install}}}
  -> tab_close {ws, tab, force?}                        <- workspace | confirm
  -> tab_revive {ws, tab, mode} | hermes {ws, tab}
  -> station_status {station} | steer {station, ws, tab}
  both roles (a screen only for its own workspace):
  -> ws_get {ws} | attach {ws, tab, cols, rows} | switch {tab} | detach
  -> pause | resume                                     no player near: keep the last frame, close the view
  -> in {d} | size {cols, rows} | focus | redraw
  -> scroll {by} | wheel {by, x, y}                     history, or the app's own wheel
  -> sel {x1, y1, x2, y2, unit: char|word|line} | copy | paste {d?} | open {x, y}
  -> find {q, step: 0 | 1 older | -1 newer} | clear   <- found {q, i, n}
  <- attached {ws, tab, state} | frame {cols, rows, cursor, full, rows_changed, ...}
  <- detached {state, reason} | workspace {ws} | toast {m} | bell | error {m} | confirm {action, ..}

A station shows one tab on up to two surfaces (its computer and an attached
monitor wall), each its own screen socket and view at its own size. tmux sizes
the window to the latest active client; `focus` (or any key) makes a view that
client, so the surface in use drives the PTY size.

Plain HTTP on the same port: /health, /board (+ /board.json, a read-only live
status page for web monitors), and token-guarded /ticket and /api/<op>.
"""
import asyncio
import base64
import glob
import concurrent.futures
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid

# Pure-Python dependencies are vendored so the bridge runs on the system
# interpreter of a clean machine with no venv or pip step.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))

import websockets
from websockets.asyncio.server import serve
from websockets.datastructures import Headers
from websockets.http11 import Response

import termcore

VERSION = "1.2.0"
HOME = os.path.expanduser("~")
DATA_DIR = os.environ.get("OMO_DATA", os.path.join(HOME, ".local", "share", "omo"))
TOKEN_FILE = os.path.join(DATA_DIR, "token")
META_FILE = os.path.join(DATA_DIR, "workspaces.json")
LOG_FILE = os.path.join(DATA_DIR, "bridge.log")
HERE = os.path.dirname(os.path.abspath(__file__))
RCFILE = os.path.join(HERE, "mcshell.bashrc")
BOARD_FILE = os.path.join(HERE, "board.html")
HERMES_ROOT = os.environ.get("OMO_HERMES_ROOT", os.path.join(HOME, ".hermes"))
HERMES_BIN = os.environ.get("OMO_HERMES_BIN") or "hermes"   # the launcher pins the one it found

# What a tab can run. A shell and Hermes as before; Claude Code and Codex
# start their CLI in the tab's shell, the same way a Hermes tab does.
TAB_KINDS = ("shell", "hermes", "claude", "codex")
TAB_NAMES = {"shell": "Shell", "hermes": "Hermes", "claude": "Claude Code", "codex": "Codex"}
# The agent CLIs: binary name, the env override (the launcher pins what it
# found, as with OMO_HERMES_BIN; set one to a path to use another build), and
# how to install it when it is missing.
AGENTS = {
    "claude": {"bin": "claude", "env": "OMO_CLAUDE_BIN",
               "install": "curl -fsSL https://claude.ai/install.sh | bash  (or npm install -g @anthropic-ai/claude-code)"},
    "codex": {"bin": "codex", "env": "OMO_CODEX_BIN", "install": "npm install -g @openai/codex"},
}

HOST = os.environ.get("OMO_HOST", "127.0.0.1")
PORT = int(os.environ.get("OMO_PORT", "8780"))
FPS = float(os.environ.get("OMO_FPS", "20"))
TMUX_SOCKET = os.environ.get("OMO_TMUX_SOCKET", "omo")
TMUX_BIN = os.environ.get("OMO_TMUX", "")
# The X display for the clipboard and for opening links; unset = the live one.
DISPLAY = os.environ.get("OMO_DISPLAY", "")
IS_MAC = sys.platform == "darwin"
IS_WSL = bool(os.environ.get("WSL_DISTRO_NAME")) or "microsoft" in os.uname().release.lower()
# How links open on the host: the desktop's opener on Linux, `open` on macOS,
# the Windows browser from WSL (wslview if installed, else explorer.exe).
URL_OPENER = os.environ.get("OMO_OPEN") or (
    "open" if IS_MAC else (shutil.which("wslview") or "explorer.exe") if IS_WSL else "xdg-open")
STARTED = int(time.time())

# Pause before a typed Enter so TUIs read it as its own keypress (see type_in).
ENTER_GAP = 0.04

# Auto-sleep: a Hermes tab nobody is looking at, that is idle ("ready", no
# draft, no child processes, screen unchanged) and untouched for SLEEP_AFTER
# seconds, is ended with a clean /exit. Its conversation is saved; opening any
# screen on the tab resumes that same conversation. 0 turns it off.
SLEEP_AFTER = float(os.environ.get("OMO_SLEEP_AFTER", "1200"))
SLEEP_SCAN = float(os.environ.get("OMO_SLEEP_SCAN", "60"))
REMEMBER_EVERY = float(os.environ.get("OMO_REMEMBER_EVERY", "20"))
SLEEP_PER_SCAN = 4                  # never put a crowd to sleep in one burst
EXIT_WAIT = 15.0                    # seconds a /exit may take before we give up on that tab
# Processes a quiet Hermes may own (its UI, its gateway, MCP servers via uv).
# Anything else under it (a shell, a build, a browser helper) means work is
# happening, and the tab stays awake.
QUIET_CMDS = ("ui-tui/dist/entry.js", "tui_gateway.entry", "mcp_death_supervisor", "uv tool uvx",
              "/.cache/uv/archive-", "cua-driver serve --embedded", "cua-driver mcp --embedded")
# What an empty Hermes composer shows (its rotating placeholders).
PLACEHOLDER_RE = re.compile(r'^(Ask me anything…|Try ".*")?$')
# Visible on screen = Hermes still has plans of its own: a goal, subagents,
# background tasks, or a question waiting for the player.
AWAKE_MARKS = re.compile(r"⊙ goal|⏳ goal|⏸ goal|⛓|\b\d+ bg\b|resumes when|Allow once|approve|Approve|\(y/n\)")
# Worker threads for tmux/ps calls. Every open screen polls through this pool,
# and keystrokes share it; the asyncio default (cpu+4) queues typing behind polls.
WORKERS = 64
POLL_ACTIVE, POLL_QUIET = 0.5, 2.0   # tmux state checks per open screen: in use / quiet
# A state check tmux has not answered by then is skipped, not fatal: under load
# or swap the session server can stall for seconds, and the screen's text comes
# from its own PTY meanwhile. (A 10 s timeout used to end every open screen.)
POLL_TIMEOUT = 2.0

LIMITS = {"workspaces": 128, "tabs_per_workspace": 12, "views": 192}   # sized for a built world: one desk per room, two views where a wall is attached
SHELLS = {"bash", "sh", "zsh", "fish", "dash"}
PROFILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
TERM_OPS = {"in", "size", "focus", "redraw", "scroll", "wheel", "sel", "copy", "paste", "open", "find", "clear"}
SCREEN_OPS = {"ws_get", "attach", "switch", "detach", "pause", "resume", *TERM_OPS}
# A screen nobody is near (the mod tells station.lua, which sends "pause")
# keeps its last picture: after PARK_AFTER seconds on screen its view (tmux
# client + PTY + frame pump) closes; "resume" re-attaches the same tab.
# An unwatched tab may then auto-sleep like any other.
PARK_AFTER = 1.5
ENDS_SELECTION = {"in", "paste", "scroll", "wheel", "find", "clear"}
PASTE_MAX = 256 * 1024
# Session server options the views depend on, applied when a view opens so a
# running server picks them up without a restart (which would end every
# shell): apps may copy to the host clipboard (OSC 52) but never read it back,
# and a view larger than the window it shows is padded with plain blanks, not
# tmux's dots and borders.
VIEW_OPTIONS = (("-s", "set-clipboard", "on"), ("-s", "get-clipboard", "off"),
                ("-gw", "copy-mode-position-format", ""), ("-gw", "fill-character", " "),
                ("-gw", "pane-border-lines", "spaces"))

if HOST not in ("127.0.0.1", "::1", "localhost"):
    raise SystemExit(f"omo-bridge: refusing to listen on {HOST}; the bridge is loopback-only")


def find_tmux():
    for cand in (TMUX_BIN, os.path.join(DATA_DIR, "bin", "tmux"), shutil.which("tmux")):
        if cand and os.path.exists(cand) and os.access(cand, os.X_OK):
            return cand
    raise SystemExit("omo-bridge: tmux not found (looked in OMO_TMUX, OMO_DATA/bin, PATH)")


def log(msg):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8", errors="replace") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except OSError:
        pass


def clean(value, n=64):
    """Untrusted text headed for a log line or an id: printable ASCII, bounded."""
    return re.sub(r"[^\x20-\x7e]", "?", str(value or ""))[:n]


def clean_name(value, n):
    """A workspace or tab name: printable text in any script ("Website · Dev"), bounded."""
    return "".join(c if c.isprintable() else "?" for c in str(value or ""))[:n]


def same(a, b):
    return hmac.compare_digest(str(a).encode(), str(b).encode())


def load_token():
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(TOKEN_FILE):
        fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(secrets.token_hex(16))
    os.chmod(TOKEN_FILE, 0o600)
    with open(TOKEN_FILE) as fh:
        return fh.read().strip()


TOKEN = load_token()

# --------------------------------------------------------------------------- tickets
# The long-lived token stays host-side (bridge + mod). The Lua client inside
# the game only ever sees a short-lived, single-use ticket bound to its
# station and workspace, so a world file or a rogue program cannot capture a
# credential that outlives the connection it was minted for.
TICKET_TTL = 90
_USED_TICKETS = {}
# The used-ticket set lives in memory, so tickets are also bound to this
# process: one minted before a restart fails the MAC instead of replaying.
_BOOT = secrets.token_hex(8)


def _ticket_mac(nonce, expiry, station, ws_id):
    msg = f"{_BOOT}:{nonce}:{expiry}:{station}:{ws_id}".encode()
    return hmac.new(TOKEN.encode(), msg, hashlib.sha256).hexdigest()[:40]


def mint_ticket(station, ws_id):
    nonce = base64.urlsafe_b64encode(secrets.token_bytes(9)).decode().rstrip("=")
    expiry = str(int(time.time()) + TICKET_TTL)
    return f"{nonce}:{expiry}:{_ticket_mac(nonce, expiry, station, ws_id)}"


def check_ticket(ticket, station, ws_id):
    try:
        nonce, expiry, mac = ticket.split(":", 2)
        expiry_i = int(expiry)
    except ValueError:
        return False
    now = int(time.time())
    if not now <= expiry_i <= now + TICKET_TTL:
        return False
    if not same(_ticket_mac(nonce, expiry, station, ws_id), mac):
        return False
    for k, exp in list(_USED_TICKETS.items()):
        if exp < now:
            _USED_TICKETS.pop(k, None)
    if nonce in _USED_TICKETS:
        return False
    _USED_TICKETS[nonce] = expiry_i
    return True


# --------------------------------------------------------------------------- tmux
class Tmux:
    """Thin, synchronous tmux client. Every call is one short subprocess."""

    def __init__(self, binary, socket_name):
        self.bin = binary
        self.sock = socket_name

    def argv(self, *args):
        return [self.bin, "-L", self.sock, *args]

    def run(self, *args, timeout=10.0):
        return subprocess.run(self.argv(*args), capture_output=True, text=True, timeout=timeout)

    def ok(self, *args):
        return self.run(*args).returncode == 0

    def out(self, *args, timeout=10.0):
        p = self.run(*args, timeout=timeout)
        return p.stdout if p.returncode == 0 else ""

    def server_id(self):
        """pid:start_time of the session server, "" when it is down."""
        return self.out("display", "-p", "#{pid}:#{start_time}").strip()

    def alive(self):
        return bool(self.server_id())

    def has_session(self, name):
        return self.ok("has-session", "-t", "=" + name)

    def sessions(self):
        return self.out("list-sessions", "-F", "#{session_name}").splitlines()

    def windows(self, session):
        """[{id, tab, cmd, pid, cwd}] for every window of a session."""
        raw = self.out("list-windows", "-t", "=" + session, "-F",
                       "#{window_id}\t#{@omo_tab}\t#{pane_current_command}\t#{pane_pid}\t#{pane_current_path}")
        rows = []
        for line in raw.splitlines():
            parts = line.split("\t")
            if len(parts) == 5:
                rows.append({"id": parts[0], "tab": parts[1], "cmd": parts[2], "pid": parts[3], "cwd": parts[4]})
        return rows


TM = Tmux(find_tmux(), TMUX_SOCKET)


def _ps_table():
    """{pid: (ppid, comm, args)} from one `ps` call: macOS and other hosts
    without /proc. BSD and GNU ps both take these -o fields."""
    try:
        out = subprocess.run(["ps", "-A", "-ww", "-o", "pid=,ppid=,comm=,args="],
                             capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    table = {}
    for line in out.splitlines():
        parts = line.split(None, 3)
        if len(parts) >= 3 and parts[0].isdigit() and parts[1].isdigit():
            table[int(parts[0])] = (int(parts[1]), os.path.basename(parts[2]), parts[3] if len(parts) > 3 else "")
    return table


HAVE_PROC = os.path.isdir("/proc/self/task")


def _children(pid):
    """Child pids of pid, lowest first, read from /proc (no subprocess), or
    from ps where there is no /proc (macOS). None when neither can answer."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    if not HAVE_PROC:
        table = _ps_table()
        return sorted(p for p, row in table.items() if row[0] == pid) if table else None
    try:
        with open(f"/proc/{pid}/task/{pid}/children") as fh:
            return sorted(int(p) for p in fh.read().split())
    except (OSError, ValueError):
        return None


def _comm(pid):
    if not HAVE_PROC:
        return _ps_table().get(int(pid), (0, "", ""))[1]
    try:
        with open(f"/proc/{pid}/comm") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def _cmdline(pid):
    if not HAVE_PROC:
        return _ps_table().get(int(pid), (0, "", ""))[2]
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            return fh.read().replace(b"\0", b" ").decode("utf-8", "replace")
    except OSError:
        return ""


def foreground(cmd, pane_pid):
    """(command, busy) for a pane. Hermes launched from the rc file runs
    before job control, so the pane still reports the shell; look one level
    down to name what is really running."""
    if cmd not in SHELLS:
        return cmd, True
    kids = _children(pane_pid)
    if kids is not None:
        names = [n for n in (_comm(k) for k in kids) if n]
        return (names[0], True) if names else (cmd, False)
    try:
        out = subprocess.run(["ps", "-o", "comm=", "--ppid", str(pane_pid)],
                             capture_output=True, text=True, timeout=3).stdout.split()
    except (OSError, subprocess.SubprocessError):
        return cmd, False
    return (out[0], True) if out else (cmd, False)


def hermes_pid(pane_pid):
    """The Hermes process a tab's shell is running, or None."""
    return next((k for k in _children(pane_pid) or () if _comm(k) == "hermes"), None)


def extra_work(pid):
    """A process under Hermes that is not its own machinery (a shell, a
    build, a server it started), or "" when Hermes owns nothing else."""
    todo = list(_children(pid) or ())
    while todo:
        p = todo.pop()
        line = _cmdline(p)
        if not line:
            continue                    # already gone
        if not any(q in line for q in QUIET_CMDS):
            return _comm(p) or "?"
        todo.extend(_children(p) or ())
    return ""


def sleep_check(target, pane_pid):
    """(fingerprint, reason) for a Hermes tab. reason "" = safe to sleep now;
    the fingerprint is the screen without its status bar (the bar's clock ticks),
    so an unchanged fingerprint over time means nothing is happening."""
    pid = hermes_pid(pane_pid)
    if pid is None:
        return "", "no hermes"
    lines = TM.out("capture-pane", "-p", "-t", target).splitlines()
    bar = next((i for i in range(len(lines) - 1, -1, -1) if HERMES_BAR.match(lines[i])), None)
    if bar is None:
        return "", "no status bar"
    status = HERMES_BAR.match(lines[bar]).group(1).strip()  # type: ignore[union-attr]
    body = [ln for i, ln in enumerate(lines) if i != bar]
    fp = hashlib.sha1("\n".join(body).encode("utf-8", "replace")).hexdigest()
    if status != "ready":
        return fp, "working"
    prompt = next((ln for ln in reversed(lines) if "❯" in ln), None)
    if prompt is None:
        return fp, "no input line"
    if not PLACEHOLDER_RE.match(prompt.split("❯", 1)[1].strip()):
        return fp, "unsent draft"
    if AWAKE_MARKS.search("\n".join(lines[-20:])):
        return fp, "goal, subagent, task or question on screen"
    work = extra_work(pid)
    if work:
        return fp, f"running {work}"
    return fp, ""


HERMES_BAR = re.compile(r"^\s*─ (.+?) │")


def agent_status(target):
    """What a Hermes TUI's status bar says: "ready" while it waits for you,
    its activity ("(°ロ°) analyzing…") while it works, "" if none shows."""
    for line in reversed(TM.out("capture-pane", "-p", "-t", target).splitlines()):
        m = HERMES_BAR.match(line)
        if m:
            return m.group(1).strip()
    return ""


def hermes_profiles():
    """Hermes profile names: directory names only, nothing inside is read."""
    root = os.path.join(HERMES_ROOT, "profiles")
    try:
        names = sorted(d for d in os.listdir(root)
                       if PROFILE_RE.match(d) and d != "default" and os.path.isdir(os.path.join(root, d)))
    except OSError:
        names = []
    return ["default", *names]


# --------------------------------------------------------------------------- metadata
class Meta:
    """workspaces.json: names, ids, profiles and tab layout. Never secrets."""

    def __init__(self, path):
        self.path = path
        self.data = {"version": 2, "workspaces": {}}
        if os.path.exists(path):
            try:
                with open(path) as fh:
                    self.data = json.load(fh)
            except (OSError, ValueError) as exc:
                backup = f"{path}.corrupt-{int(time.time())}"
                os.rename(path, backup)
                log(f"metadata unreadable ({exc}); moved to {backup}")
        self.data.setdefault("workspaces", {})

    def save(self):
        # Durable replace: a power cut leaves either the old file or the new
        # one, never an empty file that would forget every workspace.
        tmp = self.path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(self.data, fh, indent=2, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.path)
        dfd = os.open(os.path.dirname(self.path), os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)

    @property
    def workspaces(self):
        return self.data["workspaces"]


META = Meta(META_FILE)


def ws_session(ws_id):
    return "omo-" + ws_id


def new_id():
    return uuid.uuid4().hex[:12]


def shell_quote(s):
    return "'" + s.replace("'", "'\\''") + "'"


SHELL_CMD = f"exec bash --rcfile {shell_quote(RCFILE)} -i"


def hermes_title(ws_id, tab_id):
    # Stable, name-independent identity for the saved Hermes conversation.
    return f"omo:{ws_id}:{tab_id}"


def hermes_command(ws, tab_id):
    profile = ws.get("profile") or ""
    flag = f"-p {shell_quote(profile)} " if profile else ""
    return f"{shell_quote(HERMES_BIN)} {flag}chat --tui -c {shell_quote(hermes_title(ws['id'], tab_id))} --create-if-missing"


def tab_kind(kind):
    """A kind the bridge can open; anything else is a plain shell."""
    return kind if kind in TAB_KINDS else "shell"


def _runnable(path):
    return bool(path) and os.path.isfile(path) and os.access(path, os.X_OK)


def find_agent(kind):
    """Absolute path of an agent CLI, or None. An explicit OMO_<AGENT>_BIN is
    the only answer when set (a wrong path reads as missing, never as some
    other copy); otherwise the bridge's PATH, then the usual install spots,
    because a user service's PATH is often shorter than a login shell's."""
    spec = AGENTS[kind]
    pinned = os.environ.get(spec["env"], "").strip()
    if pinned:
        found = pinned if os.sep in pinned else shutil.which(pinned)
        return found if _runnable(found) else None
    name = spec["bin"]
    for cand in (shutil.which(name), os.path.join(HOME, ".local", "bin", name),
                 os.path.join(HOME, ".claude", "local", name), os.path.join(HOME, ".npm-global", "bin", name),
                 os.path.join(HOME, ".bun", "bin", name), "/opt/homebrew/bin/" + name, "/usr/local/bin/" + name):
        if _runnable(cand):
            return cand
    return None


def agents_info():
    """Which agents this PC can run, for the "+" menu: {kind: {found, install}}."""
    hermes = HERMES_BIN if os.sep in HERMES_BIN else shutil.which(HERMES_BIN)
    info = {"hermes": {"found": _runnable(hermes), "install": "https://hermes-agent.nousresearch.com/docs"}}
    for kind, spec in AGENTS.items():
        info[kind] = {"found": find_agent(kind) is not None, "install": spec["install"]}
    return info


def claude_session(ws_id, tab_id):
    # Claude Code wants a UUID; derive it from the tab so a revive or a
    # restore after a session-server restart finds the same conversation.
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"omo:{ws_id}:{tab_id}"))


def agent_command(ws, tab):
    """The autorun line of a Claude Code / Codex tab (see omo_agent in
    mcshell.bashrc: it resumes Claude's session when one exists and prints
    an install hint instead of failing when the CLI is missing)."""
    kind = tab["kind"]
    path = find_agent(kind) or ""
    session = claude_session(ws["id"], tab["id"]) if kind == "claude" else ""
    return f"omo_agent {kind} {shell_quote(path)} {shell_quote(session)}"


def autorun_command(ws, tab):
    if tab["kind"] == "hermes":
        return hermes_command(ws, tab["id"])
    if tab["kind"] in AGENTS:
        return agent_command(ws, tab)
    return ""


def tab_env(ws, tab, autorun=True):
    hermes = tab["kind"] == "hermes"
    env = {
        "OMO_WORKSPACE": ws["id"], "OMO_WORKSPACE_NAME": ws["name"],
        "OMO_TAB": tab["id"], "OMO_TAB_NAME": tab["name"], "OMO_TAB_KIND": tab["kind"],
        "OMO_HERMES_PROFILE": ws.get("profile") or "", "OMO_HERMES_BIN": HERMES_BIN,
        "TERM": "xterm-256color",
        # Always set, even empty: a session's -e environment is inherited by
        # later windows, and a shell tab must never pick up a Hermes autorun.
        "OMO_AUTORUN": autorun_command(ws, tab) if autorun else "",
        "OMO_HERMES_TITLE": hermes_title(ws["id"], tab["id"]) if hermes else "",
    }
    return [a for k, v in env.items() for a in ("-e", f"{k}={v}")]


class BridgeError(Exception):
    pass


class Workspaces:
    """All workspace/tab operations. Serialised by one lock; tmux is the truth
    for what is running, the metadata file is the truth for names and ids."""

    def __init__(self):
        self.lock = threading.Lock()
        self.listeners = {}          # ws_id -> set(callback)

    # -- queries ----------------------------------------------------------
    def _ws(self, ws_id):
        ws = META.workspaces.get(ws_id)
        if not ws:
            raise BridgeError("no such workspace")
        return ws

    def _tab(self, ws, tab_id):
        for t in ws["tabs"]:
            if t["id"] == tab_id:
                return t
        raise BridgeError("no such tab")

    def describe(self, ws_id):
        """Workspace with live state folded in."""
        with self.lock:
            return self._describe_locked(self._ws(ws_id), TM.server_id())

    def _recover_base_locked(self, ws_id):
        """tmux keeps a grouped session's windows alive while any member of
        the group exists. If the base session is gone but a view survived,
        the shells are still running: recreate the base from the view so the
        workspace is reported (truthfully) as live rather than interrupted."""
        base = ws_session(ws_id)
        if TM.has_session(base):
            return True
        for name in TM.sessions():
            if name.startswith(base + "-v") and TM.ok("new-session", "-d", "-t", "=" + name, "-s", base):
                log(f"workspace {ws_id}: base session recreated from view {name}")
                return True
        return False

    def _windows_locked(self, ws, server):
        """{tab_id: window} for the live windows of a workspace. Windows made
        before tabs were tagged are adopted by their stored id, once."""
        session = ws_session(ws["id"])
        wins = TM.windows(session)
        by_tab = {w["tab"]: w for w in wins if w["tab"]}
        for t in ws["tabs"]:
            if t["id"] in by_tab or t.get("server"):
                continue
            legacy = next((w for w in wins if not w["tab"] and w["id"] == t.get("window")), None)
            if legacy and TM.ok("set-option", "-w", "-t", f"={session}:{legacy['id']}", "@omo_tab", t["id"]):
                legacy["tab"], t["server"] = t["id"], server
                by_tab[t["id"]] = legacy
                META.save()
        return by_tab

    def _describe_locked(self, ws, server):
        alive = bool(server) and self._recover_base_locked(ws["id"])
        wins = self._windows_locked(ws, server) if alive else {}
        self._remember_cwds_locked(ws, wins)
        tabs = []
        for t in ws["tabs"]:
            win = wins.get(t["id"])
            command, busy = foreground(win["cmd"], win["pid"]) if win else (None, False)
            if win:
                state = "live"
            elif server and t.get("server") == server:
                state = "exited"
            else:
                state = "interrupted"
            tab = {"id": t["id"], "name": t["name"], "kind": t["kind"], "state": state,
                   "window": win["id"] if win else None, "command": command, "busy": busy,
                   "sleeping": bool(win and t.get("asleep") and not busy)}
            if command == "hermes":
                tab["agent"] = agent_status(f"={ws_session(ws['id'])}:{win['id']}")
            tabs.append(tab)
        if alive:
            ws_state = "live"
        elif server and ws.get("server") == server:
            ws_state = "ended"          # every tab exited on its own
        else:
            ws_state = "interrupted"
        return {"id": ws["id"], "name": ws["name"], "kind": ws["kind"], "profile": ws.get("profile") or "",
                "created": ws.get("created"), "alive": alive, "state": ws_state, "tabs": tabs,
                "last_station": ws.get("last_station"), "keep_awake": bool(ws.get("keep_awake"))}

    def list(self):
        with self.lock:
            server = TM.server_id()
            return [self._describe_locked(ws, server) for ws in META.workspaces.values()]

    # -- mutations --------------------------------------------------------
    def create(self, name, kind, station=None, world=None, profile="", tabs=None):
        """A new workspace with one Hermes or shell tab by its kind, or with
        `tabs` ([{name, kind}], an authored world's preset) in that order."""
        with self.lock:
            if len(META.workspaces) >= LIMITS["workspaces"]:
                raise BridgeError(f"workspace limit reached ({LIMITS['workspaces']}). "
                                  "Close or reuse a workspace first.")
            kind = "hermes" if kind == "hermes" else "terminal"
            n = len(META.workspaces) + 1
            name = clean_name(name, 40).strip() or (f"Hermes {n}" if kind == "hermes" else f"Terminal {n}")
            ws = {"id": new_id(), "name": name, "kind": kind, "created": int(time.time()),
                  "profile": self._check_profile(profile), "tabs": [],
                  "last_station": station, "last_world": world}
            wanted = [t for t in tabs or [] if isinstance(t, dict)][:LIMITS["tabs_per_workspace"]]
            for t in wanted or [{"kind": "hermes" if kind == "hermes" else "shell"}]:
                tkind = tab_kind(t.get("kind"))
                tab = {"id": new_id(), "name": self._tab_name(ws, t.get("name"), tkind), "kind": tkind}
                self._open_window_locked(ws, tab)
                ws["tabs"].append(tab)
            META.workspaces[ws["id"]] = ws
            META.save()
            log(f"workspace create {ws['id']} '{name}' kind={kind} profile={ws['profile'] or 'default'}")
            return self._describe_locked(ws, TM.server_id())

    @staticmethod
    def _remember_cwds_locked(ws, wins):
        """Keep each live tab's working directory in the metadata, so a tab
        restored after a session-server restart opens where it was."""
        changed = False
        for t in ws["tabs"]:
            cwd = (wins.get(t["id"]) or {}).get("cwd")
            if cwd and os.path.isabs(cwd) and t.get("cwd") != cwd:
                t["cwd"], changed = cwd, True
        if changed:
            META.save()

    def remember_cwds(self):
        """One pass over every workspace (worker thread, periodic)."""
        with self.lock:
            server = TM.server_id()
            if not server:
                return
            for ws in META.workspaces.values():
                if TM.has_session(ws_session(ws["id"])):
                    self._remember_cwds_locked(ws, self._windows_locked(ws, server))

    def restore(self, ws_id):
        """Reopen every interrupted tab of a workspace, as it was: same tab
        ids, names and order, shells in their last directory, Hermes tabs on
        their own conversation (asleep ones stay asleep until looked at).
        Only tabs the session server lost; a tab the user exited stays exited.
        Commands that were running are never re-run. Returns tabs reopened."""
        with self.lock:
            ws = META.workspaces.get(ws_id)
            server = TM.server_id()
            if not ws or not server:
                return 0            # never start a tmux server outside its service
            desc = self._describe_locked(ws, server)
            lost = {t["id"] for t in desc["tabs"] if t["state"] == "interrupted"}
            n = 0
            for tab in ws["tabs"]:
                if tab["id"] not in lost:
                    continue
                try:
                    self._open_window_locked(ws, tab, autorun=not tab.get("asleep"))
                    n += 1
                except BridgeError as exc:
                    log(f"restore {ws_id}/{tab['id']}: {exc}")
            if n:
                META.save()
                log(f"restore {ws_id} '{ws['name']}': reopened {n} tab(s)")
            return n

    def _open_window_locked(self, ws, tab, autorun=True):
        """Start this tab's window, creating the session when it is gone.
        Reviving one tab of an interrupted workspace starts only that tab."""
        session = ws_session(ws["id"])
        cwd = tab.get("cwd") if tab.get("cwd") and os.path.isdir(tab["cwd"]) else HOME
        common = ("-n", tab["name"], "-c", cwd, *tab_env(ws, tab, autorun), SHELL_CMD)
        if TM.has_session(session):
            p = TM.run("new-window", "-d", "-P", "-F", "#{window_id}", "-t", "=" + session, *common)
        else:
            p = TM.run("new-session", "-d", "-P", "-F", "#{window_id}", "-s", session,
                       "-x", "100", "-y", "30", *common)
        if p.returncode != 0:
            raise BridgeError("tmux could not open the tab: " + p.stderr.strip())
        tab["window"] = p.stdout.strip()
        TM.run("set-option", "-w", "-t", f"={session}:{tab['window']}", "@omo_tab", tab["id"])
        tab["server"] = ws["server"] = TM.server_id()

    def rename(self, ws_id, name):
        with self.lock:
            ws = self._ws(ws_id)
            name = clean_name(name, 40).strip()
            if not name:
                raise BridgeError("name cannot be empty")
            ws["name"] = name
            META.save()
            log(f"workspace rename {ws_id} -> '{name}'")
            return self._describe_locked(ws, TM.server_id())

    def _check_profile(self, profile):
        profile = (profile or "").strip()
        if profile in ("", "default"):
            return ""
        if profile not in hermes_profiles():
            raise BridgeError(f"no Hermes profile named '{clean(profile, 40)}'")
        return profile

    def set_profile(self, ws_id, profile):
        with self.lock:
            ws = self._ws(ws_id)
            ws["profile"] = self._check_profile(profile)
            META.save()
            log(f"workspace profile {ws_id} -> {ws['profile'] or 'default'}")
            return self._describe_locked(ws, TM.server_id())

    def touch(self, ws_id, station, world):
        with self.lock:
            ws = META.workspaces.get(ws_id)
            if ws and (ws.get("last_station"), ws.get("last_world")) != (station, world):
                ws["last_station"], ws["last_world"] = station, world
                META.save()

    def tab_new(self, ws_id, kind, name=None):
        with self.lock:
            ws = self._ws(ws_id)
            if len(ws["tabs"]) >= LIMITS["tabs_per_workspace"]:
                raise BridgeError(f"tab limit reached ({LIMITS['tabs_per_workspace']} per workspace)")
            kind = tab_kind(kind)
            tab = {"id": new_id(), "name": self._tab_name(ws, name, kind), "kind": kind}
            self._open_window_locked(ws, tab)
            ws["tabs"].append(tab)
            META.save()
            log(f"tab new {ws_id}/{tab['id']} '{tab['name']}' kind={kind}")
            return self._describe_locked(ws, TM.server_id()), tab["id"]

    @staticmethod
    def _tab_name(ws, name, kind):
        """The asked-for name (or the kind's: Shell, Hermes, Claude Code, Codex),
        numbered when the workspace already has it."""
        base = clean_name(name, 24).strip() or TAB_NAMES[tab_kind(kind)]
        existing = {t["name"] for t in ws["tabs"]}
        final, n = base, 2
        while final in existing:
            final, n = f"{base} {n}", n + 1
        return final[:24]

    def tab_rename(self, ws_id, tab_id, name):
        with self.lock:
            ws = self._ws(ws_id)
            tab = self._tab(ws, tab_id)
            name = clean_name(name, 24).strip()
            if not name:
                raise BridgeError("name cannot be empty")
            tab["name"] = name
            server = TM.server_id()
            win = self._windows_locked(ws, server).get(tab_id)
            if win:
                TM.run("rename-window", "-t", f"={ws_session(ws_id)}:{win['id']}", "--", name)
            META.save()
            return self._describe_locked(ws, server)

    def tab_close(self, ws_id, tab_id, force=False):
        """Returns (workspace, needs_confirm_message)."""
        with self.lock:
            ws = self._ws(ws_id)
            tab = self._tab(ws, tab_id)
            server = TM.server_id()
            desc = self._describe_locked(ws, server)
            state = next(t for t in desc["tabs"] if t["id"] == tab_id)
            if state["state"] == "live" and state["busy"] and not force:
                return desc, (f"'{tab['name']}' is still running {state['command']}. "
                              "Closing the tab ends that process. Close anyway?")
            remaining = [t for t in ws["tabs"] if t["id"] != tab_id]
            live_others = [t for t in desc["tabs"] if t["id"] != tab_id and t["state"] == "live"]
            if not remaining or (state["state"] == "live" and not live_others):
                # A workspace keeps at least one tab, and killing the last live
                # window destroys the session and every screen's view with it.
                # Open a fresh shell first so whoever is looking keeps a tab.
                fresh = {"id": new_id(), "name": "Shell", "kind": "shell"}
                self._open_window_locked(ws, fresh)
                remaining.append(fresh)
            if state["window"]:
                TM.run("kill-window", "-t", f"={ws_session(ws_id)}:{state['window']}")
            ws["tabs"] = remaining
            META.save()
            log(f"tab close {ws_id}/{tab_id} force={force}")
            return self._describe_locked(ws, TM.server_id()), None

    def tab_revive(self, ws_id, tab_id, mode):
        with self.lock:
            ws = self._ws(ws_id)
            tab = self._tab(ws, tab_id)
            desc = self._describe_locked(ws, TM.server_id())
            if next(t for t in desc["tabs"] if t["id"] == tab_id)["state"] == "live":
                raise BridgeError("that tab is already running")
            tab["kind"] = tab_kind(mode)
            tab.pop("asleep", None)
            self._open_window_locked(ws, tab)
            META.save()
            log(f"tab revive {ws_id}/{tab_id} mode={tab['kind']}")
            return self._describe_locked(ws, TM.server_id())

    def delete(self, ws_id, force=False):
        """Close a whole workspace. Returns (ok, message). Refuses while any
        tab is busy unless forced; the caller shows the warning."""
        with self.lock:
            ws = self._ws(ws_id)
            desc = self._describe_locked(ws, TM.server_id())
            busy = [t["name"] for t in desc["tabs"] if t["state"] == "live" and t["busy"]]
            if busy and not force:
                return False, (f"'{ws['name']}' still has work running in: {', '.join(busy)}. "
                               "Closing the workspace ends those processes. Close anyway?")
            session = ws_session(ws_id)
            for name in TM.sessions():
                if name == session or name.startswith(session + "-v"):
                    TM.run("kill-session", "-t", "=" + name)
            META.workspaces.pop(ws_id, None)
            META.save()
            log(f"workspace delete {ws_id} '{ws['name']}' force={force}")
            return True, f"closed workspace '{ws['name']}'"

    def hermes(self, ws_id, tab_id):
        """Launch or resume this tab's Hermes conversation in its shell. Never
        restarts a running Hermes; never re-runs anything interrupted."""
        with self.lock:
            ws = self._ws(ws_id)
            tab = self._tab(ws, tab_id)
            state = next(t for t in self._describe_locked(ws, TM.server_id())["tabs"] if t["id"] == tab_id)
            if state["state"] != "live":
                raise BridgeError("this tab is not running; revive it first")
            if state["busy"]:
                if state["command"] in ("hermes", "node") or state["command"].startswith("python"):
                    return "Hermes is already running in this tab"
                return f"'{state['command']}' is running here; finish or interrupt it first"
            target = f"={ws_session(ws_id)}:{state['window']}"
            TM.run("send-keys", "-t", target, "-l", hermes_command(ws, tab_id))
            TM.run("send-keys", "-t", target, "Enter")
            tab["kind"] = "hermes"
            tab.pop("asleep", None)
            META.save()
            return f"resuming Hermes ({ws.get('profile') or 'default'} profile)"

    def set_keep_awake(self, ws_id, on):
        with self.lock:
            ws = self._ws(ws_id)
            if on:
                ws["keep_awake"] = True
            else:
                ws.pop("keep_awake", None)
            META.save()
            log(f"workspace keep_awake {ws_id} -> {bool(on)}")
            return self._describe_locked(ws, TM.server_id())

    # -- auto-sleep ---------------------------------------------------------
    def sleep_candidates(self):
        """[(ws_id, tab_id, session, window, pane_pid)] for every awake Hermes
        tab in a workspace that allows sleeping. Cheap: one tmux call per
        workspace, no process or screen reads (the sleeper does those unlocked)."""
        out = []
        with self.lock:
            if not TM.server_id():
                return out
            for ws in META.workspaces.values():
                if ws.get("keep_awake"):
                    continue
                hermes_tabs = [t for t in ws["tabs"] if t["kind"] == "hermes"]
                if not hermes_tabs:
                    continue
                session = ws_session(ws["id"])
                wins = {w["tab"]: w for w in TM.windows(session) if w["tab"]}
                for t in hermes_tabs:
                    w = wins.get(t["id"])
                    if not w or w["cmd"] not in SHELLS:
                        continue
                    if t.get("asleep"):
                        if hermes_pid(w["pid"]) is None:
                            continue        # asleep, as marked
                        t.pop("asleep", None)   # someone started Hermes by hand: awake again
                        META.save()
                    out.append((ws["id"], t["id"], session, w["id"], w["pid"]))
        return out

    def mark_asleep(self, ws_id, tab_id, asleep):
        with self.lock:
            ws = META.workspaces.get(ws_id)
            tab = next((t for t in ws["tabs"] if t["id"] == tab_id), None) if ws else None
            if tab is None:
                return None
            if asleep:
                tab["asleep"] = True
            else:
                tab.pop("asleep", None)
            META.save()
            return self._describe_locked(ws, TM.server_id())

    def is_asleep(self, ws_id, tab_id):
        with self.lock:
            ws = META.workspaces.get(ws_id)
            return bool(ws and any(t["id"] == tab_id and t.get("asleep") for t in ws["tabs"]))

    def wake(self, ws_id, tab_id):
        """Resume a sleeping tab's conversation (worker thread). Waits out a
        /exit still in flight, then starts Hermes exactly as its tab would.
        Returns a toast, or None when the tab was not asleep."""
        if not self.is_asleep(ws_id, tab_id):
            return None
        end = time.monotonic() + EXIT_WAIT
        while time.monotonic() < end:
            with self.lock:
                ws = META.workspaces.get(ws_id)
                tab = next((t for t in ws["tabs"] if t["id"] == tab_id), None) if ws else None
                if tab is None or not tab.get("asleep"):
                    return None
                win = self._windows_locked(ws, TM.server_id()).get(tab_id)
                if win is None:
                    tab.pop("asleep", None)
                    META.save()
                    return None
                if win["cmd"] in SHELLS and hermes_pid(win["pid"]) is None:
                    command, busy = foreground(win["cmd"], win["pid"])
                    if busy:
                        # Something else took the shell; never type over it.
                        tab.pop("asleep", None)
                        META.save()
                        return f"'{command}' is running in this tab, so Hermes was not resumed"
                    target = f"={ws_session(ws_id)}:{win['id']}"
                    TM.run("send-keys", "-t", target, "-l", hermes_command(ws, tab_id))
                    TM.run("send-keys", "-t", target, "Enter")
                    tab.pop("asleep", None)
                    META.save()
                    log(f"wake {ws_id}/{tab_id}: a screen opened it; resuming its conversation")
                    return "waking Hermes…"
            time.sleep(0.25)
        log(f"wake {ws_id}/{tab_id}: the tab never returned to its shell")
        return None

    # -- change notifications --------------------------------------------
    def subscribe(self, ws_id, cb):
        self.listeners.setdefault(ws_id, set()).add(cb)

    def unsubscribe(self, ws_id, cb):
        self.listeners.get(ws_id, set()).discard(cb)


WS = Workspaces()


# --------------------------------------------------------------------------- views
class View:
    """One open screen looking at one tab: a grouped tmux session + a PTY."""

    _options_for = ""               # the session server VIEW_OPTIONS were last applied to

    def __init__(self, ws_id, tab_id, window, cols, rows, notify, on_clipboard):
        server = TM.server_id()
        if server and server != View._options_for:
            for opt in VIEW_OPTIONS:
                TM.run("set", *opt)
            View._options_for = server
        self.ws_id, self.tab_id, self.window = ws_id, tab_id, window
        self.name = f"omo-{ws_id}-v{secrets.token_hex(3)}"
        self.target = f"={self.name}:"
        self.scroll_pos, self.history, self.alt = 0, 0, False
        p = TM.run("new-session", "-d", "-t", "=" + ws_session(ws_id), "-s", self.name,
                   "-x", str(cols), "-y", str(rows))
        if p.returncode != 0:
            raise BridgeError("could not open a view: " + p.stderr.strip())
        TM.run("select-window", "-t", f"={self.name}:{window}")
        self.pty = termcore.PtyScreen(cols, rows, TM.argv("attach-session", "-t", "=" + self.name),
                                      env={"TERM": "xterm-256color"}, notify=notify, on_clipboard=on_clipboard)

    def poll(self):
        """Refresh scroll position, history size and alternate screen; returns
        the window id now shown ("" when tmux did not answer in time: the
        last known state stands and the next poll asks again)."""
        try:
            out = TM.out("display", "-p", "-t", self.target,
                         "#{pane_in_mode}\t#{scroll_position}\t#{history_size}\t#{alternate_on}\t#{window_id}",
                         timeout=POLL_TIMEOUT)
        except subprocess.TimeoutExpired:
            return ""
        parts = out.strip().split("\t")
        if len(parts) != 5:
            return ""
        try:
            self.scroll_pos = int(parts[1]) if parts[0] == "1" and parts[1] else 0
            self.history = int(parts[2] or 0)
        except ValueError:
            self.scroll_pos = 0
        self.alt = parts[3] == "1"
        return parts[4]

    def scroll(self, by):
        """Move through tmux's history by `by` lines (positive = older)."""
        self.poll()
        if by > 0:
            if self.scroll_pos == 0:
                TM.run("copy-mode", "-t", self.target)
            TM.run("send-keys", "-t", self.target, "-X", "-N", str(min(by, 100000)), "scroll-up")
        elif self.scroll_pos:
            TM.run("send-keys", "-t", self.target, "-X", "-N", str(min(-by, 100000)), "scroll-down")
            self.poll()
            if self.scroll_pos == 0:
                TM.run("send-keys", "-t", self.target, "-X", "cancel")
        self.poll()
        self.pty.notify()

    def live_again(self):
        """Typing returns a scrolled-back screen to the live terminal."""
        if self.scroll_pos:
            TM.run("send-keys", "-t", self.target, "-X", "cancel")
            self.scroll_pos = 0

    def find(self, q, step, current):
        """Find q in the whole history and screen; scroll so the chosen match
        is in view. `current` is the (line, x) found last time; step 0 keeps
        to it (or the newest match), 1 goes older, -1 newer.
        Returns (match, index from the newest, count, screen row)."""
        self.poll()
        self.live_again()
        self.poll()
        lines = TM.out("capture-pane", "-p", "-t", self.target, "-S", "-", "-E", "-").split("\n")[:-1]
        rx = termcore.matcher(q)
        hits = [(y, m.start()) for y, line in enumerate(lines) for m in rx.finditer(line)]
        if not hits:
            return None, 0, 0, None
        if current is None:
            i = len(hits) - 1
        else:
            at = next((n for n, h in enumerate(hits) if h >= current), len(hits))
            if step == 0:
                i = at if at < len(hits) and hits[at] == current else max(0, at - 1)
            else:
                i = (at - 1 if step > 0 else at + (1 if at < len(hits) and hits[at] == current else 0)) % len(hits)
        line = hits[i][0]
        height = max(1, len(lines) - self.history)
        pos = min(self.history, self.history - line + height // 2) if line < self.history else 0
        if pos > 0:
            TM.run("copy-mode", "-t", self.target)
            TM.run("send-keys", "-t", self.target, "-X", "-N", str(pos), "scroll-up")
        self.poll()
        self.pty.notify()
        return hits[i], len(hits) - i, len(hits), line - (self.history - self.scroll_pos)

    def clear_history(self):
        self.poll()
        self.live_again()
        TM.run("clear-history", "-t", self.target)
        self.poll()

    def close(self):
        try:
            self.pty.close()
        finally:
            TM.run("kill-session", "-t", "=" + self.name)


VIEWS = set()
VIEWS_LOCK = threading.Lock()
CONNS = set()               # every authenticated connection
LAST_INPUT = {}             # (ws_id, tab_id) -> monotonic time a player last typed there


# --------------------------------------------------------------------------- auto-sleep
class Sleeper:
    """Ends idle, unwatched Hermes tabs with a clean /exit and marks them
    asleep; Conn.attach wakes them. Never touches a tab that a screen shows,
    that is working, has a draft, a goal, a question, or any process of its
    own running; and never more than SLEEP_PER_SCAN tabs per scan."""

    def __init__(self):
        self.seen = {}              # (ws_id, tab_id) -> (fingerprint, quiet since)

    @staticmethod
    def watched():
        """Tabs some screen shows right now (called off the event loop, so
        copy the set defensively: a socket may open or close meanwhile)."""
        for _ in range(5):
            try:
                conns = list(CONNS)
                break
            except RuntimeError:
                time.sleep(0.01)
        else:
            return None                 # cannot tell: treat every tab as watched
        return {(c.ws_id, c.current_tab) for c in conns if c.view is not None or c.attach_lock.locked()}

    def scan(self, now=None):
        """One pass (worker thread). Returns the (ws_id, tab_id) put to sleep."""
        now = time.monotonic() if now is None else now
        slept, live = [], set()
        for ws_id, tab_id, session, window, pane_pid in WS.sleep_candidates():
            key = (ws_id, tab_id)
            live.add(key)
            watched = self.watched()
            if watched is None or key in watched:
                self.seen.pop(key, None)
                continue
            fp, reason = sleep_check(f"={session}:{window}", pane_pid)
            old = self.seen.get(key)
            since = old[1] if old and old[0] == fp and not reason else now
            since = max(since, LAST_INPUT.get(key, 0.0))
            self.seen[key] = (fp, since)
            if reason or now - since < SLEEP_AFTER or len(slept) >= SLEEP_PER_SCAN:
                continue
            if self.put_to_sleep(ws_id, tab_id, session, window, pane_pid, fp):
                slept.append(key)
                self.seen.pop(key, None)
        for key in set(self.seen) - live:
            self.seen.pop(key, None)
        return slept

    def put_to_sleep(self, ws_id, tab_id, session, window, pane_pid, fp):
        target = f"={session}:{window}"
        # Last look, right before acting: a screen may have opened, or the
        # player may have typed, since the scan read this tab.
        again, reason = sleep_check(target, pane_pid)
        watched = self.watched()
        if reason or again != fp or watched is None or (ws_id, tab_id) in watched:
            return False
        desc = WS.mark_asleep(ws_id, tab_id, True)
        if desc is None:
            return False
        TM.run("send-keys", "-t", target, "-l", "/exit")
        time.sleep(ENTER_GAP * 3)
        TM.run("send-keys", "-t", target, "Enter")
        end = time.monotonic() + EXIT_WAIT
        while time.monotonic() < end and hermes_pid(pane_pid):
            time.sleep(0.25)
        if hermes_pid(pane_pid):
            WS.mark_asleep(ws_id, tab_id, False)
            log(f"sleep {ws_id}/{tab_id}: Hermes did not exit within {EXIT_WAIT:.0f}s; left awake")
            return False
        log(f"sleep {ws_id}/{tab_id}: idle {SLEEP_AFTER / 60:.0f}m and unwatched; conversation saved")
        self.announce(ws_id)
        return True

    @staticmethod
    def announce(ws_id, desc=None):
        """Tell every screen and panel subscribed to this workspace."""
        if desc is None:
            try:
                desc = WS.describe(ws_id)
            except BridgeError:
                return
        for cb in list(WS.listeners.get(ws_id, ())):
            cb(desc)


SLEEPER = Sleeper()


async def remember_loop():
    """Note where every tab is working, so a restore can put it back there."""
    while True:
        await asyncio.sleep(REMEMBER_EVERY)
        try:
            await asyncio.to_thread(WS.remember_cwds)
        except Exception as exc:
            log(f"remember error: {exc!r}")


async def sleep_loop():
    if SLEEP_AFTER <= 0:
        log("auto-sleep off (OMO_SLEEP_AFTER=0)")
        return
    log(f"auto-sleep on: idle Hermes tabs sleep after {SLEEP_AFTER / 60:.0f}m unwatched")
    while True:
        await asyncio.sleep(SLEEP_SCAN)
        try:
            await asyncio.to_thread(SLEEPER.scan)
        except Exception as exc:        # a scan bug must never take the bridge down
            log(f"sleep scan error: {exc!r}")


# --------------------------------------------------------------------------- host desktop
# The clipboard and the browser live on the desktop the game runs on. The
# bridge is a user service without a display of its own, so it finds the live
# X display (OMO_DISPLAY pins one: the test stack uses its private Xvfb, so a
# test never touches the player's clipboard).
def _xauthority():
    """The X cookie for this user's desktop. A user service inherits no
    XAUTHORITY, and GNOME/mutter's Xwayland refuses clients without its
    cookie (a fresh /run/user/<uid>/.mutter-Xwaylandauth.* per login), so
    find it rather than fall through to some other display."""
    if os.environ.get("XAUTHORITY"):
        return os.environ["XAUTHORITY"]
    run = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    found = []
    for pattern in (".mutter-Xwaylandauth.*", "xauth_*", "gdm/Xauthority"):
        found += glob.glob(os.path.join(run, pattern))
    found = [p for p in found if os.path.isfile(p) and os.stat(p).st_uid == os.getuid()]
    if found:
        return max(found, key=os.path.getmtime)
    home = os.path.expanduser("~/.Xauthority")
    return home if os.path.isfile(home) else ""


def _x_env(disp):
    env = {**os.environ, "DISPLAY": disp,
           "XDG_RUNTIME_DIR": os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")}
    xauth = _xauthority()
    if xauth:
        env["XAUTHORITY"] = xauth
    return env


def _live_display():
    if DISPLAY:
        return DISPLAY
    cached = getattr(_live_display, "_cache", None)
    if cached and _x_ok(cached):
        return cached
    candidates = [os.environ["DISPLAY"]] if os.environ.get("DISPLAY") else []
    # Only this user's own X servers: another account's open Xvfb accepts
    # anyone, and copying there sends the player's text to that account.
    try:
        for s in sorted(os.listdir("/tmp/.X11-unix")):
            if s.startswith("X") and s[1:].isdigit() and \
                    os.stat(os.path.join("/tmp/.X11-unix", s)).st_uid == os.getuid():
                candidates.append(":" + s[1:])
    except OSError:
        pass
    for disp in candidates:
        if _x_ok(disp):
            _live_display._cache = disp
            return disp
    return os.environ.get("DISPLAY") or ":0"


def _x_ok(disp):
    try:
        return subprocess.run(["xdpyinfo", "-display", disp], capture_output=True, timeout=3,
                              env=_x_env(disp)).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _desktop_env():
    env = _x_env(_live_display())
    if DISPLAY:
        env.pop("WAYLAND_DISPLAY", None)
    return env


def _clip_tools(cmds):
    return [c for c in cmds if shutil.which(c[0]) and not (DISPLAY and c[0].startswith("wl-"))]


# macOS: pbcopy/pbpaste. WSL: the Windows clipboard (clip.exe takes UTF-16
# for anything beyond ASCII; PowerShell reads it back). Tests pin OMO_DISPLAY
# to a private Xvfb, which always means the X tools.
COPY_CMDS = (["xclip", "-selection", "clipboard"], ["xsel", "-ib"], ["wl-copy"])
PASTE_CMDS = (["xclip", "-selection", "clipboard", "-o"], ["xsel", "-ob"], ["wl-paste", "-n"])
if IS_MAC and not DISPLAY:
    COPY_CMDS, PASTE_CMDS = (["pbcopy"],), (["pbpaste"],)
elif IS_WSL and not DISPLAY:
    COPY_CMDS = (["clip.exe"], *COPY_CMDS)
    PASTE_CMDS = (["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                   "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-Clipboard -Raw"], *PASTE_CMDS)


def clipboard_copy(text):
    for cmd in _clip_tools(COPY_CMDS):
        try:
            p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, start_new_session=True, env=_desktop_env())
            p.stdin.write(text.encode("utf-16-le") if cmd[0] == "clip.exe" else text.encode())
            p.stdin.close()
            return cmd[0]
        except OSError:
            continue
    return None


def clipboard_paste():
    for cmd in _clip_tools(PASTE_CMDS):
        try:
            p = subprocess.run(cmd, capture_output=True, timeout=5, env=_desktop_env())
            if p.returncode == 0:
                out = p.stdout.decode("utf-8", "replace")
                return out[:-2] if cmd[0] == "powershell.exe" and out.endswith("\r\n") else out
        except (OSError, subprocess.SubprocessError):
            continue
    return ""


def _visible(s):
    # ASCII only, and no "?": what CC could not show it turned into "?".
    return re.sub(r"[^\x21-\x3e\x40-\x7e]", "", s)


def paste_text(given):
    """What a paste types. Without `given`, the host clipboard. CC hands a
    Ctrl+V over as a cut-down copy of that same clipboard (its own charset,
    length-capped); when it is one, paste the real thing, else what came."""
    host = clipboard_paste()
    if given is None:
        return host
    if host and _visible(given) and _visible(host).startswith(_visible(given)[:64]):
        return host
    return given


def bracketed(text):
    """Paste bytes: controls stripped (a pasted ESC could end the bracket
    early and run the rest as keys), newlines as Enter, wrapped in bracketed
    paste so shells and TUIs take it as one block instead of running each
    line (tmux drops the markers for apps that did not ask for them)."""
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", text.replace("\r\n", "\n")).replace("\n", "\r")
    return b"\x1b[200~" + text[:PASTE_MAX].encode("utf-8", "replace") + b"\x1b[201~"


def open_url(url):
    """Open a link in the host's browser. Only http(s), never one that
    carries the bridge token, never through a shell."""
    if not re.fullmatch(r"https?://[\x21-\x7e]{1,2000}", url or "") or TOKEN in url:
        return False
    try:
        subprocess.Popen([URL_OPENER, url], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True, env=_desktop_env())
        return True
    except OSError:
        return False


_LAST_CLIP = ["", 0.0]
_CLIP_LOCK = threading.Lock()


def app_copy(text):
    """An app copied (OSC 52). Every view of the pane receives the same
    sequence, so a repeat within a second is the same copy."""
    with _CLIP_LOCK:
        now = time.monotonic()
        if _LAST_CLIP[0] == text and now - _LAST_CLIP[1] < 1.0:
            return None
        _LAST_CLIP[:] = [text, now]
    return clipboard_copy(text) or "no clipboard tool"


# --------------------------------------------------------------------------- connection
class Conn:
    def __init__(self, ws):
        self.ws = ws
        self.authed = False
        self.closed = False
        self.station = None
        self.world = None
        self.role = "screen"        # "screen" (the in-game client) or "ui" (mod controls)
        self.scope = None           # a screen's one workspace
        self.view = None
        self.ws_id = None
        self.current_tab = None
        self.sel = None             # normalised (x1, y1, x2, y2) on this view's screen
        self.sel_text = None        # the text under `sel` when it was made: what a copy takes
        self.find = None            # (query, row of the current match)
        self.find_at = None         # (history line, x) of the current match
        self.dirty_all = False
        self.sender = None
        self.subscribed = None
        self.loop = asyncio.get_running_loop()
        self.wake = asyncio.Event()
        self.last_text = 0.0        # when typed text last reached the PTY (see type_in)
        self.pending_input = []     # keys typed while a steer re-attaches this screen
        self.attach_lock = asyncio.Lock()
        self.last_active = 0.0      # last input or screen change on this view (monotonic)
        self.paused = False         # no player near this screen: show the last frame, hold no view
        self.size = (51, 19)        # the last size this screen asked for (resume re-attaches at it)

    async def send(self, obj):
        try:
            await self.ws.send(json.dumps(obj))
        except websockets.ConnectionClosed:
            pass

    async def toast(self, m):
        await self.send({"t": "toast", "m": m})

    async def error(self, m):
        await self.send({"t": "error", "m": m})

    def notify(self):
        """Screen changed (called from the PTY reader thread)."""
        self.loop.call_soon_threadsafe(self.wake.set)

    def on_workspace_change(self, desc):
        asyncio.run_coroutine_threadsafe(self.send({"t": "workspace", "ws": desc}), self.loop)

    def on_clipboard(self, text):
        """OSC 52 from the pane (PTY reader thread)."""
        async def copy():
            via = await asyncio.to_thread(app_copy, text)
            if via:
                await self.toast(f"copied by the app: {len(text)} chars ({via})")
        asyncio.run_coroutine_threadsafe(copy(), self.loop)

    # -- frame pump --------------------------------------------------------
    async def push_frames(self, view):
        pty = view.pty
        prev_rows, prev_cursor, prev_size, prev_scroll = {}, None, None, None
        last_poll, bells, last_bell = 0.0, pty.bells, 0.0
        started = time.monotonic()
        try:
            while self.view is view and pty.alive:
                try:
                    await asyncio.wait_for(self.wake.wait(), 0.5)
                except TimeoutError:
                    pass
                self.wake.clear()
                if self.view is not view:
                    return
                now = time.monotonic()
                if self.paused and now - started >= PARK_AFTER and not self.dirty_all:
                    await self._park(view)
                    return
                # tmux state (scroll position, which window is shown) only moves
                # when someone uses the screen or it repaints; a quiet screen is
                # checked every 2s instead of every 0.5s, so a room full of
                # walls does not crowd out the one being typed on.
                every = POLL_ACTIVE if now - self.last_active < 10 else POLL_QUIET
                if now - last_poll > every:
                    shown, last_poll = await asyncio.to_thread(view.poll), now
                    if shown and shown != view.window:
                        break               # our window died; tmux moved the view on
                if pty.bells != bells and now - last_bell > 1.0:
                    bells, last_bell = pty.bells, now
                    await self.send({"t": "bell"})
                if self.sel and self.sel_text is not None and \
                        termcore.selection_text(pty.text_lines(), self.sel, pty.cols) != self.sel_text:
                    # The app redrew under the highlight (a TUI streaming or
                    # scrolling): drop the highlight rather than leave it on
                    # other text. A copy still takes what was highlighted.
                    self.sel = None
                frame = pty.frame(self.sel, self.find)
                size = (frame["cols"], frame["rows"])
                full = size != prev_size or self.dirty_all
                if full:
                    prev_rows, prev_size, self.dirty_all = {}, size, False
                changed = []
                for idx, row in enumerate(frame["lines"]):
                    if prev_rows.get(idx) != row:
                        prev_rows[idx] = row
                        changed.append([idx + 1, row])
                scroll = (view.scroll_pos, view.history)
                if not changed and frame["cursor"] == prev_cursor and scroll == prev_scroll and not full:
                    continue
                prev_cursor, prev_scroll = frame["cursor"], scroll
                await self.send({"t": "frame", "cols": frame["cols"], "rows": frame["rows"],
                                 "cursor": frame["cursor"], "cursor_hidden": frame["cursor_hidden"],
                                 "full": full, "rows_changed": changed, "scroll": scroll[0],
                                 "max_scroll": scroll[1], "mouse": frame["mouse"]})
                await asyncio.sleep(1.0 / FPS)
        except asyncio.CancelledError:
            raise
        except Exception as exc:        # a render bug must never leave a frozen "live" frame
            log(f"frame pump error station={self.station}: {exc!r}")
        if self.view is not view:
            return
        # The tab exited, the session server went away, or the pump broke.
        # Say which; never leave a stale frame looking healthy.
        state, reason = "exited", "this tab's process ended"
        try:
            desc = await asyncio.to_thread(WS.describe, self.ws_id)
            if desc["state"] == "interrupted":
                state, reason = "interrupted", "the session server or host restarted"
        except BridgeError:
            pass
        self.view, self.sender = None, None
        with VIEWS_LOCK:
            VIEWS.discard(view)
        await asyncio.to_thread(view.close)
        await self.send({"t": "detached", "state": state, "reason": reason})

    def redraw(self):
        self.dirty_all = True
        self.wake.set()

    async def _park(self, view):
        """Paused: drop the view quietly. The screen keeps its last frame (no
        "detached": nothing ended), and the tab counts as unwatched."""
        async with self.attach_lock:
            if self.view is not view:
                return
            self.view, self.sender = None, None
            with VIEWS_LOCK:
                VIEWS.discard(view)
        await asyncio.to_thread(view.close)
        log(f"park station={self.station} ws={self.ws_id} tab={self.current_tab} view={view.name}")

    # -- attach/switch -----------------------------------------------------
    async def attach(self, ws_id, tab_id, cols, rows):
        async with self.attach_lock:
            desc = await asyncio.to_thread(WS.describe, ws_id)
            if any(t["state"] == "interrupted" for t in desc["tabs"]):
                # The session server restarted (OOM, reboot): coming back to
                # the screen brings the workspace's terminals back as they were.
                restored = await asyncio.to_thread(WS.restore, ws_id)
                if restored:
                    desc = await asyncio.to_thread(WS.describe, ws_id)
                    await self.toast(f"restored {restored} terminal{'s' if restored != 1 else ''} after a restart")
                    Sleeper.announce(ws_id, desc)
            tab = next((t for t in desc["tabs"] if t["id"] == tab_id), None)
            if tab is None or (not tab_id and tab["state"] != "live"):
                tab = next((t for t in desc["tabs"] if t["state"] == "live"), None) or tab \
                    or (desc["tabs"][0] if desc["tabs"] else None)
            if tab is None:
                raise BridgeError("workspace has no tabs")
            await self._detach_locked()
            self.ws_id, self.current_tab = ws_id, tab["id"]
            self.sel = self.sel_text = self.find = self.find_at = None
            if self.subscribed != ws_id:
                if self.subscribed:
                    WS.unsubscribe(self.subscribed, self.on_workspace_change)
                WS.subscribe(ws_id, self.on_workspace_change)
                self.subscribed = ws_id
            await asyncio.to_thread(WS.touch, ws_id, self.station, self.world)
            if tab["state"] != "live":
                # Nothing to attach to. Report the truth and stop here.
                self.pending_input = []
                await self.send({"t": "attached", "ws": desc, "tab": tab["id"], "state": tab["state"]})
                return
            if tab.get("sleeping") or await asyncio.to_thread(WS.is_asleep, ws_id, tab["id"]):
                # Opening a screen on a sleeping tab wakes it: the same
                # conversation, resumed before the view shows it.
                woke = await asyncio.to_thread(WS.wake, ws_id, tab["id"])
                if woke:
                    await self.toast(woke)
                    desc = await asyncio.to_thread(WS.describe, ws_id)
                    tab = next((t for t in desc["tabs"] if t["id"] == tab["id"]), tab)
                    Sleeper.announce(ws_id, desc)
            view = await asyncio.to_thread(View, ws_id, tab["id"], tab["window"], cols, rows, self.notify,
                                           self.on_clipboard)
            with VIEWS_LOCK:
                full = len(VIEWS) >= LIMITS["views"]
                if not full and not self.closed:
                    VIEWS.add(view)
            if full or self.closed:
                await asyncio.to_thread(view.close)
                if full:
                    raise BridgeError(f"too many open screens ({LIMITS['views']}). Close one first.")
                return
            self.view, self.dirty_all = view, True
            await self.send({"t": "attached", "ws": desc, "tab": tab["id"], "state": "live"})
            pending, self.pending_input = self.pending_input, []
            for d in pending:
                await self.type_in(d)
            self.sender = asyncio.create_task(self.push_frames(view))
            log(f"attach station={self.station} ws={ws_id} tab={tab['id']} {cols}x{rows} view={view.name}")

    async def detach(self):
        async with self.attach_lock:
            await self._detach_locked()

    async def _detach_locked(self):
        view, self.view = self.view, None
        if self.sender:
            self.sender.cancel()
            self.sender = None
        if view:
            with VIEWS_LOCK:
                VIEWS.discard(view)
            await asyncio.to_thread(view.close)

    async def type_in(self, data):
        """Write keystrokes, giving every Enter its own moment.

        Ink and prompt_toolkit TUIs (Hermes among them) only treat Enter as
        "submit" when \\r arrives in a read of its own; glued to the text before
        it, the line just sits at the prompt. A game tick can deliver a whole
        typed line at once, so split at each \\r and let tmux flush between.
        Messages on one socket are handled in order, so nothing overtakes."""
        for part in (p for p in re.split(r"(\r)", data) if p):
            if part == "\r":
                # Keys arrive one message each, microseconds apart; measure
                # from the last text written, not from this message.
                wait = self.last_text + ENTER_GAP - time.monotonic()
                if wait > 0:
                    await asyncio.sleep(wait)
            if self.view:
                self.view.pty.write(part.encode("utf-8", "replace"))
            if part != "\r":
                self.last_text = time.monotonic()

    # -- message dispatch --------------------------------------------------
    async def hello(self, msg):
        station, ws_id = clean(msg.get("station") or "-"), clean(msg.get("ws"))
        if msg.get("token") is not None and same(msg.get("token"), TOKEN):
            self.role = "ui" if msg.get("role") == "ui" else "screen"
        elif msg.get("ticket") is not None and check_ticket(str(msg["ticket"]), station, ws_id):
            self.role, self.scope = "screen", ws_id
        else:
            await self.error("bad token")
            log(f"DENIED station={station}")
            return False
        self.authed, self.station, self.world = True, station, clean(msg.get("world") or "-")
        CONNS.add(self)
        await self.send({"t": "welcome", "version": VERSION, "limits": LIMITS, "palette": termcore.PALETTE,
                         "tmux": await asyncio.to_thread(TM.alive), "tab_kinds": list(TAB_KINDS),
                         "agents": await asyncio.to_thread(agents_info)})
        log(f"hello {self.role} station={self.station}")
        return True

    async def handle(self, msg):
        kind = msg.get("t")
        if not self.authed:
            return kind == "hello" and await self.hello(msg)
        try:
            if self.scope is not None:
                if kind not in SCREEN_OPS:
                    raise BridgeError(f"'{clean(kind, 20)}' is not allowed from a screen")
                if kind in ("ws_get", "attach") and str(msg.get("ws")) != self.scope:
                    raise BridgeError("this screen belongs to another workspace")
            await self._dispatch(kind, msg)
        except BridgeError as exc:
            await self.error(str(exc))
        except Exception as exc:  # never let one bad message drop the socket
            log(f"handler error {clean(kind, 20)}: {exc!r}")
            await self.error(f"bridge error: {exc}")
        return True

    async def _dispatch(self, kind, msg):
        call = asyncio.to_thread
        ws_id, tab_id = str(msg.get("ws") or ""), str(msg.get("tab") or "")

        if kind == "ws_list":
            await self.send({"t": "workspaces", "items": await call(WS.list)})
        elif kind == "ws_create":
            desc = await call(WS.create, msg.get("name"), msg.get("kind"), self.station, self.world,
                              msg.get("profile"))
            await self.send({"t": "workspace", "ws": desc, "created": True})
        elif kind == "ws_get":
            await self.send({"t": "workspace", "ws": await call(WS.describe, ws_id)})
        elif kind == "ws_delete":
            ok, m = await call(WS.delete, ws_id, bool(msg.get("force")))
            await self.send({"t": "workspace_deleted", "ws": ws_id, "m": m} if ok else
                            {"t": "confirm", "action": "ws_delete", "ws": ws_id, "m": m})
        elif kind == "ws_rename":
            await self.broadcast(await call(WS.rename, ws_id, msg.get("name")))
        elif kind == "ws_profile":
            desc = await call(WS.set_profile, ws_id, msg.get("profile"))
            await self.broadcast(desc)
            await self.toast(f"new Hermes tabs use the {desc['profile'] or 'default'} profile; "
                             "running tabs are unchanged")
        elif kind == "agents":
            await self.send({"t": "agents", "agents": await call(agents_info)})
        elif kind == "profiles":
            await self.send({"t": "profiles", "items": await call(hermes_profiles)})
        elif kind == "tab_new":
            desc, new_tab = await call(WS.tab_new, ws_id, msg.get("kind"), msg.get("name"))
            await self.broadcast(desc)
            await self.send({"t": "tab_created", "tab": new_tab})
        elif kind == "tab_rename":
            await self.broadcast(await call(WS.tab_rename, ws_id, tab_id, msg.get("name")))
        elif kind == "tab_close":
            desc, confirm = await call(WS.tab_close, ws_id, tab_id, bool(msg.get("force")))
            if confirm:
                await self.send({"t": "confirm", "action": "tab_close", "ws": ws_id, "tab": tab_id, "m": confirm})
            else:
                await self.broadcast(desc)
        elif kind == "tab_revive":
            await self.broadcast(await call(WS.tab_revive, ws_id, tab_id, msg.get("mode", "shell")))
        elif kind == "hermes":
            await self.toast(await call(WS.hermes, ws_id, tab_id))
        elif kind == "ws_keep_awake":
            desc = await call(WS.set_keep_awake, ws_id, bool(msg.get("on")))
            await self.broadcast(desc)
            await self.toast("Hermes here stays awake" if desc["keep_awake"]
                             else f"idle Hermes here sleeps after {SLEEP_AFTER / 60:.0f} min unwatched")
        elif kind == "attach":
            self.size = self._size(msg)
            await self.attach(ws_id, tab_id, *self.size)
        elif kind == "pause":
            self.paused = True
            self.wake.set()                 # the pump parks once the screen has a picture
        elif kind == "resume":
            was, self.paused = self.paused, False
            if was and self.view is None and self.ws_id and self.current_tab:
                await self.attach(self.ws_id, self.current_tab, *self.size)
        elif kind == "switch":
            if not self.ws_id:
                raise BridgeError("attach first")
            size = (self.view.pty.cols, self.view.pty.rows) if self.view else self._size(msg)
            await self.attach(self.ws_id, tab_id, *size)
        elif kind == "detach":
            await self.detach()
        elif kind == "station_status":
            station = clean(msg.get("station") or self.station)
            await self.send({"t": "station", "station": station,
                             "screens": [c.status() for c in self._screens(station)]})
        elif kind == "steer":
            # A UI asks every screen of a station to look at (ws, tab). The
            # screen's own socket does the attach, so the frame stream stays
            # on the connection that paints it.
            station = clean(msg.get("station") or self.station)
            screens = self._screens(station)
            for c in screens:
                if c.scope is not None and ws_id and ws_id != c.scope:
                    await c.error("this screen belongs to another workspace")
                    continue
                size = (c.view.pty.cols, c.view.pty.rows) if c.view else (51, 19)
                try:
                    if ws_id:
                        await c.attach(ws_id, tab_id, *size)
                    else:
                        await c.detach()
                        await c.send({"t": "attached", "ws": None, "tab": None, "state": "unbound"})
                except BridgeError as exc:
                    await c.error(str(exc))
            await self.send({"t": "steered", "station": station, "screens": len(screens)})
        elif kind in TERM_OPS:
            if kind == "in" and self.attach_lock.locked():
                # A tab switch (steer) is re-attaching this screen: what the player
                # types now belongs to the new tab, so keep it for the new view
                # (never block this socket, never send it to the old tab).
                self.pending_input.append(str(msg.get("d", "")))
            elif self.view:
                await self._screen_op(kind, msg)
        else:
            raise BridgeError(f"unknown message {clean(kind, 20)}")

    @staticmethod
    def _size(msg):
        return max(10, min(int(msg.get("cols", 51)), 400)), max(5, min(int(msg.get("rows", 19)), 200))

    @staticmethod
    def _screens(station):
        return [c for c in CONNS if c.role == "screen" and c.station == station and not c.closed]

    def _cell(self, msg, kx="x", ky="y"):
        """A 0-based cell on this view's screen, clamped to it."""
        pty = self.view.pty
        return (max(0, min(int(msg.get(kx) or 0), pty.cols - 1)),
                max(0, min(int(msg.get(ky) or 0), pty.rows - 1)))

    async def _screen_op(self, kind, msg):
        view, call = self.view, asyncio.to_thread
        pty = view.pty
        if kind in ENDS_SELECTION and (self.sel or self.sel_text is not None):
            self.sel = self.sel_text = None         # typing or moving ends a selection, as in any terminal
            self.wake.set()
        if kind in ("in", "paste", "wheel", "scroll", "sel"):
            self.last_active = LAST_INPUT[(self.ws_id, self.current_tab)] = time.monotonic()
        if kind == "in":
            view.live_again()
            await self.type_in(str(msg.get("d", "")))
        elif kind == "size":
            self.size = self._size(msg)
            pty.resize(*self.size)
            self.redraw()
        elif kind == "focus":
            # A focus-in report makes this view tmux's latest client, so the
            # window takes this surface's size (and apps that track focus hear it).
            pty.write(b"\x1b[I")
            self.redraw()
        elif kind == "redraw":
            self.redraw()
        elif kind == "scroll":
            await call(view.scroll, int(msg.get("by", 0)))
        elif kind == "wheel":
            await self._wheel(view, int(msg.get("by", 0)), *self._cell(msg))
        elif kind == "sel":
            if msg.get("x1") is None:
                self.sel = self.sel_text = None
            else:
                corners = (*self._cell(msg, "x1", "y1"), *self._cell(msg, "x2", "y2"))
                lines = pty.text_lines()
                self.sel = termcore.expand(lines, corners, str(msg.get("unit", "char")), pty.cols)
                self.sel_text = termcore.selection_text(lines, self.sel, pty.cols)
            self.wake.set()
        elif kind == "copy":
            # What was highlighted, not what is under those cells now: a TUI
            # may have redrawn since (Hermes streaming a reply).
            text = self.sel_text if self.sel_text is not None else \
                termcore.selection_text(pty.text_lines(), self.sel, pty.cols)
            if not text.strip():
                await self.toast("nothing selected")
                return
            via = await call(clipboard_copy, text)
            n = text.count("\n") + 1
            await self.toast(f"copied {len(text)} chars" + (f", {n} lines" if n > 1 else "")
                             + ("" if via else " - no clipboard tool on the host"))
        elif kind == "paste":
            given = msg.get("d")
            text = await call(paste_text, None if given is None else str(given))
            if not text:
                await self.toast("the clipboard is empty")
                return
            view.live_again()
            await call(pty.write, bracketed(text))
        elif kind == "open":
            url = termcore.url_at(pty.text_lines(), *self._cell(msg), pty.cols)
            if not url:
                await self.toast("no link there")
            elif await call(open_url, url):
                await self.toast("opening " + clean(urllib.parse.urlsplit(url).netloc, 48))
            else:
                await self.toast("that link is not opened from here")
        elif kind == "find":
            await self._find(view, str(msg.get("q", ""))[:200], int(msg.get("step", 0)))
        elif kind == "clear":
            await call(view.clear_history)
            await self.toast("scrollback cleared")

    async def _wheel(self, view, notches, x, y):
        """The wheel goes to an app that asked for the mouse; a full-screen
        app without it (less, man) gets arrow keys; otherwise it scrolls
        tmux's history. Positive = up."""
        if not notches:
            return
        if view.pty.mouse and not view.scroll_pos:
            button = 64 if notches > 0 else 65
            view.pty.write(f"\x1b[<{button};{x + 1};{y + 1}M".encode() * min(abs(notches), 10))
        elif view.alt and not view.scroll_pos:
            view.pty.write((b"\x1b[A" if notches > 0 else b"\x1b[B") * min(abs(notches) * 3, 60))
        else:
            await asyncio.to_thread(view.scroll, notches * 3)

    async def _find(self, view, q, step):
        """Find across the whole history; every match on screen is marked and
        the current one is scrolled into view."""
        if not q:
            self.find = self.find_at = None
            self.wake.set()
            await self.send({"t": "found", "q": "", "i": 0, "n": 0})
            return
        hit, i, n, row = await asyncio.to_thread(view.find, q, step, self.find_at)
        self.find_at, self.find = hit, (q, row)
        self.wake.set()
        await self.send({"t": "found", "q": q, "i": i, "n": n})

    async def broadcast(self, desc):
        """Tell every screen looking at this workspace that it changed."""
        for cb in list(WS.listeners.get(desc["id"], ())):
            cb(desc)
        if self.subscribed != desc["id"]:
            await self.send({"t": "workspace", "ws": desc})

    async def close(self):
        self.closed = True
        CONNS.discard(self)
        if self.subscribed:
            WS.unsubscribe(self.subscribed, self.on_workspace_change)
        await self.detach()

    def status(self):
        """What a UI wants to know about this screen connection."""
        return {"attached": self.view is not None, "ws": self.ws_id,
                "tab": self.current_tab, "role": self.role}


async def handler(ws):
    conn = Conn(ws)
    try:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(msg, dict) or not await conn.handle(msg):
                return
    except websockets.ConnectionClosed:
        pass
    finally:
        await conn.close()
        if conn.authed:
            log(f"socket closed station={conn.station}")


# --------------------------------------------------------------------------- board
async def board():
    """Read-only status for the /board page. Names and states only: no ids,
    no tokens, nothing that grants control."""
    items = await asyncio.to_thread(WS.list)
    return {"version": VERSION, "tmux": await asyncio.to_thread(TM.alive), "uptime": int(time.time()) - STARTED,
            "screens": sum(1 for c in CONNS if c.role == "screen" and c.view),
            "workspaces": [{"name": w["name"], "kind": w["kind"], "state": w["state"],
                            "profile": w["profile"] or "default",
                            "tabs": [{"name": t["name"], "kind": t["kind"], "state": t["state"],
                                      "busy": t["busy"], "command": t["command"],
                                      "sleeping": t.get("sleeping", False)} for t in w["tabs"]]}
                           for w in sorted(items, key=lambda w: (w["state"] != "live", w["name"].lower()))]}


# --------------------------------------------------------------------------- plain HTTP
def _response(code, body, ctype="text/plain; charset=utf-8"):
    if not isinstance(body, bytes):
        body = (json.dumps(body) if ctype == "application/json" else str(body)).encode()
    reason = {200: "OK", 403: "Forbidden", 404: "Not Found", 409: "Conflict", 500: "Error"}[code]
    return Response(code, reason, Headers([("Content-Type", ctype), ("Content-Length", str(len(body))),
                                           ("Cache-Control", "no-store")]), body)


def _host_ok(host):
    name = host.rsplit(":", 1)[0] if host.count(":") == 1 or host.startswith("[") else host
    return name.strip("[]") in ("127.0.0.1", "localhost", "::1")


async def _api(op, q):
    call = asyncio.to_thread
    if op == "create":
        try:
            tabs = json.loads(q["tabs"]) if q.get("tabs") else None
        except ValueError:
            raise BridgeError("tabs must be a JSON list of {name, kind}") from None
        return await call(WS.create, q.get("name"), q.get("kind", "terminal"), q.get("station"),
                          q.get("world"), q.get("profile"), tabs if isinstance(tabs, list) else None)
    if op == "get":
        return await call(WS.describe, q.get("ws", ""))
    if op == "rename":
        return await call(WS.rename, q.get("ws", ""), q.get("name", ""))
    if op == "profile":
        return await call(WS.set_profile, q.get("ws", ""), q.get("profile", ""))
    if op == "profiles":
        return {"items": await call(hermes_profiles)}
    if op == "delete":
        ok, m = await call(WS.delete, q.get("ws", ""), q.get("force") == "1")
        return {"ok": ok, "message": m}
    if op == "list":
        return {"items": await call(WS.list)}
    if op == "touch":
        await call(WS.touch, q.get("ws", ""), q.get("station"), q.get("world"))
        return {"ok": True}
    if op == "keep_awake":
        return await call(WS.set_keep_awake, q.get("ws", ""), q.get("on") == "1")
    if op == "sleep_scan":
        # Run one sleep pass now (tests, and `curl` when you want the RAM back).
        return {"slept": [f"{w}/{t}" for w, t in await call(SLEEPER.scan)]}
    return None


async def process_request(connection, request):
    """Plain HTTP endpoints on the websocket port; upgrades pass through."""
    if not _host_ok(request.headers.get("Host", "")):
        return _response(403, "forbidden host")
    if request.headers.get("Upgrade", "").lower() == "websocket":
        return None
    path, _, query = request.path.partition("?")
    q = dict(urllib.parse.parse_qsl(query))
    if path == "/health":
        return _response(200, {"ok": True, "version": VERSION, "tmux": await asyncio.to_thread(TM.alive),
                               "workspaces": len(META.workspaces), "views": len(VIEWS)}, "application/json")
    if path in ("/", "/board"):
        with open(BOARD_FILE, "rb") as fh:
            return _response(200, fh.read(), "text/html; charset=utf-8")
    if path == "/board.json":
        return _response(200, await board(), "application/json")
    if not same(q.get("token", ""), TOKEN):
        return _response(403, "forbidden")
    if path == "/ticket":
        return _response(200, mint_ticket(clean(q.get("station", "-")), clean(q.get("ws", ""))))
    if path.startswith("/api/"):
        try:
            result = await _api(path[len("/api/"):], q)
        except BridgeError as exc:
            return _response(409, {"error": str(exc)}, "application/json")
        except Exception as exc:
            log(f"api error {path}: {exc!r}")
            return _response(500, {"error": f"bridge error: {exc}"}, "application/json")
        if result is not None:
            return _response(200, result, "application/json")
    return _response(404, "not found")


def reconcile_on_start():
    """Log the truth about what survived. Never restart anything here."""
    server = TM.server_id()
    log(f"bridge v{VERSION} start on {HOST}:{PORT} tmux={'up' if server else 'DOWN'} "
        f"workspaces={len(META.workspaces)}")
    if not server:
        log("session server is not running: every workspace will report interrupted")
        return
    for desc in WS.list():
        if desc["state"] != "live":
            log(f"workspace {desc['id']} '{desc['name']}' {desc['state']}")
    # Views from a previous bridge process are orphans: their sockets are gone.
    for name in TM.sessions():
        if re.match(r"^omo-[0-9a-f]{12}-v[0-9a-f]{6}$", name):
            TM.run("kill-session", "-t", "=" + name)
            log(f"cleaned orphan view {name}")


async def main():
    asyncio.get_running_loop().set_default_executor(
        concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="omo"))
    reconcile_on_start()
    print(f"[omo-bridge] ws://{HOST}:{PORT}  data={DATA_DIR}", flush=True)
    async with serve(handler, HOST, PORT, max_size=2 ** 20, ping_interval=20,
                     process_request=process_request):
        sleeper = asyncio.create_task(sleep_loop())
        rememberer = asyncio.create_task(remember_loop())
        try:
            await asyncio.Future()
        finally:
            sleeper.cancel()
            rememberer.cancel()


if __name__ == "__main__":
    asyncio.run(main())
