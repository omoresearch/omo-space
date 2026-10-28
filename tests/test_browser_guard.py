#!/usr/bin/env python3
"""Chromium runaway guard: the eviction rule, BrowserGuard's limits, HelperWatchdog.

Compiles the two real classes on their own (they use no Minecraft or MCEF
types) and drives them with a fake clock:

- each rule on its own (budget, churn breaker, ceiling, pauses, memory levels);
- a frame-by-frame model of WebPages.open() over the 27 Oct 2026 crash
  (14 walls in view, cap 12), run with the old eviction rule (which must
  thrash, or the model is wrong) and with the real one (which must not);
- the watchdog against real child processes named jcef_helper;
- the source: every Chromium browser is created and closed in one place.

JAVA_HOME=/path/to/jdk21+ python3 -m unittest discover -s tests -p test_browser_guard.py -v
"""
import os
import re
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "mod/src/main/java/space/omo/life/client"

HARNESS = r"""
package space.omo.life.client;

import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;

public final class GuardHarness {
    static void check(boolean ok, String why) { if (!ok) throw new AssertionError(why); }

    public static void main(String[] a) throws Exception {
        switch (a[0]) {
            case "budget" -> budget();
            case "churn" -> churn();
            case "ceiling" -> ceiling();
            case "assess" -> assess();
            case "evict" -> evict();
            case "near" -> near();
            case "crash" -> crash();
            case "watchdog" -> watchdog();
            case "closing" -> closing();
            default -> throw new AssertionError("unknown case " + a[0]);
        }
        System.out.println("PASS " + a[0]);
    }

    static void budget() {
        AtomicLong t = new AtomicLong(1_000_000);
        BrowserGuard g = new BrowserGuard(t::get);
        for (int i = 0; i < BrowserGuard.BURST; i++) {
            check(g.deny("w" + i, 0).isEmpty(), "burst browser " + i + " allowed");
            g.created("w" + i);
        }
        check(g.deny("next", 0).equals("Opening pages..."), "past the burst, wait");
        t.addAndGet(BrowserGuard.REFILL_MS);
        check(g.deny("next", 0).isEmpty(), "one more after a refill");
        // However fast it asks: never more than PER_MINUTE in a minute, and the budget is usable.
        AtomicLong t2 = new AtomicLong(0);
        BrowserGuard h = new BrowserGuard(t2::get);
        int made = 0;
        for (int ms = 0; ms < 60_000; ms += 16) {
            t2.set(ms);
            if (h.deny("k" + made, 0).isEmpty()) { h.created("k" + made); made++; }
        }
        check(made == BrowserGuard.PER_MINUTE, "PER_MINUTE a minute, made " + made);
    }

    static void churn() {
        AtomicLong t = new AtomicLong(0);
        BrowserGuard g = new BrowserGuard(t::get);
        for (int i = 0; i < BrowserGuard.CHURN_OPENS; i++) {
            t.addAndGet(5_000);
            check(g.deny("wall", 0).isEmpty(), "open " + i + " allowed");
            check(g.created("wall") == (i == BrowserGuard.CHURN_OPENS - 1), "trips exactly on open " + i);
        }
        t.addAndGet(5_000);
        check(g.deny("wall", 0).contains("kept reopening"), "parked wall is refused with a reason");
        check(g.deny("other", 0).isEmpty(), "other walls are not affected");
        check(!g.created(null) && g.deny(null, 0).isEmpty(), "tabs have no churn key");
        t.addAndGet(BrowserGuard.CHURN_COOLDOWN_MS);
        g.prune();
        check(g.deny("wall", 0).isEmpty(), "back after the cooldown");
        // Normal reopening (walking away and back) never trips it.
        BrowserGuard h = new BrowserGuard(t::get);
        for (int i = 0; i < 20; i++) {
            t.addAndGet(BrowserGuard.CHURN_WINDOW_MS / (BrowserGuard.CHURN_OPENS - 1) + 1);
            check(h.deny("w", 0).isEmpty(), "slow reopen " + i);
            check(!h.created("w"), "slow reopen " + i + " does not trip");
        }
    }

    static void ceiling() {
        BrowserGuard g = new BrowserGuard(() -> 0L);
        check(g.deny("x", BrowserGuard.MAX_BROWSERS - 1).isEmpty(), "under the ceiling");
        check(g.deny("x", BrowserGuard.MAX_BROWSERS).contains("limit"), "at the ceiling");

        AtomicLong t = new AtomicLong(0);
        BrowserGuard p = new BrowserGuard(t::get);
        p.pause(10_000, "long");
        check(p.deny("x", 0).equals("long") && p.pauseReason().equals("long"), "a pause refuses with its reason");
        p.pause(1_000, "short");
        t.set(5_000);
        check(p.paused(), "a shorter pause never cuts a longer one");
        check(p.pauseReason().equals("long"), "nor replaces its reason");
        p.pause(10_000, "later");
        check(p.pauseReason().equals("later"), "the pause ending last gives the reason");
        t.set(15_001);
        check(!p.paused() && p.deny("x", 0).isEmpty(), "pause ends");
    }

    static void assess() {
        long G = 1L << 30;
        check(BrowserGuard.assess(null) == BrowserGuard.Level.OK, "no /proc: ok");
        check(BrowserGuard.assess(new BrowserGuard.Sample(30, 14, 3 * G, 40 * G)) == BrowserGuard.Level.OK,
            "a dozen walls is normal");
        check(BrowserGuard.assess(new BrowserGuard.Sample(1971, 1900, 50 * G, 1 * G)) == BrowserGuard.Level.HARD,
            "the 27 Oct crash state is HARD");
        check(BrowserGuard.assess(new BrowserGuard.Sample(90, 70, 3 * G, 40 * G)) == BrowserGuard.Level.SOFT,
            "too many helpers: SOFT");
        check(BrowserGuard.assess(new BrowserGuard.Sample(10, 5, 9 * G, 40 * G)) == BrowserGuard.Level.SOFT,
            "too much helper memory: SOFT");
        check(BrowserGuard.assess(new BrowserGuard.Sample(10, 5, 1 * G, 5 * G)) == BrowserGuard.Level.SOFT,
            "machine low on memory: SOFT");
        check(BrowserGuard.assess(new BrowserGuard.Sample(10, 5, 1 * G, 2 * G)) == BrowserGuard.Level.HARD,
            "machine nearly out of memory: browsers give way");
        check(BrowserGuard.assess(new BrowserGuard.Sample(10, 5, 1 * G, -1)) == BrowserGuard.Level.OK,
            "unknown MemAvailable is not low");
        check(new BrowserGuard.Sample(3, 1, 5L << 20, -1).describe().equals("3 helpers, 5 MB, ? MB free"),
            "log line");
    }

    /** Low machine memory keeps the nearest walls lit; Chromium over its own limits does not. */
    static void near() {
        long G = 1L << 30;
        BrowserGuard.Sample lowMachine = new BrowserGuard.Sample(19, 10, 1 * G, 2 * G);   // today's 04:54 state
        check(BrowserGuard.assess(lowMachine) == BrowserGuard.Level.HARD && !BrowserGuard.chromiumOver(lowMachine),
            "machine short, Chromium fine");
        check(BrowserGuard.nearExempt(lowMachine, 5, 0) && BrowserGuard.nearExempt(lowMachine, 5, 1),
            "the two nearest walls keep a page");
        check(!BrowserGuard.nearExempt(lowMachine, 5, BrowserGuard.NEAR_KEEP), "a third does not");
        check(!BrowserGuard.nearExempt(lowMachine, BrowserGuard.NEAR_BLOCKS + 1, 0), "a far wall does not");
        check(!BrowserGuard.nearExempt(new BrowserGuard.Sample(19, 10, 1 * G, 1 * G), 5, 0), "nothing under the floor");
        BrowserGuard.Sample runaway = new BrowserGuard.Sample(1971, 1900, 50 * G, 40 * G);
        check(BrowserGuard.chromiumOver(runaway) && !BrowserGuard.nearExempt(runaway, 1, 0), "a Chromium runaway closes all");

        AtomicLong t = new AtomicLong(0);
        BrowserGuard g = new BrowserGuard(t::get);
        g.pause(BrowserGuard.HARD_PAUSE_MS, BrowserGuard.MEMORY_PAUSE + ": the computer is low on memory");
        check(!g.deny("x", 0).isEmpty(), "a memory pause refuses an ordinary wall");
        check(g.deny("x", 0, true).isEmpty(), "and lets a near wall through");
        BrowserGuard h = new BrowserGuard(t::get);
        h.pause(BrowserGuard.REAP_HOLD_MS, "Opening pages...");
        check(!h.deny("x", 0, true).isEmpty(), "a reap hold is never bypassed");
        check(g.deny("x", BrowserGuard.MAX_BROWSERS, true).contains("limit"), "the ceiling still holds");
    }

    static void evict() {
        long E = BrowserGuard.EVICT_UNSEEN_MS;
        long now = 100_000;
        check(!BrowserGuard.evictable(now, now, now - 60_000, false), "a wall drawn this frame keeps its page");
        check(!BrowserGuard.evictable(now, now - E, now - 100, false), "a young page is kept, even unseen");
        check(!BrowserGuard.evictable(now, now - E, now - E, true), "a page in use full-window is kept");
        check(BrowserGuard.evictable(now, now - E, now - E, false), "an old page nobody asked for may go");
    }

    /**
     * WebPages.open() frame by frame: walls in view ask for their page every
     * frame; at MAX_PAGES a new page needs an evictable one. Pages paint
     * after LOAD_MS. {@code oldRule} is the rule before the fix: "unseen"
     * meant no painted frame for 5 s, and a page that had never painted
     * counted from time 0.
     */
    static final class Model {
        static final int MAX_PAGES = 12;
        static final long LOAD_MS = 2000;
        final boolean oldRule;
        final boolean guarded;
        final AtomicLong clock = new AtomicLong(0);
        final BrowserGuard guard;
        final Map<Integer, long[]> pages = new HashMap<>();   // wall -> {createdAt, lastWanted, lastPainted}
        int created;
        int evicted;

        Model(boolean oldRule, boolean guarded) {
            this.oldRule = oldRule;
            this.guarded = guarded;
            this.guard = new BrowserGuard(clock::get);
        }

        void frame(long now, int firstWall, int walls) {
            clock.set(now);
            for (int w = firstWall; w < firstWall + walls; w++) {
                long[] p = pages.get(w);
                if (p != null) {
                    p[1] = now;
                    if (now - p[0] >= LOAD_MS) p[2] = now;
                    continue;
                }
                if (guarded && !guard.deny(w, pages.size()).isEmpty()) continue;
                if (pages.size() >= MAX_PAGES && !evictOne(now)) continue;
                pages.put(w, new long[]{now, now, 0});
                if (guarded) guard.created(w);
                created++;
            }
        }

        boolean evictOne(long now) {
            Integer oldest = null;
            long oldestSeen = Long.MAX_VALUE;
            for (var e : pages.entrySet()) {
                long[] p = e.getValue();
                long seen = oldRule ? p[2] : p[1];
                boolean ok = oldRule ? now - p[2] >= 5000 : BrowserGuard.evictable(now, p[1], p[0], false);
                if (ok && seen < oldestSeen) { oldest = e.getKey(); oldestSeen = seen; }
            }
            if (oldest == null) return false;
            pages.remove(oldest);
            evicted++;
            return true;
        }

        void run(long fromMs, long toMs, int firstWall, int walls) {
            for (long ms = fromMs; ms < toMs; ms += 16) frame(ms, firstWall, walls);
        }
    }

    /**
     * As in the 27 Oct log (21:46:35): 12 walls in view and loaded; the
     * player lets go of a wall and it re-forms, so wall 0 is gone and three
     * new walls (12-14) want pages for the one slot its page frees. A new
     * page had never painted, so for the old rule it was "unseen" at once:
     * the new walls evicted each other's page every frame.
     */
    static void scene(Model m) {
        m.run(0, 10_000, 0, 12);
        m.run(10_000, 90_000, 1, 14);
    }

    static void crash() {
        // The bug, reproduced: old rule, no guard.
        Model old = new Model(true, false);
        scene(old);
        System.out.println("old rule:  " + old.created + " browsers, " + old.evicted + " evictions in 90 s");
        check(old.created > 1000, "the model must reproduce the thrash, or it proves nothing");

        // The eviction fix alone ends it: wall 0's stale page makes room for
        // one new wall, once; the other two stay dark ("Page limit reached").
        Model fixed = new Model(false, false);
        scene(fixed);
        System.out.println("new rule:  " + fixed.created + " browsers, " + fixed.evicted + " evictions");
        check(fixed.created == Model.MAX_PAGES + 1 && fixed.evicted == 1,
            "only the page out of view is evicted, once");

        // The guard alone bounds the old bug.
        Model guardedOld = new Model(true, true);
        scene(guardedOld);
        System.out.println("old rule + guard: " + guardedOld.created + " browsers");
        check(guardedOld.created <= BrowserGuard.BURST + 2 * BrowserGuard.PER_MINUTE, "budget bounds any bug");

        // And the cap still works: turn to 2 other walls, the 2 least recent pages make room.
        Model both = new Model(false, true);
        both.run(0, 20_000, 0, 12);
        both.run(20_000, 40_000, 12, 2);
        check(both.created == 14 && both.evicted == 2, "eviction still frees room for walls in view: "
            + both.created + " created, " + both.evicted + " evicted");
    }

    /** 28 Oct 09:11: draining the queue read a removed IdentityHashMap entry and crashed the game. */
    static void closing() {
        // The old drain: remove through the iterator, then read the entry. On IdentityHashMap that throws.
        java.util.Map<Object, Long> old = new java.util.IdentityHashMap<>();
        old.put(new Object(), 0L);
        try {
            var it = old.entrySet().iterator();
            var e = it.next();
            it.remove();
            e.getKey();
            check(false, "the old pattern should have thrown");
        } catch (IllegalStateException expected) {
            // "Entry was removed": the 09:11 crash report
        }
        ClosingQueue<Object> q = new ClosingQueue<>();
        java.util.List<Object> closed = new java.util.ArrayList<>();
        Object a = new Object(), b = new Object(), c = new Object();
        for (int i = 0; i < 50; i++) q.add(new Object(), 100);   // many due at once, as after closeAll
        q.add(a, 100);
        q.add(b, 200);
        q.add(a, 999);
        check(q.size() == 52 && q.contains(a), "queued once each");
        q.drain(150, closed::add);
        check(closed.size() == 51 && closed.contains(a) && !closed.contains(b), "only the due ones close: " + closed.size());
        check(q.size() == 1 && q.contains(b) && !q.contains(a), "and leave the queue");
        // A close that throws, or that queues something new, cannot corrupt the walk.
        q.add(c, 200);
        int[] calls = {0};
        try {
            q.drain(300, x -> { calls[0]++; q.add(new Object(), 1000); throw new RuntimeException("close failed"); });
            check(false, "the close's exception reaches the caller");
        } catch (RuntimeException expected) {
            check(expected.getMessage().equals("close failed"), "it is the close's own exception");
        }
        check(calls[0] == 2 && !q.contains(b) && !q.contains(c), "every due item is closed even when one throws");
        java.util.List<Object> rest = new java.util.ArrayList<>();
        q.drainAll(rest::add);
        check(q.size() == 0 && rest.size() == calls[0], "drainAll empties it, young items included");
    }

    static void watchdog() throws Exception {
        String fake = System.getProperty("fake.helper");
        BrowserGuard.Sample before = HelperWatchdog.sample();
        check(before != null, "Linux /proc is readable");
        check(before.helpers() == 0, "no helpers yet");
        check(before.availBytes() > 0, "MemAvailable read");
        Process renderer = new ProcessBuilder(fake, "60", "--type=renderer").start();
        Process gpu = new ProcessBuilder(fake, "60", "--type=gpu-process").start();
        try {
            Thread.sleep(300);
            BrowserGuard.Sample s = HelperWatchdog.sample();
            check(s.helpers() == 2, "two helpers counted, got " + s.helpers());
            check(s.renderers() == 1, "one renderer, got " + s.renderers());
            check(s.anonBytes() > 0, "helper memory read");

            AtomicInteger samples = new AtomicInteger();
            HelperWatchdog.start(x -> samples.incrementAndGet());
            CountDownLatch done = new CountDownLatch(1);
            AtomicInteger killed = new AtomicInteger(-1);
            String[] thread = {""};
            HelperWatchdog.reapOrphans(n -> {
                killed.set(n);
                thread[0] = Thread.currentThread().getName();
                done.countDown();
            });
            check(done.await(5, TimeUnit.SECONDS), "reap ran");
            check(thread[0].equals("omo-browser-watchdog"), "reaping runs on the watchdog thread, not the caller's");
            check(killed.get() == 1, "killed exactly the renderer, got " + killed.get());
            check(renderer.waitFor(5, TimeUnit.SECONDS), "renderer is gone");
            check(gpu.isAlive(), "the GPU helper is left alone");
            Thread.sleep(HelperWatchdog.PERIOD_MS + 500);
            check(samples.get() >= 1, "the watchdog samples on its own");
        } finally {
            for (Process p : new Process[]{renderer, gpu}) {
                p.descendants().forEach(ProcessHandle::destroyForcibly);
                p.destroyForcibly();
            }
        }
    }
}
"""


class BrowserGuardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        home = Path.home()
        java_home = Path(os.environ.get("JAVA_HOME", home / ".local/share/omo/toolchain/jdk-25.0.5+8"))
        cls.java = java_home / "bin/java"
        javac = java_home / "bin/javac"
        if not cls.java.is_file() or not javac.is_file():
            raise unittest.SkipTest("needs a JDK 21+ (JAVA_HOME)")
        cls.temp = tempfile.TemporaryDirectory(prefix="omo-browser-guard-")
        cls.addClassCleanup(cls.temp.cleanup)
        root = Path(cls.temp.name)
        cls.classes = root / "classes"
        cls.classes.mkdir()
        harness = root / "src/space/omo/life/client/GuardHarness.java"
        harness.parent.mkdir(parents=True)
        harness.write_text(textwrap.dedent(HARNESS))
        subprocess.run([str(javac), "--release", "21", "-Xlint:all", "-Werror", "-d", str(cls.classes),
                        str(SRC / "BrowserGuard.java"), str(SRC / "HelperWatchdog.java"),
                        str(SRC / "ClosingQueue.java"), str(harness)],
                       check=True)
        # A stand-in helper: a script named jcef_helper (what /proc/<pid>/comm shows).
        # Not a renamed `sleep`: multicall coreutils builds dispatch on argv[0].
        cls.fake = root / "jcef_helper"
        cls.fake.write_text('#!/bin/sh\nsleep "$1"\n')
        cls.fake.chmod(0o755)

    def run_case(self, name):
        r = subprocess.run([str(self.java), f"-Dfake.helper={self.fake}", "-cp", str(self.classes),
                            "space.omo.life.client.GuardHarness", name],
                           capture_output=True, text=True, timeout=60)
        if r.stdout.strip():
            print(r.stdout.strip())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_creation_budget(self):
        self.run_case("budget")

    def test_churn_breaker(self):
        self.run_case("churn")

    def test_live_ceiling_and_pauses(self):
        self.run_case("ceiling")

    def test_memory_levels(self):
        self.run_case("assess")

    def test_near_walls_survive_low_memory(self):
        self.run_case("near")

    def test_eviction_rule(self):
        self.run_case("evict")

    def test_crash_replay_old_rule_thrashes_new_rule_does_not(self):
        self.run_case("crash")

    def test_closing_queue(self):
        self.run_case("closing")

    @unittest.skipUnless(Path("/proc/self").is_dir(), "Linux only")
    def test_watchdog_counts_and_reaps_real_processes(self):
        self.run_case("watchdog")


class SingleDoor(unittest.TestCase):
    """Every browser is born in WebPages.newView and closed in WebPages.closeNow,
    so the guard, the live count and the minimum life cannot be bypassed."""

    def calls(self, pattern):
        hits = []
        for f in sorted(SRC.rglob("*.java")):
            for n, line in enumerate(f.read_text().splitlines(), 1):
                if re.search(pattern, line) and not line.strip().startswith(("*", "//")):
                    hits.append(f"{f.name}:{n}: {line.strip()}")
        return hits

    def test_browsers_are_created_in_one_place(self):
        hits = [h for h in self.calls(r"McefRuntime\.create\(") ]
        self.assertEqual(len(hits), 1, hits)
        self.assertIn("WebPages.java", hits[0])
        self.assertIn("WebView v = McefRuntime.create(url, events);", hits[0])

    def test_browsers_are_closed_in_one_place(self):
        # WebView.close() calls: any `<x>.close()` on a WebView-typed name in the web classes.
        web = SRC / "WebPages.java"
        closes = [ln.strip() for ln in web.read_text().splitlines()
                  if re.search(r"\b(v|view|gone|tab)\.close\(\)", ln)]
        self.assertEqual(closes, ["v.close();"], closes)
        for f in ("MonitorScreen.java", "WebMonitorRenderer.java", "OmoLifeClient.java"):
            text = (SRC / f).read_text()
            self.assertIsNone(re.search(r"\b(view\(\)|tab\(\w*\))\.close\(\)", text), f)


if __name__ == "__main__":
    unittest.main()
