#!/usr/bin/env python3
"""Security regressions for the bridge (review 01b): tmux format injection,
an empty/short token, cross-origin browsers, the unauthenticated board, and
data-directory hygiene. Each check runs a real bridge on a disposable tmux
server and a scratch data dir, like tests/test_bridge.py; nothing touches the
live bridge, its token or its tmux socket.

    python3 tests/test_bridge_security.py
    python3 -m unittest tests.test_bridge_security

OMO_TEST_BRIDGE=/path/to/omobridge.py runs the same checks against another
copy of the bridge (to see them fail on an unfixed one).
"""
import asyncio
import json
import os
import re
import stat
import sys
import time
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_bridge as tb  # noqa: E402  (Env, Client, the pinned tmux)



class SecEnv(tb.Env):
    """A test bridge whose tmux server and bridge both run with their cwd in
    a marker directory: a #(touch pN) job, if tmux ever ran one, leaves pN
    there."""

    def __init__(self):
        super().__init__()
        self.mark = os.path.join(self.dir, "mark")
        os.makedirs(self.mark)

    def _in_mark(self, fn):
        old = os.getcwd()
        os.chdir(self.mark)
        try:
            fn()
        finally:
            os.chdir(old)

    def start_server(self):
        self._in_mark(super().start_server)

    def start_bridge(self):
        self._in_mark(super().start_bridge)

    def pwned(self):
        return sorted(f for f in os.listdir(self.mark) if f.startswith("p"))

    def window_names(self, ws_id):
        return self.tmux("list-windows", "-t", f"=omo-{ws_id}", "-F", "#{window_name}").stdout.splitlines()

    def request(self, path, headers=None):
        """(status, headers, body) without raising on 4xx."""
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.headers, r.read().decode()
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            e.close()
            return e.code, e.headers, body

    def log_text(self):
        try:
            with open(os.path.join(self.data, "bridge.log")) as fh:
                return fh.read()
        except OSError:
            return ""


async def hello(env, origin=None, **msg):
    """'welcome', 'error', or 'refused' (the handshake itself was refused)."""
    kw = {"origin": origin} if origin else {}
    try:
        ws = await tb.websockets.connect(f"ws://127.0.0.1:{env.port}/ws", **kw)
    except Exception:
        return "refused"
    try:
        await ws.send(json.dumps({"t": "hello", **msg}))
        try:
            m = json.loads(await asyncio.wait_for(ws.recv(), 5))
        except Exception:
            return "refused"
        return m.get("t")
    finally:
        await ws.close()


async def ui_rename(env, ws_id, tab_id, name):
    c = tb.Client(env)
    await c.open(token=env.token, role="ui")
    await c.send(t="tab_rename", ws=ws_id, tab=tab_id, name=name)
    await c.expect(lambda m: m["t"] == "workspace", 5)
    await c.close()


async def ui_attach(env, ws_id):
    c = tb.Client(env)
    await c.open(token=env.token, role="ui")
    await c.send(t="attach", ws=ws_id, tab="", cols=60, rows=12)
    m = await c.expect(lambda m: m["t"] == "attached", 8)
    await c.close()
    return m


class BridgeSecurity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not tb.TMUX:
            raise unittest.SkipTest("no tmux")
        cls.env = SecEnv()
        os.chmod(cls.env.data, 0o755)       # a pre-existing, too-open data dir
        cls.env.start_server()
        cls.env.start_bridge()

    @classmethod
    def tearDownClass(cls):
        cls.env.cleanup()

    # -- 1. tmux format injection (critical) ----------------------------------
    def test_tab_names_never_run_tmux_jobs(self):
        env = self.env
        names = [f"#(touch p{i})" for i in range(10)] + ["w#{pid}|#{socket_path}", "x##y#[fg=red]"]
        ws = tb.api(env, "create", name="#(touch pws) fmt", kind="terminal",
                    tabs=json.dumps([{"name": n, "kind": "shell"} for n in names]))
        try:
            time.sleep(2.0)                 # a tmux job would have run by now
            self.assertEqual(env.pwned(), [], "tmux ran #(...) from a tab name")
            # tmux itself keeps a literal "#[" (a style) as "##[" in #{window_name}.
            self.assertEqual(env.window_names(ws["id"]), [n.replace("#[", "##[") for n in names],
                             "tmux expanded a tab name")
            self.assertEqual([t["name"] for t in ws["tabs"]], names, "stored names must stay unescaped")
            tab = ws["tabs"][0]["id"]
            for i in range(3):
                asyncio.run(ui_rename(env, ws["id"], tab, f"#(touch pr{i})"))
            asyncio.run(ui_rename(env, ws["id"], tab, "r#{pid}"))
            time.sleep(2.0)
            self.assertEqual(env.pwned(), [], "tmux ran #(...) from a renamed tab")
            self.assertEqual(env.window_names(ws["id"])[0], "r#{pid}")
        finally:
            tb.api(env, "delete", ws=ws["id"], force=1)

    def test_restored_cwd_is_literal(self):
        env = self.env
        cwd = os.path.join(env.mark, "c#{pid}#(touch pc)")
        os.makedirs(cwd)
        ws = tb.api(env, "create", name="Restore", kind="terminal")
        try:
            env.stop_bridge()
            meta_path = os.path.join(env.data, "workspaces.json")
            with open(meta_path) as fh:
                meta = json.load(fh)
            meta["workspaces"][ws["id"]]["tabs"][0]["cwd"] = cwd
            with open(meta_path, "w") as fh:
                json.dump(meta, fh)
            env.tmux("kill-server")
            time.sleep(0.5)
            env.start_server()
            env.start_bridge()
            att = asyncio.run(ui_attach(env, ws["id"]))
            self.assertTrue(att and att["state"] == "live", str(att))
            time.sleep(1.5)
            path = env.tmux("display", "-p", "-t", f"=omo-{ws['id']}:", "#{pane_current_path}").stdout.strip()
            self.assertEqual(env.pwned(), [], "tmux ran #(...) from a restored cwd")
            self.assertEqual(path, cwd, "the restored cwd was format-expanded")
        finally:
            tb.api(env, "delete", ws=ws["id"], force=1)

    # -- 3. Origin / Sec-Fetch-Site (medium) ----------------------------------
    def test_cross_origin_http_is_refused(self):
        env, p = self.env, self.env.port
        for hdrs in ({"Origin": "https://evil.example"}, {"Origin": "null"},
                     {"Origin": f"http://127.0.0.1:{p + 1}"}, {"Sec-Fetch-Site": "cross-site"}):
            code, _, _ = env.request("/board.json", hdrs)
            self.assertEqual(code, 403, f"{hdrs} -> {code}")
        code, _, _ = env.request(f"/api/list?token={env.token}", {"Origin": "https://evil.example"})
        self.assertEqual(code, 403, "a cross-origin request with the token must still be refused")
        for hdrs in ({}, {"Origin": f"http://127.0.0.1:{p}"}, {"Origin": f"http://localhost:{p}"},
                     {"Sec-Fetch-Site": "same-origin"}, {"Sec-Fetch-Site": "none"}):
            code, _, _ = env.request("/board.json", hdrs)
            self.assertEqual(code, 200, f"{hdrs} -> {code}")

    def test_cross_origin_websocket_is_refused(self):
        env = self.env
        self.assertEqual(asyncio.run(hello(env, "https://evil.example", token=env.token, role="ui")), "refused")
        self.assertEqual(asyncio.run(hello(env, "null", token=env.token, role="ui")), "refused")
        # CC:T's Netty handshaker sends the bridge's own origin; the mod's
        # Java client sends none. Both keep working.
        self.assertEqual(asyncio.run(hello(env, f"http://127.0.0.1:{env.port}", token=env.token)), "welcome")
        self.assertEqual(asyncio.run(hello(env, None, token=env.token)), "welcome")

    # -- 2b/M4. unauthenticated board ------------------------------------------
    def test_board_hides_process_names_without_the_token(self):
        env = self.env
        ws = tb.api(env, "create", name="Board", kind="terminal")
        try:
            pub = env.http("/board.json")
            tabs = [t for w in pub["workspaces"] if w["name"] == "Board" for t in w["tabs"]]
            self.assertTrue(tabs and all("command" not in t for t in tabs), str(tabs))
            self.assertTrue(all({"name", "state", "busy", "sleeping"} <= set(t) for t in tabs))
            full = env.http(f"/board.json?token={env.token}")
            tabs = [t for w in full["workspaces"] if w["name"] == "Board" for t in w["tabs"]]
            self.assertTrue(tabs and all("command" in t for t in tabs), str(tabs))
        finally:
            tb.api(env, "delete", ws=ws["id"], force=1)

    def test_board_headers_and_version(self):
        env = self.env
        for path in ("/board", "/board.json"):
            code, h, _ = env.request(path)
            self.assertEqual(code, 200)
            self.assertEqual(h.get("X-Content-Type-Options"), "nosniff", path)
            self.assertIn("frame-ancestors 'none'", h.get("Content-Security-Policy", ""), path)
            self.assertIn("default-src 'none'", h.get("Content-Security-Policy", ""), path)
        self.assertEqual(env.http("/health")["version"], "1.3.0")

    # -- 5. hygiene --------------------------------------------------------------
    def test_data_dir_and_files_are_private(self):
        env = self.env
        tb.api(env, "delete", ws=tb.api(env, "create", name="Perm", kind="terminal")["id"], force=1)
        self.assertEqual(stat.S_IMODE(os.stat(env.data).st_mode), 0o700)
        for f in ("token", "bridge.log", "workspaces.json"):
            self.assertEqual(stat.S_IMODE(os.stat(os.path.join(env.data, f)).st_mode), 0o600, f)

    def test_denied_lines_are_rate_limited(self):
        env = self.env
        before = env.log_text().count("DENIED station=")

        async def burst():
            return [await hello(env, None, token="wrong", station=f"spam{i}") for i in range(25)]
        self.assertTrue(all(r == "error" for r in asyncio.run(burst())))
        time.sleep(0.3)
        logged = env.log_text().count("DENIED station=") - before
        self.assertLessEqual(logged, 5, f"{logged} DENIED lines for 25 refused hellos")


class EmptyToken(unittest.TestCase):
    """2a: an empty or short token file must never mean 'no auth'."""

    def check_token(self, content):
        env = SecEnv()
        try:
            with open(os.path.join(env.data, "token"), "w") as fh:
                fh.write(content)
            env.start_server()
            try:
                env.start_bridge()
            except SystemExit:
                return              # refusing to start is acceptable too
            tok = env.token
            self.assertRegex(tok, r"^[0-9a-f]{32,}$", "the bridge runs on an empty/short token")
            for path in ("/api/list", "/api/list?token=", "/ticket?station=x&ws=y", "/ticket?token=&ws=y",
                         f"/api/list?token={content.strip()}"):
                code, _, _ = env.request(path)
                self.assertEqual(code, 403, f"{content!r}: {path} -> {code}")
            self.assertEqual(asyncio.run(hello(env, None, token="", role="ui")), "error")
            self.assertEqual(asyncio.run(hello(env, None, token=content.strip(), role="ui")), "error")
            self.assertEqual(asyncio.run(hello(env, None, token=tok, role="ui")), "welcome")
        finally:
            env.cleanup()

    def test_empty_token_file(self):
        self.check_token("")

    def test_blank_token_file(self):
        self.check_token("  \n")

    def test_short_token_file(self):
        self.check_token("abc123")


if __name__ == "__main__":
    unittest.main(verbosity=2)
