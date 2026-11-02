#!/usr/bin/env python3
"""Store installs (CurseForge / Modrinth): the mod's side.

- CcHttpRule.java decides like launcher/omo_setup.py cct_rule_state whether
  CC: Tweaked lets stations reach 127.0.0.1:<port> (same fixtures, both run).
- OmoConfig.java reads config/omolife.properties (written by
  `omo_setup.py link`): JVM flags win, bad values fall back, Windows paths
  survive Java's escapes.
- The station setup screen and panel lead to the README install section
  through vanilla's ConfirmLinkScreen, and the mod never downloads anything.

Skips the Java part without a JDK (JAVA_HOME or javac on PATH).

    python3 tests/test_store_install.py
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
JAVA = REPO / "mod/src/main/java/space/omo/life"
SOURCE_TOML = (REPO / "config/computercraft-server.toml").read_text()


def jdk_tool(name):
    home = os.environ.get("JAVA_HOME")
    if home and (Path(home) / "bin" / name).is_file():
        return str(Path(home) / "bin" / name)
    return shutil.which(name)


def without_our_rule(text):
    start = text.index("\t[[http.rules]]\n\t\t#Allow CC:T")
    end = text.index("\t[[http.rules]]", start + 1)
    return text[:start] + text[end:]


# name -> (toml, port); the expected answer comes from omo_setup.cct_rule_state.
FIXTURES = {
    "repo config": (SOURCE_TOML, 8780),
    "repo config, other port": (SOURCE_TOML, 8790),
    "rule without port": (SOURCE_TOML.replace("\t\tport = 8780\n", ""), 8780),
    "cc default (no rule)": (without_our_rule(SOURCE_TOML), 8780),
    "deny first": ('[http]\n[[http.rules]]\nhost = "$private"\naction = "deny"\n'
                   '[[http.rules]]\nhost = "127.0.0.1"\nport = 8780\naction = "allow"\n', 8780),
    "allow with comment": ('[[http.rules]] # ours\n  host = "127.0.0.1" # loopback\n  port = 8790\n'
                           '  action = "allow"\n', 8790),
    "other table ends rules": ('[[http.rules]]\nhost = "example.com"\naction = "allow"\n[execution]\n'
                               'host = "127.0.0.1"\naction = "allow"\n', 8780),
    "empty": ("", 8780),
}


def python_states():
    """cct_rule_state for every fixture, in a child process (importing
    omo_setup here would pin its HOME before test_launcher fakes one)."""
    code = textwrap.dedent("""
        import json, sys
        sys.path.insert(0, sys.argv[1])
        import omo_setup
        fixtures = json.load(sys.stdin)
        print(json.dumps({k: omo_setup.cct_rule_state(t, p) for k, (t, p) in fixtures.items()}))
    """)
    with tempfile.TemporaryDirectory() as tmp:
        env = dict(os.environ, HOME=tmp, OMO_DATA=str(Path(tmp) / "omo"))
        run = subprocess.run([sys.executable, "-c", code, str(REPO / "launcher")], input=json.dumps(FIXTURES),
                             capture_output=True, text=True, env=env, check=True)
    return json.loads(run.stdout)


CHECK = textwrap.dedent("""
    import java.nio.file.Files;
    import java.nio.file.Path;
    import java.util.Properties;
    import space.omo.life.OmoConfig;
    import space.omo.life.client.CcHttpRule;

    public class Check {
        static void eq(String what, Object got, Object want) {
            if (!java.util.Objects.equals(got, want)) {
                throw new AssertionError(what + ": got " + got + ", want " + want);
            }
        }

        public static void main(String[] a) throws Exception {
            // CC:T rule fixtures: one "name<TAB>port<TAB>file" line each, answers printed for Python to compare.
            for (String line : Files.readAllLines(Path.of(a[0]))) {
                String[] f = line.split("\\t");
                String toml = Files.readString(Path.of(f[2]));
                System.out.println(f[0] + "\\t" + CcHttpRule.state(toml, Integer.parseInt(f[1])).name().toLowerCase());
            }
            eq("null file", CcHttpRule.state(null, 8780), CcHttpRule.State.MISSING);
            eq("allows ok", CcHttpRule.allows(CcHttpRule.State.OK), true);
            eq("allows open", CcHttpRule.allows(CcHttpRule.State.OPEN), true);
            eq("allows missing", CcHttpRule.allows(CcHttpRule.State.MISSING), false);

            // config/omolife.properties
            Path dir = Path.of(a[1]);
            eq("missing file", OmoConfig.read(dir.resolve("nope.properties")).isEmpty(), true);
            Path file = dir.resolve("omolife.properties");
            Files.writeString(file, "bridgePort=18790\\nbridgeData=C:\\\\\\\\Users\\\\\\\\me\\\\\\\\OmoResearch\\nshowAllBlocks=TRUE\\n");
            Properties p = OmoConfig.read(file);
            eq("port from file", OmoConfig.port(null, p, 8780), 18790);
            eq("flag wins", OmoConfig.port("9000", p, 8780), 9000);
            eq("blank flag ignored", OmoConfig.port(" ", p, 8780), 18790);
            eq("bad flag falls back", OmoConfig.port("http", new Properties(), 8780), 8780);
            eq("out of range", OmoConfig.port("70000", new Properties(), 8780), 8780);
            eq("windows path", OmoConfig.value(null, p, "bridgeData"), "C:\\\\Users\\\\me\\\\OmoResearch");
            eq("showAll from file", OmoConfig.flag(null, p, "showAllBlocks"), true);
            eq("showAll flag off wins", OmoConfig.flag("false", p, "showAllBlocks"), false);
            eq("showAll default", OmoConfig.flag(null, new Properties(), "showAllBlocks"), false);
            eq("before init", OmoConfig.get().isEmpty(), true);
            OmoConfig.init(dir);
            eq("after init", OmoConfig.get().getProperty("bridgePort"), "18790");
            eq("showAllBlocks()", OmoConfig.showAllBlocks(), true);
            System.out.println("PASS config");
        }
    }
""")


class StoreInstallTest(unittest.TestCase):
    def test_java_rule_and_config_match_the_launcher(self):
        javac, java = jdk_tool("javac"), jdk_tool("java")
        if not javac or not java:
            self.skipTest("no JDK")
        want = python_states()
        self.assertEqual(want["repo config"], "ok")
        self.assertEqual(want["cc default (no rule)"], "missing")
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            lines = []
            for i, (name, (toml, port)) in enumerate(FIXTURES.items()):
                (tmp / f"f{i}.toml").write_text(toml)
                lines.append(f"{name}\t{port}\t{tmp / f'f{i}.toml'}")
            (tmp / "fixtures.txt").write_text("\n".join(lines) + "\n")
            (tmp / "cfg").mkdir()
            (tmp / "Check.java").write_text(CHECK)
            subprocess.run([javac, "-d", str(tmp / "classes"), str(JAVA / "OmoConfig.java"),
                            str(JAVA / "client/CcHttpRule.java"), str(tmp / "Check.java")], check=True)
            run = subprocess.run([java, "-cp", str(tmp / "classes"), "Check", str(tmp / "fixtures.txt"),
                                  str(tmp / "cfg")], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr + run.stdout)
            got = dict(line.split("\t") for line in run.stdout.splitlines() if "\t" in line)
            self.assertEqual(got, want)
            self.assertIn("PASS config", run.stdout)

    def test_setup_guide_wiring(self):
        link = (JAVA / "station/BridgeLink.java").read_text()
        url = re.search(r'SETUP_GUIDE = "([^"]+)"', link).group(1)
        self.assertEqual(url, "https://github.com/omoresearch/omo-space#install")
        self.assertRegex((REPO / "README.md").read_text(), r"(?m)^## Install$")   # the anchor exists
        help_src = (JAVA / "client/BridgeHelp.java").read_text()
        self.assertIn("ConfirmLinkScreen.confirmLinkNow(", help_src)
        self.assertIn("computercraft-server.toml", help_src)
        self.assertIn("BridgeHelp.openGuide(this)", (JAVA / "client/StationSetupScreen.java").read_text())
        self.assertIn("BridgeHelp.problem()", (JAVA / "client/StationPanel.java").read_text())
        self.assertIn("OmoConfig.port(", link)

    def test_mod_downloads_nothing(self):
        # Only loopback HTTP: no URL other than the docs page and 127.0.0.1 is ever requested by the mod.
        for p in (REPO / "mod/src/main/java").rglob("*.java"):
            text = p.read_text()
            self.assertNotRegex(text, r"\bopenStream\(|Files\.copy\(\s*\w+\.openStream|HttpResponse\.BodyHandlers\.ofFile",
                                str(p))


if __name__ == "__main__":
    unittest.main()
