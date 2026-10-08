#!/usr/bin/env python3
"""macOS and Windows support, tested on any host: no network, no Mac, no
Windows. Each check runs omo_setup.py in a fresh process with OMO_PLATFORM
forcing the platform, so module-level paths (data dir, Prism, Java) are
computed exactly as they would be there.

What a real Mac or Windows machine is still needed for (stated, not faked):
launchd actually starting the agents, WSL actually forwarding the port,
Prism/Java actually running. See docs/PLATFORMS.md.

Covers: platform detection; the data dir agrees with the mod's
BridgeLink.dataDir(); Mojang library/argument rules per OS (real 26.2
version file shape); every pinned macOS/Windows download is in the manifest
with a hash; Java and Prism archive layouts (Temurin's Contents/Home bundle,
javaw.exe; Prism's .app with symlinks, prismlauncher.exe) unpacked and
re-verified, including a damaged file; archive path traversal and escaping
symlinks refused; the launchd plists; WSL path mapping and its setup errors;
Windows play writing the argfile and deleting it; game-process detection
from ps/PowerShell output; the bridge's ps fallback and clipboard choices.

    python3 tests/test_platforms.py
"""
import io
import json
import os
import plistlib
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import unittest
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LAUNCHER = REPO / "launcher"
ROOT = Path(tempfile.mkdtemp(prefix="omo-platform-test-"))
MANIFEST = json.loads((LAUNCHER / "manifest.json").read_text())


def run_as(platform, code, env=None, home=None, check=True):
    """Run `code` with omo_setup imported as S on `platform`, in a fresh HOME.
    Returns parsed JSON printed by the code (last line)."""
    home = Path(home or tempfile.mkdtemp(dir=ROOT))
    full = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(home), "OMO_PLATFORM": platform,
            "OMO_NO_SERVICES": "1", "PYTHONDONTWRITEBYTECODE": "1", "LOCALAPPDATA": str(home / "AppData/Local"),
            "APPDATA": str(home / "AppData/Roaming"), **(env or {})}
    prog = "import json, sys\nsys.path.insert(0, %r)\nimport omo_setup as S\nS.say = lambda m: None\n" % str(LAUNCHER)
    p = subprocess.run([sys.executable, "-c", prog + textwrap.dedent(code)], env=full,
                       capture_output=True, text=True, timeout=60)
    if check and p.returncode != 0:
        raise AssertionError(f"{platform}: exit {p.returncode}\n{p.stdout}\n{p.stderr}")
    if not check:
        return p
    return json.loads(p.stdout.strip().splitlines()[-1])


def make_tgz(path, files):
    with tarfile.open(path, "w:gz") as tf:
        for name, (data, mode) in files.items():
            info = tarfile.TarInfo(name)
            if isinstance(data, tuple):           # ("symlink", target)
                info.type, info.linkname = tarfile.SYMTYPE, data[1]
                tf.addfile(info)
            else:
                info.size, info.mode = len(data), mode
                tf.addfile(info, io.BytesIO(data))


def make_zip(path, files):
    with zipfile.ZipFile(path, "w") as zf:
        for name, (data, mode) in files.items():
            info = zipfile.ZipInfo(name)
            if isinstance(data, tuple):
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                zf.writestr(info, data[1])
            else:
                info.external_attr = (stat.S_IFREG | mode) << 16
                zf.writestr(info, data)


class Detection(unittest.TestCase):
    def test_this_host_and_forced(self):
        import platform as pf
        sys.path.insert(0, str(LAUNCHER))
        env = dict(os.environ)
        os.environ.pop("OMO_PLATFORM", None)
        try:
            import omo_setup as S
            here = S.detect_platform()
        finally:
            os.environ.clear()
            os.environ.update(env)
        if sys.platform.startswith("linux") and pf.machine() in ("x86_64", "AMD64"):
            self.assertEqual(here, "linux-x86_64")
        for plat in ("macos-arm64", "windows-x86_64", "linux-x86_64"):
            self.assertEqual(run_as(plat, "print(json.dumps([S.ARCH, S.OS]))"), [plat, plat.split("-")[0]])

    def test_unsupported_platform_refuses_setup_with_a_reason(self):
        p = run_as("linux-arm64", "S.setup()", check=False)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("does not support linux-arm64", p.stderr)


class DataDirs(unittest.TestCase):
    """The launcher writes the token where the mod reads it."""

    def test_paths_per_platform(self):
        code = "print(json.dumps([str(S.OMO), str(S.USER_PRISM), str(S.OWN_PRISM), str(S.java_exe(S.JAVA_DIR))]))"
        home = ROOT / "h-mac"
        omo, prism, own, java = run_as("macos-arm64", code, home=home)
        self.assertEqual(omo, str(home / "Library/Application Support/OmoResearch"))
        self.assertEqual(prism, str(home / "Library/Application Support/PrismLauncher"))
        self.assertTrue(own.endswith("bin/prism/Prism Launcher.app/Contents/MacOS/prismlauncher"))
        self.assertTrue(java.endswith("temurin-25/Contents/Home/bin/java"))
        home = ROOT / "h-win"
        omo, prism, own, java = run_as("windows-x86_64", code, home=home)
        self.assertEqual(omo, str(home / "AppData/Local/OmoResearch"))
        self.assertEqual(prism, str(home / "AppData/Roaming/PrismLauncher"))
        self.assertTrue(own.endswith("bin/prism/prismlauncher.exe"))
        self.assertTrue(java.endswith("temurin-25/bin/javaw.exe"))
        home = ROOT / "h-lin"
        omo, prism, own, java = run_as("linux-x86_64", code, home=home)
        self.assertEqual(omo, str(home / ".local/share/omo"))            # unchanged on Linux
        self.assertTrue(own.endswith("bin/PrismLauncher.AppImage"))

    def test_mod_reads_the_same_dirs(self):
        """BridgeLink.dataDir() must name the same folders as default_data_dir()."""
        src = (REPO / "mod/src/main/java/space/omo/life/station/BridgeLink.java").read_text()
        self.assertIn('Path.of(home, "Library", "Application Support", "OmoResearch")', src)
        self.assertIn('System.getenv("LOCALAPPDATA")', src)
        self.assertIn('.resolve("OmoResearch")', src)
        self.assertIn('Path.of(home, ".local", "share", "omo")', src)
        java = shutil.which("java") or str(Path.home() / ".local/share/Java/temurin-25/bin/java")
        if not Path(java).exists():
            self.skipTest("no java to run the mod's dataDir()")
        # Run the real method body (copied from the source, not retyped) under each os.name.
        body = src[src.index("public static Path dataDir()"):]
        body = body[:body.index("\n    }\n") + 6]
        prog = ROOT / "D.java"
        prog.write_text("import java.nio.file.Path;\npublic class D {\n" + body.replace("public static", "static")
                        + "\n public static void main(String[] a) { System.out.println(dataDir()); }\n}\n")
        for osname, want in (("Mac OS X", "Library/Application Support/OmoResearch"),
                             ("Windows 11", "AppData/Local/OmoResearch"), ("Linux", ".local/share/omo")):
            out = subprocess.run([java, f"-Dos.name={osname}", f"-Duser.home={ROOT}/jh", str(prog)],
                                 capture_output=True, text=True, timeout=60,
                                 env={**os.environ, "LOCALAPPDATA": f"{ROOT}/jh/AppData/Local"})
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(out.stdout.strip(), f"{ROOT}/jh/{want}", osname)


class MojangRules(unittest.TestCase):
    """The real 26.2 version file's rule shapes."""
    LIBS = [
        {"name": "lwjgl-natives-linux", "rules": [{"action": "allow", "os": {"name": "linux"}}]},
        {"name": "lwjgl-natives-macos", "rules": [{"action": "allow", "os": {"name": "osx"}}]},
        {"name": "lwjgl-natives-windows", "rules": [{"action": "allow", "os": {"name": "windows"}}]},
        {"name": "java-objc-bridge", "rules": [{"action": "allow", "os": {"name": "osx"}}]},
        {"name": "plain"},
    ]
    JVM = [{"rules": [{"action": "allow", "os": {"name": "osx"}}], "value": ["-XstartOnFirstThread"]},
           {"rules": [{"action": "allow", "os": {"name": "windows"}}], "value": "-XX:HeapDumpPath=x"},
           {"rules": [{"action": "allow", "os": {"arch": "x86"}}], "value": "-Xss1M"}]

    def picks(self, plat):
        code = f"""
        libs = {self.LIBS!r}
        jvm = {self.JVM!r}
        print(json.dumps([[l["name"] for l in libs if S.allowed(l.get("rules"))],
                          [j["value"] for j in jvm if S.allowed(j["rules"])]]))"""
        return run_as(plat, code)

    def test_each_os_gets_only_its_natives_and_flags(self):
        self.assertEqual(self.picks("linux-x86_64"), [["lwjgl-natives-linux", "plain"], []])
        self.assertEqual(self.picks("macos-arm64"),
                         [["lwjgl-natives-macos", "java-objc-bridge", "plain"], [["-XstartOnFirstThread"]]])
        self.assertEqual(self.picks("windows-x86_64"),
                         [["lwjgl-natives-windows", "plain"], ["-XX:HeapDumpPath=x"]])


class ManifestPins(unittest.TestCase):
    def test_every_supported_platform_is_pinned(self):
        for plat in ("macos-arm64", "macos-x86_64", "windows-x86_64", "linux-x86_64"):
            for part in ("java", "prism"):
                spec = MANIFEST[part][plat]
                self.assertRegex(spec["sha256"], r"^[0-9a-f]{64}$", f"{part} {plat}")
                self.assertGreater(spec["size"], 10_000_000)
                self.assertTrue(spec["url"].startswith("https://github.com/"), spec["url"])
        self.assertIn("aarch64_mac", MANIFEST["java"]["macos-arm64"]["url"])
        self.assertIn("x64_mac", MANIFEST["java"]["macos-x86_64"]["url"])
        self.assertTrue(MANIFEST["java"]["windows-x86_64"]["url"].endswith("_windows_hotspot_25.0.4.1_1.zip"))
        self.assertIn("MinGW-w64", MANIFEST["prism"]["windows-x86_64"]["url"])

    def test_real_archives_when_cached(self):
        """With OMO_REAL_ARCHIVES=<dir> holding the pinned downloads, check
        the real files against the manifest and their real layout."""
        d = os.environ.get("OMO_REAL_ARCHIVES")
        if not d:
            self.skipTest("set OMO_REAL_ARCHIVES to a dir with the pinned archives")
        sys.path.insert(0, str(LAUNCHER))
        import omo_setup as S
        for plat, part, inner in (("macos-arm64", "java", "Contents/Home/bin/java"),
                                  ("macos-x86_64", "java", "Contents/Home/bin/java"),
                                  ("windows-x86_64", "java", "bin/javaw.exe"),
                                  ("macos-arm64", "prism", "Prism Launcher.app/Contents/MacOS/prismlauncher"),
                                  ("windows-x86_64", "prism", "prismlauncher.exe")):
            spec = MANIFEST[part][plat]
            path = Path(d) / spec["url"].rsplit("/", 1)[1].replace("%2B", "+")
            self.assertTrue(S.verify(path, spec), f"{path} does not match the manifest")
            strip = 1 if part == "java" else 0
            names = ["/".join(Path(n).parts[strip:]) for n, *_ in S.archive_files(path)]
            self.assertIn(inner, names, f"{plat} {part}")


class Archives(unittest.TestCase):
    """Install Java and Prism from archives shaped exactly like the real ones."""

    def install(self, plat, java_archive, prism_archive):
        home = Path(tempfile.mkdtemp(dir=ROOT))
        java_name = "jre.zip" if java_archive.suffix == ".zip" else "jre.tar.gz"
        code = f"""
        import hashlib, shutil
        from pathlib import Path
        def spec(p):
            return {{"url": "file://" + str(p), "sha256": hashlib.sha256(Path(p).read_bytes()).hexdigest(),
                     "size": Path(p).stat().st_size}}
        S.MANIFEST["java"][S.ARCH] = spec({str(java_archive)!r})
        S.MANIFEST["java"][S.ARCH]["url"] = "https://x/{java_name}"
        S.MANIFEST["prism"][S.ARCH] = spec({str(prism_archive)!r})
        S.MANIFEST["prism"][S.ARCH]["url"] = "https://x/prism.zip"
        # Seed the download cache so no network is touched.
        S.DL.mkdir(parents=True, exist_ok=True)
        shutil.copy({str(java_archive)!r}, S.DL / S.archive_name(S.MANIFEST["java"][S.ARCH], "temurin25-jre"))
        shutil.copy({str(prism_archive)!r}, S.DL / S.archive_name(S.MANIFEST["prism"][S.ARCH], "prism-11.1.0"))
        S.runs = lambda *a: False           # never execute foreign binaries here
        java = S.ensure_java()
        prism = S.ensure_prism()
        out = {{"java": str(java), "java_ok": java.is_file(), "prism": str(prism), "prism_ok": prism.is_file(),
               "matches": S.java_tree_matches(S.DL / S.archive_name(S.MANIFEST["java"][S.ARCH], "temurin25-jre"),
                                              S.JAVA_DIR)}}
        link = S.PRISM_HOME / "Prism Launcher.app/Contents/Frameworks/Qt.framework/Versions/Current"
        out["link"] = __import__("os").readlink(link) if link.is_symlink() else None
        out["exec"] = bool(prism.stat().st_mode & 0o111)
        out["macosx_skipped"] = not (S.PRISM_HOME / "__MACOSX").exists()
        # Damage one Java file: the tree no longer matches and repair reinstalls it.
        bad = next(p for p in S.JAVA_DIR.rglob("*") if p.is_file() and p.name == "modules")
        bad.write_bytes(b"x")
        out["damaged_detected"] = not S.java_tree_matches(S.DL / S.archive_name(S.MANIFEST["java"][S.ARCH], "temurin25-jre"), S.JAVA_DIR)
        S.ensure_java(verify_all=True)
        out["repaired"] = S.java_tree_matches(S.DL / S.archive_name(S.MANIFEST["java"][S.ARCH], "temurin25-jre"), S.JAVA_DIR)
        # Second ensure_prism with the same pin does not unpack again.
        stamp = (S.PRISM_HOME / ".omo-source").stat().st_mtime_ns
        S.ensure_prism()
        out["prism_kept"] = (S.PRISM_HOME / ".omo-source").stat().st_mtime_ns == stamp
        print(json.dumps(out))"""
        return run_as(plat, code, home=home)

    def test_macos_bundle_layouts(self):
        jre = ROOT / "mac-jre.tar.gz"
        make_tgz(jre, {"jdk-25.0.4.1+1-jre/Contents/Home/bin/java": (b"#!/bin/sh\n", 0o755),
                       "jdk-25.0.4.1+1-jre/Contents/Home/lib/modules": (os.urandom(3000), 0o644),
                       "jdk-25.0.4.1+1-jre/Contents/Info.plist": (b"<plist/>", 0o644)})
        prism = ROOT / "mac-prism.zip"
        make_zip(prism, {"Prism Launcher.app/Contents/MacOS/prismlauncher": (b"\xcf\xfa\xed\xfe", 0o755),
                         "Prism Launcher.app/Contents/Frameworks/Qt.framework/Versions/A/Qt": (b"qt", 0o755),
                         "Prism Launcher.app/Contents/Frameworks/Qt.framework/Versions/Current": (("link", "A"), 0),
                         "__MACOSX/Prism Launcher.app/._Contents": (b"junk", 0o644)})
        out = self.install("macos-arm64", jre, prism)
        self.assertTrue(out["java"].endswith("Contents/Home/bin/java") and out["java_ok"], out)
        self.assertTrue(out["prism_ok"] and out["exec"], out)
        self.assertEqual(out["link"], "A")                 # framework symlinks survive
        self.assertTrue(out["macosx_skipped"] and out["matches"] and out["damaged_detected"] and out["repaired"], out)
        self.assertTrue(out["prism_kept"])

    def test_windows_layouts(self):
        jre = ROOT / "win-jre.zip"
        make_zip(jre, {"jdk-25.0.4.1+1-jre/bin/javaw.exe": (b"MZ", 0o644),
                       "jdk-25.0.4.1+1-jre/bin/java.exe": (b"MZ", 0o644),
                       "jdk-25.0.4.1+1-jre/lib/modules": (os.urandom(3000), 0o644)})
        prism = ROOT / "win-prism.zip"
        make_zip(prism, {"prismlauncher.exe": (b"MZ", 0o644), "Qt6Core.dll": (b"MZ", 0o644)})
        out = self.install("windows-x86_64", jre, prism)
        self.assertTrue(out["java"].endswith("temurin-25/bin/javaw.exe") and out["java_ok"], out)
        self.assertTrue(out["prism"].endswith("bin/prism/prismlauncher.exe") and out["prism_ok"], out)
        self.assertTrue(out["matches"] and out["damaged_detected"] and out["repaired"], out)

    def test_hostile_archives_are_refused(self):
        sys.path.insert(0, str(LAUNCHER))
        import omo_setup as S
        for name, files in (("dotdot.zip", {"a/../../evil": (b"x", 0o644)}),
                            ("abs.tar.gz", {"/tmp/omo-evil": (b"x", 0o644)}),
                            ("link.zip", {"app/escape": (("link", "../../../etc"), 0)})):
            arc = ROOT / name
            (make_zip if name.endswith(".zip") else make_tgz)(arc, files)
            dest = ROOT / ("x-" + name)
            with self.assertRaises(SystemExit, msg=name):
                S.extract(arc, dest)
            self.assertFalse((ROOT / "evil").exists() or Path("/tmp/omo-evil").exists())


class MacServices(unittest.TestCase):
    def test_launch_agents(self):
        code = """
        import plistlib
        from pathlib import Path
        S.find_hermes = lambda: Path("/opt/homebrew/bin/hermes")
        S.pick_python = lambda: "/opt/homebrew/bin/python3"
        agents = S.launch_agents(Path("/opt/homebrew/bin/tmux"))
        print(json.dumps({k: plistlib.loads(v) for k, v in agents.items()}, default=str))"""
        agents = run_as("macos-arm64", code)
        tmux, bridge = agents["omo-tmux.service"], agents["omo-bridge.service"]
        self.assertEqual(tmux["Label"], "space.omo.research.tmux")
        self.assertEqual(tmux["ProgramArguments"][:4], ["/opt/homebrew/bin/tmux", "-D", "-L", "omo"])
        self.assertTrue(tmux["ProgramArguments"][-1].endswith("OmoResearch/tmux.conf"))
        self.assertEqual(tmux["KeepAlive"], {"SuccessfulExit": False})
        self.assertTrue(tmux["RunAtLoad"])
        self.assertEqual(bridge["ProgramArguments"][0], "/opt/homebrew/bin/python3")
        self.assertTrue(bridge["ProgramArguments"][1].endswith("bridge/omobridge.py"))
        env = bridge["EnvironmentVariables"]
        self.assertEqual((env["OMO_TMUX"], env["OMO_HERMES_BIN"], env["OMO_OPEN"]),
                         ("/opt/homebrew/bin/tmux", "/opt/homebrew/bin/hermes", "open"))
        self.assertTrue(env["OMO_DATA"].endswith("Library/Application Support/OmoResearch"))
        self.assertIn("/opt/homebrew/bin", env["PATH"])

    def test_launchctl_flow_never_restarts_a_changed_tmux(self):
        """Fake launchctl: first run bootstraps both; a changed tmux.conf on a
        loaded server is NOT reloaded (that ends shells); a changed bridge is."""
        bin_ = ROOT / "fakebin"
        bin_.mkdir(exist_ok=True)
        log = ROOT / "launchctl.log"
        (bin_ / "launchctl").write_text(f"""#!/bin/sh
echo "$*" >> {log}
case "$1" in print) [ -f {ROOT}/loaded-"$(basename "$2")" ] && exit 0; exit 113 ;;
bootstrap) touch {ROOT}/loaded-"$(basename "$3" .plist)" ;; esac
exit 0
""")
        (bin_ / "launchctl").chmod(0o755)
        code = """
        from pathlib import Path
        S.wait_ready = lambda t: None
        S.find_hermes = lambda: None
        tmux = Path(sys.argv[0]).parent  # unused
        S.ensure_services_macos(Path("/opt/homebrew/bin/tmux"))
        S.TMUX_CONF.write_text("changed by hand")
        S.ensure_services_macos(Path("/opt/homebrew/bin/tmux"))
        # A changed tmux AGENT (tmux moved: brew reinstall) must not be reloaded
        # either; a changed bridge agent must be.
        S.find_hermes = lambda: Path("/opt/homebrew/bin/hermes")
        S.ensure_services_macos(Path("/usr/local/bin/tmux"))
        print(json.dumps(True))"""
        home = Path(tempfile.mkdtemp(dir=ROOT))
        run_as("macos-arm64", code, home=home, env={"PATH": f"{bin_}:{os.environ['PATH']}"})
        calls = log.read_text().splitlines()
        boots = [c for c in calls if c.startswith("bootstrap")]
        self.assertEqual(len([b for b in boots if "tmux" in b]), 1, calls)    # loaded once, never reloaded
        self.assertEqual(len([b for b in boots if "bridge" in b]), 2, calls)  # reloaded after its agent changed
        self.assertFalse([c for c in calls if c.startswith("bootout") and "tmux" in c], calls)
        self.assertTrue([c for c in calls if c.startswith("bootout") and "bridge" in c], calls)
        self.assertTrue((home / "Library/LaunchAgents/space.omo.research.tmux.plist").exists())


class Windows(unittest.TestCase):
    def test_wsl_paths(self):
        out = run_as("windows-x86_64", r"""
        print(json.dumps([S.wsl_path(r"C:\Users\Kim\AppData\Local\OmoResearch\app\launcher\omo_setup.py"),
                          S.wsl_path("d:/games/omo")]))""")
        self.assertEqual(out, ["/mnt/c/Users/Kim/AppData/Local/OmoResearch/app/launcher/omo_setup.py",
                               "/mnt/d/games/omo"])
        p = run_as("windows-x86_64", r"S.wsl_path(r'\\server\share\x')", check=False)
        self.assertIn("only drive-letter paths", p.stderr)

    def fake_wsl(self, script):
        bin_ = Path(tempfile.mkdtemp(dir=ROOT))
        (bin_ / "wsl").write_text("#!/bin/sh\n" + script)
        (bin_ / "wsl").chmod(0o755)
        return {"PATH": f"{bin_}:/usr/bin:/bin"}

    def services(self, script, extra=""):
        code = "S.HERE = __import__('pathlib').PureWindowsPath(r'C:\\Omo\\app\\launcher')\n" + extra + \
               "S.ensure_services_windows()\nprint(json.dumps((S.OMO / 'token').read_text()))"
        env = {**self.fake_wsl(script), "OMO_NO_SERVICES": ""}
        return run_as("windows-x86_64", code, env=env, check=False)

    def test_wsl_missing_or_unready_gives_the_fix(self):
        p = run_as("windows-x86_64", "S.ensure_services_windows()", env={"PATH": "/nonexistent"}, check=False)
        self.assertIn("wsl --install", p.stderr)
        p = self.services('echo "no distro" >&2; exit 1')
        self.assertIn("no Linux distribution yet", p.stderr)
        p = self.services('echo Linux; echo /usr/bin/python3; exit 0')
        self.assertIn("systemd=true", p.stderr)

    def test_wsl_services_run_setup_inside_and_copy_the_token(self):
        log = ROOT / "wsl.log"
        script = f"""echo "$*" >> {log}
case "$*" in
  *"uname -s"*) echo Linux; echo /usr/bin/python3; echo /usr/bin/systemctl ;;
  *omo_setup.py*) echo "services: bridge and session server ready" ;;
  *token*) echo 0123456789abcdef0123456789abcdef ;;
esac
exit 0
"""
        p = self.services(script, extra="S.wait_ready = lambda t: None\n")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout.strip().splitlines()[-1]), "0123456789abcdef0123456789abcdef")
        calls = log.read_text()
        self.assertIn("-e env OMO_PLATFORM=linux-x86_64 OMO_BRIDGE_PORT=8780 python3 "
                      "/mnt/c/Omo/app/launcher/omo_setup.py services", calls)
        self.assertIn("enable-linger", calls)

    def test_wsl_bad_token_is_refused(self):
        script = """case "$*" in *"uname -s"*) echo Linux; echo /usr/bin/python3; echo /usr/bin/systemctl ;;
  *token*) echo 'not-a-token' ;; esac; exit 0
"""
        p = self.services(script, extra="S.wait_ready = lambda t: None\n")
        self.assertIn("no token yet", p.stderr)

    def test_play_argfile_is_written_then_deleted(self):
        """The token goes through a file Java reads (no /dev/fd on Windows),
        never the command line, and the file is gone after launch."""
        rec = ROOT / "win-play.json"
        fake = ROOT / "fakejava.py"
        fake.write_text(f"""import json, shlex, sys, time
args = sys.argv[1:]
f = [a for a in args if a.startswith("@")][0][1:]
json.dump({{"argv": sys.argv, "file": open(f).read()}}, open({str(rec)!r}, "w"))
time.sleep(0.5)
""")
        code = f"""
        import subprocess as sp
        S.WIN = True                                     # take the Windows branch on this host
        S.ARGFILE_GRACE = 0.2
        S.GAME.mkdir(parents=True, exist_ok=True)
        game = ["--accessToken", "MC-SECRET", "--username", "Kim"]
        f = S.write_argfile(game)
        try:
            S.launch_windows([sys.executable, {str(fake)!r}, f"@{{f}}"], dict(__import__('os').environ), f)
        except SystemExit as e:
            code = e.code
        print(json.dumps({{"exists": f.exists(), "code": code}}))"""
        out = run_as("windows-x86_64", code)
        self.assertEqual(out, {"exists": False, "code": 0})
        r = json.loads(rec.read_text())
        self.assertNotIn("MC-SECRET", " ".join(r["argv"]))
        self.assertIn('"MC-SECRET"', r["file"])

    def test_play_on_windows_uses_the_argfile_path(self):
        """play() itself, Omo sign-in mode, with launch_windows captured."""
        code = """
        import time
        from pathlib import Path
        import contextlib
        S.WIN = True
        S.setup_lock = contextlib.nullcontext      # msvcrt exists only on Windows
        S.installed = lambda: True
        S.instance_pids = lambda root=None: []
        S.bridge_problem = lambda *a: None
        S.migrate = lambda: None
        S.client_id = lambda: "cid"
        S.omo_auth.fresh = lambda cid, path: {"profile": {"name": "Kim", "id": "1"}, "client_id": "cid",
                                              "minecraft": {"access_token": "MC-SECRET"}}
        S.version_json = lambda: {"id": "26.2", "arguments": {"jvm": ["-cp", "${classpath}"],
                                  "game": ["--accessToken", "${auth_access_token}"]},
                                  "assetIndex": {"id": "1"}, "downloads": {"client": {"url": "u", "sha1": "s", "size": 1}},
                                  "libraries": []}
        S.java_path = lambda: Path("C:/java/bin/javaw.exe")
        S.GAME.mkdir(parents=True, exist_ok=True)
        seen = {}
        def capture(cmd, env, argfile):
            seen.update(cmd=cmd, argfile=str(argfile), content=Path(argfile).read_text())
            raise SystemExit(0)
        S.launch_windows = capture
        try:
            S.play()
        except SystemExit:
            pass
        print(json.dumps(seen))"""
        seen = run_as("windows-x86_64", code)
        self.assertTrue(seen["cmd"][-1].startswith("@") and seen["cmd"][-1][1:] == seen["argfile"])
        self.assertNotIn("MC-SECRET", " ".join(seen["cmd"]))
        self.assertIn("MC-SECRET", seen["content"])
        self.assertTrue(any(a.startswith("-Domo.data=") and a.endswith("OmoResearch") for a in seen["cmd"]))


class GameDetection(unittest.TestCase):
    def detect(self, plat, rows, cwds=None, root_expr="S.GAME"):
        code = f"""
        S.java_processes = lambda: {rows!r}
        S.mac_cwds = lambda pids: {cwds or {}!r}
        print(json.dumps(S.instance_pids({root_expr})))"""
        return run_as(plat, code)

    def test_windows_command_lines(self):
        game = str(ROOT / "h-detect" / "AppData/Local/OmoResearch/runtime/game")
        win_game = game.replace("/", "\\")
        rows = [(11, f'"C:\\j\\bin\\javaw.exe" -Domo.game={win_game} -cp x KnotClient'),
                (12, 'C:\\other\\javaw.exe -jar unrelated.jar'),
                (13, f'"C:\\j\\bin\\javaw.exe" -Domo.game={win_game}-backup -cp x')]  # a prefix is not a match
        out = run_as("windows-x86_64", f"""
        S.java_processes = lambda: {rows!r}
        print(json.dumps(S.instance_pids()))""", home=ROOT / "h-detect")
        self.assertEqual(out, [11])

    def test_macos_cwd_and_prism_instance(self):
        home = ROOT / "h-mdetect"
        omo = home / "Library/Application Support/OmoResearch/runtime"
        rows = [(21, "/x/Contents/Home/bin/java -cp a b"), (22, "/x/Contents/Home/bin/java -cp a b"),
                (23, f"/x/bin/java -Djava.library.path={omo}/prism/instances/OmoResearch/natives -cp z")]
        cwds = {21: str(omo / "game"), 22: "/Users/kim/elsewhere"}
        out = run_as("macos-arm64", f"""
        S.java_processes = lambda: {rows!r}
        S.mac_cwds = lambda pids: {cwds!r}
        print(json.dumps(S.instance_pids()))""", home=home)
        self.assertEqual(out, [21, 23])

    def test_java_processes_parses_ps(self):
        bin_ = Path(tempfile.mkdtemp(dir=ROOT))
        (bin_ / "ps").write_text("#!/bin/sh\ncat <<'E'\n  101 /Library/Java/Home/bin/java -Xmx2g -cp a Main\n"
                                 "  102 /bin/zsh -l\n  103 /opt/x/Contents/Home/bin/java -Domo.game=/g\nE\n")
        (bin_ / "ps").chmod(0o755)
        out = run_as("macos-arm64", "print(json.dumps(S.java_processes()))", env={"PATH": f"{bin_}:/usr/bin:/bin"})
        self.assertEqual([r[0] for r in out], [101, 103])


class Bridge(unittest.TestCase):
    """The bridge's host-desktop pieces on macOS and WSL, imported for real."""

    def bridge_eval(self, code, env):
        prog = ("import sys, json; sys.path.insert(0, %r); sys.path.insert(0, %r)\n" %
                (str(REPO / "bridge/vendor"), str(REPO / "bridge")))
        tmp = Path(tempfile.mkdtemp(dir=ROOT))
        full = {"PATH": os.environ["PATH"], "HOME": str(tmp), "OMO_DATA": str(tmp / "d"),
                "OMO_TMUX": shutil.which("tmux") or "/bin/true", **env}
        p = subprocess.run([sys.executable, "-c", prog + "import omobridge as B\n" + textwrap.dedent(code)],
                           env=full, capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.loads(p.stdout.strip().splitlines()[-1])

    def test_ps_fallback_finds_children_without_proc(self):
        child = subprocess.Popen(["sleep", "30"])
        try:
            out = self.bridge_eval(f"""
            B.HAVE_PROC = False
            print(json.dumps([B._children({os.getpid()}), B._comm({child.pid}), B._cmdline({child.pid})]))""", {})
            kids, comm, cmd = out
            self.assertIn(child.pid, kids)
            self.assertEqual(comm, "sleep")
            self.assertEqual(cmd, "sleep 30")
            # And the /proc path agrees on the same process.
            same = self.bridge_eval(f"print(json.dumps(B._children({os.getpid()})))", {})
            self.assertIn(child.pid, same)
        finally:
            child.kill()

    def test_clipboard_and_opener_choices(self):
        code = "print(json.dumps([B.COPY_CMDS[0], B.PASTE_CMDS[0][0], B.URL_OPENER]))"
        wsl = self.bridge_eval(code, {"WSL_DISTRO_NAME": "Ubuntu"})
        self.assertEqual(wsl[0], ["clip.exe"])
        self.assertEqual(wsl[1], "powershell.exe")
        self.assertIn(Path(wsl[2]).name, ("explorer.exe", "wslview"))
        lin = self.bridge_eval(code, {})
        self.assertEqual(lin, [["xclip", "-selection", "clipboard"], "xclip", "xdg-open"])
        pinned = self.bridge_eval(code, {"WSL_DISTRO_NAME": "Ubuntu", "OMO_DISPLAY": ":98"})
        self.assertEqual(pinned[0], ["xclip", "-selection", "clipboard"])   # tests keep their private X

    def test_macos_clipboard_branch(self):
        out = self.bridge_eval("""
        import importlib
        sys.platform = "darwin"
        importlib.reload(B)
        print(json.dumps([B.COPY_CMDS, B.PASTE_CMDS, B.URL_OPENER, B.IS_MAC]))""", {})
        self.assertEqual(out, [[["pbcopy"]], [["pbpaste"]], "open", True])

    def test_wsl_clipboard_copy_sends_utf16(self):
        bin_ = Path(tempfile.mkdtemp(dir=ROOT))
        got = ROOT / "clip.bin"
        (bin_ / "clip.exe").write_text(f"#!/bin/sh\ncat > {got}\n")
        (bin_ / "clip.exe").chmod(0o755)
        self.bridge_eval("""
        import time
        print(json.dumps(B.clipboard_copy("héllo ✓")))
        time.sleep(0.5)""", {"WSL_DISTRO_NAME": "Ubuntu", "PATH": f"{bin_}:{os.environ['PATH']}"})
        self.assertEqual(got.read_bytes().decode("utf-16-le"), "héllo ✓")


class Launcher(unittest.TestCase):
    def test_gui_choice_and_process_stop(self):
        sys.path.insert(0, str(LAUNCHER))
        import importlib.util
        spec = importlib.util.spec_from_file_location("omo_launcher", LAUNCHER / "omo-launcher.py")
        L = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(L)
        self.assertFalse(L.wants_gui([], {}, "linux"))
        self.assertTrue(L.wants_gui([], {"DISPLAY": ":0"}, "linux"))
        self.assertTrue(L.wants_gui([], {}, "macos"))
        self.assertTrue(L.wants_gui([], {}, "windows"))
        self.assertFalse(L.wants_gui(["--tui"], {}, "windows"))
        p = subprocess.Popen(["sh", "-c", "sleep 30 & wait"], start_new_session=True)
        L.stop_tree(p)
        self.assertEqual(p.wait(timeout=5), -15)


class Installers(unittest.TestCase):
    def test_install_sh_refuses_windows_and_names_mac_needs(self):
        src = (REPO / "scripts/install.sh").read_text()
        self.assertIn("Darwin-arm64|Darwin-x86_64", src)
        self.assertIn("brew install tmux", src)
        self.assertIn("install.ps1", src)
        p = subprocess.run(["bash", "-n", str(REPO / "scripts/install.sh")], capture_output=True)
        self.assertEqual(p.returncode, 0, p.stderr)

    def test_install_ps1_parses(self):
        pwsh = shutil.which("pwsh")
        if not pwsh:
            self.skipTest("pwsh not installed")
        p = subprocess.run([pwsh, "-NoProfile", "-Command",
                            "$e=$null; [System.Management.Automation.Language.Parser]::ParseFile("
                            f"'{REPO / 'scripts/install.ps1'}', [ref]$null, [ref]$e) | Out-Null; $e.Count"],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(p.stdout.strip(), "0", p.stdout + p.stderr)


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        shutil.rmtree(ROOT, ignore_errors=True)
