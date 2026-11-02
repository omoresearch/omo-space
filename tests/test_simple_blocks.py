#!/usr/bin/env python3
"""Two blocks by default: the Omo Computer and the Omo Monitor.

- HiddenBlocks.java (no Minecraft classes) decides what leaves the creative
  menu: every computercraft:* item and every omolife item except
  hermes_computer and monitor.
- mod/tools/SimpleBlocksPack.java generates the built-in data pack that
  switches off CC: Tweaked's recipes and recipe advancements (listed from
  the CC:T jar) and the hidden Omo ones, with an always-false Fabric load
  condition, and never overwrites the hand-written CC-free Omo Computer
  recipe. Run against a stand-in jar, and the real pinned CC:T jar when one
  is installed.
- Wiring: nothing is unregistered, the pack is only registered while hiding
  is on, the flag and the lang name.

Skips the Java part without a JDK (JAVA_HOME or java on PATH).

    python3 tests/test_simple_blocks.py
"""
import glob
import json
import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MOD = REPO / "mod"
RES = MOD / "src/main/resources"
JAVA_SRC = MOD / "src/main/java/space/omo/life"
HAND = RES / "resourcepacks/simple_blocks"
DISABLED = {"fabric:load_conditions": [{"condition": "fabric:not", "value": {"condition": "fabric:true"}}]}


def jdk_tool(name):
    home = os.environ.get("JAVA_HOME")
    if home and (Path(home) / "bin" / name).is_file():
        return str(Path(home) / "bin" / name)
    return shutil.which(name)


def real_cct_jar():
    dirs = [os.environ.get("OMO_MODS_DIR", "")]
    home = Path.home()
    for prism in (home / ".local/share/omo/runtime/prism", home / ".local/share/PrismLauncher"):
        dirs += [str(prism / f"instances/{i}/.minecraft/mods") for i in ("OmoTest", "OmoResearch")]
    for d in dirs:
        hits = sorted(glob.glob(os.path.join(d, "cc-tweaked-26.2-fabric-1.120.*.jar"))) if d else []
        if hits:
            return Path(hits[-1])
    return None


class SimpleBlocksTest(unittest.TestCase):
    def generate(self, jar, tmp):
        java = jdk_tool("java")
        if not java:
            self.skipTest("no JDK")
        pack = tmp / "pack"
        shutil.copytree(HAND, pack)                       # what build.sh has copied before it runs
        run = subprocess.run([java, str(MOD / "tools/SimpleBlocksPack.java"), str(jar), str(RES / "data"), str(pack)],
                             capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        return pack

    def check_pack(self, pack, cc_files):
        for rel in cc_files:
            self.assertEqual(json.loads((pack / rel).read_text()), DISABLED, rel)
        for name in ("terminal", "slim_screen", "name_plate", "status_lamp", "linker", "pocket_hermes",
                     "pocket_terminal"):
            self.assertEqual(json.loads((pack / f"data/omolife/recipe/{name}.json").read_text()), DISABLED, name)
        for adv in ("get_pocket", "link_screen", "recipes/redstone/terminal", "recipes/tools/linker"):
            self.assertEqual(json.loads((pack / f"data/omolife/advancement/{adv}.json").read_text()), DISABLED, adv)
        self.assertFalse((pack / "data/omolife/recipe/monitor.json").exists())     # unchanged: no CC parts
        computer = json.loads((pack / "data/omolife/recipe/hermes_computer.json").read_text())
        self.assertEqual(computer["result"]["id"], "omolife:hermes_computer")      # hand-written, kept
        self.assertNotIn("computercraft", json.dumps(computer))                    # craftable without CC:T recipes
        for adv in ("place_station", "open_hermes", "big_screen", "root"):         # the trail stays
            self.assertFalse((pack / f"data/omolife/advancement/{adv}.json").exists(), adv)

    def test_stand_in_jar(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            jar = tmp / "cc-tweaked-26.2-fabric-1.120.9.jar"
            cc_files = ["data/computercraft/recipe/computer_normal.json",
                        "data/computercraft/recipe/turtle_normal/minecraft/diamond_pickaxe.json",
                        "data/computercraft/advancement/recipes/redstone/monitor_normal.json"]
            with zipfile.ZipFile(jar, "w") as z:
                for f in cc_files:
                    z.writestr(f, "{}")
                z.writestr("data/computercraft/loot_table/blocks/computer_normal.json", "{}")   # never touched
                z.writestr("data/computercraft/lua/rom/startup.lua", "")
            pack = self.generate(jar, tmp)
            self.check_pack(pack, cc_files)
            self.assertFalse((pack / "data/computercraft/loot_table").exists())

    def test_real_cct_jar(self):
        jar = real_cct_jar()
        if not jar:
            self.skipTest("no CC: Tweaked jar installed")
        with zipfile.ZipFile(jar) as z:
            cc_files = [n for n in z.namelist() if n.endswith(".json") and
                        n.startswith(("data/computercraft/recipe/", "data/computercraft/advancement/"))]
        self.assertIn("data/computercraft/recipe/computer_advanced.json", cc_files)
        with tempfile.TemporaryDirectory() as tmp:
            self.check_pack(self.generate(jar, Path(tmp)), cc_files)

    def test_hidden_rule(self):
        javac, java = jdk_tool("javac"), jdk_tool("java")
        if not javac or not java:
            self.skipTest("no JDK")
        check = textwrap.dedent("""
            import space.omo.life.HiddenBlocks;
            public class Check {
                public static void main(String[] a) {
                    String[][] hidden = {{"computercraft", "computer_normal"}, {"computercraft", "monitor_advanced"},
                        {"computercraft", "turtle_normal"}, {"omolife", "terminal"}, {"omolife", "slim_screen"},
                        {"omolife", "name_plate"}, {"omolife", "status_lamp"}, {"omolife", "linker"},
                        {"omolife", "pocket_hermes"}, {"omolife", "pocket_terminal"}};
                    String[][] shown = {{"omolife", "hermes_computer"}, {"omolife", "monitor"},
                        {"minecraft", "stone"}, {"mcef", "anything"}};
                    for (String[] h : hidden) if (!HiddenBlocks.hidden(h[0], h[1])) throw new AssertionError(h[1]);
                    for (String[] s : shown) if (HiddenBlocks.hidden(s[0], s[1])) throw new AssertionError(s[1]);
                    System.out.println("PASS hidden rule");
                }
            }
        """)
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "Check.java").write_text(check)
            subprocess.run([javac, "-d", str(tmp / "c"), str(JAVA_SRC / "HiddenBlocks.java"), str(tmp / "Check.java")],
                           check=True)
            run = subprocess.run([java, "-cp", str(tmp / "c"), "Check"], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_wiring(self):
        omo = (JAVA_SRC / "OmoLife.java").read_text()
        self.assertIn("OmoConfig.showAllBlocks()", omo)
        self.assertIn("ResourceLoader.registerBuiltinPack(id(HiddenBlocks.PACK)", omo)
        self.assertIn("PackActivationType.ALWAYS_ENABLED", omo)
        self.assertIn("MODIFY_OUTPUT_ALL", omo)
        # Everything stays registered (old worlds, /give, Omo's own CC:T computers and monitors).
        for name in ("hermes_computer", "terminal", "monitor", "slim_screen", "name_plate", "status_lamp"):
            self.assertIn(f'Registry.register(BuiltInRegistries.BLOCK, id("{name}")', omo)
        self.assertIn("showAllBlocks()", (JAVA_SRC / "link/Links.java").read_text())
        self.assertIn("tools/SimpleBlocksPack.java", (MOD / "build.sh").read_text())
        meta = json.loads((HAND / "pack.mcmeta").read_text())["pack"]
        self.assertIn("min_format", meta)
        lang = json.loads((RES / "assets/omolife/lang/en_us.json").read_text())
        self.assertEqual(lang["block.omolife.hermes_computer"], "Omo Computer")
        self.assertEqual(lang["block.omolife.monitor"], "Omo Monitor")


if __name__ == "__main__":
    unittest.main()
