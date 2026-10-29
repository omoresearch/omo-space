#!/usr/bin/env python3
"""tests/bench.py without a game: stats, regression verdicts and exit codes, /proc
helpers, the port guard, scene layout, and that the bench hooks stay test-only."""
import contextlib
import io
import json
import math
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import bench  # noqa: E402


class Percentile(unittest.TestCase):
    def test_nearest_rank_definition(self):
        # rank ceil(q/100 * n), index rank - 1, for every q and several n
        for n in (1, 2, 3, 4, 7, 10, 99, 100, 101, 1000):
            vals = list(range(1, n + 1))
            for q in range(1, 101):
                want = vals[max(0, math.ceil(q * n / 100) - 1)]
                self.assertEqual(bench.percentile(vals, q), want, (n, q))

    def test_exact_ranks_on_1_to_100_are_not_off_by_one(self):
        # q/100*n in floats is 7.000000000000001 for q=7: a ceil of that is rank 8
        vals = list(range(1, 101))
        for q in range(1, 101):
            self.assertEqual(bench.percentile(vals, q), q, q)

    def test_not_interpolated_and_not_rounded_index(self):
        vals = [1, 2, 3, 4]
        self.assertEqual(bench.percentile(vals, 50), 2)       # interpolation: 2.5, round(0.5*3): 3
        self.assertEqual(bench.percentile(vals, 25), 1)
        self.assertEqual(bench.percentile(vals, 26), 2)
        self.assertEqual(bench.percentile(vals, 75), 3)
        self.assertEqual(bench.percentile(vals, 76), 4)

    def test_edges(self):
        self.assertEqual(bench.percentile([], 50), 0.0)
        self.assertEqual(bench.percentile([7], 0), 7)
        self.assertEqual(bench.percentile([7], 99), 7)
        self.assertEqual(bench.percentile([1, 2, 3], 0), 1)       # clamped low
        self.assertEqual(bench.percentile([1, 2, 3], 150), 3)     # clamped high
        self.assertIsInstance(bench.percentile([1, 2], 50), float)


class FrameStats(unittest.TestCase):
    def test_steady_60fps(self):
        s = bench.frame_stats([16.67] * 600)
        self.assertEqual(s["frames"], 600)
        self.assertAlmostEqual(s["fps_mean"], 60.0, delta=0.1)
        self.assertAlmostEqual(s["fps_low1"], 60.0, delta=0.1)
        self.assertEqual(s["frame_p50_ms"], 16.67)
        self.assertEqual(s["frame_p95_ms"], 16.67)
        self.assertEqual(s["frame_max_ms"], 16.67)
        self.assertEqual(s["stutters"], 0)
        self.assertEqual(s["stutters_per_min"], 0.0)

    def test_spikes_show_in_tail_and_stutters_not_in_median(self):
        frames = [10.0] * 990 + [120.0] * 10                   # 1% hitches of 120 ms
        s = bench.frame_stats(frames, wall_s=11.1)
        self.assertEqual(s["frame_p50_ms"], 10.0)
        self.assertEqual(s["frame_p95_ms"], 10.0)
        self.assertEqual(s["frame_p99_ms"], 10.0)               # exactly 1%: the 99th is still fine
        self.assertEqual(s["frame_max_ms"], 120.0)
        self.assertEqual(s["stutters"], 10)
        self.assertAlmostEqual(s["fps_low1"], 1000 / 120.0, places=1)
        self.assertAlmostEqual(s["stutters_per_min"], 10 / (11.1 / 60), places=1)

    def test_one_more_hitch_reaches_p99(self):
        s = bench.frame_stats([10.0] * 989 + [120.0] * 11)
        self.assertEqual(s["frame_p99_ms"], 120.0)

    def test_order_of_frames_does_not_matter(self):
        frames = [5.0, 30.0, 12.0, 8.0, 50.0, 9.0] * 50
        self.assertEqual(bench.frame_stats(frames), bench.frame_stats(sorted(frames)))

    def test_mean_fps_is_frames_over_time_not_mean_of_rates(self):
        s = bench.frame_stats([10.0, 30.0])                     # 2 frames in 40 ms
        self.assertEqual(s["fps_mean"], 50.0)                   # not (100 + 33.3) / 2

    def test_low1_is_mean_of_worst_percent_with_at_least_one_frame(self):
        self.assertAlmostEqual(bench.frame_stats([10.0] * 50 + [40.0])["fps_low1"], 25.0)
        s = bench.frame_stats([10.0] * 180 + [20.0, 40.0])      # 182 frames: worst 1 frame
        self.assertAlmostEqual(s["fps_low1"], 25.0)
        s = bench.frame_stats([10.0] * 198 + [20.0, 40.0])      # 200 frames: worst 2 frames
        self.assertAlmostEqual(s["fps_low1"], 1000 / 30.0, places=2)

    def test_stutter_needs_25ms_even_when_double_the_median(self):
        s = bench.frame_stats([5.0] * 100 + [12.0] * 5)         # 2.4x median but only 12 ms
        self.assertEqual(s["stutters"], 0)
        self.assertEqual(bench.frame_stats([5.0] * 100 + [25.0])["stutters"], 0)    # strictly over
        self.assertEqual(bench.frame_stats([5.0] * 100 + [25.01])["stutters"], 1)

    def test_stutter_needs_double_the_median_when_that_is_over_25ms(self):
        frames = [20.0] * 100 + [39.0, 40.0, 41.0]
        self.assertEqual(bench.frame_stats(frames)["stutters"], 1)

    def test_stutters_per_minute_without_wall_time_uses_frame_time(self):
        s = bench.frame_stats([10.0] * 5999 + [100.0])          # 60.09 s of frames
        self.assertAlmostEqual(s["stutters_per_min"], 1 / (60.09 / 60), places=2)

    def test_empty_and_single(self):
        self.assertEqual(bench.frame_stats([]), {"frames": 0})
        s = bench.frame_stats([16.0])
        self.assertEqual((s["frames"], s["frame_p99_ms"], s["fps_mean"]), (1, 16.0, 62.5))


def run_of(**scene):
    return {"scenes": {"web6": scene}}


class Verdicts(unittest.TestCase):
    def v(self, metric, base, new):
        return bench.verdict(metric, base, new)

    def test_every_threshold_is_well_formed(self):
        for metric, (higher_worse, rel, floor) in bench.THRESHOLDS.items():
            self.assertIsInstance(higher_worse, bool, metric)
            self.assertTrue(0 < rel < 1, metric)
            self.assertGreater(floor, 0, metric)
        self.assertFalse(bench.THRESHOLDS["fps_mean"][0])
        self.assertFalse(bench.THRESHOLDS["fps_low1"][0])
        self.assertTrue(bench.THRESHOLDS["frame_p95_ms"][0])

    def test_both_thresholds_must_be_crossed(self):
        # frame_p95_ms: 10% and 1 ms
        self.assertEqual(self.v("frame_p95_ms", 20.0, 25.0), "REGRESSION")
        self.assertEqual(self.v("frame_p95_ms", 20.0, 20.9), "same")       # under both
        self.assertEqual(self.v("frame_p95_ms", 5.0, 5.9), "same")         # +18% but < 1 ms
        self.assertEqual(self.v("frame_p95_ms", 50.0, 54.0), "same")       # +4 ms but only 8%
        self.assertEqual(self.v("frame_p95_ms", 50.0, 56.0), "REGRESSION")

    def test_thresholds_are_strict(self):
        self.assertEqual(self.v("frame_p95_ms", 20.0, 22.0), "same")       # exactly +10%
        self.assertEqual(self.v("frame_p95_ms", 20.0, 22.01), "REGRESSION")
        self.assertEqual(self.v("frame_p95_ms", 5.0, 6.0), "same")         # exactly +1 ms
        self.assertEqual(self.v("frame_p95_ms", 5.0, 6.01), "REGRESSION")

    def test_direction(self):
        self.assertEqual(self.v("fps_mean", 60.0, 45.0), "REGRESSION")
        self.assertEqual(self.v("fps_mean", 45.0, 60.0), "better")
        self.assertEqual(self.v("frame_p99_ms", 40.0, 20.0), "better")
        self.assertEqual(self.v("game_rss_mb", 3000.0, 3400.0), "REGRESSION")
        self.assertEqual(self.v("game_rss_mb", 3000.0, 3150.0), "same")    # 5%, under 200 MB

    def test_zero_base_needs_only_the_floor(self):
        self.assertEqual(self.v("stutters_per_min", 0.0, 3.0), "same")
        self.assertEqual(self.v("stutters_per_min", 0.0, 3.5), "REGRESSION")
        self.assertEqual(self.v("bridge_cpu_pct", 0.0, 0.0), "same")


class Compare(unittest.TestCase):
    def verdicts(self, base, new):
        return {m: v for _, m, _, _, _, v in bench.compare(run_of(**base), run_of(**new))}

    def test_rows_carry_values_and_percent(self):
        rows = bench.compare(run_of(frame_p95_ms=20.0), run_of(frame_p95_ms=25.0))
        self.assertEqual(rows, [("web6", "frame_p95_ms", 20.0, 25.0, 25.0, "REGRESSION")])

    def test_metrics_outside_thresholds_are_ignored(self):
        self.assertEqual(bench.compare(run_of(frames=100, gc_ms=5), run_of(frames=1, gc_ms=900)), [])

    def test_scenes_missing_from_either_run_are_skipped(self):
        rows = bench.compare({"scenes": {"idle": {"fps_mean": 60}}}, {"scenes": {"web6": {"fps_mean": 30}}})
        self.assertEqual(rows, [])

    def test_lost_metric_is_missing_and_fails(self):
        # a scene that recorded no frames comes back as {"frames": 0}: it must not pass
        rows = bench.compare(run_of(fps_mean=60.0, frame_p95_ms=17.0), run_of(frames=0))
        self.assertEqual({r[5] for r in rows}, {"MISSING"})
        self.assertEqual(len(bench.failures(rows)), 2)
        self.assertEqual(self.verdicts({"game_rss_mb": 3000.0}, {"game_rss_mb": None})["game_rss_mb"],
                         "MISSING")

    def test_metric_absent_in_base_is_skipped(self):
        self.assertEqual(bench.compare(run_of(game_rss_mb=None), run_of(game_rss_mb=4000.0)), [])

    def test_failures_are_regressions_and_missing_only(self):
        rows = bench.compare(run_of(fps_mean=60.0, frame_p95_ms=20.0, game_rss_mb=3000.0),
                             run_of(fps_mean=80.0, frame_p95_ms=30.0))
        self.assertEqual(sorted(r[1] for r in bench.failures(rows)), ["frame_p95_ms", "game_rss_mb"])

    def test_format_handles_missing_and_zero_base(self):
        rows = bench.compare(run_of(fps_mean=60.0, stutters_per_min=0.0),
                             run_of(stutters_per_min=9.0))
        text = bench.format_compare(rows)
        self.assertIn("MISSING", text)
        self.assertIn("n/a", text)
        self.assertIn("REGRESSION", text)


class CompareCli(unittest.TestCase):
    def run_cli(self, base, new):
        with tempfile.TemporaryDirectory() as d:
            a, b = Path(d) / "a.json", Path(d) / "b.json"
            a.write_text(json.dumps(base))
            b.write_text(json.dumps(new))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = bench.main(["compare", str(a), str(b)])
            return code, out.getvalue()

    def test_regression_exits_1(self):
        code, out = self.run_cli(run_of(frame_p95_ms=20.0, fps_mean=60.0), run_of(frame_p95_ms=30.0, fps_mean=60.0))
        self.assertEqual(code, 1)
        self.assertIn("1 regression(s)", out)

    def test_no_regression_exits_0(self):
        code, out = self.run_cli(run_of(frame_p95_ms=20.0, fps_mean=60.0), run_of(frame_p95_ms=19.0, fps_mean=61.0))
        self.assertEqual(code, 0)
        self.assertIn("no regressions", out)

    def test_improvement_exits_0(self):
        self.assertEqual(self.run_cli(run_of(fps_mean=30.0), run_of(fps_mean=60.0))[0], 0)

    def test_missing_metric_exits_1(self):
        self.assertEqual(self.run_cli(run_of(fps_mean=60.0), run_of(frames=0))[0], 1)

    def test_nothing_in_common_exits_2(self):
        self.assertEqual(self.run_cli({"scenes": {"idle": {"fps_mean": 60}}}, {"scenes": {}})[0], 2)

    def test_same_file_is_never_a_regression(self):
        run = {"scenes": {"idle": dict.fromkeys(bench.THRESHOLDS, 12.5),
                          "web6": dict.fromkeys(bench.THRESHOLDS, 40.0)}}
        self.assertEqual(self.run_cli(run, run)[0], 0)

    def test_show(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "r.json"
            f.write_text(json.dumps({"env": {"git": "abc"}, "scenes": {"idle": {"fps_mean": 60}}}))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(bench.main(["show", str(f)]), 0)
            self.assertIn("fps_mean=60", out.getvalue())

    def test_unknown_scene_is_refused_before_touching_a_game(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(bench.main(["run", "--scenes", "idle,nope"]), 2)
        self.assertIn("nope", err.getvalue())


class FakeDriver:
    """The acceptance module's `call` as far as measure() uses it."""

    def __init__(self, stop_reply):
        self.stop_reply, self.ops = stop_reply, []

    def call(self, op, body=None, timeout=40):
        self.ops.append(op)
        return self.stop_reply if op == "bench_stop" else {}


class Measure(unittest.TestCase):
    def test_output_has_every_compared_metric(self):
        d = FakeDriver({"wall_s": 1.0, "frame_ms": [10.0] * 100, "render_cpu_ms": 500.0,
                        "server_cpu_ms": 250.0, "gc_count": 1, "gc_ms": 3, "java_heap_mb": 900,
                        "pages": 6, "truncated": False})
        me = os.getpid()
        out = bench.measure(d, 0, 0, me, me)
        self.assertEqual(d.ops[-2:], ["bench_start", "bench_stop"])
        for metric in bench.THRESHOLDS:
            self.assertIsNotNone(out.get(metric), metric)
        self.assertEqual(out["render_cpu_ms_per_frame"], 5.0)
        self.assertEqual(out["server_cpu_pct"], 25.0)
        self.assertEqual(out["pages"], 6)
        self.assertFalse(out["truncated"])

    def test_unattributed_cpu_and_no_frames_are_none_not_zero(self):
        out = bench.measure(FakeDriver({"wall_s": 1.0, "frame_ms": []}), 0, 0, None, None)
        self.assertEqual(out["frames"], 0)
        self.assertIsNone(out["render_cpu_ms_per_frame"])
        self.assertIsNone(out["server_cpu_pct"])
        self.assertIsNone(out["game_rss_mb"])
        self.assertIsNone(out["bridge_cpu_pct"])


class ProcHelpers(unittest.TestCase):
    def test_descendants_walks_the_whole_tree(self):
        ppid_of = {10: 1, 11: 10, 12: 11, 13: 10, 20: 1}
        self.assertEqual(sorted(bench.descendants(10, ppid_of)), [11, 12, 13])
        self.assertEqual(bench.descendants(20, ppid_of), [])

    def test_own_process_metrics(self):
        me = os.getpid()
        self.assertGreater(bench.rss_mb(me), 1)
        self.assertGreaterEqual(bench.cpu_ticks(me), 0)
        self.assertEqual(bench.parents().get(me), os.getppid())

    def test_missing_process_is_zero_not_an_error(self):
        self.assertEqual(bench.rss_mb(2 ** 30), 0.0)
        self.assertEqual(bench.cpu_ticks(2 ** 30), 0)

    def test_find_game_ignores_non_java(self):
        self.assertIsNone(bench.find_game(1))                  # no JVM with -Domo.test.port=1


class FakeEnv:
    def __init__(self, port, driver, bridge_pid=None):
        self.PORT, self.DRIVER_PORT, self._bridge = port, driver, bridge_pid

    def bridge_pid(self):
        return self._bridge


class Ports(unittest.TestCase):
    BRIDGE, DRIVER, OURS, GAME, OTHER = 18794, 18793, 111, 222, 333

    def bad(self, holders, bridge_pid=OURS, game_pid=GAME):
        env = FakeEnv(self.BRIDGE, self.DRIVER, bridge_pid)
        return bench.foreign_ports(env, game_pid, holders=lambda port: holders.get(port, set()))

    def test_our_own_test_stack_is_fine(self):
        self.assertEqual(self.bad({self.BRIDGE: {self.OURS}, self.DRIVER: {self.GAME}}), [])

    def test_bridge_not_started_yet_is_fine(self):
        self.assertEqual(self.bad({self.DRIVER: {self.GAME}}, bridge_pid=None), [])

    def test_someone_elses_bridge_port_is_refused(self):
        # seen: a robot sim on the default 8794 would have answered the bench as its "bridge"
        self.assertEqual(self.bad({self.BRIDGE: {self.OTHER}, self.DRIVER: {self.GAME}}, bridge_pid=None),
                         [("OMO_TEST_BRIDGE_PORT", self.BRIDGE)])
        # even with a live test bridge pid file: the listener is not it
        self.assertEqual(self.bad({self.BRIDGE: {self.OTHER}, self.DRIVER: {self.GAME}}),
                         [("OMO_TEST_BRIDGE_PORT", self.BRIDGE)])

    def test_someone_elses_driver_port_is_refused_even_with_our_game_running(self):
        # the game's driver could not bind, so the port answers for another program
        self.assertEqual(self.bad({self.BRIDGE: {self.OURS}, self.DRIVER: {self.OTHER}}),
                         [("OMO_TEST_PORT", self.DRIVER)])

    def test_listener_of_unknown_owner_is_refused(self):
        self.assertEqual(self.bad({self.BRIDGE: {bench.UNKNOWN}, self.DRIVER: {bench.UNKNOWN}}),
                         [("OMO_TEST_BRIDGE_PORT", self.BRIDGE), ("OMO_TEST_PORT", self.DRIVER)])

    def test_shared_port_is_refused(self):
        self.assertEqual(self.bad({self.BRIDGE: {self.OURS, self.OTHER}, self.DRIVER: {self.GAME}}),
                         [("OMO_TEST_BRIDGE_PORT", self.BRIDGE)])

    def test_driver_port_nobody_holds_is_refused(self):
        self.assertEqual(self.bad({self.BRIDGE: {self.OURS}}), [("OMO_TEST_PORT", self.DRIVER)])

    def test_parse_ss(self):
        out = ('LISTEN 0 50 127.0.0.1:8794 0.0.0.0:* users:(("python3",pid=4242,fd=3))\n'
               'LISTEN 0 50 [::1]:8794 [::]:* users:(("python3",pid=4242,fd=4),("python3",pid=77,fd=4))\n')
        self.assertEqual(bench.parse_ss_pids(out), {4242, 77})
        self.assertEqual(bench.parse_ss_pids("LISTEN 0 50 127.0.0.1:8794 0.0.0.0:*\n"), {bench.UNKNOWN})
        self.assertEqual(bench.parse_ss_pids(""), set())

    def test_real_listener_by_pid(self):
        import socket
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            s.listen(1)
            port = s.getsockname()[1]
            pids = bench.listener_pids(port)
            if pids is None:
                self.skipTest("no ss on this machine")
            self.assertEqual(pids, {os.getpid()})
            env = FakeEnv(port, port, bridge_pid=None)
            self.assertIn(("OMO_TEST_BRIDGE_PORT", port), bench.foreign_ports(env, os.getpid()))
            env = FakeEnv(port, port, bridge_pid=os.getpid())
            self.assertEqual(bench.foreign_ports(env, os.getpid()), [])


class Layout(unittest.TestCase):
    def test_slots_never_overlap_and_cap_at_twelve(self):
        cells = set()
        for xs, ys in bench.wall_slots(12) + bench.wall_slots(12, bench.TERM_ROWS):
            for x in xs:
                for y in ys:
                    self.assertNotIn((x, y), cells)
                    cells.add((x, y))
        self.assertEqual(len(bench.wall_slots(99)), 12)
        self.assertEqual(len(bench.wall_slots(6)), 6)

    def test_walls_fit_the_cleared_box(self):
        # clear_bench() empties x -64..63, y -60..-41: every wall must lie inside it
        for xs, ys in bench.wall_slots(12) + bench.wall_slots(12, bench.TERM_ROWS):
            self.assertTrue(-64 <= min(xs) and max(xs) <= 63 and -60 <= min(ys) and max(ys) <= -41)

    def test_scene_names(self):
        self.assertEqual(list(bench.SCENES), ["idle", "web6", "web12", "term6", "mixed"])


class TestOnlyHooks(unittest.TestCase):
    """The frame log lives inside TestDriver.java, which release builds leave out."""
    SRC = ROOT / "mod/src/main/java"

    def test_frame_log_and_bench_ops_only_in_test_driver(self):
        for f in self.SRC.rglob("*.java"):
            if f.name == "TestDriver.java":
                continue
            text = f.read_text(encoding="utf-8")
            self.assertNotIn("FrameLog", text, f)
            self.assertNotIn("bench_start", text, f)
        driver = (self.SRC / "space/omo/life/client/TestDriver.java").read_text(encoding="utf-8")
        for needle in ("class FrameLog", '"bench_start"', '"bench_stop"'):
            self.assertIn(needle, driver)

    def test_release_build_excludes_test_driver_and_package_checks_it(self):
        build = (ROOT / "mod/build.sh").read_text()
        m = re.search(r"TEST_ONLY='([^']+)'", build)
        self.assertIsNotNone(m)
        self.assertRegex("src/main/java/space/omo/life/client/TestDriver.java", m.group(1))
        self.assertIn('TEST="${OMO_TEST:-0}"', build)
        package = (ROOT / "scripts/package.sh").read_text()
        self.assertIn("OMO_TEST=0 mod/build.sh", package)
        self.assertIn("grep -q 'TestDriver'", package)      # TestDriver$FrameLog.class matches too


if __name__ == "__main__":
    unittest.main()
