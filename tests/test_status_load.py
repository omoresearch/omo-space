#!/usr/bin/env python3
"""Status polls must not hold the workspace lock through tmux calls.

The in-game Name Plates and Status Lamps ask the bridge to describe every
watched workspace every 2 s (/api/get, StationStatus). Each describe used to
run display + has-session + list-windows + capture-pane while holding the one
workspace lock, so with ~100 workspaces the lock was busy almost all the time
and a NEW computer's attach (which needs that lock three times) waited 8-12 s.

Checks, on a private tmux server and bridge (never the live ones):
  * a burst of status reads shares one tmux window listing (no per-workspace
    has-session / list-windows), and every answer is still complete;
  * reads stay truthful: a change made through the bridge shows at once, a
    window that dies on its own reads 'exited' within a second;
  * Hermes status bars are still reported.

    python3 tests/test_status_load.py
    OMO_TEST_BRIDGE=/old/omobridge.py python3 tests/test_status_load.py   # red on the old bridge
"""
import concurrent.futures
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRIDGE = os.environ.get("OMO_TEST_BRIDGE") or os.path.join(REPO, "bridge", "omobridge.py")
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
        self.dir = tempfile.mkdtemp(prefix="omo-status-test-")
        self.data = os.path.join(self.dir, "data")
        self.stub = os.path.join(self.dir, "stub")
        hroot = os.path.join(self.dir, "hermes")
        for d in (self.data, self.stub, os.path.join(hroot, "profiles")):
            os.makedirs(d)
        with open(os.path.join(self.stub, "hermes"), "w") as fh:
            fh.write('#!/bin/sh\nwhile :; do printf " ─ ready │ stub │ 0s\\n ❯ "; '
                     'IFS= read -r l || exit 0; [ "$l" = /exit ] && exit 0; done\n')
        # Every tmux call the bridge makes is counted by subcommand.
        self.calls = os.path.join(self.dir, "tmux-calls")
        self.shim = os.path.join(self.stub, "tmux-count")
        with open(self.shim, "w") as fh:
            fh.write(f'#!/bin/sh\necho "$3" >> {self.calls}\nexec {TMUX} "$@"\n')
        for f in ("hermes", "tmux-count"):
            os.chmod(os.path.join(self.stub, f), 0o755)
        self.sock = f"omostatus{os.getpid()}"
        self.port = free_port()
        env = {**os.environ, "PATH": f"{self.stub}:{os.environ['PATH']}"}
        subprocess.run([TMUX, "-L", self.sock, "-f", os.path.join(REPO, "config", "omo-tmux.conf"), "start-server"],
                       env=env, check=True)
        benv = {**os.environ, "OMO_DATA": self.data, "OMO_PORT": str(self.port), "OMO_TMUX": self.shim,
                "OMO_TMUX_SOCKET": self.sock, "OMO_HERMES_ROOT": hroot,
                "OMO_HERMES_BIN": os.path.join(self.stub, "hermes"), "OMO_SLEEP_AFTER": "0",
                "OMO_REMEMBER_EVERY": "3600", "OMO_DISPLAY": ":65000", "DISPLAY": ":65000"}
        benv.pop("WAYLAND_DISPLAY", None)
        self.bridge = subprocess.Popen([sys.executable, BRIDGE], env=benv, stdout=subprocess.DEVNULL,
                                       stderr=open(os.path.join(self.dir, "stderr"), "w"))
        for _ in range(100):
            try:
                self.http("/health")
                break
            except OSError:
                time.sleep(0.1)
        self.token = open(os.path.join(self.data, "token")).read().strip()

    def http(self, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=20) as r:
            return json.loads(r.read().decode())

    def api(self, op, **q):
        qs = "&".join(f"{k}={urllib.request.quote(str(v))}" for k, v in {"token": self.token, **q}.items())
        return self.http(f"/api/{op}?{qs}")

    def tmux(self, *args):
        return subprocess.run([TMUX, "-L", self.sock, *args], capture_output=True, text=True)

    def counted(self):
        try:
            with open(self.calls) as fh:
                words = fh.read().split()
        except OSError:
            words = []
        open(self.calls, "w").close()
        return {w: words.count(w) for w in set(words)}

    def cleanup(self):
        self.bridge.terminate()
        try:
            self.bridge.wait(5)
        except subprocess.TimeoutExpired:
            self.bridge.kill()
        self.tmux("kill-server")
        shutil.rmtree(self.dir, ignore_errors=True)


def run(env):
    ids = [env.api("create", name=f"Status {i}", kind="hermes")["id"] for i in range(12)]
    time.sleep(1.5)                                 # stub TUIs paint their status bars
    env.counted()
    with concurrent.futures.ThreadPoolExecutor(24) as pool:
        descs = list(pool.map(lambda w: env.api("get", ws=w), ids * 2))
    calls = env.counted()
    per_ws = calls.get("has-session", 0) + calls.get("list-windows", 0)
    check("24 status reads share the tmux window listing (no per-workspace has-session/list-windows)",
          per_ws <= 6 and calls.get("display", 0) <= 6, calls)
    check("every status read is complete: live, its tab live, Hermes named",
          all(d["state"] == "live" and d["tabs"][0]["state"] == "live" and d["tabs"][0]["command"] == "hermes"
              for d in descs), [d["state"] for d in descs][:4])
    check("Hermes status bars are still read", all(d["tabs"][0].get("agent") == "ready" for d in descs),
          [d["tabs"][0].get("agent") for d in descs][:4])

    # A change through the bridge is seen by the very next read.
    d0 = env.api("get", ws=ids[0])
    with urllib.request.urlopen(f"http://127.0.0.1:{env.port}/board.json", timeout=20) as r:
        json.loads(r.read())
    ws = env.api("create", name="Fresh", kind="terminal")
    got = env.api("get", ws=ws["id"])
    check("a workspace created through the bridge reads live at once",
          got["state"] == "live" and got["tabs"][0]["state"] == "live", got["state"])
    names = {w["name"] for w in env.api("list")["items"]}
    check("the list includes it at once", "Fresh" in names)

    # A window that ends on its own (not through the bridge) is noticed quickly.
    target = f"=omo-{ids[1]}:{d0['tabs'][0]['window'] or ''}"
    win = env.api("get", ws=ids[1])["tabs"][0]["window"]
    env.tmux("new-window", "-d", "-t", f"=omo-{ids[1]}", "sleep 600")    # keep the session alive
    env.tmux("kill-window", "-t", f"=omo-{ids[1]}:{win}")
    time.sleep(1.0)
    after = env.api("get", ws=ids[1])["tabs"][0]["state"]
    check("a window killed outside the bridge reads 'exited' within a second", after == "exited", after or target)

    err = open(os.path.join(env.dir, "stderr")).read()
    check("the bridge logged no traceback", "Traceback" not in err, err[-800:])


def main():
    env = Env()
    try:
        run(env)
    finally:
        env.cleanup()
    print(f"\n{sum(RESULTS)}/{len(RESULTS)} status-load checks passed")
    return 0 if all(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
