#!/usr/bin/env python3
"""Omo Space performance benchmark: the same scenes, measured the same way, every time.

Runs against the ISOLATED test stack and OmoTest instance, like tests/acceptance.py
(tests/testenv.py: own bridge, tmux socket and data dir; never the live game or bridge).

  export OMO_TEST_BRIDGE_PORT=18794 OMO_TEST_PORT=18793   # if the defaults are taken
  tests/launch-test.sh &                         # OmoTest on Xvfb, test driver up
  tests/bench.py run --out bench-main.json       # every scene, results as JSON
  tests/bench.py run --scenes idle,web6 --seconds 30
  tests/bench.py compare bench-main.json bench-mine.json   # exit 1 on a regression or a lost metric
  tests/bench.py show bench-mine.json

Scenes (each starts from a cleared area in the flat world, player at a fixed spot):
  idle      nothing Omo in view: the floor everything else is measured against
  web6      6 web walls (3x2 monitors) showing a local, constantly animating page
  web12     12 web walls, the page cap (WebPages.MAX_PAGES)
  term6     6 terminal stations, each with a linked 3x2 wall printing a line every 50 ms
  mixed     web6 + term6 in one view

What is measured, per scene, after a warm-up:
  every world frame (test driver FrameLog, not a 50 ms sample): fps, p50/p95/p99/max
  frame time, 1% low fps, stutters (frames > 2x median and > 25 ms)
  render- and server-thread CPU ms per frame, GC count/ms, heap
  game RSS, browser helper processes (count, RSS), test bridge CPU %

Frame times on Xvfb use software GL, so absolute numbers are not a player's FPS.
Compare runs from the same machine and build mode; the deltas are what matter.
"""
import argparse
import json
import math
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent

# What counts as a regression in `compare`: worse by more than REL and by more
# than the absolute floor (tiny numbers jitter by large ratios).
THRESHOLDS = {                       # metric: (higher_is_worse, relative, absolute floor)
    "frame_p95_ms": (True, 0.10, 1.0),
    "frame_p99_ms": (True, 0.15, 2.0),
    "fps_mean": (False, 0.10, 2.0),
    "fps_low1": (False, 0.15, 2.0),
    "stutters_per_min": (True, 0.25, 3.0),
    "render_cpu_ms_per_frame": (True, 0.10, 0.3),
    "game_rss_mb": (True, 0.10, 200.0),
    "helpers_rss_mb": (True, 0.15, 200.0),
    "bridge_cpu_pct": (True, 0.25, 5.0),
}


# --------------------------------------------------------------------------- pure stats

def percentile(sorted_vals, q):
    """Nearest-rank percentile of an already sorted list (q in 0..100):
    the value at rank ceil(q/100 * n), i.e. index ceil(q/100 * n) - 1.
    Computed as q*n/100, not q/100*n: 0.07*100 is 7.000000000000001 in floats,
    whose ceil would be one rank too high."""
    if not sorted_vals:
        return 0.0
    k = max(0, min(len(sorted_vals) - 1, math.ceil(q * len(sorted_vals) / 100.0) - 1))
    return float(sorted_vals[k])


def frame_stats(frame_ms, wall_s=None):
    """Summary of a list of frame intervals in ms."""
    if not frame_ms:
        return {"frames": 0}
    s = sorted(frame_ms)
    total_ms = sum(frame_ms)
    p50 = percentile(s, 50)
    worst = s[-max(1, len(s) // 100):]                        # the worst 1% of frames
    stutter_ms = max(2 * p50, 25.0)
    stutters = sum(1 for v in frame_ms if v > stutter_ms)
    minutes = (wall_s if wall_s else total_ms / 1000.0) / 60.0
    return {
        "frames": len(frame_ms),
        "fps_mean": round(1000.0 * len(frame_ms) / total_ms, 2) if total_ms else 0.0,
        "fps_low1": round(1000.0 / (sum(worst) / len(worst)), 2),
        "frame_p50_ms": round(p50, 2),
        "frame_p95_ms": round(percentile(s, 95), 2),
        "frame_p99_ms": round(percentile(s, 99), 2),
        "frame_max_ms": round(s[-1], 2),
        "stutters": stutters,
        "stutters_per_min": round(stutters / minutes, 2) if minutes else 0.0,
    }


def verdict(metric, bv, nv):
    """'REGRESSION', 'better' or 'same' for one metric: a change counts only when it
    is bigger than the absolute floor AND the relative threshold (both strict)."""
    higher_worse, rel, floor = THRESHOLDS[metric]
    diff = nv - bv
    big = abs(diff) > floor and (abs(diff) > rel * abs(bv) if bv else True)
    if not big:
        return "same"
    worse = diff > 0 if higher_worse else diff < 0
    return "REGRESSION" if worse else "better"


def compare(base, new):
    """Rows (scene, metric, base, new, delta%, verdict) for scenes in both runs.

    A scene only in one run is skipped (a subset run compares against a full
    baseline). A metric the base has but the new run lost (no frames recorded,
    no game process found) is MISSING, which fails like a regression: a scene
    that stopped rendering must not pass as "no regressions"."""
    rows = []
    for scene, b in base.get("scenes", {}).items():
        n = new.get("scenes", {}).get(scene)
        if n is None:
            continue
        for metric in THRESHOLDS:
            if b.get(metric) is None:
                continue
            bv = float(b[metric])
            if n.get(metric) is None:
                rows.append((scene, metric, bv, None, None, "MISSING"))
                continue
            nv = float(n[metric])
            pct = ((nv - bv) / abs(bv) * 100.0) if bv else None
            rows.append((scene, metric, bv, nv, pct, verdict(metric, bv, nv)))
    return rows


def failures(rows):
    return [r for r in rows if r[5] in ("REGRESSION", "MISSING")]


def format_compare(rows):
    out = [f"{'scene':8} {'metric':26} {'base':>10} {'new':>10} {'delta':>8}  verdict"]
    for scene, metric, bv, nv, pct, v in rows:
        new = f"{nv:10.2f}" if nv is not None else f"{'-':>10}"
        delta = f"{pct:+7.1f}%" if pct is not None else f"{'n/a':>8}"
        out.append(f"{scene:8} {metric:26} {bv:10.2f} {new} {delta}  {v}")
    return "\n".join(out)


# --------------------------------------------------------------------------- /proc metrics

def _read(path):
    try:
        return Path(path).read_bytes()
    except OSError:
        return b""


def rss_mb(pid):
    for line in _read(f"/proc/{pid}/status").decode(errors="replace").splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) / 1024.0
    return 0.0


def cpu_ticks(pid):
    stat = _read(f"/proc/{pid}/stat").decode(errors="replace")
    if not stat:
        return 0
    rest = stat[stat.rfind(")") + 2:].split()
    return int(rest[11]) + int(rest[12])


def parents(proc="/proc"):
    """pid -> ppid for every visible process."""
    out = {}
    for d in os.listdir(proc):
        if d.isdigit():
            stat = _read(f"{proc}/{d}/stat").decode(errors="replace")
            if stat:
                out[int(d)] = int(stat[stat.rfind(")") + 2:].split()[1])
    return out


def descendants(root, ppid_of):
    kids = {}
    for pid, ppid in ppid_of.items():
        kids.setdefault(ppid, []).append(pid)
    seen, todo = [], [root]
    while todo:
        for c in kids.get(todo.pop(), []):
            seen.append(c)
            todo.append(c)
    return seen


def find_game(driver_port):
    """The OmoTest JVM: a java process started with -Domo.test.port=<driver port>."""
    want = f"-Domo.test.port={driver_port}".encode()
    for d in os.listdir("/proc"):
        if d.isdigit():
            cmd = _read(f"/proc/{d}/cmdline")
            if want in cmd and b"java" in cmd.split(b"\0")[0]:
                return int(d)
    return None


def helpers(game_pid):
    """Browser helper processes under the game (count, summed RSS MB)."""
    pids = [p for p in descendants(game_pid, parents())
            if b"jcef_helper" in _read(f"/proc/{p}/cmdline").split(b"\0")[0]]
    return len(pids), round(sum(rss_mb(p) for p in pids), 1)


# --------------------------------------------------------------------------- scenes

def _driver():
    """The acceptance helpers (driver calls, block placement, page pinning)."""
    import acceptance as a     # noqa: E402  (importing it runs nothing)
    return a


ANIM_PAGE = """<!doctype html><title>bench</title>
<style>body{margin:0;background:#123;color:#fff;font:48px sans-serif;overflow:hidden}
.b{position:absolute;width:120px;height:120px;background:#e94;border-radius:20px;
animation:m 2s linear infinite}@keyframes m{0%{left:0;top:0}50%{left:70vw;top:60vh}100%{left:0;top:0}}</style>
<div class=b></div><p id=t></p>
<script>setInterval(()=>t.textContent=performance.now().toFixed(0),50)</script>"""

FAR_STAND = "tp @s 0.5 -60 18.5 180 0"        # 18 blocks back, facing the walls (north)
COLS = [-12, -8, -4, 0, 4, 8]                 # 3-wide walls with a 1-block gap
ROWS = [(-60, -59), (-57, -56)]               # two rows of 2-high walls


def serve_page():
    import http.server
    import threading
    body = ANIM_PAGE.encode()

    class Page(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Page)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}/"


TERM_ROWS = [(-54, -53), (-51, -50)]           # mixed: terminals above the web walls


def wall_slots(n, rows=ROWS):
    """Up to 12 (xs, ys) wall footprints, all at z=0 facing the stand point."""
    slots = [(range(x, x + 3), ys) for ys in rows for x in COLS]
    return slots[:n]


def clear(a):
    a.clear_bench()
    for w in a.testenv.api("list")["items"]:
        a.testenv.api("delete", ws=w["id"], force=1)


def scene_web(a, n, url, z=0):
    for xs, ys in wall_slots(n):
        a.build_wall(xs, ys, stand=f"tp @s {xs[1] + 0.5} -60 3.5 180 0")
        a.pin_page((xs[0], ys[1], z), url)
    a.wait_for(lambda: a.call("pages")["count"] >= n, 120, 2)


def open_station(a, st):
    a.open_block(st, face="north")
    return a.LIVE in a.term_text()


def scene_term(a, n, rows=ROWS):
    """n stations behind the walls (z=-3), each linked to its own wall, printing nonstop."""
    a.call("cmd", {"command": "item replace entity @s hotbar.8 with omolife:linker"})
    for xs, ys in wall_slots(n, rows):
        st = (xs[1], -60, -3)
        a.setblock(st, "omolife:terminal[facing=north]")
        a.build_wall(xs, ys, stand=f"tp @s {xs[1] + 0.5} -60 3.5 180 0")
        a.call("slot", {"slot": 8})
        a.call("cmd", {"command": f"tp @s {st[0] + 0.5} -60 -6.5 0 20"})
        time.sleep(0.5)
        a.call("use", {"x": st[0], "y": st[1], "z": st[2], "face": "north"})
        a.call("cmd", {"command": f"tp @s {xs[1] + 0.5} -60 3.5 180 10"})
        time.sleep(0.5)
        a.call("use", {"x": xs[1], "y": ys[0], "z": 0, "face": "south"})
        a.wait_for(lambda: a.be(st).get("workspace"), 30, 1)
        a.call("cmd", {"command": f"tp @s {st[0] + 0.5} -60 -6.5 0 20"})
        a.call("slot", {"slot": 0})
        if a.wait_for(lambda: open_station(a, st), 40, 2):
            a.type_line("while :; do date '+%T.%N bench line'; sleep 0.05; done")
        a.call("close")


def measure(a, seconds, warmup, game_pid, bridge_pid):
    a.call("cmd", {"command": FAR_STAND})
    a.call("look", {"yaw": 180, "pitch": 0})
    time.sleep(warmup)
    hz = os.sysconf("SC_CLK_TCK")
    b0 = cpu_ticks(bridge_pid) if bridge_pid else 0
    a.call("bench_start")
    t0 = time.time()
    time.sleep(seconds)
    r = a.call("bench_stop", timeout=60)
    wall = time.time() - t0
    b1 = cpu_ticks(bridge_pid) if bridge_pid else 0
    frames = r.get("frame_ms", [])
    out: dict = frame_stats(frames, r.get("wall_s"))
    nf = max(1, out.get("frames", 0))
    count, h_rss = helpers(game_pid) if game_pid else (0, 0.0)
    wall_s = r.get("wall_s") or wall
    render_ms, server_ms = r.get("render_cpu_ms"), r.get("server_cpu_ms")
    out.update({
        # None (not 0) when the driver could not attribute the CPU: compare reports it MISSING
        "render_cpu_ms_per_frame": round(render_ms / nf, 3) if render_ms is not None and frames else None,
        "server_cpu_pct": round(server_ms / 10.0 / wall_s, 1) if server_ms is not None else None,
        "truncated": bool(r.get("truncated")),
        "gc_count": r.get("gc_count"), "gc_ms": r.get("gc_ms"), "java_heap_mb": r.get("java_heap_mb"),
        "pages": r.get("pages"),
        "game_rss_mb": round(rss_mb(game_pid), 1) if game_pid else None,
        "helpers": count, "helpers_rss_mb": h_rss,
        "bridge_cpu_pct": round((b1 - b0) / hz / wall * 100.0, 1) if bridge_pid else None,
    })
    return out


SCENES = {
    "idle": lambda a, url: None,
    "web6": lambda a, url: scene_web(a, 6, url),
    "web12": lambda a, url: scene_web(a, 12, url),
    "term6": lambda a, url: scene_term(a, 6),
    "mixed": lambda a, url: (scene_web(a, 6, url), scene_term(a, 6, TERM_ROWS)),
}


def environment():
    def sh(cmd):
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            return ""
    rev = sh(["git", "-C", str(HERE.parent), "rev-parse", "--short", "HEAD"])
    dirty = bool(sh(["git", "-C", str(HERE.parent), "status", "--porcelain", "--untracked-files=no"]))
    mem = next((line.split()[1] for line in _read("/proc/meminfo").decode().splitlines()
                if line.startswith("MemTotal:")), "0")
    return {"git": rev + ("+dirty" if dirty else ""), "host": platform.node(), "cpus": os.cpu_count(),
            "mem_gb": round(int(mem) / 1048576, 1), "time": time.strftime("%Y-%m-%d %H:%M:%S")}


def port_taken(port):
    """True when something is listening on 127.0.0.1:port."""
    import socket
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


UNKNOWN = -1          # a listener whose pid ss cannot show (another user's process)


def parse_ss_pids(out):
    """Listener pids from `ss -Hltnp` output; UNKNOWN for a line without one."""
    import re
    pids = set()
    for line in out.splitlines():
        if not line.strip():
            continue
        found = [int(p) for p in re.findall(r"pid=(\d+)", line)]
        pids.update(found or [UNKNOWN])
    return pids


def listener_pids(port):
    """Pids listening on TCP :port (empty set: nobody), or None when ss is unavailable."""
    try:
        out = subprocess.run(["ss", "-Hltnp", "sport", "=", f":{port}"],
                             capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    return parse_ss_pids(out)


def foreign_ports(testenv, game_pid, holders=listener_pids):
    """Test-stack ports held by a program that is not our test bridge / test game.

    The default test ports (bridge 8794, driver 8793) are taken by other local
    services on some machines (seen: a robot sim and its voice server); talking
    to those as if they were the test stack would corrupt the run, so the
    listener's pid must be the test bridge's / the OmoTest JVM's. A bridge port
    nobody holds is fine (testenv.up() starts the bridge); a driver port nobody
    holds is not (the game's driver is not up)."""
    bad = []
    checks = (("OMO_TEST_BRIDGE_PORT", testenv.PORT, testenv.bridge_pid(), True),
              ("OMO_TEST_PORT", testenv.DRIVER_PORT, game_pid, False))
    for key, port, ours, may_be_free in checks:
        pids = holders(port)
        if pids is None:                                    # no ss: best effort
            ok = (not port_taken(port) and may_be_free) or (port_taken(port) and ours is not None)
        elif not pids:
            ok = may_be_free
        else:
            ok = ours is not None and pids == {ours}
        if not ok:
            bad.append((key, port))
    return bad


def cmd_run(args):
    names = [s for s in args.scenes.split(",") if s]
    unknown = [s for s in names if s not in SCENES]
    if unknown or not names:
        print(f"bench: unknown scene(s) {unknown}; choose from {','.join(SCENES)}", file=sys.stderr)
        return 2
    a = _driver()
    port = a.testenv.DRIVER_PORT
    game_pid = find_game(port)
    if game_pid is None:
        print(f"bench: no OmoTest JVM with -Domo.test.port={port}; start tests/launch-test.sh "
              "with the same OMO_TEST_PORT / OMO_TEST_BRIDGE_PORT first", file=sys.stderr)
        return 2
    bad = foreign_ports(a.testenv, game_pid)
    if bad:
        print("bench: another program holds " + ", ".join(f"{k}={v}" for k, v in bad)
              + "; set free ports with OMO_TEST_BRIDGE_PORT / OMO_TEST_PORT for both"
              " launch-test.sh and bench.py and retry", file=sys.stderr)
        return 2
    a.testenv.up()
    a.enter_world()
    url = serve_page()
    result = {"env": environment(), "seconds": args.seconds, "scenes": {}}
    if args.label:
        result["env"]["label"] = args.label
    try:
        for name in names:
            print(f"[{name}] building...", flush=True)
            clear(a)
            SCENES[name](a, url)
            print(f"[{name}] measuring {args.seconds}s...", flush=True)
            r = measure(a, args.seconds, args.warmup, game_pid, a.testenv.bridge_pid())
            result["scenes"][name] = r
            print(f"[{name}] fps {r.get('fps_mean')} p95 {r.get('frame_p95_ms')} ms "
                  f"p99 {r.get('frame_p99_ms')} ms stutters/min {r.get('stutters_per_min')} "
                  f"render {r.get('render_cpu_ms_per_frame')} ms/frame pages {r.get('pages')}", flush=True)
    finally:
        clear(a)
        Path(args.out).write_text(json.dumps(result, indent=1) + "\n")
        print(f"wrote {args.out}")
    return 0


def cmd_compare(args):
    rows = compare(json.loads(Path(args.base).read_text()), json.loads(Path(args.new).read_text()))
    print(format_compare(rows))
    if not rows:
        print("\nnothing to compare: no scene and metric in both runs")
        return 2
    bad = failures(rows)
    print(f"\n{len(bad)} regression(s)" if bad else "\nno regressions")
    return 1 if bad else 0


def cmd_show(args):
    data = json.loads(Path(args.file).read_text())
    print(json.dumps(data.get("env", {})))
    for name, r in data.get("scenes", {}).items():
        print(f"{name:8} " + " ".join(f"{k}={v}" for k, v in r.items()))
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--scenes", default=",".join(SCENES))
    r.add_argument("--seconds", type=float, default=20)
    r.add_argument("--warmup", type=float, default=8)
    r.add_argument("--label", default="")
    r.add_argument("--out", default="bench.json")
    c = sub.add_parser("compare")
    c.add_argument("base")
    c.add_argument("new")
    s = sub.add_parser("show")
    s.add_argument("file")
    args = p.parse_args(argv)
    return {"run": cmd_run, "compare": cmd_compare, "show": cmd_show}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
