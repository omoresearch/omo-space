#!/usr/bin/env python3
"""Isolated Omo test stack: a disposable session server and bridge.

Acceptance tests never touch the live stack (port 8780, tmux socket "omo",
~/.local/share/omo, the systemd units). They run against:

  data dir  $XDG_RUNTIME_DIR/omo-test (else /tmp/omo-test-$UID), mode 0700:
            token (0600), workspaces.json, bridge.log, bridge.out, bridge.pid,
            bin/tmux -> the static tmux the live stack uses
  tmux      socket "omo-test", started with config/omo-tmux.conf; tests may
            kill and restart it freely
  bridge    127.0.0.1:8794, a background process owned by this helper (not
            systemd), so "restart the bridge" restarts THIS process

  testenv.py up                  start (or reuse) tmux + bridge
  testenv.py down                stop the bridge, kill the test session server
  testenv.py status              the test bridge's /health
  testenv.py restart-bridge      restart only the bridge (shells keep running)
  testenv.py restart-tmux        kill and restart the session server (every shell ends)
  testenv.py prepare-instance D  point the Prism test instance in D at this stack
  testenv.py env                 shell exports: eval "$(tests/testenv.py env)"

Overrides: OMO_TEST_DATA, OMO_TEST_BRIDGE_PORT (8794), OMO_TEST_PORT (game driver, 8793).
"""
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "launcher"))
import omo_setup  # noqa: E402  (pure helpers: CC:T rule, port holder, file writes)

LIVE_DATA = Path.home() / ".local/share/omo"
RUNTIME = os.environ.get("XDG_RUNTIME_DIR", "")
DATA = Path(os.environ.get("OMO_TEST_DATA") or
            (Path(RUNTIME) / "omo-test" if RUNTIME and os.path.isdir(RUNTIME) else f"/tmp/omo-test-{os.getuid()}"))
PORT = int(os.environ.get("OMO_TEST_BRIDGE_PORT", "8794"))
DRIVER_PORT = int(os.environ.get("OMO_TEST_PORT", "8793"))
SOCKET = "omo-test"
TMUX = DATA / "bin/tmux"
TOKEN_FILE = DATA / "token"
PIDFILE = DATA / "bridge.pid"
OUT = DATA / "bridge.out"
CONF = REPO / "config/omo-tmux.conf"


def say(msg):
    print(f"[testenv] {msg}", flush=True)


def guard():
    if PORT == omo_setup.PORT or DATA.resolve() == LIVE_DATA.resolve():
        raise SystemExit(f"testenv: refusing, port {PORT} / data {DATA} would overlap the live Omo stack")
    if any(c in str(DATA) for c in ' "\'\\'):
        raise SystemExit(f"testenv: the data dir must not contain spaces or quotes: {DATA}")


# --------------------------------------------------------------------------- pieces
def token():
    DATA.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not TOKEN_FILE.exists():
        fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(os.urandom(16).hex())
    TOKEN_FILE.chmod(0o600)
    return TOKEN_FILE.read_text().strip()


def link_tmux():
    src = LIVE_DATA / "bin/tmux"
    src = src if src.exists() else Path(shutil.which("tmux") or "")
    if not src.is_file():
        raise SystemExit("testenv: no tmux found (~/.local/share/omo/bin/tmux or PATH)")
    TMUX.parent.mkdir(parents=True, exist_ok=True)
    if TMUX.is_symlink() and Path(os.readlink(TMUX)) == src:
        return
    TMUX.unlink(missing_ok=True)
    TMUX.symlink_to(src)


def tmux(*args):
    return subprocess.run([str(TMUX), "-L", SOCKET, *map(str, args)], capture_output=True, text=True, timeout=15)


def tmux_alive():
    return tmux("list-sessions").returncode == 0


def start_tmux():
    if tmux_alive():
        return
    env = {k: v for k, v in os.environ.items() if k not in ("TMUX", "TMUX_PANE")}
    env["OMO_SESSION_SERVER"] = "1"
    p = subprocess.run([str(TMUX), "-L", SOCKET, "-f", str(CONF), "start-server"], env=env,
                       capture_output=True, text=True, timeout=15)
    if p.returncode != 0 or not tmux_alive():
        raise SystemExit(f"testenv: test session server did not start: {p.stderr.strip()}")
    say(f"session server up (tmux -L {SOCKET})")


def kill_tmux():
    tmux("kill-server")
    end = time.monotonic() + 5
    while tmux_alive() and time.monotonic() < end:
        time.sleep(0.1)


def health():
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=3) as r:
            return json.loads(r.read().decode())
    except (OSError, ValueError):
        return None


def api(op, **params):
    """Authenticated bridge API call against the TEST bridge."""
    q = urllib.parse.urlencode({"token": token(), **params})
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/{op}?{q}", timeout=10) as r:
        return json.loads(r.read().decode())


def bridge_pid():
    """The PID in the pid file, if that process is still our bridge."""
    try:
        pid = int(PIDFILE.read_text())
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
    except (OSError, ValueError):
        return None
    return pid if b"omobridge.py" in cmdline else None


def start_bridge():
    if bridge_pid():
        h = health()
        if h and h.get("ok") and h.get("tmux"):
            return
        stop_bridge()                               # ours but unhealthy: start clean
    holder = omo_setup.port_holder(PORT)
    if holder:
        raise SystemExit(f"testenv: port {PORT} is held by {holder}, not the test bridge; stop it first")
    # The test game's own display for the clipboard; links open nowhere.
    display = os.environ.get("OMO_TEST_DISPLAY", ":98")
    env = {**os.environ, "OMO_DATA": str(DATA), "OMO_PORT": str(PORT), "OMO_HOST": "127.0.0.1",
           "OMO_TMUX_SOCKET": SOCKET, "OMO_TMUX": str(TMUX), "OMO_DISPLAY": display, "DISPLAY": display,
           "OMO_OPEN": "true"}
    env.pop("WAYLAND_DISPLAY", None)
    hermes = omo_setup.find_hermes()
    if hermes:
        env["OMO_HERMES_BIN"] = str(hermes)
    with open(OUT, "ab") as out:
        p = subprocess.Popen([omo_setup.pick_python(), str(REPO / "bridge/omobridge.py")], env=env, cwd=DATA,
                             stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                             start_new_session=True)
    PIDFILE.write_text(str(p.pid))
    end = time.monotonic() + 15
    while time.monotonic() < end:
        h = health()
        if h and h.get("ok") and h.get("tmux"):
            say(f"bridge up on 127.0.0.1:{PORT} (pid {p.pid}, v{h.get('version')})")
            return
        if p.poll() is not None:
            break
        time.sleep(0.2)
    tail = OUT.read_text(errors="replace").splitlines()[-15:]
    raise SystemExit("testenv: test bridge did not become healthy:\n  " + "\n  ".join(tail))


def stop_bridge():
    pid = bridge_pid()
    if pid:
        os.kill(pid, signal.SIGTERM)
        end = time.monotonic() + 5
        while Path(f"/proc/{pid}").exists() and time.monotonic() < end:
            time.sleep(0.1)
        if Path(f"/proc/{pid}").exists():
            os.kill(pid, signal.SIGKILL)
        say(f"bridge stopped (pid {pid})")
    PIDFILE.unlink(missing_ok=True)


# --------------------------------------------------------------------------- commands
def up():
    guard()
    DATA.mkdir(mode=0o700, parents=True, exist_ok=True)
    DATA.chmod(0o700)
    link_tmux()
    token()
    start_tmux()
    start_bridge()


def stop_test_display():
    """Close Prism and Xvfb on the test display. A Prism left running there
    captures the user's own launches (Prism is single-instance) and runs
    their game on a screen nobody can see."""
    display = os.environ.get("OMO_TEST_DISPLAY", ":98")
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            args = open(f"/proc/{pid}/cmdline", "rb").read().split(b"\0")
            env = open(f"/proc/{pid}/environ", "rb").read().split(b"\0")
        except OSError:
            continue
        prog = os.path.basename(args[0].decode(errors="replace"))
        on_display = f"DISPLAY={display}".encode() in env
        if (prog.startswith("prismlauncher") or prog == "PrismLauncher.AppImage") and on_display \
                or prog == "Xvfb" and len(args) > 1 and args[1].decode() == display:
            os.kill(int(pid), signal.SIGTERM)
            say(f"stopped {prog} on {display} (pid {pid})")


def down():
    stop_bridge()
    if TMUX.exists() and tmux_alive():
        kill_tmux()
        say("session server stopped")
    stop_test_display()


def restart_bridge():
    stop_bridge()
    start_bridge()


def restart_tmux():
    kill_tmux()
    start_tmux()


def prepare_instance(inst):
    """Point a disposable Prism instance at this stack: JVM properties for the
    mod (data dir, bridge port, test driver) and a CC:T rule for the test port."""
    guard()
    inst = Path(inst)
    cfg = inst / "instance.cfg"
    if not cfg.is_file():
        raise SystemExit(f"testenv: no Prism instance at {inst}")
    running = omo_setup.instance_pids(inst)
    if running:     # CC:T hot-reloads its config: editing it would rewire a live game
        raise SystemExit(f"testenv: {inst.name} is running (pid {running[0]}); quit it before a test launch")
    ours = [f"-Domo.data={DATA}", f"-Domo.port={PORT}", f"-Domo.test.port={DRIVER_PORT}"]
    lines = cfg.read_text().splitlines()
    general = lines.index("[General]") if "[General]" in lines else None
    if general is None:
        raise SystemExit(f"testenv: {cfg} has no [General] section")
    end = next((i for i in range(general + 1, len(lines)) if lines[i].startswith("[")), len(lines))
    want = {"OverrideJavaArgs": "true"}
    for i in range(general + 1, end):
        key, _, value = lines[i].partition("=")
        if key == "JvmArgs":
            keep = [a for a in shlex.split(value.strip().strip('"')) if not a.startswith("-Domo.")]
            want["JvmArgs"] = '"' + " ".join(ours + keep) + '"'
    want.setdefault("JvmArgs", '"' + " ".join(ours + ["-Dfile.encoding=UTF-8"]) + '"')
    for i in range(general + 1, end):
        key = lines[i].partition("=")[0]
        if key in want:
            lines[i] = f"{key}={want.pop(key)}"
    lines[general + 1:general + 1] = [f"{k}={v}" for k, v in want.items()]
    omo_setup.write_if_changed(cfg, ("\n".join(lines) + "\n").encode())
    omo_setup.ensure_cct_rule(inst / ".minecraft/config/computercraft-server.toml", PORT)
    say(f"instance {inst.name}: {' '.join(ours)}; CC:T may reach 127.0.0.1:{PORT}")


def env_exports():
    for key, value in (("OMO_TEST_DATA", DATA), ("OMO_TEST_BRIDGE_PORT", PORT), ("OMO_TEST_PORT", DRIVER_PORT)):
        print(f"export {key}={shlex.quote(str(value))}")


COMMANDS = {
    "up": up, "down": down, "restart-bridge": restart_bridge, "restart-tmux": restart_tmux,
    "status": lambda: print(json.dumps(health(), indent=1)), "env": env_exports,
}

if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["prepare-instance"] and len(args) == 2:
        prepare_instance(args[1])
    elif len(args) == 1 and args[0] in COMMANDS:
        COMMANDS[args[0]]()
    else:
        sys.exit(__doc__)
