#!/usr/bin/env python3
"""Launcher tests in a throwaway HOME: no network, no systemd, no real Prism,
Java or Microsoft.

Covers resumable downloads (partial, full-size and stale .part, 416, servers
that ignore Range, bad hashes, reusable local copies, parallel downloads with
one percentage), Mojang's library rules and launch arguments, the device-code
sign-in against a local stand-in for Microsoft/Xbox/Mojang (pending, slow
down, declined, expired, Xbox errors, an unapproved app, no game, refresh,
0600 storage, no token on any command line or output), the one-time world
copy from the previous launcher's Prism instance, unit rendering, the setup
lock, the CC:T loopback rule, atomic installs, the desktop entry, the window's
state logic, and offline end-to-end setup + play + repair in both modes (Omo
signs in itself / Omo's private headless Prism), with a fake Java and Prism
that record how they were started.

    python3 tests/test_launcher.py        (honours TMPDIR for the throwaway HOME)
"""
import contextlib
import gzip
import hashlib
import http.server
import io
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(tempfile.mkdtemp(prefix="omo-launcher-test-"))
FAKE_HOME = ROOT / "home"
FAKE_HOME.mkdir()
os.environ.update(HOME=str(FAKE_HOME), OMO_DATA=str(FAKE_HOME / ".local/share/omo"),
                  OMO_PRISM_DATA=str(FAKE_HOME / ".local/share/PrismLauncher"),
                  XDG_DATA_HOME=str(FAKE_HOME / ".local/share"), XDG_STATE_HOME=str(FAKE_HOME / ".local/state"),
                  OMO_NO_SERVICES="1", PATH="/usr/bin:/bin")
for key in ("OMO_MSA_CLIENT_ID", "OMO_AUTH_BASE", "OMO_BRIDGE_PORT", "OMO_JVM_ARGS"):
    os.environ.pop(key, None)
LAUNCHER = Path(__file__).resolve().parent.parent / "launcher"
sys.path.insert(0, str(LAUNCHER))
import omo_auth as A  # noqa: E402
import omo_setup as S  # noqa: E402

assert S.HOME == FAKE_HOME and str(S.OMO).startswith(str(ROOT)), "refusing to run outside the fake HOME"
S.say = lambda msg: None                      # keep test output readable
CLIENT_ID = "00000000-omo-test-client"


# --------------------------------------------------------------------------- a tiny Range-aware server
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        srv = self.server
        data = srv.files.get(self.path)
        if data is None:
            self.send_error(404)
            return
        rng = self.headers.get("Range")
        srv.seen.append(rng)
        srv.paths.append(self.path)
        if rng and srv.ranges:
            start = int(rng.split("=")[1].split("-")[0])
            if start >= len(data):
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{len(data)}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = data[start:]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
        else:
            body = data
            self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class Server:
    def __init__(self, health=None, handler=Handler):
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.httpd.files, self.httpd.seen, self.httpd.paths, self.httpd.ranges = {}, [], [], True
        if health is not None:
            self.httpd.files["/health"] = json.dumps(health).encode()
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"

    def add(self, name, data):
        self.httpd.files["/" + name] = data
        return f"{self.url}/{name}"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def spec_for(data, size=True, algo="sha512"):
    s = {algo: hashlib.new(algo, data).hexdigest()}
    if size:
        s["size"] = len(data)
    return s


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def tree_bytes(root):
    return {p.relative_to(root): p.read_bytes() for p in sorted(Path(root).rglob("*")) if p.is_file()}


@contextlib.contextmanager
def fake_game(root, via_cwd=False):
    """A process that looks like a game's java for `root` in /proc: by an
    argument naming it (Prism's way) or by its working directory (Omo's)."""
    bindir = ROOT / "fakejava"
    bindir.mkdir(exist_ok=True)
    java = bindir / "java"
    if not java.exists():
        java.symlink_to(sys.executable)
    Path(root).mkdir(parents=True, exist_ok=True)
    argv = [str(java), "-c", "import time; time.sleep(60)"]
    if not via_cwd:
        argv.append(f"-Djava.library.path={root}/natives")
    p = subprocess.Popen(argv, cwd=root if via_cwd else None)
    try:
        deadline = time.monotonic() + 5
        while p.pid not in S.instance_pids(root) and time.monotonic() < deadline:
            time.sleep(0.05)
        yield p.pid
    finally:
        p.kill()
        p.wait()


# --------------------------------------------------------------------------- downloads
class Downloads(unittest.TestCase):
    def setUp(self):
        self.srv = Server()
        self.data = os.urandom(300_000)
        self.url = self.srv.add("blob.bin", self.data)
        self.dest = ROOT / "dl" / self._testMethodName / "blob.bin"
        self.part = self.dest.with_name("blob.bin.part")
        self.dest.parent.mkdir(parents=True)

    def tearDown(self):
        self.srv.close()

    def fetch(self, spec, seeds=()):
        S.download(self.url, self.dest, spec, "blob", seeds)
        self.assertEqual(self.dest.read_bytes(), self.data)
        self.assertFalse(self.part.exists())
        return self.srv.httpd.seen

    def test_fresh(self):
        self.assertEqual(self.fetch(spec_for(self.data)), [None])

    def test_already_verified_is_not_fetched(self):
        self.dest.write_bytes(self.data)
        self.assertEqual(self.fetch(spec_for(self.data)), [])

    def test_resume_partial(self):
        self.part.write_bytes(self.data[:100_000])
        self.assertEqual(self.fetch(spec_for(self.data)), ["bytes=100000-"])

    def test_full_size_part_is_verified_without_network(self):
        self.part.write_bytes(self.data)
        self.assertEqual(self.fetch(spec_for(self.data)), [])

    def test_full_size_corrupt_part_is_discarded(self):
        self.part.write_bytes(os.urandom(len(self.data)))
        self.assertEqual(self.fetch(spec_for(self.data)), [None])

    def test_416_on_complete_part_without_size(self):
        self.part.write_bytes(self.data)
        self.assertEqual(self.fetch(spec_for(self.data, size=False)), [f"bytes={len(self.data)}-"])

    def test_416_on_stale_part_restarts(self):
        self.part.write_bytes(os.urandom(len(self.data)))
        self.assertEqual(self.fetch(spec_for(self.data, size=False)), [f"bytes={len(self.data)}-", None])

    def test_server_ignoring_range_restarts_cleanly(self):
        self.srv.httpd.ranges = False
        self.part.write_bytes(self.data[:1000])
        self.assertEqual(self.fetch(spec_for(self.data)), ["bytes=1000-"])

    def test_bad_hash_gives_up(self):
        with self.assertRaises(SystemExit):
            S.download(self.url, self.dest, spec_for(b"something else"), "blob")
        self.assertFalse(self.dest.exists())
        self.assertFalse(self.part.exists())

    def test_matching_local_copy_is_reused_without_network(self):
        good, bad = self.dest.with_name("good-seed"), self.dest.with_name("bad-seed")
        good.write_bytes(self.data)
        bad.write_bytes(self.data[:-1] + b"x")
        self.assertEqual(self.fetch(spec_for(self.data, algo="sha1"), [ROOT / "missing", bad, good]), [])
        self.assertEqual(good.read_bytes(), self.data)                  # the copy's source is left as it was

    def test_bad_local_copy_is_ignored(self):
        seed = self.dest.with_name("seed")
        seed.write_bytes(os.urandom(len(self.data)))
        self.assertEqual(self.fetch(spec_for(self.data, algo="sha1"), [seed]), [None])

    def test_many_files_one_percentage(self):
        blobs = {f"f{i}": os.urandom(40_000 + i) for i in range(12)}
        items = [(self.srv.add(n, b), self.dest.parent / "many" / n, spec_for(b, algo="sha1"), [])
                 for n, b in blobs.items()]
        (self.dest.parent / "many").mkdir()
        (self.dest.parent / "many/f0").write_bytes(blobs["f0"])        # already here: not fetched
        (self.dest.parent / "many/f1").write_bytes(b"short")           # wrong size: fetched again
        lines = []
        clock = iter(range(10**6))                                     # every progress call may print
        with mock.patch.object(S, "say", lines.append), mock.patch.object(S.time, "monotonic", lambda: next(clock)):
            S.download_all(items, "bundle")
        self.assertEqual({p.name: p.read_bytes() for p in (self.dest.parent / "many").iterdir()}, blobs)
        self.assertEqual(len(self.srv.httpd.seen), 11)
        pcts = [float(l.split("%")[0]) for l in lines if "%" in l]
        self.assertTrue(pcts and pcts == sorted(pcts) and pcts[-1] <= 99.9, lines)
        self.assertIn("verified  bundle (11 files)", lines[-1])
        lines.clear()
        with mock.patch.object(S, "say", lines.append):
            S.download_all(items, "bundle")
        self.assertEqual(lines, ["  ok        bundle"])


# --------------------------------------------------------------------------- minecraft rules and arguments
class GameRules(unittest.TestCase):
    def test_rules(self):
        linux, osx = {"action": "allow", "os": {"name": "linux"}}, {"action": "allow", "os": {"name": "osx"}}
        self.assertTrue(S.allowed(None))
        self.assertTrue(S.allowed([linux]))
        self.assertFalse(S.allowed([osx]))
        self.assertFalse(S.allowed([{"action": "allow", "os": {"arch": "x86"}}]))              # 32-bit only
        self.assertFalse(S.allowed([{"action": "allow", "features": {"is_demo_user": True}}]))
        self.assertFalse(S.allowed([{"action": "allow"}, {"action": "disallow", "os": {"name": "linux"}}]))

    def test_maven_path(self):
        self.assertEqual(S.maven_path("org.lwjgl:lwjgl-glfw:3.4.1:natives-linux"),
                         "org/lwjgl/lwjgl-glfw/3.4.1/lwjgl-glfw-3.4.1-natives-linux.jar")
        self.assertEqual(S.maven_path("net.fabricmc:fabric-loader:0.19.5"),
                         "net/fabricmc/fabric-loader/0.19.5/fabric-loader-0.19.5.jar")

    def test_every_download_is_pinned(self):
        m = S.MANIFEST
        self.assertRegex(m["minecraft"]["sha1"], r"^[0-9a-f]{40}$")
        for lib in m["fabric"]["libraries"]:
            self.assertRegex(lib["sha1"], r"^[0-9a-f]{40}$")
            self.assertGreater(lib["size"], 0)
        for spec in (m["java"][S.ARCH], m["prism"][S.ARCH], m["tmux"][S.ARCH], *m["mods"]):
            self.assertTrue(spec["url"].startswith("https://") and ("sha256" in spec or "sha512" in spec))
        self.assertTrue(m["microsoft"]["client_id"] is None or isinstance(m["microsoft"]["client_id"], str))

    def test_play_without_a_sign_in_says_so(self):
        with mock.patch.dict(os.environ, {"OMO_MSA_CLIENT_ID": CLIENT_ID}), \
                mock.patch.multiple(S, installed=lambda: True, bridge_problem=lambda: None, migrate=lambda: None,
                                    ACCOUNT=ROOT / "no-account.json"), \
                mock.patch.object(S.os, "execve") as execve, self.assertRaises(SystemExit) as cm:
            S.play()
        self.assertIn("Not signed in", str(cm.exception))
        execve.assert_not_called()

    def test_argfile_quoting_round_trips(self):
        import shlex
        args = ["--accessToken", 'a "b" c\\d', "", "/x y/z"]
        self.assertEqual(shlex.split("\n".join(S.argfile_line(a) for a in args)), args)


# --------------------------------------------------------------------------- sign-in against a stand-in for Microsoft
class FakeAuth(http.server.BaseHTTPRequestHandler):
    """Microsoft, Xbox Live and Mojang in one: what each endpoint answers is
    set on the server (see AuthServer)."""

    def do_POST(self):
        s = self.server
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n).decode()
        name = self.path.strip("/")
        s.calls.append(name)
        if name == "devicecode":
            return self.reply(200, {"device_code": "DEV-1", "user_code": "ABCD-EFGH", "interval": 0,
                                    "verification_uri": "https://www.microsoft.com/link", "expires_in": 900})
        if name == "token":
            form = dict(p.split("=", 1) for p in raw.split("&"))
            s.forms.append(form)
            if form["grant_type"] == "refresh_token":
                if s.refresh_error:
                    return self.reply(400, {"error": s.refresh_error})
                s.issued += 1
                return self.reply(200, {"access_token": f"MSA-ACCESS-SECRET-{s.issued}",
                                        "refresh_token": f"MSA-REFRESH-SECRET-{s.issued}"})
            if s.token_script:
                err = s.token_script.pop(0)
                return self.reply(400, {"error": err})
            s.issued += 1
            return self.reply(200, {"access_token": f"MSA-ACCESS-SECRET-{s.issued}",
                                    "refresh_token": f"MSA-REFRESH-SECRET-{s.issued}"})
        body = json.loads(raw)
        if name == "xbl":
            assert body["Properties"]["RpsTicket"].startswith("d=MSA-ACCESS-SECRET-")
            return self.reply(200, {"Token": "XBL-SECRET", "DisplayClaims": {"xui": [{"uhs": "UHS1"}]}})
        if name == "xsts":
            if s.xsts_xerr:
                return self.reply(401, {"XErr": s.xsts_xerr})
            return self.reply(200, {"Token": "XSTS-SECRET"})
        if name == "minecraft":
            assert body["identityToken"] == "XBL3.0 x=UHS1;XSTS-SECRET"
            if s.mc_status != 200:
                return self.reply(s.mc_status, {"error": "Invalid app registration"})
            s.minted += 1
            return self.reply(200, {"access_token": f"MC-ACCESS-SECRET-{s.minted}", "expires_in": 86400})
        self.reply(404, {})

    def do_GET(self):
        s = self.server
        s.calls.append("profile")
        if s.profile_status != 200:
            return self.reply(s.profile_status, {"error": "NOT_FOUND"})
        assert self.headers["Authorization"].startswith("Bearer MC-ACCESS-SECRET-")
        self.reply(200, {"id": "0123456789abcdef0123456789abcdef", "name": "OmoTester"})

    def reply(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class AuthServer(Server):
    def __init__(self):
        super().__init__(handler=FakeAuth)
        h = self.httpd
        h.calls, h.forms, h.token_script, h.issued, h.minted = [], [], [], 0, 0
        h.refresh_error = h.xsts_xerr = None
        h.mc_status = h.profile_status = 200


class SignIn(unittest.TestCase):
    def setUp(self):
        self.srv = AuthServer()
        self.h = self.srv.httpd
        self.endpoints = mock.patch.dict(A.ENDPOINTS, {k: f"{self.srv.url}/{k}" for k in A.ENDPOINTS})
        self.endpoints.start()
        self.path = ROOT / "auth" / self._testMethodName / "account.json"

    def tearDown(self):
        self.endpoints.stop()
        self.srv.close()

    def sign_in(self):
        shown = []
        account = A.sign_in(CLIENT_ID, self.path, shown.append, sleep=lambda s: None)
        return account, shown

    def test_device_code_flow_with_pending_and_slow_down(self):
        self.h.token_script = ["authorization_pending", "slow_down", "authorization_pending"]
        naps = []
        account = A.sign_in(CLIENT_ID, self.path, lambda c: naps.append(c["user_code"]), sleep=naps.append)
        self.assertEqual(naps, ["ABCD-EFGH", 0, 0, 5, 5])                 # slow_down adds 5 s to the interval
        self.assertEqual(account["profile"]["name"], "OmoTester")
        self.assertEqual(self.h.calls, ["devicecode", "token", "token", "token", "token", "xbl", "xsts",
                                        "minecraft", "profile"])
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(A.load(self.path)["minecraft"]["access_token"], "MC-ACCESS-SECRET-1")
        self.assertEqual(self.h.forms[0]["client_id"], CLIENT_ID)

    def test_declined_and_expired(self):
        for err, text in (("authorization_declined", "cancelled"), ("expired_token", "expired")):
            self.h.token_script = [err]
            with self.assertRaises(A.AuthError) as cm:
                self.sign_in()
            self.assertIn(text, str(cm.exception))
        self.assertFalse(self.path.exists())

    def test_xbox_and_mojang_refusals_are_plain(self):
        cases = ((("xsts_xerr", 2148916233), "no Xbox profile"), (("xsts_xerr", 2148916238), "child account"),
                 (("mc_status", 403), "has not approved"), (("profile_status", 404), "does not own Minecraft"))
        for (attr, value), text in cases:
            with self.subTest(attr=attr, value=value):
                setattr(self.h, attr, value)
                with self.assertRaises(A.AuthError) as cm:
                    self.sign_in()
                self.assertIn(text, str(cm.exception))
                self.assertNotIn("SECRET", str(cm.exception))
                setattr(self.h, attr, 200 if attr != "xsts_xerr" else None)

    def test_refresh_only_near_expiry(self):
        self.sign_in()
        self.assertEqual(A.fresh(CLIENT_ID, self.path)["minecraft"]["access_token"], "MC-ACCESS-SECRET-1")
        account = A.load(self.path)
        account["minecraft"]["expires_at"] = int(time.time()) + 60
        A.save(self.path, account)
        renewed = A.fresh(CLIENT_ID, self.path)
        self.assertEqual(renewed["minecraft"]["access_token"], "MC-ACCESS-SECRET-2")
        self.assertEqual(self.h.forms[-1]["refresh_token"], "MSA-REFRESH-SECRET-1")
        self.assertEqual(A.load(self.path)["msa"]["refresh_token"], "MSA-REFRESH-SECRET-2")   # rotated
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_expired_refresh_means_signed_out(self):
        self.sign_in()
        account = A.load(self.path)
        account["minecraft"]["expires_at"] = 0
        A.save(self.path, account)
        self.h.refresh_error = "invalid_grant"
        with self.assertRaises(A.AuthError) as cm:
            A.fresh(CLIENT_ID, self.path)
        self.assertTrue(cm.exception.signed_out)

    def test_other_client_id_or_no_file_is_signed_out(self):
        with self.assertRaises(A.AuthError):
            A.fresh(CLIENT_ID, self.path)
        self.sign_in()
        with self.assertRaises(A.AuthError) as cm:
            A.fresh("another-client", self.path)
        self.assertTrue(cm.exception.signed_out)
        A.sign_out(self.path)
        self.assertIsNone(A.load(self.path))

    def test_cli_shows_the_code_and_never_a_token(self):
        env = {**os.environ, "OMO_MSA_CLIENT_ID": CLIENT_ID, "OMO_AUTH_BASE": self.srv.url}
        p = subprocess.run([sys.executable, str(LAUNCHER / "omo_setup.py"), "signin"], env=env,
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("signin: code ABCD-EFGH https://www.microsoft.com/link 900", p.stdout)
        self.assertIn("signin: signed in as OmoTester", p.stdout)
        self.assertNotIn("SECRET", p.stdout + p.stderr)
        self.assertEqual(stat.S_IMODE(S.ACCOUNT.stat().st_mode), 0o600)
        p = subprocess.run([sys.executable, str(LAUNCHER / "omo_setup.py"), "signout"], env=env,
                           capture_output=True, text=True, timeout=30)
        self.assertFalse(S.ACCOUNT.exists())

    def test_cli_without_client_id_explains_the_prism_path(self):
        p = subprocess.run([sys.executable, str(LAUNCHER / "omo_setup.py"), "signin"],
                           capture_output=True, text=True, timeout=30)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("Microsoft window", p.stderr)


# --------------------------------------------------------------------------- units, desktop entry
class Units(unittest.TestCase):
    def test_render_quotes_and_escapes(self):
        odd = ROOT / "odd dir 100%h"               # an unescaped %h would expand to $HOME
        odd.mkdir(exist_ok=True)
        tmux = odd / "tmux"
        tmux.write_text("#!/bin/sh\n")
        tmux.chmod(0o755)
        subs = {"TMUX": tmux, "TMUX_CONF": odd / "tmux.conf", "PYTHON": S.pick_python(),
                "BRIDGE": odd / "omobridge.py", "OMO_DATA": odd / "data", "HERMES": "",
                "CLAUDE": odd / "claude", "CODEX": "", "PORT": 8780}
        out = ROOT / "units"
        out.mkdir(exist_ok=True)
        for unit in S.UNITS:
            text = S.render_unit((S.REPO / "scripts" / unit).read_text(), subs)
            self.assertNotRegex(text, r"@[A-Z_]+@")
            self.assertIn(str(odd).replace("%", "%%"), text)
            (out / unit).write_text(text)
        self.assertIn(f'ExecStart="{str(tmux).replace("%", "%%")}" -D -L omo', (out / "omo-tmux.service").read_text())
        if shutil.which("systemd-analyze"):
            p = subprocess.run(["systemd-analyze", "verify", str(out / "omo-tmux.service")],
                               capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)

    def test_bridge_unit_pins_agent_clis(self):
        subs = {"TMUX": "/t", "TMUX_CONF": "/c", "PYTHON": "/p", "BRIDGE": "/b", "OMO_DATA": "/d",
                "HERMES": "/h/hermes", "CLAUDE": "/h/claude", "CODEX": "", "PORT": 8780}
        text = S.render_unit((S.REPO / "scripts/omo-bridge.service").read_text(), subs)
        self.assertIn('Environment="OMO_CLAUDE_BIN=/h/claude" "OMO_CODEX_BIN="', text)

    def test_bridge_port_reaches_the_unit_and_the_game(self):
        # OMO_BRIDGE_PORT moved the CC:T rule and the readiness check, but the
        # unit started the bridge on 8780 and the mod dialled 8780: setup waited
        # on a port nothing listened on, then failed.
        subs = {"TMUX": "/t", "TMUX_CONF": "/c", "PYTHON": "/p", "BRIDGE": "/b", "OMO_DATA": "/d",
                "HERMES": "", "CLAUDE": "", "CODEX": "", "PORT": 18781}
        text = S.render_unit((S.REPO / "scripts/omo-bridge.service").read_text(), subs)
        self.assertIn('"OMO_PORT=18781"', text)
        units = ROOT / "port-units"
        units.mkdir(exist_ok=True)
        with mock.patch.object(S, "PORT", 18781), mock.patch.object(S, "SYSTEMD_USER", units), \
                mock.patch.object(S, "TMUX_CONF", units / "tmux.conf"), \
                mock.patch.object(S, "systemctl"), mock.patch.object(S, "wait_ready", return_value=None), \
                mock.patch.dict(os.environ, {"OMO_NO_SERVICES": ""}):
            S.ensure_services("/t")
        self.assertIn('"OMO_PORT=18781"', (units / "omo-bridge.service").read_text())
        with mock.patch.object(S, "PORT", 18781), mock.patch.object(S, "local_jvm", return_value={}):
            with mock.patch.dict(os.environ, {"OMO_BRIDGE_PORT": "18781", "OMO_JVM_ARGS": ""}):
                self.assertIn("-Domo.port=18781", S.extra_jvm_args())
            with mock.patch.dict(os.environ, {"OMO_BRIDGE_PORT": "18781", "OMO_JVM_ARGS": "-Domo.port=9"}):
                self.assertEqual([a for a in S.extra_jvm_args() if "omo.port" in a], ["-Domo.port=9"])
            with mock.patch.dict(os.environ, {"OMO_JVM_ARGS": ""}):
                os.environ.pop("OMO_BRIDGE_PORT", None)
                self.assertNotIn("-Domo.port=18781", S.extra_jvm_args())   # default: the mod's own default

    def test_find_agent_cli(self):
        home = Path(tempfile.mkdtemp(dir=ROOT))
        cli = home / ".local/bin/claude"
        cli.parent.mkdir(parents=True)
        cli.write_text("#!/bin/sh\n")
        cli.chmod(0o755)
        old_home, old_path = S.HOME, os.environ.get("PATH", "")
        S.HOME, os.environ["PATH"] = home, str(home / "nothing")
        try:
            self.assertEqual(S.find_agent_cli("claude"), cli)
            self.assertIsNone(S.find_agent_cli("codex"))
        finally:
            S.HOME, os.environ["PATH"] = old_home, old_path

    def test_refuses_ambiguous_paths_and_leftovers(self):
        for bad in ('/a"b', "/a$b", "/a\\b", "/a\nb"):
            with self.assertRaises(SystemExit):
                S.unit_value(bad)
        with self.assertRaises(SystemExit):
            S.render_unit("ExecStart=@PYTHON@ @NEW@", {"PYTHON": "/usr/bin/python3"})

    def test_python_prefers_system(self):
        if Path("/usr/bin/python3").exists():
            self.assertEqual(S.pick_python(), "/usr/bin/python3")

    def test_missing_systemctl_is_a_clear_message(self):
        with mock.patch.object(S.shutil, "which", return_value=None):
            with self.assertRaises(SystemExit) as cm:
                S.systemctl("daemon-reload")
        self.assertIn("systemctl was not found", str(cm.exception))

    def test_desktop_entry_and_icon(self):
        self.assertEqual(S.desktop_exec_arg('/a b/100%/x"y'), '"/a b/100%%/x\\\\"y"')
        S.write_desktop_entry()
        share = FAKE_HOME / ".local/share"
        entry = share / f"applications/{S.APP_ID}.desktop"
        text = entry.read_text()
        self.assertIn(str(LAUNCHER / "omo-launcher.py"), text)
        self.assertIn(f"Icon={S.APP_ID}\n", text)
        self.assertTrue((share / f"icons/hicolor/scalable/apps/{S.APP_ID}.svg").is_file())
        if shutil.which("desktop-file-validate"):
            p = subprocess.run(["desktop-file-validate", str(entry)], capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)


# --------------------------------------------------------------------------- guards, files, config
class Memory(unittest.TestCase):
    """Game heap from the machine's RAM; runtime/local-jvm.json still wins."""
    BASE = {"min": 2048, "max": 6144}

    def tearDown(self):
        S.LOCAL_JVM.unlink(missing_ok=True)

    def test_manifest_base(self):
        self.assertEqual(S.MANIFEST["memory_mb"], self.BASE)

    def test_heap_by_ram(self):
        cases = {   # MemTotal MB (nominal and what such machines report) -> max heap MB
            4096: 2048, 8192: 3072, 7800: 3072, 11 * 1024: 4096, 12 * 1024: 4864, 15 * 1024: 6144,
            16384: 6144, 15900: 6144, 32768: 6144, 65536: 6144, 128 * 1024: 6144}
        for total, top in cases.items():
            self.assertEqual(S.default_memory_mb(self.BASE, total), {"min": 2048, "max": top}, total)
        for unknown in (None, 0, -1):
            self.assertEqual(S.default_memory_mb(self.BASE, unknown), self.BASE)
        prev = 0
        for total in range(1024, 256 * 1024, 64):
            mem = S.default_memory_mb(self.BASE, total)
            self.assertTrue(mem["min"] <= mem["max"] <= 6144 and mem["max"] >= prev, total)
            self.assertTrue(mem["max"] <= max(2048, total * 0.4), total)
            prev = mem["max"]

    def test_this_machine(self):
        total = S.total_ram_mb()
        self.assertTrue(total is None or 256 <= total < 64 * 2**20, total)
        with mock.patch.object(S, "total_ram_mb", return_value=8192):
            self.assertEqual(S.memory_mb(), {"min": 2048, "max": 3072})

    def test_local_jvm_wins(self):
        with mock.patch.object(S, "total_ram_mb", return_value=8192):
            S.LOCAL_JVM.parent.mkdir(parents=True, exist_ok=True)
            S.LOCAL_JVM.write_text(json.dumps({"memory_mb": {"max": 7168}}))
            self.assertEqual(S.memory_mb(), {"min": 2048, "max": 7168})
            S.LOCAL_JVM.write_text(json.dumps({"memory_mb": {"min": 1024, "max": "2048"}}))
            self.assertEqual(S.memory_mb(), {"min": 1024, "max": 2048})
            S.LOCAL_JVM.write_text("not json")
            self.assertEqual(S.memory_mb(), {"min": 2048, "max": 3072})


class Guards(unittest.TestCase):
    def test_setup_lock_excludes_a_second_launcher(self):
        code = "import omo_setup as S\nwith S.setup_lock(): pass"
        with S.setup_lock():
            p = subprocess.run([sys.executable, "-c", code], cwd=LAUNCHER, capture_output=True, text=True)
            self.assertNotEqual(p.returncode, 0)
            self.assertIn("already running", p.stderr)
        p = subprocess.run([sys.executable, "-c", code], cwd=LAUNCHER, capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)

    def test_running_game_is_detected(self):
        other = ROOT / "games/other"
        self.assertEqual(S.instance_pids(ROOT / "games/prism"), [])
        with fake_game(ROOT / "games/prism") as pid:                        # Prism's java names its instance
            self.assertIn(pid, S.instance_pids(ROOT / "games/prism"))
            self.assertEqual(S.instance_pids(other), [])
        with fake_game(ROOT / "games/omo", via_cwd=True) as pid:             # Omo's java runs in the game dir
            self.assertIn(pid, S.instance_pids(ROOT / "games/omo"))
            link = ROOT / "games/link"
            link.symlink_to(ROOT / "games/omo")
            self.assertIn(pid, S.instance_pids(link))                         # the private Prism's .minecraft link
            self.assertEqual(S.instance_pids(other), [])

    def test_install_file_is_atomic_and_skips_identical(self):
        d = ROOT / "install"
        d.mkdir(exist_ok=True)
        (d / "src.jar").write_bytes(b"one")
        self.assertTrue(S.install_file(d / "src.jar", d / "dest.jar"))
        self.assertFalse(S.install_file(d / "src.jar", d / "dest.jar"))
        (d / "src.jar").write_bytes(b"two")
        self.assertTrue(S.install_file(d / "src.jar", d / "dest.jar"))
        self.assertEqual((d / "dest.jar").read_bytes(), b"two")
        self.assertEqual(sorted(p.name for p in d.iterdir()), ["dest.jar", "src.jar"])

    def test_starter_world_copy_is_atomic(self):
        src = ROOT / "world-src"
        src.mkdir(exist_ok=True)
        (src / "level.dat").write_bytes(b"level")
        (src / "session.lock").write_bytes(b"lock")
        name = S.MANIFEST["world_name"]
        leftover = S.GAME / "saves" / f".{name}.omo-tmp"                      # left by an interrupted copy
        leftover.mkdir(parents=True, exist_ok=True)
        S.ensure_starter_world(src)
        world = S.GAME / "saves" / name
        self.assertEqual(sorted(p.name for p in world.iterdir()), ["level.dat"])
        self.assertFalse(leftover.exists())
        (world / "level.dat").write_bytes(b"played")
        S.ensure_starter_world(src)                                        # never overwrites a save
        self.assertEqual((world / "level.dat").read_bytes(), b"played")
        shutil.rmtree(S.GAME)

    def test_ini_set_keeps_other_keys(self):
        cfg = ROOT / "ini" / "prismlauncher.cfg"
        cfg.parent.mkdir()
        cfg.write_text("[General]\nLanguage=de\nGeometry=abc\n\n[UI]\nx=1\n")
        S.ini_set(cfg, "General", {"Language": "en_US", "QuitAfterGameStop": "true"})
        self.assertEqual(cfg.read_text(), "[General]\nLanguage=en_US\nGeometry=abc\n\nQuitAfterGameStop=true\n"
                                          "[UI]\nx=1\n")
        S.ini_set(cfg, "New", {"k": 1})
        self.assertTrue(cfg.read_text().endswith("[New]\nk=1\n"))


class CCTRule(unittest.TestCase):
    SOURCE = S.REPO / "config/computercraft-server.toml"

    def setUp(self):
        self.path = ROOT / "cct" / self._testMethodName / "computercraft-server.toml"
        self.path.parent.mkdir(parents=True)

    def test_source_allows_only_the_bridge_port(self):
        text = self.SOURCE.read_text()
        self.assertEqual(S.cct_rule_state(text, 8780), "ok")
        self.assertEqual(S.cct_rule_state(text, 22), "missing")

    def test_absent_file_is_created_for_the_given_port(self):
        self.assertEqual(S.ensure_cct_rule(self.path, 8794), "absent")
        text = self.path.read_text()
        self.assertEqual(S.cct_rule_state(text, 8794), "ok")
        self.assertEqual(S.cct_rule_state(text, 8780), "missing")

    def test_open_rule_is_tightened(self):
        self.path.write_text(self.SOURCE.read_text().replace("\t\tport = 8780\n", ""))
        self.assertEqual(S.ensure_cct_rule(self.path), "open")
        self.assertEqual(self.path.read_text(), self.SOURCE.read_text())

    def test_missing_rule_goes_before_the_private_deny(self):
        text = self.SOURCE.read_text()
        start = text.index("\t[[http.rules]]\n\t\t#Allow CC:T")
        end = text.index("\t[[http.rules]]", start + 1)
        self.path.write_text(text[:start] + text[end:])
        self.assertEqual(S.ensure_cct_rule(self.path), "missing")
        new = self.path.read_text()
        self.assertEqual(S.cct_rule_state(new, 8780), "ok")
        self.assertLess(new.index('host = "127.0.0.1"'), new.index('host = "$private"'))

    def test_ok_file_is_left_alone(self):
        shutil.copy2(self.SOURCE, self.path)
        before = self.path.stat().st_mtime_ns
        self.assertEqual(S.ensure_cct_rule(self.path), "ok")
        self.assertEqual(self.path.stat().st_mtime_ns, before)


# --------------------------------------------------------------------------- the one-time world copy
class Migration(unittest.TestCase):
    def setUp(self):
        base = ROOT / "migration" / self._testMethodName
        self.old = base / "PrismLauncher/instances/OmoResearch/.minecraft"
        for world in ("Omo Workshop", "Omo Workshop (17 Sep)"):
            (self.old / "saves" / world / "region").mkdir(parents=True)
            (self.old / "saves" / world / "level.dat").write_bytes(os.urandom(64))
            (self.old / "saves" / world / "region/r.0.0.mca").write_bytes(os.urandom(4096))
            (self.old / "saves" / world / "session.lock").write_bytes(b"\xe2\x98\x83")
        (self.old / "options.txt").write_text("fov:90\n")
        (self.old / "config").mkdir()
        (self.old / "config/omolife.json").write_text("{}")
        (self.old / "mods").mkdir()
        (self.old / "mods/old.jar").write_bytes(b"jar")
        self.patches = [mock.patch.object(S, "OLD_GAME", self.old), mock.patch.object(S, "RUNTIME", base / "runtime"),
                        mock.patch.object(S, "GAME", base / "runtime/game")]
        for p in self.patches:
            p.start()
        self.before = tree_bytes(self.old)

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_copies_worlds_and_settings_once_and_never_changes_the_source(self):
        self.assertEqual(S.old_worlds(), ["Omo Workshop", "Omo Workshop (17 Sep)"])
        S.migrate()
        for world in ("Omo Workshop", "Omo Workshop (17 Sep)"):
            self.assertEqual(tree_bytes(S.GAME / "saves" / world),
                             {k: v for k, v in tree_bytes(self.old / "saves" / world).items()
                              if k.name != "session.lock"})
        self.assertEqual((S.GAME / "options.txt").read_text(), "fov:90\n")
        self.assertTrue((S.GAME / "config/omolife.json").exists())
        self.assertFalse((S.GAME / "mods").exists())                    # mods are Omo's own, pinned
        self.assertEqual(tree_bytes(self.old), self.before)
        self.assertEqual(S.old_worlds(), [])
        (S.GAME / "saves/Omo Workshop/level.dat").write_bytes(b"played in Omo")
        shutil.rmtree(S.GAME / "saves/Omo Workshop (17 Sep)")
        S.migrate()                                                     # once: the marker stops a second copy
        self.assertEqual((S.GAME / "saves/Omo Workshop/level.dat").read_bytes(), b"played in Omo")
        self.assertFalse((S.GAME / "saves/Omo Workshop (17 Sep)").exists())

    def test_existing_worlds_here_are_never_overwritten(self):
        (S.GAME / "saves/Omo Workshop").mkdir(parents=True)
        (S.GAME / "saves/Omo Workshop/level.dat").write_bytes(b"mine")
        S.migrate()
        self.assertEqual((S.GAME / "saves/Omo Workshop/level.dat").read_bytes(), b"mine")
        self.assertTrue((S.GAME / "saves/Omo Workshop (17 Sep)/level.dat").exists())

    def test_waits_while_the_old_game_runs(self):
        with fake_game(self.old.parent):
            S.migrate()
        self.assertFalse(S.GAME.exists())
        self.assertFalse((S.RUNTIME / ".migrated").exists())
        S.migrate()
        self.assertTrue((S.GAME / "saves/Omo Workshop/level.dat").exists())


# --------------------------------------------------------------------------- bridge diagnosis
class Bridge(unittest.TestCase):
    def setUp(self):
        self.port = S.PORT

    def tearDown(self):
        S.PORT = self.port

    def test_nothing_listening(self):
        S.PORT = free_port()
        self.assertIn("not running", S.bridge_problem())

    def test_port_held_by_someone_else(self):
        srv = Server()                              # answers /health with 404: not a bridge
        S.PORT = srv.port
        try:
            self.assertIn(f"pid {os.getpid()}", S.bridge_problem())
        finally:
            srv.close()

    def test_readiness_needs_the_session_server(self):
        for health, expect in (({"ok": True, "tmux": False}, "session server"), ({"ok": True, "tmux": True}, None)):
            srv = Server(health)
            S.PORT = srv.port
            try:
                problem = S.bridge_problem()
                self.assertTrue(problem is None if expect is None else expect in problem, problem)
            finally:
                srv.close()

    def test_play_refuses_when_the_bridge_is_down(self):
        S.PORT = free_port()
        with mock.patch.object(S, "installed", return_value=True), mock.patch.object(S.os, "execve") as execve, \
                self.assertRaises(SystemExit) as cm:
            S.play()
        self.assertIn("cannot play", str(cm.exception))
        execve.assert_not_called()


# --------------------------------------------------------------------------- the window's logic (no display needed)
def load_launcher_ui():
    import importlib.util
    spec = importlib.util.spec_from_file_location("omo_launcher", LAUNCHER / "omo-launcher.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class LauncherUI(unittest.TestCase):
    UI = load_launcher_ui()
    BASE = {"installed": True, "running": False, "mods": {"a": True}, "omolife": True, "tmux": True,
            "bridge_problem": None, "hermes": {"installed": False}, "worlds_to_copy": [],
            "account": {"mode": "omo", "signed_in": True, "name": "OmoTester"}}

    def test_readiness_needs_bridge_and_session_server(self):
        self.assertTrue(self.UI.readiness(self.BASE)[1])
        down = {**self.BASE, "bridge_problem": "the session server (omo-tmux) is not running"}
        lines, ready = self.UI.readiness(down)
        self.assertFalse(ready)
        self.assertIn("session server", lines[1][2])
        self.assertFalse(self.UI.readiness({"error": "boom"})[1])

    def test_hermes_line_names_model_and_provider(self):
        s = {**self.BASE, "hermes": {"installed": True, "path": "/h", "model": "qwen3-coder", "provider": "local"}}
        self.assertIn("qwen3-coder via local", self.UI.readiness(s)[0][2][2])

    def test_terminal_menu_explains_the_sign_in(self):
        # The menu showed no account line until signed in, and "4) Sign in"
        # without Omo's client ID printed "signin: FAILED" with no way forward.
        line = self.UI.account_line
        self.assertEqual(line({"mode": "omo", "signed_in": True, "name": "OmoTester"}),
                         ("ok", "Signed in as OmoTester"))
        state, text = line({"mode": "prism", "signed_in": False})
        self.assertEqual(state, "warn")
        self.assertIn("2) Play", text)
        self.assertIn("Microsoft sign-in window", text)
        self.assertIn("4) Sign in", line({"mode": "omo", "signed_in": False})[1])
        s = {**self.BASE, "account": {"mode": "prism", "signed_in": False, "name": None}}
        out = io.StringIO()
        with mock.patch.object(self.UI, "status", return_value=s), \
                mock.patch("builtins.input", side_effect=["4", "q"]), \
                mock.patch.object(self.UI.subprocess, "run") as run, contextlib.redirect_stdout(out):
            self.UI.tui()
        run.assert_not_called()
        self.assertIn("[--] Account", out.getvalue())
        self.assertIn("Microsoft sign-in window", out.getvalue())
        self.assertNotIn("FAILED", out.getvalue())

    def test_page_for_each_state(self):
        page = self.UI.page_for
        self.assertEqual(page({**self.BASE, "installed": False}), "welcome")
        self.assertEqual(page({**self.BASE, "account": {"mode": "omo", "signed_in": False}}), "signin")
        self.assertEqual(page({**self.BASE, "account": {"mode": "prism", "signed_in": False}}), "home")
        self.assertEqual(page(self.BASE), "home")
        self.assertEqual(page({**self.BASE, "running": True}), "playing")
        self.assertEqual(page({"error": "boom"}), "home")

    def test_setup_output_becomes_steps_and_percentages(self):
        parse = self.UI.parse_line
        self.assertEqual(parse("== step 3/7: Minecraft\n"), ("step", 3, 7, "Minecraft"))
        self.assertEqual(parse("   42.5%  Minecraft sounds and textures (204 of 480 MB)\n"),
                         ("percent", 0.425, "Minecraft sounds and textures (204 of 480 MB)"))
        self.assertEqual(parse("== plan: Session server | Java | Minecraft\n"),
                         ("plan", ["Session server", "Java", "Minecraft"]))
        self.assertEqual(parse("  verified  Temurin 25 JRE\n"), ("did", "Verified: Temurin 25 JRE"))
        self.assertEqual(parse("  reused    asset index 32 (/home/x/.local/share/PrismLauncher/32.json)\n"),
                         ("did", "Reused: asset index 32"))
        self.assertEqual(parse("signin: code ABCD-EFGH https://www.microsoft.com/link 900\n"),
                         ("code", "ABCD-EFGH", "https://www.microsoft.com/link"))
        self.assertEqual(parse("tmux: installed /x/bin/tmux\n"), ("info", "tmux: installed /x/bin/tmux"))

    def watch(self, script, marker_after=None):
        log = ROOT / f"play-{self._testMethodName}.log"
        log.write_text("earlier run\nlaunching: old\n")               # only this run's output counts
        game_log = ROOT / f"game-{self._testMethodName}/logs/latest.log"
        game_log.parent.mkdir(parents=True)
        game_log.write_text(f"[old] {S.WINDOW_UP} from an earlier game\n")
        os.utime(game_log, (time.time() - 100, time.time() - 100))
        offset = log.stat().st_size                                 # before the child can write
        with open(log, "ab") as out:
            p = subprocess.Popen(["sh", "-c", script], stdout=out, stderr=subprocess.STDOUT)
        w = self.UI.PlayWatch(p, log, offset, game_log, running=lambda: p.poll() is None)
        states, t0 = [], time.monotonic()
        while time.monotonic() - t0 < 10:
            st = w.poll()
            if not states or states[-1] != st:
                states.append(st)
            late = marker_after is not None and time.monotonic() - t0 > marker_after
            if late and not game_log.read_text().startswith("new"):
                game_log.write_text(f"new game\n[Render thread/INFO]: {S.WINDOW_UP} LWJGL\n")
            if st[0] in ("over", "failed"):
                break
            time.sleep(0.05)
        p.kill()
        p.wait()
        return states

    def test_play_watch_follows_a_game(self):
        states = self.watch("sleep 0.3; echo 'launching: Minecraft'; sleep 1.5", marker_after=0.8)
        self.assertEqual(states, [("preflight",), ("launched",), ("window",), ("over", 0)])

    def test_play_watch_ends_with_the_game_not_its_launcher(self):
        game = {"up": True}
        log = ROOT / "play-outlived.log"
        log.write_text("launching: Minecraft (headless Prism)\n")
        game_log = ROOT / "outlived/logs/latest.log"
        game_log.parent.mkdir(parents=True)
        game_log.write_text(f"{S.WINDOW_UP} LWJGL\n")
        prism = subprocess.Popen(["sleep", "30"])                   # a Prism that stays after its game
        try:
            w = self.UI.PlayWatch(prism, log, 0, game_log, running=lambda: game["up"])
            self.assertEqual([w.poll(), w.poll(), w.poll()], [("launched",), ("window",), ("window",)])
            game["up"] = False
            self.assertEqual(w.poll(), ("over", 0))
        finally:
            prism.kill()
            prism.wait()

    def test_play_watch_reports_a_failed_preflight(self):
        states = self.watch("echo 'cannot play: bridge down'; exit 1")
        self.assertEqual(states[-1], ("failed", "cannot play: bridge down"))

    def test_play_watch_game_exits_before_its_window(self):
        states = self.watch("echo 'launching: Minecraft'; exit 3")
        self.assertEqual(states[-1][0], "failed")


# --------------------------------------------------------------------------- offline setup + play + repair
FAKE_JAVA = rb'''#!/usr/bin/env python3
# Records how it was started (argv, @argfile contents, cwd, a few env vars),
# then behaves like a game: its log says the window is up, it runs a while.
import json, os, shlex, sys, time
args = sys.argv[1:]
if args == ["-version"]:
    print("openjdk 25 (fake)")
    sys.exit(0)
expanded = []
for a in args:
    expanded += shlex.split(open(a[1:]).read()) if a.startswith("@") else [a]
record = {"argv": sys.argv, "expanded": expanded, "cwd": os.getcwd(),
          "env": {k: os.environ.get(k) for k in ("QT_QPA_PLATFORM", "WAYLAND_DISPLAY", "DISPLAY", "XDG_SESSION_TYPE")}}
with open(os.environ["FAKE_RECORD"], "w") as fh:
    json.dump(record, fh)
os.makedirs("logs", exist_ok=True)
with open("logs/latest.log", "w") as fh:
    fh.write("[Render thread/INFO]: Backend library: LWJGL version 3.4.1\n")
time.sleep(float(os.environ.get("FAKE_GAME_SECONDS", "0")))
'''


def targz(files):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, (data, mode) in files.items():
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), mode
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def sha1_spec(data):
    return {"sha1": hashlib.sha1(data).hexdigest(), "size": len(data)}


class SetupEndToEnd(unittest.TestCase):
    """setup, play and repair against a local server standing in for Mojang,
    Fabric, GitHub and Modrinth, in both modes."""

    @classmethod
    def setUpClass(cls):
        cls.srv = Server()
        cls.bridge = Server({"ok": True, "tmux": True})
        add = cls.srv.add
        m = S.MANIFEST
        cls.saved = json.loads(json.dumps(m))
        tmux = b"#!/bin/sh\necho 'tmux 3.7b (fake)'\n"
        jre = targz({"jdk-25-jre/bin/java": (FAKE_JAVA, 0o755), "jdk-25-jre/lib/modules": (os.urandom(5000), 0o644)})
        lib, natives, mac, fabric = (os.urandom(3000 + i) for i in range(4))
        client = os.urandom(9000)
        cls.objects = {f"minecraft/sounds/{i}.ogg": os.urandom(2000 + i) for i in range(20)}
        index = json.dumps({"objects": {k: sha1_spec(v) | {"hash": hashlib.sha1(v).hexdigest()}
                                        for k, v in cls.objects.items()}}).encode()
        for v in cls.objects.values():
            h = hashlib.sha1(v).hexdigest()
            add(f"res/{h[:2]}/{h}", v)

        def artifact(path, data, rules=None):
            a = {"name": path, "downloads": {"artifact": {"path": path, "url": add(path, data), **sha1_spec(data)}}}
            return a | ({"rules": rules} if rules else {})
        version = {
            "id": "26.2", "type": "release",
            "arguments": {
                "game": ["--username", "${auth_player_name}", "--version", "${version_name}",
                         "--gameDir", "${game_directory}", "--assetsDir", "${assets_root}",
                         "--assetIndex", "${assets_index_name}", "--uuid", "${auth_uuid}",
                         "--accessToken", "${auth_access_token}", "--clientId", "${clientid}",
                         "--xuid", "${auth_xuid}", "--versionType", "${version_type}",
                         {"rules": [{"action": "allow", "features": {"is_demo_user": True}}], "value": "--demo"}],
                "jvm": [{"rules": [{"action": "allow", "os": {"name": "osx"}}], "value": ["-XstartOnFirstThread"]},
                        {"rules": [{"action": "allow", "os": {"arch": "x86"}}], "value": "-Xss1M"},
                        "-Djava.library.path=${natives_directory}/java",
                        "-Dminecraft.launcher.brand=${launcher_name}", "-cp", "${classpath}"]},
            "assetIndex": {"id": "32", "url": add("index.json", index), **sha1_spec(index)},
            "downloads": {"client": {"url": add("client.jar", client), **sha1_spec(client)}},
            "libraries": [artifact("com/example/lib/1.0/lib-1.0.jar", lib),
                          artifact("org/lwjgl/lwjgl/3.4.1/lwjgl-3.4.1-natives-linux.jar", natives,
                                   [{"action": "allow", "os": {"name": "linux"}}]),
                          artifact("org/lwjgl/lwjgl/3.4.1/lwjgl-3.4.1-natives-macos.jar", mac,
                                   [{"action": "allow", "os": {"name": "osx"}}])],
        }
        vjson = json.dumps(version).encode()
        m["minecraft"] = {"version": "26.2", "url": add("26.2.json", vjson), **sha1_spec(vjson),
                          "resources": f"{cls.srv.url}/res/"}
        m["fabric"]["libraries"] = [{"name": "net.fabricmc:fabric-loader:0.19.5", "url": f"{cls.srv.url}/maven/",
                                     **sha1_spec(fabric)}]
        add("maven/" + S.maven_path("net.fabricmc:fabric-loader:0.19.5"), fabric)
        tmux_gz = gzip.compress(tmux)
        m["tmux"][S.ARCH] = {"url": add("tmux.gz", tmux_gz), **spec_for(tmux_gz, False, "sha256")}
        m["java"][S.ARCH] = {"url": add("jre.tgz", jre), **spec_for(jre, True, "sha256")}
        m["prism"][S.ARCH] = {"url": add("prism", FAKE_JAVA), **spec_for(FAKE_JAVA, True, "sha256")}
        mod = os.urandom(4000)
        m["mods"] = [{"id": "fake", "file": "fake-mod-1.0.jar", "url": add("fake.jar", mod), **spec_for(mod)}]
        jars = ROOT / "shipped"
        jars.mkdir()
        (jars / m["omolife"]["file"]).write_bytes(b"omolife")
        cls.mod_jars = mock.patch.object(S, "MOD_JARS", (jars,))
        cls.mod_jars.start()
        cls.port = mock.patch.object(S, "PORT", cls.bridge.port)
        cls.port.start()
        (ROOT / "e2e-manifest.json").write_text(json.dumps(m))
        # The player's own Prism already has the client jar: it is reused, not downloaded.
        old = FAKE_HOME / ".local/share/PrismLauncher/libraries/com/mojang/minecraft/26.2/minecraft-26.2-client.jar"
        old.parent.mkdir(parents=True)
        old.write_bytes(client)
        cls.tmux_bin, cls.lib = tmux, lib

    @classmethod
    def tearDownClass(cls):
        cls.mod_jars.stop()
        cls.port.stop()
        S.MANIFEST.clear()
        S.MANIFEST.update(cls.saved)
        cls.srv.close()
        cls.bridge.close()

    def play(self, *args, client=None, seconds="0"):
        """Run play in a real process (it execs), with this test's manifest."""
        record = ROOT / "record.json"
        record.unlink(missing_ok=True)
        env = {**os.environ, "FAKE_RECORD": str(record), "FAKE_GAME_SECONDS": seconds,
               "OMO_BRIDGE_PORT": str(self.bridge.port), "DISPLAY": ":97", "WAYLAND_DISPLAY": "wayland-9"}
        env.pop("OMO_MSA_CLIENT_ID", None)
        if client:
            env["OMO_MSA_CLIENT_ID"] = client
        code = ("import json, sys, omo_setup as S\n"
                "S.MANIFEST.clear(); S.MANIFEST.update(json.load(open(sys.argv[1])))\n"
                f"S.MOD_JARS = ({str(ROOT / 'shipped')!r},)\n"
                "S.play(*sys.argv[2:3] == ['--show'] and [True] or [])\n")
        p = subprocess.run([sys.executable, "-c", code, str(ROOT / "e2e-manifest.json"), *args], cwd=LAUNCHER,
                           env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertNotIn("SECRET", p.stdout + p.stderr)
        return json.loads(record.read_text())

    def test_setup_play_repair_in_both_modes(self):
        # -- Omo signs in itself (a client ID is configured): no Prism at all.
        os.environ["OMO_MSA_CLIENT_ID"] = CLIENT_ID
        try:
            S.setup()
            self.assertFalse(S.OWN_PRISM.exists())
            self.assertTrue(S.installed())
            self.assertEqual(S.account_status(), {"mode": "omo", "signed_in": False, "name": None})
            A.save(S.ACCOUNT, {"client_id": CLIENT_ID, "msa": {"refresh_token": "MSA-REFRESH-SECRET"},
                               "minecraft": {"access_token": "MC-ACCESS-SECRET", "expires_at": time.time() + 3600},
                               "profile": {"id": "0123abcd", "name": "OmoTester"}})
            self.assertEqual(S.account_status()["name"], "OmoTester")
        finally:
            del os.environ["OMO_MSA_CLIENT_ID"]
        self.assertEqual((S.OMO / "bin/tmux").read_bytes(), self.tmux_bin)
        mods = S.GAME / "mods"
        self.assertTrue((mods / "fake-mod-1.0.jar").exists() and (mods / S.MANIFEST["omolife"]["file"]).exists())
        self.assertEqual(S.cct_rule_state((S.GAME / "config/computercraft-server.toml").read_text(), S.PORT), "ok")
        self.assertTrue((S.GAME / "saves" / S.MANIFEST["world_name"] / "level.dat").exists())
        for data in self.objects.values():
            h = hashlib.sha1(data).hexdigest()
            self.assertEqual((S.MCDIR / "assets/objects" / h[:2] / h).read_bytes(), data)
        self.assertFalse((S.MCDIR / "libraries/org/lwjgl/lwjgl/3.4.1/lwjgl-3.4.1-natives-macos.jar").exists())
        self.assertNotIn("/client.jar", self.srv.httpd.paths)          # reused from the player's Prism
        self.assertFalse(list(S.OMO.rglob("*.part")) + list(S.OMO.rglob("*.omo-tmp")))

        rec = self.play(client=CLIENT_ID)
        self.assertNotIn("MC-ACCESS-SECRET", " ".join(rec["argv"]))          # never on the command line
        self.assertTrue(rec["argv"][-1].startswith("@/dev/fd/"))
        game = rec["expanded"][rec["expanded"].index(S.MANIFEST["fabric"]["main_class"]) + 1:]
        self.assertEqual(game[game.index("--accessToken") + 1], "MC-ACCESS-SECRET")
        self.assertEqual(game[game.index("--username") + 1], "OmoTester")
        self.assertEqual(game[game.index("--gameDir") + 1], str(S.GAME))
        self.assertEqual(game[game.index("--version") + 1], "fabric-loader-0.19.5-26.2")
        self.assertNotIn("--demo", game)
        self.assertNotIn("${", " ".join(rec["expanded"]))
        jvm = rec["argv"][1:-1]
        cp = jvm[jvm.index("-cp") + 1].split(os.pathsep)
        self.assertTrue(cp[0].endswith("fabric-loader-0.19.5.jar") and cp[-1].endswith("versions/26.2/26.2.jar"))
        self.assertFalse(any("macos" in c for c in cp) or "-XstartOnFirstThread" in jvm or "-Xss1M" in jvm)
        self.assertIn("-DFabricMcEmu= net.minecraft.client.main.Main ", jvm)
        self.assertEqual(rec["cwd"], str(S.GAME))
        # On X (XWayland) and nowhere else: no Wayland socket to wander off to.
        self.assertEqual((rec["env"]["WAYLAND_DISPLAY"], rec["env"]["XDG_SESSION_TYPE"], rec["env"]["DISPLAY"]),
                         (None, "x11", ":97"))

        # -- No client ID: Omo's private Prism, visible once for the sign-in, then headless.
        S.setup()
        self.assertTrue(S.OWN_PRISM.exists() and S.installed())
        cfg = (S.PRISM_DIR / "prismlauncher.cfg").read_text()
        self.assertIn("QuitAfterGameStop=true", cfg)
        self.assertIn(f"JavaPath={S.JAVA_DIR / 'bin/java'}", cfg)
        self.assertEqual((S.INSTANCE / ".minecraft").resolve(), S.GAME.resolve())
        self.assertEqual((S.PRISM_DIR / "libraries").resolve(), (S.MCDIR / "libraries").resolve())
        self.assertEqual(S.account_status(), {"mode": "prism", "signed_in": False, "name": None})
        rec = self.play()
        self.assertEqual(rec["argv"][1:], ["--dir", str(S.PRISM_DIR), "-l", S.INSTANCE_ID])
        self.assertEqual(rec["env"]["QT_QPA_PLATFORM"], "xcb")               # the sign-in window shows, on X
        (S.PRISM_DIR / "accounts.json").write_text(json.dumps(
            {"formatVersion": 3, "accounts": [{"type": "MSA", "profile": {"name": "OmoTester"}}]}))
        self.assertEqual(S.account_status(), {"mode": "prism", "signed_in": True, "name": "OmoTester"})
        self.assertEqual(self.play()["env"]["QT_QPA_PLATFORM"], "xcb")   # -l opens no Prism window; offscreen never launched
        self.assertEqual(self.play("--show")["env"]["QT_QPA_PLATFORM"], "xcb")

        # -- Damage what repair promises to check; it must restore all of it.
        (S.OMO / "bin/tmux").write_bytes(b"#!/bin/sh\necho tampered\n")
        (S.JAVA_DIR / "lib/modules").write_bytes(b"truncated")
        (mods / "fake-mod-1.0.jar").write_bytes(b"corrupt")
        lib = S.MCDIR / "libraries/com/example/lib/1.0/lib-1.0.jar"
        lib.write_bytes(b"x" * len(self.lib))                               # right size, wrong bytes
        cct = S.GAME / "config/computercraft-server.toml"
        cct.write_text(cct.read_text().replace(f"\t\tport = {S.PORT}\n", ""))
        world = S.GAME / "saves" / S.MANIFEST["world_name"]
        before_world, account = tree_bytes(world), (S.PRISM_DIR / "accounts.json").read_bytes()
        seen = len(self.srv.httpd.seen)
        S.repair()
        self.assertEqual((S.OMO / "bin/tmux").read_bytes(), self.tmux_bin)
        self.assertTrue(S.java_tree_matches(S.DL / "temurin25-jre.tar.gz", S.JAVA_DIR))
        self.assertTrue(S.verify(mods / "fake-mod-1.0.jar", S.MANIFEST["mods"][0]))
        self.assertEqual(lib.read_bytes(), self.lib)
        self.assertEqual(S.cct_rule_state(cct.read_text(), S.PORT), "ok")
        self.assertEqual(tree_bytes(world), before_world)                  # saves untouched
        self.assertEqual((S.PRISM_DIR / "accounts.json").read_bytes(), account)
        self.assertEqual(len(self.srv.httpd.seen) - seen, 2)                # only the bad mod and library

        # -- With the game running, repair touches no game file and says why.
        (mods / "fake-mod-1.0.jar").write_bytes(b"corrupt again")
        with fake_game(S.GAME, via_cwd=True), self.assertRaises(SystemExit) as cm:
            S.repair()
        self.assertIn("Minecraft is running", str(cm.exception))
        self.assertEqual((mods / "fake-mod-1.0.jar").read_bytes(), b"corrupt again")


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        shutil.rmtree(ROOT, ignore_errors=True)
