"""The bridge's reader for the omo-space Hermes plugin's activity feed.

A fake HERMES_ROOT in a temp dir holds feeds for the default profile and a
named one. Nothing here touches the live bridge, its token or ~/.hermes.

    python3 -m unittest tests.test_hermes_feed -v
"""
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bridge"))
import hermesfeed as hf  # noqa: E402

NOW = time.time()


def write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(obj if isinstance(obj, str) else json.dumps(obj))


def presence(sid, **kw):
    s = {"v": 1, "session_id": sid, "profile": "default", "platform": "cli", "model": "m", "state": "idle",
         "tool": None, "tool_since": None, "turns": 1, "tool_calls": 0, "last_tools": [], "started": NOW - 60,
         "updated": NOW - 5, "pid": os.getpid(), "parent_session_id": None, "omo": None, "kanban": None}
    s.update(kw)
    return s


def event(ts, kind="notify", sid="s1", profile="default", **kw):
    return {"v": 1, "ts": ts, "profile": profile, "session_id": sid, "kind": kind, **kw}


class Feed(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="omo-hermes-root-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.default = self.feed(self.root, "default")
        self.dev = self.feed(os.path.join(self.root, "profiles", "dev"), "dev")
        self.reader = hf.FeedReader(self.root, started=NOW - 30, rescan=0)

    def feed(self, home, profile, ns="omo-space"):
        d = os.path.join(home, "plugin-data", ns, "feed")
        write(os.path.join(d, "omo-feed.json"), {"v": 1, "plugin": "omo-space", "plugin_version": "0.1.0",
                                                 "profile": profile, "updated": NOW})
        os.makedirs(os.path.join(d, "sessions"), exist_ok=True)
        return d

    def put(self, feed, s):
        write(os.path.join(feed, "sessions", s["session_id"] + ".json"), s)

    def append(self, feed, *events, name="events.jsonl"):
        with open(os.path.join(feed, name), "a") as fh:
            for ev in events:
                fh.write((ev if isinstance(ev, str) else json.dumps(ev)) + "\n")

    def snap(self, now=NOW):
        return self.reader.snapshot(now)

    # -- discovery ------------------------------------------------------------
    def test_discovers_default_and_profile_feeds(self):
        self.put(self.default, presence("a"))
        self.put(self.dev, presence("b", profile="dev", platform="telegram"))
        got = {s["session_id"]: s for s in self.snap()["sessions"]}
        self.assertEqual(set(got), {"a", "b"})
        self.assertEqual((got["b"]["profile"], got["b"]["platform"]), ("dev", "telegram"))

    def test_no_hermes_root_is_empty_not_an_error(self):
        r = hf.FeedReader(os.path.join(self.root, "nope"), rescan=0)
        self.assertEqual(r.snapshot(), {"sessions": [], "events": [], "tabs": {}})
        self.assertEqual(r.new_notifies(), [])

    def test_marker_of_another_version_is_ignored(self):
        d = self.feed(os.path.join(self.root, "profiles", "future"), "future")
        write(os.path.join(d, "omo-feed.json"), {"v": 2, "profile": "future"})
        self.put(d, presence("f"))
        self.assertNotIn("f", [s["session_id"] for s in self.snap()["sessions"]])

    # -- staleness --------------------------------------------------------------
    def test_stale_after_15_minutes(self):
        self.put(self.default, presence("old", updated=NOW - 16 * 60))
        self.put(self.default, presence("new", updated=NOW - 14 * 60))
        self.assertEqual([s["session_id"] for s in self.snap()["sessions"]], ["new"])

    def test_dead_pid_is_stale(self):
        p = subprocess.Popen(["true"])
        p.wait()
        self.put(self.default, presence("dead", pid=p.pid))
        self.put(self.default, presence("nopid", pid=None))
        self.assertEqual([s["session_id"] for s in self.snap()["sessions"]], ["nopid"])

    def test_pid_of_another_user_counts_as_alive(self):
        with mock.patch.object(hf.os, "kill", side_effect=PermissionError):
            self.assertIs(hf.pid_alive(1), True)
        with mock.patch.object(hf.os, "kill", side_effect=ProcessLookupError):
            self.assertIs(hf.pid_alive(12345), False)
        self.assertIsNone(hf.pid_alive(True))

    # -- bounded, tolerant reading ---------------------------------------------
    def test_tail_reads_at_most_64k_of_a_2mb_file(self):
        line = json.dumps(event(NOW - 100, "tool_end", tool="terminal", ok=True)) + "\n"
        with open(os.path.join(self.default, "events.jsonl"), "w") as fh:
            fh.write(line * (2 * 1024 * 1024 // len(line) + 1))
        self.append(self.default, event(NOW - 1, "turn_end"))
        self.assertGreater(os.path.getsize(os.path.join(self.default, "events.jsonl")), 2 * 1024 * 1024)
        hf.STATS["bytes"] = 0
        t0 = time.perf_counter()
        evs = self.snap()["events"]
        self.assertLessEqual(hf.STATS["bytes"], hf.TAIL_BYTES + 3 * hf.MAX_FILE)
        self.assertLess(time.perf_counter() - t0, 0.5)
        self.assertEqual(len(evs), hf.MAX_EVENTS)
        self.assertEqual(evs[-1]["kind"], "turn_end")

    def test_unchanged_files_are_not_read_again(self):
        self.put(self.default, presence("a"))
        self.append(self.default, event(NOW - 1, "turn_start"))
        self.snap()
        hf.STATS["bytes"] = 0
        self.snap()
        self.assertEqual(hf.STATS["bytes"], 0)
        self.append(self.default, event(NOW, "turn_end"))
        self.assertEqual(self.snap()["events"][-1]["kind"], "turn_end")

    def test_previous_generation_is_read_after_a_rotation(self):
        self.append(self.default, event(NOW - 10, "turn_start"), name="events.1.jsonl")
        self.append(self.default, event(NOW - 1, "turn_end"))
        self.assertEqual([e["kind"] for e in self.snap()["events"]], ["turn_start", "turn_end"])

    def test_malformed_lines_and_files_are_skipped(self):
        self.append(self.default, "{not json", "[1, 2]", json.dumps(event(NOW - 3, "bogus_kind")),
                    json.dumps(event("yesterday", "turn_end")), "",
                    json.dumps(event(NOW - 2, "tool_start", tool="terminal", args="SECRET")))
        write(os.path.join(self.default, "sessions", "bad.json"), "{oops")
        write(os.path.join(self.default, "sessions", "list.json"), "[]")
        self.put(self.default, presence("../../etc"))
        self.put(self.default, presence("ok", last_tools=["a", 3, "b"], turns="many"))
        snap = self.snap()
        self.assertEqual([s["session_id"] for s in snap["sessions"]], ["ok"])
        self.assertEqual((snap["sessions"][0]["last_tools"], snap["sessions"][0]["turns"]), (["a", "b"], 0))
        self.assertEqual([e["kind"] for e in snap["events"]], ["tool_start"])
        self.assertNotIn("args", snap["events"][0])

    def test_caps_at_200_sessions(self):
        for i in range(230):
            self.put(self.default, presence(f"s{i:03d}", updated=NOW - i))
        self.assertEqual(len(self.snap()["sessions"]), hf.MAX_SESSIONS)

    # -- never outside HERMES_ROOT ---------------------------------------------
    def test_cache_forgets_pruned_sessions(self):
        """A long-running bridge must not keep one cache entry per session it ever saw."""
        for i in range(30):
            self.put(self.default, presence(f"gone-{i}"))
        self.snap()
        sdir = os.path.join(self.default, "sessions")
        for i in range(30):
            os.unlink(os.path.join(sdir, f"gone-{i}.json"))
        self.put(self.default, presence("kept"))
        self.reader.dirs.clear()            # the listing cache would hide the change within 2 s
        self.assertEqual([s["session_id"] for s in self.snap()["sessions"]], ["kept"])
        cached = [p for p in self.reader.files if os.path.dirname(p) == sdir]
        self.assertEqual(cached, [os.path.join(sdir, "kept.json")])

    def test_symlink_escape_is_refused(self):
        outside = tempfile.mkdtemp(prefix="omo-outside-")
        self.addCleanup(shutil.rmtree, outside, True)
        evil = self.feed(outside, "evil")
        self.put(evil, presence("evil"))
        os.makedirs(os.path.join(self.root, "profiles"), exist_ok=True)
        os.symlink(outside, os.path.join(self.root, "profiles", "evil"))
        # A file-level link out of the root, inside a real feed.
        secret = os.path.join(outside, "secret.json")
        write(secret, presence("secret"))
        os.symlink(secret, os.path.join(self.default, "sessions", "secret.json"))
        os.symlink(os.path.join(evil, "events.jsonl"), os.path.join(self.dev, "events.jsonl"))
        self.append(evil, event(NOW, text="from outside"))
        snap = self.snap()
        self.assertEqual(snap["sessions"], [])
        self.assertEqual(snap["events"], [])
        self.assertEqual(self.reader.new_notifies(NOW + 1), [])

    def test_never_writes(self):
        self.put(self.default, presence("a"))
        self.append(self.default, event(NOW, text="hi"))
        before = sorted((p, os.stat(os.path.join(d, p)).st_mtime_ns) for d, _, fs in os.walk(self.root) for p in fs)
        self.snap()
        self.reader.new_notifies(NOW + 1)
        after = sorted((p, os.stat(os.path.join(d, p)).st_mtime_ns) for d, _, fs in os.walk(self.root) for p in fs)
        self.assertEqual(before, after)

    # -- workspace tabs ----------------------------------------------------------
    def test_presence_matches_its_workspace_tab(self):
        self.put(self.default, presence("t", state="tool", tool="terminal", tool_since=NOW - 12, turns=3,
                                        omo={"workspace": "ws1", "tab": "tb1", "title": "omo:ws1:tb1"}))
        self.put(self.default, presence("old", state="ended", updated=NOW - 1,
                                        omo={"workspace": "ws1", "tab": "tb1", "title": "omo:ws1:tb1"}))
        self.put(self.dev, presence("cron", profile="dev", platform="cron"))
        snap = self.snap()
        self.assertEqual(set(snap["tabs"]), {("ws1", "tb1")})
        act = snap["tabs"][("ws1", "tb1")]
        self.assertEqual((act["state"], act["tool"], act["turns"], act["tool_for"]), ("tool", "terminal", 3, 12))
        self.assertEqual(set(act), {"state", "tool", "tool_since", "turns", "updated", "tool_for"})

    # -- notify -------------------------------------------------------------------
    def test_notify_forwarded_once_and_only_after_bridge_start(self):
        self.append(self.default, event(NOW - 60, text="before the bridge started"),
                    event(NOW - 1, text="build finished", level="success"),
                    event(NOW - 1, "turn_end"))
        self.append(self.dev, event(NOW - 1, profile="dev", sid="d1", text="x" * 300))
        first = self.reader.new_notifies(NOW)
        self.assertEqual(sorted(hf.toast_text(e) for e in first), ["default: build finished", "dev: " + "x" * 140])
        self.assertEqual(self.reader.new_notifies(NOW + 1), [])
        # Rotation moves the line into events.1.jsonl: still not repeated.
        os.replace(os.path.join(self.default, "events.jsonl"), os.path.join(self.default, "events.1.jsonl"))
        self.append(self.default, event(NOW + 2, text="next"))
        self.assertEqual([e["text"] for e in self.reader.new_notifies(NOW + 3)], ["next"])

    def test_toast_text_is_clamped(self):
        t = hf.toast_text({"profile": "p" * 100, "text": "y" * 140})
        self.assertEqual(len(t), hf.TOAST_MAX)
        self.assertTrue(t.endswith("…"))

    def test_a_notify_burst_is_capped(self):
        self.append(self.default, *[event(NOW - 1 + i / 100, text=f"n{i}") for i in range(20)])
        self.assertEqual(len(self.reader.new_notifies(NOW)), hf.NOTIFY_PER_TICK)
        self.assertEqual(self.reader.new_notifies(NOW), [])


class Board(unittest.TestCase):
    """/board.json through the real bridge module, with tmux and workspaces faked."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="omo-board-test-")
        cls.root = os.path.join(cls.tmp, "hermes")
        os.makedirs(cls.root)
        if "omobridge" not in sys.modules:   # never the live token: a private data dir
            os.environ["OMO_DATA"] = os.path.join(cls.tmp, "data")
            os.environ.setdefault("OMO_TMUX", "/bin/true")
        import omobridge
        cls.b = omobridge

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, True)

    def board(self, root):
        items = [{"id": "ws1", "name": "Desk", "kind": "hermes", "state": "live", "profile": "",
                  "tabs": [{"id": "tb1", "name": "Hermes", "kind": "hermes", "state": "live", "busy": True,
                            "command": "hermes", "sleeping": False}]}]
        with mock.patch.object(self.b, "FEED", hf.FeedReader(root, rescan=0)), \
                mock.patch.object(self.b.WS, "list", return_value=items), \
                mock.patch.object(self.b.TM, "alive", return_value=True):
            return asyncio.run(self.b.board())

    def test_existing_keys_unchanged_without_a_feed(self):
        got = self.board(os.path.join(self.tmp, "no-hermes"))
        self.assertEqual(set(got), {"version", "tmux", "uptime", "screens", "workspaces", "hermes"})
        self.assertEqual(got["workspaces"], [{"name": "Desk", "kind": "hermes", "state": "live", "profile": "default",
                                              "tabs": [{"name": "Hermes", "kind": "hermes", "state": "live",
                                                        "busy": True, "command": "hermes", "sleeping": False}]}])
        self.assertEqual(got["hermes"], {"sessions": [], "events": []})
        json.dumps(got)

    def test_activity_on_the_tab_and_elsewhere_sessions(self):
        d = os.path.join(self.root, "plugin-data", "omo-space", "feed")
        write(os.path.join(d, "omo-feed.json"), {"v": 1, "profile": "default", "updated": NOW})
        now = time.time()
        write(os.path.join(d, "sessions", "here.json"),
              presence("here", state="tool", tool="terminal", tool_since=now - 4, updated=now,
                       omo={"workspace": "ws1", "tab": "tb1", "title": "omo:ws1:tb1"}))
        write(os.path.join(d, "sessions", "tg.json"), presence("tg", platform="telegram", updated=now))
        write(os.path.join(d, "sessions", "gone.json"), presence("gone", updated=now,
                                                                   omo={"workspace": "wsX", "tab": "t"}))
        got = self.board(self.root)
        tab = got["workspaces"][0]["tabs"][0]
        self.assertEqual((tab["activity"]["state"], tab["activity"]["tool"]), ("tool", "terminal"))
        where = {s["session_id"]: (s["elsewhere"], s["workspace"]) for s in got["hermes"]["sessions"]}
        self.assertEqual(where, {"here": (False, "Desk"), "tg": (True, None), "gone": (True, None)})
        json.dumps(got)


if __name__ == "__main__":
    unittest.main()
