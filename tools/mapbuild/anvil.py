"""Read-only Anvil region reader for Minecraft 26.2 saves (overworld). Never writes the save.

Point it at a save with OMO_SAVE=/path/to/saves/<world> (or call use_save(path)). Without it, it
picks the most recently played world under the Omo launcher's saves folder.

    import anvil
    anvil.block(10, 64, -3)        # 'minecraft:oak_planks[...]', 'minecraft:air', or None (not generated)
    anvil.surface(10, -3)          # (y, name) of the ground, ignoring trees and plants
    anvil.block_entities(cx, cz)   # raw block entity compounds of one chunk
"""
import glob
import io
import math
import os
import struct
import zlib

import nbtlib

DEFAULT_SAVES = os.path.expanduser("~/.local/share/omo/runtime/game/saves")


def _newest_save():
    worlds = [d for d in glob.glob(os.path.join(DEFAULT_SAVES, "*")) if os.path.isfile(os.path.join(d, "level.dat"))]
    return max(worlds, key=lambda d: os.path.getmtime(os.path.join(d, "level.dat"))) if worlds else ""


SAVE = os.environ.get("OMO_SAVE") or _newest_save()
REG = os.path.join(SAVE, "dimensions/minecraft/overworld/region")

_cache = {}
_sec_cache = {}


def use_save(path):
    """Read another save (clears the caches). Also accepts a test copy or a backup extract."""
    global SAVE, REG
    SAVE, REG = path, os.path.join(path, "dimensions/minecraft/overworld/region")
    _cache.clear()
    _sec_cache.clear()


def _load_chunk(cx, cz):
    key = (REG, cx, cz)
    if key in _cache:
        return _cache[key]
    path = os.path.join(REG, f"r.{cx >> 5}.{cz >> 5}.mca")   # region = block >> 9 = chunk >> 5
    res = None
    if os.path.exists(path):
        with open(path, "rb") as f:
            data = f.read()
        idx = 4 * ((cx & 31) + (cz & 31) * 32)
        off = int.from_bytes(data[idx:idx + 3], "big")
        if off:
            o = off * 4096
            ln = struct.unpack(">I", data[o:o + 4])[0]
            comp = data[o + 4]
            raw = data[o + 5:o + 4 + ln]
            if comp == 2:
                raw = zlib.decompress(raw)
            res = nbtlib.File.parse(io.BytesIO(raw))
    _cache[key] = res
    return res


def _section(cx, cz, sy):
    key = (REG, cx, cz, sy)
    if key in _sec_cache:
        return _sec_cache[key]
    ch = _load_chunk(cx, cz)
    out = None
    if ch is not None:
        for s in ch.get("sections", []):
            if int(s["Y"]) == sy:
                bs = s.get("block_states")
                if bs is None:
                    break
                pal = []
                for p in bs["palette"]:
                    name = str(p["Name"])
                    props = p.get("Properties")
                    if props:
                        name += "[" + ",".join(f"{k}={v}" for k, v in sorted(props.items())) + "]"
                    pal.append(name)
                if "data" not in bs:
                    out = (pal, None, 0)
                else:
                    bits = max(4, math.ceil(math.log2(len(pal))))
                    out = (pal, [int(x) & 0xFFFFFFFFFFFFFFFF for x in bs["data"]], bits)
                break
    _sec_cache[key] = out
    return out


def block(x, y, z):
    """Full block state string, or None if the chunk was never generated."""
    cx, cz, sy = x >> 4, z >> 4, y >> 4
    sec = _section(cx, cz, sy)
    if sec is None:
        return None if _load_chunk(cx, cz) is None else "minecraft:air"
    pal, data, bits = sec
    if data is None:
        return pal[0]
    i = ((y & 15) * 16 + (z & 15)) * 16 + (x & 15)
    per = 64 // bits
    return pal[(data[i // per] >> ((i % per) * bits)) & ((1 << bits) - 1)]


def name(state):
    """'minecraft:oak_stairs[facing=east]' -> 'oak_stairs'. Compare names, never substrings of states:
    'waterlogged=false' contains 'water'."""
    return state.split("[")[0].split(":")[-1] if state else None


def block_entities(cx, cz):
    ch = _load_chunk(cx, cz)
    return list(ch.get("block_entities", [])) if ch is not None else []


def chunk_status(cx, cz):
    ch = _load_chunk(cx, cz)
    return None if ch is None else str(ch.get("Status"))


SOFT = ("air", "cave_air", "void_air", "snow", "short_grass", "tall_grass", "fern", "large_fern", "leaf_litter",
        "vine", "dead_bush", "sweet_berry_bush")


def surface(x, z, top=320, bottom=-64):
    """(y, name) of the highest real ground block: skips air, plants, leaves and logs (chunk heightmaps
    count tree tops, which puts a ceiling in the canopy)."""
    for y in range(top, bottom, -1):
        b = block(x, y, z)
        if b is None:
            return None
        n = name(b) or ""
        if n in SOFT or n.endswith("leaves") or n.endswith("_log") or "flower" in n or n.endswith("sapling"):
            continue
        return y, n
    return None
