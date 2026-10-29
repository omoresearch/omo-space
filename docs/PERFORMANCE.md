# Performance

Omo Space adds three kinds of load to Minecraft: terminal walls (the bridge streams
every screen, the game draws every character), web walls (one Chromium renderer per
page, drawn as a texture), and the bridge itself. Measure before and after any change
that touches rendering, the browser or the bridge, using the same benchmark each time.

## The benchmark

`tests/bench.py` builds the same scenes in the flat test world and records every
world frame through the test driver (`bench_start` / `bench_stop`). It never touches
the live game or bridge: it runs on the isolated test stack, like `tests/acceptance.py`.

    export OMO_TEST_BRIDGE_PORT=18794 OMO_TEST_PORT=18793     # only if 8794/8793 are taken
    OMO_TEST=1 mod/build.sh                                  # the bench needs the test driver
    tests/launch-test.sh &                                   # OmoTest on Xvfb, test driver up
    python3 tests/bench.py run --label main --out bench-main.json
    # ...make the change, rebuild with OMO_TEST=1 mod/build.sh, relaunch the test game...
    python3 tests/bench.py run --label mine --out bench-mine.json
    python3 tests/bench.py compare bench-main.json bench-mine.json   # exit 1 on a regression

| Scene | What is in view |
|---|---|
| `idle` | nothing Omo: the floor everything else is measured against |
| `web6` / `web12` | 6 / 12 web walls (3x2) on a local, constantly animating page; 12 is `WebPages.MAX_PAGES` |
| `term6` | 6 terminal stations, each linked to a 3x2 wall that prints a line every 50 ms |
| `mixed` | `web6` and `term6` together |

Per scene: frames, mean fps, 1% low fps, p50/p95/p99/max frame time, stutters per
minute (frames over twice the median and over 25 ms), render- and server-thread CPU
per frame, GC, heap, game RSS, Chromium helper count and RSS, open pages, test bridge
CPU. `compare` flags a metric when it is worse by more than both its relative and
its absolute threshold (`THRESHOLDS` in the script) and exits 1 if any metric is, or
if a metric the baseline has is missing from the new run (a scene that recorded no
frames must not pass). Scenes only in one of the runs are skipped, so a subset run
can be compared with a full baseline; exit 2 when nothing overlaps.

Notes:

- Xvfb renders with software GL, so absolute fps is not what a player sees. Compare
  runs from the same machine, build mode and settings; the delta is the result.
- `--scenes idle,web6 --seconds 30` runs a subset for longer. Run the same command
  twice on the same build first: the difference between those two runs is the noise
  you can expect.
- The bench only talks to its own test stack: the bridge port must be free or held
  by the test bridge, the driver port by the OmoTest JVM (checked by pid with `ss`).
  `launch-test.sh` likewise refuses a driver port someone else holds. If other
  programs use the defaults (8794 bridge, 8793 driver), export `OMO_TEST_BRIDGE_PORT`
  and `OMO_TEST_PORT` with free ports for both `launch-test.sh` and the bench.
- The bench starts a second Minecraft (on Xvfb). Don't run it while someone plays
  on the same machine: it costs them frames and skews both.
- The frame log holds 131072 frames (about 36 min at 60 fps); a longer scene is
  marked `truncated`.
- `tests/test_bench.py` covers the statistics, the regression verdicts and exit
  codes, the port guard and the scene layout without a game.

## Profiling a running game

To see where the frame time goes in a real session, record 60 s with Java Flight
Recorder (low overhead, read-only, no restart):

    jcmd <java pid> JFR.start name=omo settings=default duration=60s filename=/tmp/omo.jfr
    jfr print --stack-depth 40 --events jdk.ExecutionSample /tmp/omo.jfr > samples.txt

Then count the frames of the `Render thread` samples. With Iris shaders loaded,
terminal walls are drawn quad by quad (`WebMonitorRenderer.terminalQuads`) instead of
through CC:T's own monitor renderer, so they cost more there than without shaders.
