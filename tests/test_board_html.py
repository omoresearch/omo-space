"""bridge/board.html's script, run under Node with a tiny DOM stub (skipped without node).

Checks the Board renders recent omo_notify messages (the in-game toast only
reaches open station panels, so the wall is where a player sees them),
escapes them, drops old ones and keeps the Elsewhere list.

    python3 -m unittest tests.test_board_html -v
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
BOARD = os.path.join(HERE, "..", "bridge", "board.html")
NODE = shutil.which("node") or next((p for p in (
    os.path.expanduser("~/.local/share/omo/toolchain/node22/bin/node"),) if os.path.exists(p)), None)

HARNESS = r"""
const fs = require("fs");
const [html, boardJson] = [fs.readFileSync(process.argv[2], "utf8"), fs.readFileSync(process.argv[3], "utf8")];
const script = html.match(/<script>([\s\S]*)<\/script>/)[1]
  .replace(/setInterval\([^)]*\);?/g, "").replace(/^tick\(\); refresh\(\);$/m, "");
const els = {};
global.document = { getElementById: (id) => els[id] || (els[id] = { id, innerHTML: "", textContent: "",
  classList: { toggle() {} } }) };
global.fetch = async () => ({ json: async () => JSON.parse(boardJson) });
eval(script + "\n;refresh().then(() => { process.stdout.write(JSON.stringify(Object.fromEntries(" +
  "Object.entries(els).map(([k, v]) => [k, v.innerHTML || v.textContent])))); });");
"""


@unittest.skipUnless(NODE, "node not found")
class BoardHtml(unittest.TestCase):
    def render(self, board):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        h, b = os.path.join(d, "h.js"), os.path.join(d, "b.json")
        with open(h, "w") as fh:
            fh.write(HARNESS)
        with open(b, "w") as fh:
            fh.write(json.dumps(board))
        out = subprocess.run([str(NODE), h, BOARD, b], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def board(self, events, sessions=()):
        return {"version": "t", "tmux": True, "uptime": 1, "screens": 0, "workspaces": [],
                "hermes": {"sessions": list(sessions), "events": events}}

    def test_recent_notify_is_on_the_wall_and_escaped(self):
        now = time.time()
        got = self.render(self.board([
            {"ts": now - 4000, "profile": "old", "session_id": "a", "kind": "notify", "text": "too old", "level": "info"},
            {"ts": now - 30, "profile": "p1", "session_id": "b", "kind": "tool_end", "tool": "terminal"},
            {"ts": now - 20, "profile": "p1", "session_id": "b", "kind": "notify", "text": "build <b>done</b>",
             "level": "success"},
        ]))
        notes = got["notes"]
        self.assertIn("Messages", notes)
        self.assertIn("build &lt;b&gt;done&lt;/b&gt;", notes)
        self.assertIn('class="note success"', notes)
        self.assertIn("p1", notes)
        self.assertNotIn("too old", notes)

    def test_at_most_three_newest_first(self):
        now = time.time()
        evs = [{"ts": now - 60 + i, "profile": "p", "session_id": "s", "kind": "notify", "text": f"n{i}",
                "level": "info"} for i in range(5)]
        notes = self.render(self.board(evs))["notes"]
        found = re.findall(r'class="txt">(n\d)<', notes)
        self.assertEqual(found, ["n4", "n3", "n2"])

    def test_no_messages_no_section_and_elsewhere_still_listed(self):
        got = self.render(self.board([], sessions=[{"profile": "tg", "platform": "telegram", "state": "tool",
                                                    "tool": "terminal", "tool_for": 3, "age": 1, "turns": 1,
                                                    "elsewhere": True}]))
        self.assertEqual(got["notes"], "")
        self.assertIn("Elsewhere", got["elsewhere"])
        self.assertIn("running terminal", got["elsewhere"])


if __name__ == "__main__":
    unittest.main()
