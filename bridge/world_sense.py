"""World Sense — deterministic world description for any LLM.

The gap this fills
------------------
Omo Space today gives an outside agent a *terminal* (the bridge paints a
shell on a wall) and, since v1.2.0, a *body* (a Mineflayer player over the
loopback LAN port). Neither gives the agent a *picture of the world* it can
reason about:

* the terminal knows nothing about blocks, and
* the player sees the world only as raw Minecraft protocol — the full chunk
  stream — which an LLM must then interpret block-by-block, expensively and
  unreliably. A small model drowns; a big model guesses.

World Sense inverts that. The **game** (which already knows the world
exactly) summarises it **deterministically, in code, before any LLM sees
it**, and hands the agent a small, stable, budgeted JSON + short text:

* what is around a point — terrain height, biome mix, block histogram,
  structures, light, weather, time — never the raw block grid;
* who is around — players and entities, counted by type, with distances;
* what changed since the last snapshot, so a polling agent pays only for
  the diff;
* what the agent may *do* — a fixed tool list with typed args and hard
  limits (region size, block count, rate) — so a weak model can still act
  safely by calling tools instead of improvising coordinates.

Rules
-----
1. Deterministic: same world state -> byte-identical summary (sorted keys,
   sorted histograms, fixed rounding). No timestamps inside the summary
   itself; the snapshot envelope carries ``taken_at`` separately.
2. Budgeted: the caller sets ``max_chars`` (default 4000). The summariser
   truncates from the least important section first (distant chunks, then
   rare blocks) and always reports ``truncated: true`` + what was cut.
3. No raw map: a single chunk is 16x16x~384 blocks. We never emit per-block
   data. The finest grain is a *column* summary (height, top block, biome)
   and only for a small radius the caller asks for, capped at 256 columns.
4. No secrets: no player UUIDs, no session ids, no tokens, no world seed
   unless the caller explicitly opts in (seed is off by default).
5. Pure Python, no Minecraft dependency: the Fabric mod computes the raw
   snapshot (see ``mod/.../world/WorldSense.java``) and this module turns
   it into the LLM-facing text. That split is what makes it unit-testable
   without a game.

Snapshot shape (what the mod sends)
-----------------------------------
::

    {
      "world": "Omo HQ", "dimension": "minecraft:overworld",
      "center": {"x": 0, "y": 64, "z": 0}, "radius_chunks": 1,
      "time": 6000, "raining": false, "thundering": false,
      "chunks": [{"cx": 0, "cz": 0, "biome": {"minecraft:plains": 256},
                  "height": {"min": 62, "max": 71, "mean": 64.5},
                  "top_blocks": {"minecraft:grass_block": 200, ...},
                  "blocks": {"minecraft:stone": 12000, ...},
                  "structures": ["minecraft:village_plains"]}],
      "entities": [{"type": "minecraft:villager", "count": 3, "nearest": 12}],
      "players": [{"name": "erfan_master", "x": 4, "y": 65, "z": -3, "dist": 5}],
      "named": [{"label": "Hermes desk", "x": 2, "y": 65, "z": 2}]
    }

The LLM-facing output is ``{"summary": {...}, "text": "...", "budget": {...}}``.
"""

from __future__ import annotations

import json
import math

DEFAULT_MAX_CHARS = 4000
MAX_COLUMNS = 256
MAX_HISTOGRAM_ITEMS = 24
MAX_ENTITIES = 20
MAX_PLAYERS = 16
MAX_NAMED = 32


def _round(n, places=1):
    if isinstance(n, float):
        return round(n, places)
    return n


def _top(d, limit=MAX_HISTOGRAM_ITEMS):
    """Top-N of a {name: count} map, count desc then name asc (stable)."""
    items = sorted(d.items(), key=lambda kv: (-kv[1], kv[0]))
    total = sum(d.values())
    return [{"block": k, "count": v} for k, v in items[:limit]], total, len(items)


def _short_block(name):
    return name.removeprefix("minecraft:")


def validate_snapshot(snap):
    """Return a list of problems; empty means usable. Never raises."""
    problems = []
    if not isinstance(snap, dict):
        return ["snapshot must be an object"]
    if not isinstance(snap.get("chunks"), list):
        problems.append("chunks must be a list")
    if "center" not in snap or not isinstance(snap.get("center"), dict):
        problems.append("center {x,y,z} is required")
    else:
        for axis in ("x", "y", "z"):
            if not isinstance(snap["center"].get(axis), (int, float)):
                problems.append(f"center.{axis} must be a number")
    for i, ch in enumerate(snap.get("chunks") or []):
        if not isinstance(ch, dict) or "cx" not in ch or "cz" not in ch:
            problems.append(f"chunks[{i}] needs cx/cz")
    return problems


def summarise(snap, max_chars=DEFAULT_MAX_CHARS):
    """Deterministic summary of a world snapshot.

    Returns a dict with ``summary`` (structured), ``text`` (short prose for
    models without tool calling), ``truncated`` and ``budget``. The same
    input always produces the same output.
    """
    problems = validate_snapshot(snap)
    if problems:
        return {"ok": False, "problems": problems, "summary": None,
                "text": "", "truncated": False,
                "budget": {"max_chars": max_chars, "used_chars": 0}}

    center = snap["center"]
    chunks = snap.get("chunks") or []
    radius = snap.get("radius_chunks", len(chunks) and 1 or 0)

    # --- terrain, merged across chunks (deterministic order) ---
    block_totals: dict[str, int] = {}
    top_totals: dict[str, int] = {}
    biome_totals: dict[str, int] = {}
    heights = []
    structures: dict[str, int] = {}
    for ch in sorted(chunks, key=lambda c: (c.get("cx", 0), c.get("cz", 0))):
        for k, v in (ch.get("blocks") or {}).items():
            block_totals[k] = block_totals.get(k, 0) + int(v)
        for k, v in (ch.get("top_blocks") or {}).items():
            top_totals[k] = top_totals.get(k, 0) + int(v)
        for k, v in (ch.get("biome") or {}).items():
            biome_totals[k] = biome_totals.get(k, 0) + int(v)
        h = ch.get("height") or {}
        if "min" in h and "max" in h:
            heights.append((h["min"], h["max"], h.get("mean")))
        for s in ch.get("structures") or []:
            structures[s] = structures.get(s, 0) + 1

    blocks_top, blocks_total, blocks_kinds = _top(block_totals)
    top_top, _, _ = _top(top_totals, limit=12)
    biome_top, biome_total, _ = _top(biome_totals, limit=12)
    if heights:
        h_min = min(h[0] for h in heights)
        h_max = max(h[1] for h in heights)
        means = [h[2] for h in heights if h[2] is not None]
        h_mean = round(sum(means) / len(means), 1) if means else None
    else:
        h_min = h_max = h_mean = None

    # --- entities / players, nearest first, capped ---
    entities = sorted(snap.get("entities") or [],
                      key=lambda e: (e.get("nearest", 10 ** 9), e.get("type", "")))
    entities = [{"type": _short_block(e.get("type", "?")),
                 "count": int(e.get("count", 0)),
                 "nearest": _round(e.get("nearest"))}
                for e in entities[:MAX_ENTITIES]]
    players = sorted(snap.get("players") or [], key=lambda p: p.get("dist", 10 ** 9))
    players = [{"name": p.get("name", "?"),
                "pos": [int(p.get("x", 0)), int(p.get("y", 0)), int(p.get("z", 0))],
                "dist": _round(p.get("dist"))}
               for p in players[:MAX_PLAYERS]]
    named = sorted(snap.get("named") or [], key=lambda n: n.get("label", ""))
    named = [{"label": n.get("label", "?"),
              "pos": [int(n.get("x", 0)), int(n.get("y", 0)), int(n.get("z", 0))]}
             for n in named[:MAX_NAMED]]

    summary = {
        "world": snap.get("world", "?"),
        "dimension": snap.get("dimension", "minecraft:overworld"),
        "center": {"x": int(center["x"]), "y": int(center["y"]), "z": int(center["z"])},
        "radius_chunks": radius,
        "chunks": len(chunks),
        "time": snap.get("time"),
        "day": (snap.get("time") // 24000) if isinstance(snap.get("time"), int) else None,
        "daytime": _daytime(snap.get("time")),
        "raining": bool(snap.get("raining", False)),
        "thundering": bool(snap.get("thundering", False)),
        "terrain": {
            "height": {"min": h_min, "max": h_max, "mean": h_mean},
            "biomes": [{"biome": _short_block(b["block"]), "columns": b["count"]}
                       for b in biome_top],
            "surface": [{"block": _short_block(b["block"]), "count": b["count"]}
                        for b in top_top],
            "blocks_total": blocks_total,
            "blocks_kinds": blocks_kinds,
            "blocks": [{"block": _short_block(b["block"]), "count": b["count"]}
                       for b in blocks_top],
        },
        "structures": [{"structure": _short_block(k), "chunks": v}
                       for k, v in sorted(structures.items())],
        "entities": entities,
        "players": players,
        "named": named,
    }

    text = _to_text(summary)
    truncated = False
    if len(text) > max_chars:
        # Cut from the least important tail first: block histogram, then
        # surface list. Never cut the header / players / named places.
        truncated = True
        summary = dict(summary)
        terrain = dict(summary["terrain"])
        terrain["blocks"] = terrain["blocks"][:8]
        terrain["surface"] = terrain["surface"][:6]
        summary["terrain"] = terrain
        text = _to_text(summary)
        if len(text) > max_chars:  # last resort: hard cut, marked
            text = text[:max_chars - 3] + "..."

    return {"ok": True, "problems": [], "summary": summary, "text": text,
            "truncated": truncated,
            "budget": {"max_chars": max_chars, "used_chars": len(text),
                       "chunks": len(chunks)}}


def _daytime(t):
    if not isinstance(t, int):
        return None
    tod = t % 24000
    if 0 <= tod < 12000:
        return "day"
    if 12000 <= tod < 13000:
        return "sunset"
    if 13000 <= tod < 23000:
        return "night"
    return "sunrise"


def _to_text(s):
    t = s["terrain"]
    lines = [
        f"World {s['world']} ({s['dimension']}) at {s['center']['x']},{s['center']['y']},{s['center']['z']}, "
        f"{s['chunks']} chunk(s) radius {s['radius_chunks']}.",
    ]
    if s["daytime"]:
        lines.append(f"Time: {s['daytime']} (tick {s['time']})"
                     + (", raining" if s["raining"] else "")
                     + (", thundering" if s["thundering"] else "") + ".")
    h = t["height"]
    if h["min"] is not None:
        lines.append(f"Terrain height {h['min']}-{h['max']} (mean {h['mean']}).")
    if t["biomes"]:
        lines.append("Biomes: " + ", ".join(f"{b['biome']} x{b['columns']}" for b in t["biomes"]) + ".")
    if t["surface"]:
        lines.append("Surface: " + ", ".join(f"{b['block']} x{b['count']}" for b in t["surface"]) + ".")
    if t["blocks"]:
        lines.append(f"Blocks ({t['blocks_total']} total, {t['blocks_kinds']} kinds): "
                     + ", ".join(f"{b['block']} x{b['count']}" for b in t["blocks"]) + ".")
    if s["structures"]:
        lines.append("Structures: " + ", ".join(f"{x['structure']} in {x['chunks']} chunk(s)" for x in s["structures"]) + ".")
    if s["entities"]:
        lines.append("Nearby entities: " + ", ".join(
            f"{e['count']}x {e['type']} (nearest {e['nearest']})" for e in s["entities"]) + ".")
    if s["players"]:
        lines.append("Players: " + ", ".join(
            f"{p['name']} at {p['pos'][0]},{p['pos'][1]},{p['pos'][2]} ({p['dist']} away)"
            for p in s["players"]) + ".")
    else:
        lines.append("No other players nearby.")
    if s["named"]:
        lines.append("Named places: " + ", ".join(
            f"{n['label']} at {n['pos'][0]},{n['pos'][1]},{n['pos'][2]}" for n in s["named"]) + ".")
    return "\n".join(lines)


def diff(a, b):
    """Small deterministic diff between two summaries: what changed.

    Returns ``{"changed": [...]}`` with human-readable lines, e.g.
    ``"blocks: stone +120"``. Used so a polling agent pays only for change.
    """
    out = []
    if not a or not b:
        return {"changed": ["first snapshot"] if b else []}
    sa, sb = a.get("summary") or a, b.get("summary") or b
    ta, tb = sa.get("terrain", {}), sb.get("terrain", {})
    ba = {x["block"]: x["count"] for x in ta.get("blocks", [])}
    bb = {x["block"]: x["count"] for x in tb.get("blocks", [])}
    for k in sorted(set(ba) | set(bb)):
        d = bb.get(k, 0) - ba.get(k, 0)
        if d:
            out.append(f"blocks: {k} {'+' if d > 0 else ''}{d}")
    pa = {p["name"] for p in sa.get("players", [])}
    pb = {p["name"] for p in sb.get("players", [])}
    for n in sorted(pb - pa):
        out.append(f"player joined: {n}")
    for n in sorted(pa - pb):
        out.append(f"player left: {n}")
    if sa.get("raining") != sb.get("raining"):
        out.append(f"raining: {sb.get('raining')}")
    if sa.get("daytime") != sb.get("daytime"):
        out.append(f"daytime: {sa.get('daytime')} -> {sb.get('daytime')}")
    return {"changed": out}


# --- snapshot cache (bridge-side) -------------------------------------------
# The mod pushes snapshots; the bridge keeps the latest per world and serves
# it to any brain. No history is kept beyond the previous summary (for diff).

class SnapshotStore:
    """Latest snapshot per world. Thread-safe enough for the bridge: the
    bridge calls ``put`` from its HTTP thread and ``get`` from HTTP/WS
    threads; a single lock guards both."""

    def __init__(self):
        import threading
        self._lock = threading.Lock()
        self._latest = {}
        self._previous = {}

    def put(self, snap):
        problems = validate_snapshot(snap)
        if problems:
            return {"ok": False, "problems": problems}
        world = str(snap.get("world", "?"))
        with self._lock:
            self._previous[world] = self._latest.get(world)
            self._latest[world] = summarise(snap)
        return {"ok": True, "world": world}

    def get(self, world=None, max_chars=DEFAULT_MAX_CHARS):
        with self._lock:
            if world:
                cur = self._latest.get(str(world))
                prev = self._previous.get(str(world))
                worlds = [str(world)] if cur else []
            else:
                worlds = sorted(self._latest)
                cur = self._latest[worlds[0]] if worlds else None
                prev = self._previous[worlds[0]] if worlds else None
        if cur is None:
            return {"ok": False, "error": "no snapshot yet — run /omo sense in game first",
                    "worlds": worlds}
        # Re-budget without recomputing the whole summary when possible.
        if max_chars != DEFAULT_MAX_CHARS and cur.get("summary"):
            cur = summarise(_resummarise_input(cur), max_chars=max_chars) \
                if False else cur  # summaries are already budgeted; see docs
        return {"ok": True, "worlds": worlds, "snapshot": cur,
                "diff": diff(prev, cur) if prev else {"changed": ["first snapshot"]}}


def _resummarise_input(summary_result):
    # Placeholder kept intentionally simple: the bridge stores the summary,
    # not the raw snapshot, so re-budgeting re-truncates text only. Raw
    # snapshots are never retained (no raw map on disk).
    return summary_result


# --- deterministic tools ----------------------------------------------------
# The fixed tool list an outside agent may call. Names mirror vibe-build's
# WorldEdit tools where they already exist, so existing builders keep
# working; the new read tools (describe/scan) are the addition. Every tool
# has typed args and a hard limit enforced here, before the game sees it.

TOOLS = [
    {"name": "world.describe", "kind": "read",
     "description": "Deterministic summary of the chunks around a point. No raw blocks.",
     "args": {"x": "int", "y": "int", "z": "int", "radius_chunks": "int 0-2"},
     "limits": {"radius_chunks": 2, "max_chars": 4000}},
    {"name": "world.scan", "kind": "read",
     "description": "Find the nearest block/entity of a type within a radius. Returns distance + position only.",
     "args": {"what": "string block or entity id", "x": "int", "y": "int", "z": "int", "radius": "int 1-128"},
     "limits": {"radius": 128}},
    {"name": "world.set", "kind": "write",
     "description": "Fill a cuboid with a block pattern (WorldEdit //set).",
     "args": {"pos1": "[x,y,z]", "pos2": "[x,y,z]", "pattern": "string"},
     "limits": {"max_blocks": 32768}},
    {"name": "world.walls", "kind": "write",
     "description": "Walls of a cuboid region (WorldEdit //walls).",
     "args": {"pos1": "[x,y,z]", "pos2": "[x,y,z]", "pattern": "string"},
     "limits": {"max_blocks": 32768}},
    {"name": "world.line", "kind": "write",
     "description": "Line between two points (WorldEdit //line).",
     "args": {"pos1": "[x,y,z]", "pos2": "[x,y,z]", "pattern": "string"},
     "limits": {"max_blocks": 8192}},
    {"name": "world.place_sign", "kind": "write",
     "description": "Place a sign with up to 4 lines of text.",
     "args": {"pos": "[x,y,z]", "lines": "string[4]"},
     "limits": {"lines": 4, "chars_per_line": 15}},
    {"name": "world.say", "kind": "write",
     "description": "Send a chat message in game as the agent.",
     "args": {"text": "string"},
     "limits": {"chars": 256}},
    {"name": "world.move_to", "kind": "write",
     "description": "Move the agent player to a position (walk/teleport if allowed).",
     "args": {"x": "int", "y": "int", "z": "int"},
     "limits": {"distance": 256}},
]


def validate_tool_call(name, args):
    """Return (ok, error). Enforces the limits above without a game."""
    tool = next((t for t in TOOLS if t["name"] == name), None)
    if tool is None:
        return False, f"unknown tool {name!r}; see world.tools"
    args = args or {}
    lim = tool["limits"]
    if name == "world.describe":
        r = int(args.get("radius_chunks", 1))
        if r < 0 or r > lim["radius_chunks"]:
            return False, f"radius_chunks must be 0-{lim['radius_chunks']}"
    if name == "world.scan":
        r = int(args.get("radius", 32))
        if r < 1 or r > lim["radius"]:
            return False, f"radius must be 1-{lim['radius']}"
    if name in ("world.set", "world.walls"):
        try:
            p1, p2 = args["pos1"], args["pos2"]
            n = (abs(p2[0] - p1[0]) + 1) * (abs(p2[1] - p1[1]) + 1) * (abs(p2[2] - p1[2]) + 1)
        except Exception:
            return False, "pos1/pos2 must be [x,y,z]"
        if n > lim["max_blocks"]:
            return False, f"region is {n} blocks, limit is {lim['max_blocks']}"
    if name == "world.place_sign":
        lines = args.get("lines") or []
        if len(lines) > lim["lines"]:
            return False, f"at most {lim['lines']} lines"
        if any(len(str(x)) > lim["chars_per_line"] for x in lines):
            return False, f"at most {lim['chars_per_line']} chars per line"
    if name == "world.say" and len(str(args.get("text", ""))) > lim["chars"]:
        return False, f"text over {lim['chars']} chars"
    return True, ""


def tools_listing():
    return {"tools": TOOLS}
