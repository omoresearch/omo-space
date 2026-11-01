"""Hermes activity feed reader: what every Hermes agent on this computer is doing.

The omo-space Hermes plugin writes a small metadata-only feed per profile
(feed format v1):

  <HERMES_ROOT>[/profiles/<p>]/plugin-data/<ns>/feed/
      omo-feed.json            marker {v, plugin, plugin_version, profile, updated}
      sessions/<id>.json       one presence snapshot per session (atomic replace)
      events.jsonl             append-only events, rotated to events.1.jsonl

This module only ever reads. It is bounded (at most the last 64 KiB of an
events file, 200 sessions, 50 events), cached by mtime so a /board.json poll
costs a few stat calls, tolerant (bad JSON is skipped) and never follows a
symlink out of HERMES_ROOT. No message text is in the feed; a `notify` event's
`text` (<=140 chars, written by the agent for the player) is the one exception.
"""
import glob
import json
import os
import re
import threading
import time

FRESH_FOR = 15 * 60          # a presence not updated for this long is stale
TAIL_BYTES = 64 * 1024       # never read more than this of an events file
MAX_FILE = 64 * 1024         # a marker or presence larger than this is not ours
MAX_SESSIONS = 200
MAX_EVENTS = 50
RESCAN_EVERY = 5.0           # seconds between globbing for new feeds
TOAST_MAX = 160
NOTIFY_PER_TICK = 5          # a burst of notifies never floods the player
MARKER_GLOBS = ("plugin-data/*/feed/omo-feed.json", "profiles/*/plugin-data/*/feed/omo-feed.json")
SID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
CTRL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
KINDS = {"session_start", "session_end", "turn_start", "turn_end", "tool_start", "tool_end",
         "kanban_claimed", "kanban_completed", "kanban_blocked", "compaction", "api_error", "notify"}
STATS = {"bytes": 0}         # bytes read from feed files (tests check the bound)


# --------------------------------------------------------------------------- values
def _num(x):
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def _int(x):
    return int(x) if isinstance(x, int) and not isinstance(x, bool) else None


def _str(x, n=64):
    return CTRL_RE.sub("", x)[:n] if isinstance(x, str) else None


def _bool(x):
    return x if isinstance(x, bool) else None


def inside(path, root_real):
    """True when path, symlinks resolved, stays under root_real."""
    real = os.path.realpath(path)
    return real == root_real or real.startswith(root_real.rstrip(os.sep) + os.sep)


def pid_alive(pid):
    """True/False for a real pid, None when there is no pid to check."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return None
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True             # someone else's process: it exists
    except (ProcessLookupError, OSError):
        return False
    return True


def read_json(path, root_real):
    """A small JSON object from a regular file inside the root, else None."""
    if not inside(path, root_real):
        return None
    try:
        with open(path, "rb") as fh:
            raw = fh.read(MAX_FILE + 1)
    except OSError:
        return None
    STATS["bytes"] += len(raw)
    if len(raw) > MAX_FILE:
        return None
    try:
        obj = json.loads(raw)
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def read_tail(path, root_real, nbytes=TAIL_BYTES):
    """The last complete lines within the final `nbytes` of a file."""
    if not inside(path, root_real):
        return []
    try:
        with open(path, "rb") as fh:
            size = os.fstat(fh.fileno()).st_size
            start = max(0, size - nbytes)
            fh.seek(start)
            raw = fh.read(nbytes)
    except OSError:
        return []
    STATS["bytes"] += len(raw)
    lines = raw.split(b"\n")
    if start > 0:
        lines = lines[1:]       # the first one is cut
    return lines


# --------------------------------------------------------------------------- records
def norm_session(d, profile="default"):
    """A presence snapshot reduced to known, typed fields; None if unusable."""
    if not isinstance(d, dict) or d.get("v", 1) != 1:
        return None
    sid = d.get("session_id")
    if not isinstance(sid, str) or not SID_RE.match(sid):
        return None
    updated = _num(d.get("updated"))
    if updated is None:
        return None
    omo = d.get("omo") if isinstance(d.get("omo"), dict) else None
    kanban = d.get("kanban") if isinstance(d.get("kanban"), dict) else None
    tools = d.get("last_tools")
    tools = tools if isinstance(tools, list) else []
    return {
        "session_id": sid, "profile": _str(d.get("profile")) or profile,
        "platform": _str(d.get("platform"), 32), "model": _str(d.get("model")),
        "state": _str(d.get("state"), 16) or "idle", "tool": _str(d.get("tool")),
        "tool_since": _num(d.get("tool_since")), "turns": _int(d.get("turns")) or 0,
        "tool_calls": _int(d.get("tool_calls")) or 0,
        "last_tools": [t[:64] for t in tools if isinstance(t, str)][:5],
        "started": _num(d.get("started")), "updated": updated, "pid": _int(d.get("pid")),
        "parent_session_id": _str(d.get("parent_session_id"), 128),
        "omo": {"workspace": _str(omo.get("workspace")), "tab": _str(omo.get("tab")),
                "title": _str(omo.get("title"), 128)} if omo else None,
        "kanban": {"task_id": _str(kanban.get("task_id"), 128), "board": _str(kanban.get("board")),
                   "state": _str(kanban.get("state"), 16)} if kanban else None,
    }


def norm_event(d, profile="default"):
    if not isinstance(d, dict) or d.get("v", 1) != 1 or d.get("kind") not in KINDS:
        return None
    ts = _num(d.get("ts"))
    if ts is None:
        return None
    ev = {"ts": ts, "profile": _str(d.get("profile")) or profile, "session_id": _str(d.get("session_id"), 128),
          "kind": d["kind"]}
    for key, val in (("tool", _str(d.get("tool"))), ("duration_ms", _num(d.get("duration_ms"))),
                     ("ok", _bool(d.get("ok"))), ("platform", _str(d.get("platform"), 32)),
                     ("model", _str(d.get("model"))), ("interrupted", _bool(d.get("interrupted"))),
                     ("status_code", _int(d.get("status_code")))):
        if val is not None:
            ev[key] = val
    if d["kind"] == "notify":
        ev["text"] = _str(d.get("text"), 140) or ""
        ev["level"] = d.get("level") if d.get("level") in ("info", "success", "warn") else "info"
    return ev


def parse_events(lines, profile="default"):
    out = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            ev = norm_event(json.loads(line), profile)
        except ValueError:
            continue
        if ev:
            out.append(ev)
    return out


def is_fresh(s, now):
    return now - s["updated"] <= FRESH_FOR and pid_alive(s["pid"]) is not False


def activity(s, now):
    """What a workspace tab card shows for the Hermes running in it."""
    return {"state": s["state"], "tool": s["tool"], "tool_since": s["tool_since"], "turns": s["turns"],
            "updated": s["updated"],
            "tool_for": int(now - s["tool_since"]) if s["tool"] and s["tool_since"] else None}


def match_tabs(sessions):
    """{(workspace id, tab id): the presence to show}: a live one beats an
    ended one, then the most recently updated wins."""
    best = {}
    for s in sessions:
        omo = s.get("omo") or {}
        if not (omo.get("workspace") and omo.get("tab")):
            continue
        key = (omo["workspace"], omo["tab"])
        rank = (s["state"] != "ended", s["updated"])
        if key not in best or rank > (best[key]["state"] != "ended", best[key]["updated"]):
            best[key] = s
    return best


def toast_text(ev):
    text = f"{ev.get('profile') or 'hermes'}: {ev.get('text') or ''}".strip()
    return text if len(text) <= TOAST_MAX else text[:TOAST_MAX - 1] + "…"


# --------------------------------------------------------------------------- reader
class FeedReader:
    """Reads every feed under a Hermes root. Thread-safe; nothing is written."""

    def __init__(self, root, started=None, rescan=RESCAN_EVERY):
        self.root = root
        self.started = time.time() if started is None else started
        self.rescan = rescan
        self.lock = threading.Lock()
        self.feeds = []              # [(feed dir, profile)]
        self.scanned = 0.0
        self.files = {}              # path -> (stat key, parsed)
        self.dirs = {}               # sessions dir -> (stat key, [names])
        self.seen = set()            # notify keys already forwarded

    def _root_real(self):
        return os.path.realpath(self.root)

    def discover(self, root_real):
        found = []
        for pattern in MARKER_GLOBS:
            for marker in sorted(glob.glob(os.path.join(self.root, pattern))):
                if not inside(marker, root_real) or not inside(os.path.dirname(marker), root_real):
                    continue
                m = self._cached(marker, root_real, read_json)
                if not m or m.get("v") != 1:
                    continue
                found.append((os.path.dirname(marker), _str(m.get("profile")) or "default"))
        return found

    def _cached(self, path, root_real, reader):
        try:
            st = os.stat(path)
        except OSError:
            self.files.pop(path, None)
            return None
        key = (st.st_ino, st.st_size, st.st_mtime_ns)
        hit = self.files.get(path)
        if hit and hit[0] == key:
            return hit[1]
        val = reader(path, root_real)
        self.files[path] = (key, val)
        return val

    def _session_names(self, sdir, root_real):
        if not inside(sdir, root_real):
            return []
        try:
            st = os.stat(sdir)
        except OSError:
            return []
        key = (st.st_ino, st.st_mtime_ns)
        hit = self.dirs.get(sdir)
        # A dir changed within the last moment may change again inside the
        # same mtime tick: only trust the cached listing once it has settled.
        if hit and hit[0] == key and time.time() - st.st_mtime > 2:
            return hit[1]
        try:
            names = [n for n in os.listdir(sdir) if n.endswith(".json")]
        except OSError:
            names = []
        if len(names) > MAX_SESSIONS:   # keep the newest
            def mtime(n):
                try:
                    return os.stat(os.path.join(sdir, n)).st_mtime
                except OSError:
                    return 0
            names = sorted(names, key=mtime, reverse=True)[:MAX_SESSIONS]
        self.dirs[sdir] = (key, names)
        return names

    def _refresh(self, now):
        root_real = self._root_real()
        if not os.path.isdir(root_real):
            self.feeds = []
            return root_real
        if now - self.scanned >= self.rescan or not self.scanned:
            self.feeds = self.discover(root_real)
            self.scanned = now
            live = {d for d, _ in self.feeds}
            self.files = {p: v for p, v in self.files.items() if os.path.dirname(p) in live
                          or os.path.dirname(os.path.dirname(p)) in live}
        return root_real

    def _sessions_locked(self, now, root_real):
        out = []
        for fdir, profile in self.feeds:
            sdir = os.path.join(fdir, "sessions")
            names = self._session_names(sdir, root_real)
            for name in names:
                s = norm_session(self._cached(os.path.join(sdir, name), root_real, read_json), profile)
                if s:
                    out.append(s)
            # Forget snapshots that left the dir (pruned by the writer, or past the cap): a
            # long-running bridge would otherwise keep one cache entry per session ever seen.
            keep = {os.path.join(sdir, n) for n in names}
            for path in [p for p in self.files if os.path.dirname(p) == sdir and p not in keep]:
                del self.files[path]
        out.sort(key=lambda s: s["updated"], reverse=True)
        return out[:MAX_SESSIONS]

    def _events_locked(self, root_real):
        """[(feed dir, event)] from the tail of each feed, oldest first."""
        out = []
        for fdir, profile in self.feeds:
            evs = self._cached(os.path.join(fdir, "events.jsonl"), root_real,
                               lambda p, r: parse_events(read_tail(p, r), profile)) or []
            if len(evs) < MAX_EVENTS:   # just rotated: the rest is in the previous generation
                old = self._cached(os.path.join(fdir, "events.1.jsonl"), root_real,
                                   lambda p, r: parse_events(read_tail(p, r), profile)) or []
                evs = old + evs
            out.extend((fdir, ev) for ev in evs)
        out.sort(key=lambda fe: fe[1]["ts"])
        return out

    def snapshot(self, now=None):
        """{"sessions": fresh presences, "events": the last 50}, plus
        `tabs`: {(ws id, tab id): activity} for the board's workspace cards."""
        now = time.time() if now is None else now
        with self.lock:
            root_real = self._refresh(now)
            sessions = [s for s in self._sessions_locked(now, root_real) if is_fresh(s, now)]
            events = [ev for _, ev in self._events_locked(root_real)][-MAX_EVENTS:]
        for s in sessions:
            s["age"] = int(now - s["updated"])
            s["tool_for"] = int(now - s["tool_since"]) if s["tool"] and s["tool_since"] else None
        tabs = {key: activity(s, now) for key, s in match_tabs(sessions).items()}
        return {"sessions": sessions, "events": events, "tabs": tabs}

    def new_notifies(self, now=None):
        """Notify events newer than the bridge start not forwarded before,
        each once: deduped by (feed, ts, session id), so a rotation that moves
        the line into events.1.jsonl does not repeat it."""
        now = time.time() if now is None else now
        fresh = []
        with self.lock:
            root_real = self._refresh(now)
            for fdir, ev in self._events_locked(root_real):
                if ev["kind"] != "notify" or ev["ts"] <= self.started or not ev.get("text"):
                    continue
                key = (fdir, ev["ts"], ev.get("session_id"))
                if key in self.seen:
                    continue
                self.seen.add(key)
                fresh.append(ev)
            if len(self.seen) > 4096:   # forget what can no longer be in any tail window
                cut = sorted(k[1] for k in self.seen)[-2048]
                self.seen = {k for k in self.seen if k[1] >= cut}
        return fresh[-NOTIFY_PER_TICK:]
