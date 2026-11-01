#!/usr/bin/env python3
"""Who may reach host shells, and when worlds open to LAN for agent bodies.

Regression tests for the v1.2.0 hole: AgentLan opened every single-player
world to LAN in offline mode, and in a cheats-on world PlayerList.isOp() is
true for every joiner, so any local process could join as "op" and open a
station shell.

HostAccess and AgentLanFlags have no Minecraft dependencies, so they are
compiled and run on their own. A few source checks pin the wiring
(Stations.allowed uses HostAccess and never isOp, the bind mixin is
registered). Skips without a JDK (JAVA_HOME or javac on PATH).

    python3 tests/test_host_access.py
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
JAVA = REPO / "mod/src/main/java/space/omo/life"
SOURCES = [JAVA / "station/HostAccess.java", JAVA / "client/AgentLanFlags.java"]


def jdk_tool(name):
    home = os.environ.get("JAVA_HOME")
    if home and (Path(home) / "bin" / name).is_file():
        return str(Path(home) / "bin" / name)
    return shutil.which(name)


CHECK = textwrap.dedent("""
    import java.net.InetAddress;
    import java.util.Set;
    import java.util.UUID;
    import space.omo.life.client.AgentLanFlags;
    import space.omo.life.station.HostAccess;

    public class Check {
        static void eq(String what, Object got, Object want) {
            if (!java.util.Objects.equals(got, want)) {
                throw new AssertionError(what + ": got " + got + ", want " + want);
            }
        }

        public static void main(String[] a) throws Exception {
            UUID owner = UUID.fromString("0f0e0d0c-0b0a-0908-0706-050403020100");
            // An offline login under the owner's own name gets the name-derived offline UUID.
            UUID fakeOwner = UUID.nameUUIDFromBytes("OfflinePlayer:Owner".getBytes("UTF-8"));
            UUID agent = UUID.nameUUIDFromBytes("OfflinePlayer:omo".getBytes("UTF-8"));

            eq("owner", HostAccess.allowed(owner, owner, false, true), true);
            eq("offline joiner (cheats for everyone is not an op entry)", HostAccess.allowed(agent, owner, false, true), false);
            eq("offline joiner using the owner's name", HostAccess.allowed(fakeOwner, owner, false, true), false);
            eq("real operator", HostAccess.allowed(agent, owner, true, true), true);
            eq("dedicated server, not op", HostAccess.allowed(agent, null, false, true), false);
            eq("dedicated server, op", HostAccess.allowed(agent, null, true, true), true);
            eq("no player", HostAccess.allowed(null, null, false, true), false);
            // Offline world (agent LAN): an ops.json name only counts from this computer.
            eq("offline op name from another computer", HostAccess.allowed(agent, owner, true, false), false);
            eq("offline owner from another computer is still the owner by UUID", HostAccess.allowed(owner, owner, false, false), true);

            // Agent LAN is opt-in: only an explicit true turns it on.
            eq("unset", AgentLanFlags.enabled((String) null), false);
            eq("empty", AgentLanFlags.enabled(""), false);
            eq("false", AgentLanFlags.enabled("false"), false);
            eq("yes is not true", AgentLanFlags.enabled("yes"), false);
            eq("true", AgentLanFlags.enabled("true"), true);
            eq("TRUE", AgentLanFlags.enabled(" TRUE "), true);
            eq("default property", AgentLanFlags.enabled(), false);

            // The agent listener binds loopback unless remote joins were allowed on purpose.
            eq("loopback bind", AgentLanFlags.bindAddress(false, null).isLoopbackAddress(), true);
            eq("remote keeps vanilla's bind", AgentLanFlags.bindAddress(true, null), null);

            // Agent bodies only exist while the feature is on.
            Set<String> names = AgentLanFlags.names(null);
            eq("default names", names, Set.of("omo", "teacher"));
            eq("custom names", AgentLanFlags.names(" Bob, ,alice "), Set.of("bob", "alice"));
            eq("agent when on", AgentLanFlags.isAgent(true, names, "OMO"), true);
            eq("no agent when off", AgentLanFlags.isAgent(false, names, "omo"), false);
            eq("player", AgentLanFlags.isAgent(true, names, "Steve"), false);
            eq("off by default", AgentLanFlags.isAgent("omo"), false);

            // Neutral name tag (no third-party trademarks).
            eq("tag", AgentLanFlags.TAG_PREFIX, "Agent - ");
            System.out.println("PASS host access and agent LAN flags");
        }
    }
""")


class HostAccessTest(unittest.TestCase):
    def test_rules(self):
        javac, java = jdk_tool("javac"), jdk_tool("java")
        if not javac or not java:
            self.skipTest("no JDK")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "classes"
            check = Path(tmp) / "Check.java"
            check.write_text(CHECK)
            subprocess.run([javac, "-d", str(out), *map(str, SOURCES), str(check)], check=True)
            run = subprocess.run([java, "-cp", str(out), "Check"], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr + run.stdout)
            self.assertIn("PASS", run.stdout)

    def test_wiring(self):
        stations = (JAVA / "station/Stations.java").read_text()
        allowed = stations[stations.index("public static boolean allowed("):]
        allowed = allowed[:allowed.index("\n    }\n")]
        self.assertIn("HostAccess.allowed(", allowed)
        self.assertIn("usesAuthentication()", allowed, "offline worlds trust ops.json only from loopback")
        self.assertIn("isLocal(player)", allowed)
        self.assertNotIn("isOp(", allowed, "isOp is true for everyone in a cheats-on LAN world")
        self.assertNotIn("isSingleplayerOwner(", allowed, "vanilla matches the owner by name only")

        lan = (JAVA / "client/AgentLan.java").read_text()
        self.assertIn("AgentLanFlags.enabled()", lan)
        self.assertNotIn("ChatGPT", lan)
        self.assertNotIn("isSingleplayerOwner(", lan)
        for mixin in ("AgentSkinMixin", "AgentRegistrySyncMixin"):
            src = (JAVA / f"mixin/{mixin}.java").read_text()
            self.assertIn("AgentLanFlags.isAgent", src, mixin)

        mixins = json.loads((REPO / "mod/src/main/resources/omolife.mixins.json").read_text())
        self.assertIn("AgentLanBindMixin", mixins["client"])
        bind = (JAVA / "mixin/AgentLanBindMixin.java").read_text()
        self.assertIn("startTcpServerListener", bind)

    def test_no_third_party_trademark_tags(self):
        hits = [str(p.relative_to(REPO)) for p in (REPO / "mod/src").rglob("*")
                if p.is_file() and p.suffix in (".java", ".json", ".lua") and re.search(r"ChatGPT", p.read_text())]
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
