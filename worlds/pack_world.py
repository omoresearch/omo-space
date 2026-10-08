#!/usr/bin/env python3
"""Pack a built save for the repo: copy it, drop what belongs to one player or
one machine, and keep only the chunks around the village.

    python3 worlds/pack_world.py SAVE "worlds/My World" [x0 z0 x1 z1]

Dropped: players/, stats/, advancements/, session.lock, level.dat_old, the owner's account id in level.dat, the
old mcterm datapack (it carried a token), Distant Horizons' LOD cache (stale
after edits; it rebuilds), serverconfig/ (a per-world CC:T config would
override the launcher's bridge-port HTTP rule) and every chunk outside the box
(default: 400 blocks around the lake; the game regenerates them on demand).
"""
import os
import shutil
import struct
import sys

DROP = ("players", "stats", "advancements", "session.lock", "level.dat_old", "datapacks/mcterm",
        "dimensions/minecraft/overworld/data/DistantHorizons.sqlite", "serverconfig", "computercraft")


def prune(path, box):
    """Rewrite one region file with only the chunks inside box (in chunks)."""
    cx0, cz0, cx1, cz1 = box
    rx, rz = (int(v) for v in os.path.basename(path).split(".")[1:3])
    data = open(path, "rb").read()
    if len(data) < 8192:
        return
    header, stamps, body = bytearray(4096), bytearray(data[4096:8192]), bytearray()
    for i in range(1024):
        off = struct.unpack(">I", b"\0" + data[i * 4:i * 4 + 3])[0]
        if not off:
            continue
        if not (cx0 <= rx * 32 + (i & 31) <= cx1 and cz0 <= rz * 32 + (i >> 5) <= cz1):
            stamps[i * 4:i * 4 + 4] = bytes(4)
            continue
        start = off * 4096
        chunk = data[start:start + 4 + struct.unpack(">I", data[start:start + 4])[0]]
        sectors = (len(chunk) + 4095) // 4096
        header[i * 4:i * 4 + 4] = struct.pack(">I", ((2 + len(body) // 4096) << 8) | sectors)
        body += chunk + bytes(sectors * 4096 - len(chunk))
    if body:
        open(path, "wb").write(bytes(header) + bytes(stamps) + bytes(body))
    else:
        os.remove(path)


def strip_owner(level_dat):
    """Remove the owner's Minecraft account id (Data.singleplayer_uuid) from level.dat.

    The id is an IntArray tag; it is found in the gzip'd NBT and the tag (type 11,
    name, length, 4 ints) is cut out, so no NBT library is needed."""
    import gzip
    raw = gzip.decompress(open(level_dat, "rb").read())
    name = b"singleplayer_uuid"
    tag = b"\x0b" + struct.pack(">H", len(name)) + name
    i = raw.find(tag)
    if i < 0:
        return False
    n = struct.unpack(">i", raw[i + len(tag):i + len(tag) + 4])[0]
    raw = raw[:i] + raw[i + len(tag) + 4 + 4 * n:]
    open(level_dat, "wb").write(gzip.compress(raw))
    return True


def main(src, dst, x0=-360, z0=-560, x1=440, z1=240):
    shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(src, dst)
    for rel in DROP:
        p = os.path.join(dst, rel)
        if os.path.isdir(p):
            shutil.rmtree(p)
        elif os.path.exists(p):
            os.remove(p)
    if os.path.exists(os.path.join(dst, "level.dat")):
        strip_owner(os.path.join(dst, "level.dat"))
    for kind in ("region", "entities", "poi"):
        d = os.path.join(dst, "dimensions/minecraft/overworld", kind)
        for f in sorted(os.listdir(d)) if os.path.isdir(d) else ():
            if f.endswith(".mca"):
                prune(os.path.join(d, f), (x0 >> 4, z0 >> 4, x1 >> 4, z1 >> 4))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], *(int(v) for v in sys.argv[3:7]))
