#!/usr/bin/env python3
"""The "+" new-tab menu's kinds (mod/.../client/TabKind.java).

TabKind has no Minecraft dependencies, so it is compiled and run on its own:
the kinds and their bridge ids, what the menu says next to each one (missing
CLI, a bridge too old for agent tabs), and the remembered last choice
(written atomically, unreadable or unknown -> Shell). Skips without a JDK
(JAVA_HOME or javac on PATH).

    python3 tests/test_tab_kinds.py
"""
import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SOURCE = REPO / "mod/src/main/java/space/omo/life/client/TabKind.java"


def jdk_tool(name):
    home = os.environ.get("JAVA_HOME")
    if home and (Path(home) / "bin" / name).is_file():
        return str(Path(home) / "bin" / name)
    return shutil.which(name)


CHECK = textwrap.dedent("""
    import java.nio.file.Files;
    import java.nio.file.Path;
    import java.util.List;
    import space.omo.life.client.TabKind;

    public class Check {
        static void eq(String what, Object got, Object want) {
            if (!java.util.Objects.equals(got, want)) {
                throw new AssertionError(what + ": got " + got + ", want " + want);
            }
        }

        public static void main(String[] a) throws Exception {
            // The menu, in order, with the ids the bridge takes.
            eq("menu order", List.of(TabKind.values()).toString(), "[SHELL, HERMES, CLAUDE, CODEX]");
            eq("ids", TabKind.CLAUDE.id + " " + TabKind.CODEX.id + " " + TabKind.HERMES.id, "claude codex hermes");
            eq("labels", TabKind.CLAUDE.label + "/" + TabKind.CODEX.label, "Claude Code/Codex");
            eq("of id", TabKind.of("codex"), TabKind.CODEX);
            eq("of unknown", TabKind.of("rm -rf"), TabKind.SHELL);
            eq("of null", TabKind.of(null), TabKind.SHELL);
            eq("agents", TabKind.CLAUDE.agent() && TabKind.CODEX.agent() && !TabKind.HERMES.agent()
                && !TabKind.SHELL.agent(), true);
            eq("distinct colours", java.util.Arrays.stream(TabKind.values()).mapToInt(k -> k.colour).distinct()
                .count(), 4L);

            // A bridge from before agent tabs sends no tab_kinds: only shell and Hermes.
            eq("old bridge: shell", TabKind.SHELL.supportedBy(null), true);
            eq("old bridge: hermes", TabKind.HERMES.supportedBy(null), true);
            eq("old bridge: claude", TabKind.CLAUDE.supportedBy(null), false);
            eq("new bridge: codex", TabKind.CODEX.supportedBy(List.of("shell", "hermes", "claude", "codex")), true);

            // The detail column of the menu.
            eq("shortcut", TabKind.SHELL.menuDetail(true, Boolean.TRUE), "Ctrl+Shift+T");
            eq("hermes shortcut", TabKind.HERMES.menuDetail(true, null), "Ctrl+Shift+H");
            eq("found agent", TabKind.CLAUDE.menuDetail(true, Boolean.TRUE), "");
            eq("unknown availability", TabKind.CLAUDE.menuDetail(true, null), "");
            eq("missing", TabKind.CODEX.menuDetail(true, Boolean.FALSE), "not installed");
            eq("old bridge", TabKind.CODEX.menuDetail(false, Boolean.TRUE), "update bridge");

            // The remembered last choice.
            Path dir = Path.of(a[0]);
            Path f = dir.resolve("omolife-newtab.txt");
            eq("nothing saved yet", TabKind.readLast(f), TabKind.SHELL);
            TabKind.writeLast(f, TabKind.CLAUDE);
            eq("saved", TabKind.readLast(f), TabKind.CLAUDE);
            eq("file holds the id", Files.readString(f).trim(), "claude");
            eq("no temp file left", Files.list(dir).count(), 1L);
            TabKind.writeLast(f, TabKind.CODEX);
            eq("overwritten", TabKind.readLast(f), TabKind.CODEX);
            Files.writeString(f, "something else\\n");
            eq("junk reads as shell", TabKind.readLast(f), TabKind.SHELL);
            TabKind.writeLast(dir.resolve("no/such/dir/x.txt"), TabKind.CLAUDE);   // must not throw
            eq("unwritable is ignored", TabKind.readLast(dir.resolve("no/such/dir/x.txt")), TabKind.SHELL);
            System.out.println("PASS");
        }
    }
""")


@unittest.skipUnless(jdk_tool("javac") and jdk_tool("java"), "no JDK (set JAVA_HOME)")
class TabKinds(unittest.TestCase):
    def test_kinds(self):
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
