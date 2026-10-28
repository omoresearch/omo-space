#!/usr/bin/env python3
"""Key translation for web monitors (mod/.../client/PageKeys.java).

PageKeys has no Minecraft or MCEF dependencies, so it is compiled and run on
its own. The values checked are the ones verified against the pinned MCEF
Chromium: X keysyms JCEF needs on Linux, Enter typing '\\r', and the
event.code shim's mapping. Skips without a JDK (JAVA_HOME or javac on PATH).

    python3 tests/test_web_keys.py
"""
import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SOURCE = REPO / "mod/src/main/java/space/omo/life/client/PageKeys.java"


def jdk_tool(name):
    home = os.environ.get("JAVA_HOME")
    if home and (Path(home) / "bin" / name).is_file():
        return str(Path(home) / "bin" / name)
    return shutil.which(name)


CHECK = textwrap.dedent("""
    import space.omo.life.client.PageKeys;

    public class Check {
        static void eq(String what, long got, long want) {
            if (got != want) throw new AssertionError(what + ": got " + got + ", want " + want);
        }

        public static void main(String[] a) {
            // Printable keys keep MCEF's behaviour: the keysym is the ASCII code.
            eq("W keysym", PageKeys.linuxKeysym(87), 'W');
            eq("A keysym", PageKeys.linuxKeysym(65), 'A');
            eq("space keysym", PageKeys.linuxKeysym(32), ' ');
            eq("1 keysym", PageKeys.linuxKeysym(49), '1');
            // Keys MCEF sent as other keys.
            eq("left shift", PageKeys.linuxKeysym(340), 0xFFE1);
            eq("left ctrl", PageKeys.linuxKeysym(341), 0xFFE3);
            eq("left alt", PageKeys.linuxKeysym(342), 0xFFE9);
            eq("F5", PageKeys.linuxKeysym(294), 0xFFC2);
            eq("Home", PageKeys.linuxKeysym(268), 0xFF50);
            eq("keypad 1", PageKeys.linuxKeysym(321), 0xFFB1);
            eq("backtick", PageKeys.linuxKeysym(96), '`');
            eq("quote", PageKeys.linuxKeysym(39), '\\'');
            eq("Enter", PageKeys.linuxKeysym(257), 0xFF0D);
            eq("unknown", PageKeys.linuxKeysym(-1), 0);
            // AWT codes: JCEF decides Enter, Tab, Backspace, Delete, Esc and arrows by these.
            eq("Enter awt", PageKeys.awtKeyCode(257), 10);
            eq("keypad Enter awt", PageKeys.awtKeyCode(335), 10);
            eq("Up awt", PageKeys.awtKeyCode(265), 38);
            eq("Delete awt", PageKeys.awtKeyCode(261), 127);
            eq("F1 awt", PageKeys.awtKeyCode(290), 112);
            eq("W awt", PageKeys.awtKeyCode(87), 87);
            // Enter types '\\r' (GLFW never reports a character for it).
            eq("Enter types", PageKeys.typesReturn(257) ? 1 : 0, 1);
            eq("keypad Enter types", PageKeys.typesReturn(335) ? 1 : 0, 1);
            eq("W types", PageKeys.typesReturn(87) ? 1 : 0, 0);
            // GLFW shift|ctrl|alt|super -> AWT extended masks.
            eq("mods", PageKeys.awtModifiers(1 | 2 | 4 | 8), 64 | 128 | 512 | 256);
            if (!PageKeys.CODE_SHIM.contains("'Key' + String.fromCharCode(k)")) throw new AssertionError("shim");
            System.out.println("ok");
        }
    }
""")


class WebKeys(unittest.TestCase):
    def test_mapping(self):
        javac, java = jdk_tool("javac"), jdk_tool("java")
        if not javac or not java:
            self.skipTest("needs a JDK (JAVA_HOME or javac on PATH)")
        with tempfile.TemporaryDirectory(prefix="omo-web-keys-") as tmp:
            check = Path(tmp) / "Check.java"
            check.write_text(CHECK)
            out = Path(tmp) / "classes"
            subprocess.run([javac, "-d", str(out), str(SOURCE), str(check)], check=True)
            run = subprocess.run([java, "-cp", str(out), "Check"], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(run.stdout.strip(), "ok")


if __name__ == "__main__":
    unittest.main()
