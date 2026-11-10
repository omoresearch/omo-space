"""Tests for World Sense + BYO brain. Run: python3 -m pytest tests/test_world_sense.py -v
(or python3 tests/test_world_sense.py for a stdlib-only run).

No game, no network, no key needed: the echo backend and a hand-built
snapshot cover the whole loop.
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bridge"))

import world_sense
import brain
import cost_ledger

SNAP = {
    "world": "Omo HQ", "dimension": "minecraft:overworld",
    "center": {"x": 0, "y": 64, "z": 0}, "radius_chunks": 1,
    "time": 6000, "raining": False, "thundering": False,
    "chunks": [{
        "cx": 0, "cz": 0,
        "biome": {"minecraft:plains": 200, "minecraft:forest": 56},
        "height": {"min": 62, "max": 71, "mean": 64.5},
        "top_blocks": {"minecraft:grass_block": 200, "minecraft:oak_leaves": 56},
        "blocks": {"minecraft:stone": 12000, "minecraft:dirt": 3000,
                   "minecraft:grass_block": 200, "minecraft:oak_log": 40},
        "structures": ["minecraft:village_plains"],
    }],
    "entities": [{"type": "minecraft:villager", "count": 3, "nearest": 12}],
    "players": [{"name": "erfan_master", "x": 4, "y": 65, "z": -3, "dist": 5}],
    "named": [{"label": "Hermes desk", "x": 2, "y": 65, "z": 2}],
}


class TestSummarise(unittest.TestCase):
    def test_ok_and_deterministic(self):
        a = world_sense.summarise(SNAP)
        b = world_sense.summarise(SNAP)
        self.assertTrue(a["ok"])
        self.assertEqual(a, b)  # byte-identical for the same input
        self.assertIn("plains", a["text"])
        self.assertIn("erfan_master", a["text"])
        self.assertIn("village_plains", a["text"])
        self.assertEqual(a["summary"]["terrain"]["height"]["min"], 62)
        self.assertEqual(a["summary"]["terrain"]["height"]["max"], 71)

    def test_no_raw_blocks(self):
        out = world_sense.summarise(SNAP)
        blob = json.dumps(out)
        # The summary is histograms + counts, never a per-block grid.
        self.assertLess(len(blob), 4000)
        self.assertNotIn("minecraft:air", blob)

    def test_invalid_snapshot(self):
        out = world_sense.summarise({"chunks": "nope"})
        self.assertFalse(out["ok"])
        self.assertTrue(out["problems"])

    def test_budget_truncates_and_reports(self):
        big = dict(SNAP)
        big["chunks"] = [dict(SNAP["chunks"][0], blocks={
            f"minecraft:block_{i}": 100 for i in range(60)})]
        out = world_sense.summarise(big, max_chars=400)
        self.assertTrue(out["truncated"])
        self.assertLessEqual(len(out["text"]), 400)

    def test_diff(self):
        a = world_sense.summarise(SNAP)
        snap2 = json.loads(json.dumps(SNAP))
        snap2["players"] = []
        snap2["raining"] = True
        b = world_sense.summarise(snap2)
        d = world_sense.diff(a, b)
        self.assertIn("player left: erfan_master", d["changed"])
        self.assertIn("raining: True", d["changed"])


class TestStore(unittest.TestCase):
    def test_put_get_and_diff(self):
        store = world_sense.SnapshotStore()
        self.assertFalse(store.get()["ok"])  # nothing yet
        self.assertTrue(store.put(SNAP)["ok"])
        got = store.get("Omo HQ")
        self.assertTrue(got["ok"])
        self.assertEqual(got["snapshot"]["summary"]["world"], "Omo HQ")
        self.assertFalse(store.put({"chunks": "nope"})["ok"])


class TestTools(unittest.TestCase):
    def test_known_tools_listed(self):
        names = {t["name"] for t in world_sense.tools_listing()["tools"]}
        self.assertIn("world.describe", names)
        self.assertIn("world.set", names)
        self.assertIn("world.say", names)

    def test_limits_enforced(self):
        ok, _ = world_sense.validate_tool_call(
            "world.set", {"pos1": [0, 64, 0], "pos2": [4, 68, 4], "pattern": "stone"})
        self.assertTrue(ok)
        ok, err = world_sense.validate_tool_call(
            "world.set", {"pos1": [0, 0, 0], "pos2": [100, 100, 100], "pattern": "stone"})
        self.assertFalse(ok)
        self.assertIn("32768", err)
        ok, _ = world_sense.validate_tool_call("world.nope", {})
        self.assertFalse(ok)


class TestBrain(unittest.TestCase):
    def test_echo_backend_is_deterministic(self):
        msgs = [{"role": "user", "content": "describe the world.describe area"}]
        r = brain.complete("echo", None, msgs, tools=world_sense.TOOLS, workspace="t")
        self.assertEqual(r["tool_calls"][0]["name"], "world.describe")

    def test_unknown_backend(self):
        with self.assertRaises(brain.BrainError):
            brain.complete("gpt-99", None, [], workspace="t")

    def test_openrouter_needs_key(self):
        os.environ.pop("OPENROUTER_API_KEY", None)
        with self.assertRaises(brain.BrainError):
            brain.complete("openrouter", "anthropic/claude-haiku-4.5",
                           [{"role": "user", "content": "hi"}], workspace="t")

    def test_cost_ledger_records_and_totals(self):
        with tempfile.TemporaryDirectory() as d:
            brain.complete("echo", None,
                           [{"role": "user", "content": "hello world.describe"}],
                           tools=world_sense.TOOLS, workspace="ws1", data_dir=d)
            t = cost_ledger.totals(d, workspace="ws1")
            self.assertEqual(t["calls"], 1)
            self.assertGreater(t["prompt_tokens"], 0)
            # No prompt text is stored in the ledger.
            with open(os.path.join(d, "cost_ledger.jsonl")) as fh:
                self.assertNotIn("hello world", fh.read())


if __name__ == "__main__":
    unittest.main()
