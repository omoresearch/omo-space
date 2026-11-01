#!/usr/bin/env python3
"""Survey an Omo Space save before building (read-only).

  python3 omo_scan.py stations X Y Z [R=40] [DY=12]   stations and monitors near a point, with their
                                                      binding (Workspace) or preset (OmoPreset)
  python3 omo_scan.py web X Y Z [R=64]                web walls whose pages a player at X Y Z keeps open
  python3 omo_scan.py player                          where each player was at the last save

Set OMO_SAVE=/path/to/saves/<world> to pick the save (default: the most recently played Omo world).
The save lags the game by up to one autosave; bridge.log has the live station bindings.
"""
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import anvil  # noqa: E402
import nbtlib  # noqa: E402

MAX_PAGES = 12      # client limit (WebPages.MAX_PAGES): the 13th wall in range shows "Page limit reached"
WEB_RANGE = 64      # blocks within which the client keeps a wall's page open


def omolife_entities(x0, z0, r):
    for cx in range((x0 - r) >> 4, ((x0 + r) >> 4) + 1):
        for cz in range((z0 - r) >> 4, ((z0 + r) >> 4) + 1):
            for be in anvil.block_entities(cx, cz):
                if str(be.get("id", "")).startswith("omolife:"):
                    yield be


def stations(x, y, z, r=40, dy=12):
    keys = ("Workspace", "WorkspaceName", "On", "OmoSource", "OmoUrl", "OmoPreset")
    rows = []
    for be in omolife_entities(x, z, r):
        bx, by, bz = int(be["x"]), int(be["y"]), int(be["z"])
        if abs(by - y) <= dy and abs(bx - x) <= r and abs(bz - z) <= r:
            rows.append((str(be["id"]), bx, by, bz, {k: str(be[k])[:70] for k in keys if k in be}))
    for row in sorted(rows, key=lambda t: (t[0], t[2], t[1], t[3])):
        print(*row)
    print(f"{len(rows)} omolife block entities within {r} blocks")


def web_walls(x, y, z, r=WEB_RANGE):
    """Web walls = connected groups of monitor panels with OmoSource web. Groups, not builds: players
    re-point any wall to a URL, so count what is in the save."""
    cells = {}
    for be in omolife_entities(x, z, r + 16):
        if str(be["id"]) == "omolife:monitor" and str(be.get("OmoSource", "")) == "web":
            cells[(int(be["x"]), int(be["y"]), int(be["z"]))] = str(be.get("OmoUrl", ""))
    seen, walls = set(), []
    for c in cells:
        if c in seen:
            continue
        stack, group = [c], []
        seen.add(c)
        while stack:
            p = stack.pop()
            group.append(p)
            for d in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)):
                q = (p[0] + d[0], p[1] + d[1], p[2] + d[2])
                if q in cells and q not in seen:
                    seen.add(q)
                    stack.append(q)
        near = min(((gx - x) ** 2 + (gy - y) ** 2 + (gz - z) ** 2) ** 0.5 for gx, gy, gz in group)
        if near <= r:
            walls.append((round(near), len(group), cells[group[0]], min(group)))
    for near, n, url, origin in sorted(walls):
        print(f"{near:4d} blocks  {n:3d} panels  at {origin}  {url}")
    verdict = "OK" if len(walls) <= MAX_PAGES else f"OVER: {len(walls) - MAX_PAGES} wall(s) will show 'Page limit reached'"
    print(f"{len(walls)} web walls within {r} blocks of ({x}, {y}, {z}); limit {MAX_PAGES}: {verdict}")


def players():
    for f in sorted(glob.glob(os.path.join(anvil.SAVE, "players/data/*.dat")), key=os.path.getmtime, reverse=True):
        try:
            pos = [round(float(v), 1) for v in nbtlib.load(f)["Pos"]]
        except Exception:  # noqa: BLE001  (bots and old files)
            continue
        print(os.path.basename(f)[:-4], pos)


def main(argv):
    if not anvil.SAVE:
        sys.exit("no save found: set OMO_SAVE=/path/to/saves/<world>")
    print(f"save: {anvil.SAVE}", file=sys.stderr)
    if len(argv) >= 4 and argv[0] in ("stations", "web"):
        nums = [int(float(a)) for a in argv[1:]]
        (stations if argv[0] == "stations" else web_walls)(*nums)
    elif argv[:1] == ["player"]:
        players()
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
