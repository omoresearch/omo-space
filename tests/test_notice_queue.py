#!/usr/bin/env python3
"""The HUD's Hermes notice stack (mod/.../client/NoticeQueue.java).

NoticeQueue has no Minecraft dependencies, so it is compiled and run on its
own: at most three at once (a fourth pushes the oldest out), each shown five
seconds with a fade in and out, section-sign formatting codes and control
characters stripped before drawing, clamped text, unknown levels as info, and
the hermesNotices client config toggle (default on, other keys kept, atomic).
Skips without a JDK (JAVA_HOME or javac on PATH).

    python3 tests/test_notice_queue.py
"""
import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SOURCE = REPO / "mod/src/main/java/space/omo/life/client/NoticeQueue.java"


def jdk_tool(name):
    home = os.environ.get("JAVA_HOME")
    if home and (Path(home) / "bin" / name).is_file():
        return str(Path(home) / "bin" / name)
    return shutil.which(name)


CHECK = textwrap.dedent("""
    import java.nio.file.Files;
    import java.nio.file.Path;
    import java.util.List;
    import space.omo.life.client.NoticeQueue;
    import space.omo.life.client.NoticeQueue.Level;
    import space.omo.life.client.NoticeQueue.Notice;

    public class Check {
        static void eq(String what, Object got, Object want) {
            if (!java.util.Objects.equals(got, want)) {
                throw new AssertionError(what + ": got " + got + ", want " + want);
            }
        }

        static List<String> texts(List<Notice> ns) {
            return ns.stream().map(Notice::text).toList();
        }

        public static void main(String[] a) throws Exception {
            eq("limits", NoticeQueue.MAX + " " + NoticeQueue.SHOW_MS, "3 5000");

            // Expiry: shown for SHOW_MS, then gone.
            NoticeQueue q = new NoticeQueue();
            eq("added", q.add("default", "build finished", "success", 1000), true);
            eq("visible at once", texts(q.visible(1000)), List.of("build finished"));
            eq("still there just before expiry", q.visible(1000 + NoticeQueue.SHOW_MS - 1).size(), 1);
            eq("gone at expiry", q.visible(1000 + NoticeQueue.SHOW_MS).size(), 0);
            eq("forgotten", q.isEmpty(), true);

            // Max three: a fourth pushes the oldest out; order is oldest first.
            q.add("a", "one", "info", 0);
            q.add("b", "two", "info", 100);
            q.add("c", "three", "info", 200);
            q.add("d", "four", "warn", 300);
            eq("max three, oldest out", texts(q.visible(300)), List.of("two", "three", "four"));
            eq("each expires on its own clock", texts(q.visible(100 + NoticeQueue.SHOW_MS)), List.of("three", "four"));
            q.clear();
            eq("cleared", q.visible(400).isEmpty(), true);

            // Cleaning: section-sign codes, controls, bidi overrides, whitespace.
            q.add("\\u00a7cdev\\u00a7r", "\\u00a7l\\u00a7kBoom\\u00a7r tests\\n\\tare  \\u00a7agreen\\u0007\\u202e!", "info", 0);
            Notice n = q.visible(0).get(0);
            eq("profile cleaned", n.profile(), "dev");
            eq("text cleaned", n.text(), "Boom tests are green!");
            eq("no section sign anywhere", n.text().indexOf('\\u00a7') + n.profile().indexOf('\\u00a7'), -2);
            eq("trailing sign alone", NoticeQueue.clean("end\\u00a7"), "end");
            eq("null", NoticeQueue.clean(null), "");
            eq("leading/trailing space", NoticeQueue.clean("  hi  there  "), "hi there");
            eq("emoji kept", NoticeQueue.clean("done \\uD83C\\uDF89"), "done \\uD83C\\uDF89");

            // Empty after cleaning: dropped. Empty profile: "hermes".
            q.clear();
            eq("empty text dropped", q.add("p", "\\u00a7a\\u00a7l  \\n", "info", 0), false);
            eq("null text dropped", q.add("p", null, "info", 0), false);
            q.add("", "hello", null, 0);
            eq("default profile", q.visible(0).get(0).profile(), "hermes");

            // Clamping.
            q.clear();
            q.add("p".repeat(100), "y".repeat(500), "info", 0);
            n = q.visible(0).get(0);
            eq("text clamped", n.text().length(), NoticeQueue.TEXT_MAX);
            eq("text ellipsis", n.text().endsWith("\\u2026"), true);
            eq("profile clamped", n.profile().length(), NoticeQueue.PROFILE_MAX);

            // Levels.
            eq("success", Level.of("success"), Level.SUCCESS);
            eq("WARN", Level.of(" WARN "), Level.WARN);
            eq("unknown", Level.of("loud"), Level.INFO);
            eq("null level", Level.of(null), Level.INFO);

            // Fade: 0 at birth, full in the middle, eased out, 0 after expiry.
            Notice f = new Notice("p", "t", Level.INFO, 10_000);
            eq("birth", NoticeQueue.opacity(f, 10_000), 0f);
            eq("half fade-in", NoticeQueue.opacity(f, 10_000 + NoticeQueue.FADE_IN_MS / 2), 0.5f);
            eq("held", NoticeQueue.opacity(f, 12_000), 1f);
            float out = NoticeQueue.opacity(f, 10_000 + NoticeQueue.SHOW_MS - NoticeQueue.FADE_OUT_MS / 4);
            eq("fading out", out > 0f && out < 0.5f, true);
            eq("expired", NoticeQueue.opacity(f, 10_000 + NoticeQueue.SHOW_MS), 0f);
            eq("before birth", NoticeQueue.opacity(f, 9_000), 0f);
            eq("timer full", NoticeQueue.remaining(f, 10_000), 1f);
            eq("timer half", NoticeQueue.remaining(f, 10_000 + NoticeQueue.SHOW_MS / 2), 0.5f);
            eq("timer empty", NoticeQueue.remaining(f, 99_000), 0f);

            // Client config: config/omolife-client.properties hermesNotices=.
            Path dir = Path.of(a[0]);
            Path cfg = dir.resolve("omolife-client.properties");
            eq("missing file: on", NoticeQueue.readEnabled(cfg), true);
            NoticeQueue.writeEnabled(cfg, false);
            eq("off", NoticeQueue.readEnabled(cfg), false);
            eq("file says so", Files.readString(cfg).contains("hermesNotices=false"), true);
            eq("no temp file left", Files.list(dir).count(), 1L);
            Files.writeString(cfg, "other=1\\nhermesNotices = FALSE\\n");
            eq("case and spaces", NoticeQueue.readEnabled(cfg), false);
            NoticeQueue.writeEnabled(cfg, true);
            eq("on again", NoticeQueue.readEnabled(cfg), true);
            eq("other keys kept", Files.readString(cfg).contains("other=1"), true);
            Files.writeString(cfg, "hermesNotices=maybe\\n");
            eq("junk reads as on", NoticeQueue.readEnabled(cfg), true);
            NoticeQueue.writeEnabled(dir.resolve("no/such/dir/x.properties"), false);    // must not throw
            eq("unwritable is ignored", NoticeQueue.readEnabled(dir.resolve("no/such/dir/x.properties")), true);
            System.out.println("PASS");
        }
    }
""")


@unittest.skipUnless(jdk_tool("javac") and jdk_tool("java"), "no JDK (set JAVA_HOME)")
class NoticeQueueTest(unittest.TestCase):
    def test_queue(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "Check.java").write_text(CHECK)
            out = d / "classes"
            subprocess.run([jdk_tool("javac"), "-d", str(out), str(SOURCE), str(d / "Check.java")],
                           check=True, capture_output=True, text=True)
            data = d / "data"
            data.mkdir()
            p = subprocess.run([jdk_tool("java"), "-cp", str(out), "Check", str(data)],
                               capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertIn("PASS", p.stdout)

    def test_pure(self):
        """No Minecraft or Fabric types: the test above compiles it alone."""
        src = SOURCE.read_text()
        self.assertNotIn("net.minecraft", src)
        self.assertNotIn("net.fabricmc", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
