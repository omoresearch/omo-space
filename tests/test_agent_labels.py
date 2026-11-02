#!/usr/bin/env python3
"""Agent body labels (mod/.../client/AgentLabels.java, -Domo.agents.labels).

AgentLabels has no Minecraft dependencies, so it is compiled and run on its
own: no flag means no labels (behaviour unchanged), names are matched
case-insensitively and must be Minecraft usernames, labels are plain text
(no section-sign formatting codes, no control characters) capped in length,
and bad input is skipped instead of failing the game. Skips without a JDK
(JAVA_HOME or javac on PATH).

    python3 tests/test_agent_labels.py
"""
import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SOURCE = REPO / "mod/src/main/java/space/omo/life/client/AgentLabels.java"


def jdk_tool(name):
    home = os.environ.get("JAVA_HOME")
    if home and (Path(home) / "bin" / name).is_file():
        return str(Path(home) / "bin" / name)
    return shutil.which(name)


CHECK = textwrap.dedent("""
    package space.omo.life.client;

    import java.util.Map;

    public class Check {
        static void eq(String what, Object got, Object want) {
            if (!java.util.Objects.equals(got, want)) {
                throw new AssertionError(what + ": got " + got + ", want " + want);
            }
        }

        public static void main(String[] a) {
            if (a.length > 0 && a[0].equals("live")) {
                // What the game sees: the flag as passed on this JVM.
                System.out.print(AgentLabels.all() + "|" + AgentLabels.label("Omo") + "|" + AgentLabels.label(null));
                return;
            }
            eq("no flag", AgentLabels.parse(null), Map.of());
            eq("empty", AgentLabels.parse(""), Map.of());
            eq("blank", AgentLabels.parse("  ;  ; "), Map.of());

            Map<String, String> m = AgentLabels.parse("omo=Omo; Builder_Bot = Builder - Room 2 ;x=a=b");
            eq("plain", m.get("omo"), "Omo");
            eq("name lower-cased and trimmed, label trimmed", m.get("builder_bot"), "Builder - Room 2");
            eq("label keeps later '='", m.get("x"), "a=b");
            eq("order kept", m.keySet().toString(), "[omo, builder_bot, x]");

            eq("malformed entries skipped",
               AgentLabels.parse("noequals;=Label;omo=;  =x;ok=Fine").toString(), "{ok=Fine}");
            eq("non-username names skipped",
               AgentLabels.parse("bad name=X;way_too_long_username=X;tab\\tname=X;émile=X;good_1=Y").toString(),
               "{good_1=Y}");
            eq("last duplicate wins", AgentLabels.parse("omo=A;OMO=B").get("omo"), "B");

            // No formatting-code injection: section signs and control chars are dropped.
            eq("section sign stripped", AgentLabels.parse("omo=\\u00a7k\\u00a7cRed\\u00a7r Omo").get("omo"), "Red Omo");
            eq("trailing section sign", AgentLabels.parse("omo=Omo\\u00a7").get("omo"), "Omo");
            eq("controls stripped", AgentLabels.parse("omo=O\\u0000m\\u001bo\\u007f\\n").get("omo"), "Omo");
            eq("bidi/format chars stripped", AgentLabels.parse("omo=\\u202eOmo\\u200b").get("omo"), "Omo");
            eq("only junk -> skipped", AgentLabels.parse("omo=\\u00a7\\u0001 ").toString(), "{}");
            eq("spaces collapsed", AgentLabels.parse("omo=Launch    Room\\u00a0 Omo").get("omo"), "Launch Room Omo");

            // Length caps.
            String longLabel = AgentLabels.parse("omo=" + "x".repeat(500)).get("omo");
            eq("label capped", longLabel.length(), AgentLabels.MAX_LABEL);
            String emoji = AgentLabels.parse("omo=" + "\\uD83D\\uDE00".repeat(100)).get("omo");
            eq("cap counts code points, never splits a pair", emoji.codePointCount(0, emoji.length()), AgentLabels.MAX_LABEL);
            eq("no dangling space at the cap",
               AgentLabels.parse("omo=" + "x".repeat(AgentLabels.MAX_LABEL - 1) + " y").get("omo").endsWith(" "), false);
            StringBuilder many = new StringBuilder();
            for (int i = 0; i < 1000; i++) many.append("n").append(i).append("=L;");
            eq("entries capped", AgentLabels.parse(many.toString()).size(), AgentLabels.MAX_ENTRIES);
            eq("huge input does not throw", AgentLabels.parse(";".repeat(1_000_000) + "omo=Omo").size() <= 1, true);

            boolean immutable = false;
            try { AgentLabels.parse("omo=Omo").put("x", "y"); } catch (UnsupportedOperationException e) { immutable = true; }
            eq("result is read-only", immutable, true);
            System.out.println("ok");
        }
    }
""")


class AgentLabelsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        javac, cls.java = jdk_tool("javac"), jdk_tool("java")
        if not javac or not cls.java:
            raise unittest.SkipTest("needs a JDK (JAVA_HOME or javac on PATH)")
        cls.tmp = tempfile.TemporaryDirectory(prefix="omo-agent-labels-")
        cls.addClassCleanup(cls.tmp.cleanup)
        root = Path(cls.tmp.name)
        check = root / "src/space/omo/life/client/Check.java"
        check.parent.mkdir(parents=True)
        check.write_text(CHECK, encoding="utf-8")
        cls.classes = root / "classes"
        subprocess.run([javac, "--release", "21", "-Xlint:all", "-Werror", "-encoding", "UTF-8",
                        "-d", str(cls.classes), str(SOURCE), str(check)], check=True)

    def run_java(self, *args):
        return subprocess.run([self.java, *args, "-cp", str(self.classes), "space.omo.life.client.Check"]
                              + (["live"] if args else []),
                              capture_output=True, text=True, timeout=60)

    def test_parse_rules(self):
        r = self.run_java()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "ok")

    def test_default_has_no_labels(self):
        r = self.run_java("-Dunused=1")
        self.assertEqual((r.returncode, r.stdout), (0, "{}|null|null"), r.stderr)

    def test_flag_is_read_and_matched_case_insensitively(self):
        r = self.run_java("-Domo.agents.labels=omo=My Agent;other=Other")
        self.assertEqual((r.returncode, r.stdout), (0, "{omo=My Agent, other=Other}|My Agent|null"), r.stderr)

    def test_garbage_flag_does_not_fail_class_init(self):
        r = self.run_java("-Domo.agents.labels====;;\u00a7=\u00a7;=")
        self.assertEqual((r.returncode, r.stdout), (0, "{}|null|null"), r.stderr)


if __name__ == "__main__":
    unittest.main()
