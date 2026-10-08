#!/usr/bin/env python3
"""Write the Omo room kits: structure templates builders place with
`/place template omolife:<kit>`, a structure block, or `/omo kit <kit>`.

Every kit is drawn facing south: the viewer stands on the +z side and looks
north at it; x runs to the viewer's right, y up, and y 0 is the first layer
above the floor the kit stands on. Unset cells are air, so a kit clears its
own box when placed.

Stations carry an OmoPreset (CONTRACT P): a generic name, a kind, tabs; never
a workspace id, station id or computer id, so each player's copy makes its own
workspace on first use. Monitor walls are stored already joined (CC:T's
XIndex/YIndex/Width/Height), so they are one wall however the kit is placed
or rotated (not mirrored). Web walls carry their pinned page.

  python3 mod/tools/make_kits.py
"""
import gzip
import struct
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "src/main/resources/data/omolife/structure"
DATA_VERSION = 4903                       # Minecraft 26.2

BOARD = "http://127.0.0.1:8780/board"
DOCS = "https://hermes-agent.nousresearch.com/docs/"


# --------------------------------------------------------------------------- NBT
class Byte(int):
    pass


class Double(float):
    pass


class IntArray(list):
    pass


def _payload(v):
    """(tag id, bytes) for a Python value: bool/Byte byte, int int, float/Double double, str,
    IntArray int array, list, dict."""
    if isinstance(v, (bool, Byte)):
        return 1, struct.pack(">b", int(v))
    if isinstance(v, int):
        return 3, struct.pack(">i", v)
    if isinstance(v, float):
        return 6, struct.pack(">d", v)
    if isinstance(v, str):
        b = v.encode("utf-8")
        return 8, struct.pack(">H", len(b)) + b
    if isinstance(v, IntArray):
        return 11, struct.pack(f">i{len(v)}i", len(v), *v)
    if isinstance(v, list):
        items = [_payload(x) for x in v]
        kind = items[0][0] if items else 0
        assert all(k == kind for k, _ in items), "NBT lists hold one type"
        return 9, struct.pack(">bi", kind, len(items)) + b"".join(p for _, p in items)
    if isinstance(v, dict):
        out = b""
        for k, x in v.items():
            kind, p = _payload(x)
            name = k.encode("utf-8")
            out += struct.pack(">bH", kind, len(name)) + name + p
        return 10, out + b"\x00"
    raise TypeError(type(v))


def write_nbt(path, root):
    _, body = _payload(root)
    raw = b"\x0a\x00\x00" + body                     # unnamed root compound
    path.write_bytes(gzip.compress(raw, mtime=0))


# --------------------------------------------------------------------------- kits
def block(spec):
    """'minecraft:lantern[hanging=true]' -> palette entry."""
    name, _, props = spec.partition("[")
    entry = {"Name": name if ":" in name else "minecraft:" + name}
    if props:
        entry["Properties"] = dict(p.split("=") for p in props.rstrip("]").split(","))
    return entry


class Kit:
    def __init__(self, name, w, h, d):
        self.name, self.size = name, (w, h, d)
        self.cells = {}                              # (x, y, z) -> (spec, nbt)
        self.entities = []

    def put(self, x, y, z, spec, nbt=None):
        w, h, d = self.size
        assert 0 <= x < w and 0 <= y < h and 0 <= z < d, (self.name, x, y, z)
        self.cells[(x, y, z)] = (spec, nbt)

    def fill(self, x1, y1, z1, x2, y2, z2, spec):
        for x in range(x1, x2 + 1):
            for y in range(y1, y2 + 1):
                for z in range(z1, z2 + 1):
                    self.put(x, y, z, spec)

    def station(self, x, y, z, hermes, preset, signal=False):
        nbt = {"id": "omolife:station", "OmoPreset": preset}
        if signal:
            nbt["OmoSignal"] = True
        self.put(x, y, z, f"omolife:{'hermes_computer' if hermes else 'terminal'}[facing=south]", nbt)

    def wall(self, x0, y0, z, w, h, url=None):
        """A joined w x h monitor wall facing south, bottom-left panel at (x0, y0)."""
        for i in range(w):
            for j in range(h):
                edges = "".join(c for c, on in (("l", i > 0), ("r", i < w - 1), ("u", j < h - 1), ("d", j > 0))
                                if on) or "none"
                nbt = {"id": "omolife:monitor", "XIndex": i, "YIndex": j, "Width": w, "Height": h}
                if url:
                    nbt.update({"OmoSource": "web", "OmoUrl": url, "OmoZoom": 100})
                self.put(x0 + i, y0 + j, z, f"omolife:monitor[facing=south,orientation=north,state={edges}]", nbt)

    def sign(self, x, y, z, *lines):
        text = {"messages": [{"text": t} if isinstance(t, str) else t for t in (list(lines) + [""] * 4)[:4]],
                "color": "black", "has_glowing_text": False}
        self.put(x, y, z, "dark_oak_wall_sign[facing=south]",
                 {"id": "minecraft:sign", "front_text": text, "back_text": text, "is_waxed": True})

    def frame(self, x, y, z, item):
        """A glow item frame on the south face of the block behind (x, y, z - 1)."""
        self.entities.append({"pos": [Double(x + 0.5), Double(y + 0.5), Double(z + 0.03125)],
                              "blockPos": [x, y, z],
                              "nbt": {"id": "minecraft:glow_item_frame", "Facing": Byte(3), "Fixed": True,
                                      "block_pos": IntArray([x, y, z]), "Item": item}})

    def save(self):
        w, h, d = self.size
        palette, index, blocks = [], {}, []
        for x in range(w):
            for y in range(h):
                for z in range(d):
                    spec, nbt = self.cells.get((x, y, z), ("air", None))
                    if spec not in index:
                        index[spec] = len(palette)
                        palette.append(block(spec))
                    entry = {"pos": [x, y, z], "state": index[spec]}
                    if nbt:
                        entry["nbt"] = nbt
                    blocks.append(entry)
        OUT.mkdir(parents=True, exist_ok=True)
        write_nbt(OUT / f"{self.name}.nbt", {"DataVersion": DATA_VERSION, "size": [w, h, d],
                                             "palette": palette, "blocks": blocks, "entities": self.entities})
        print(f"{self.name}: {w}x{h}x{d}, {len(palette)} block states, {len(self.entities)} entities")


def pocket(hermes, name):
    return {"id": f"omolife:pocket_{'hermes' if hermes else 'terminal'}", "count": 1,
            "components": {"minecraft:custom_data": {"OmoPreset": {"Name": name,
                                                                   "Kind": "hermes" if hermes else "terminal"}}}}


def hermes_desk():
    """A Hermes Computer at a dark-oak desk in front of its 3x2 terminal wall; the
    redstone lamp beside it lights while Hermes works, the name plate says whose desk it is."""
    k = Kit("hermes_desk", 5, 4, 3)
    k.fill(0, 0, 0, 4, 3, 0, "smooth_quartz")
    k.fill(0, 0, 1, 4, 0, 2, "stripped_dark_oak_log[axis=x]")
    k.wall(1, 1, 1, 3, 2)
    k.station(2, 1, 2, True, {"Name": "Hermes Desk", "Kind": "hermes"}, signal=True)
    k.put(3, 1, 2, "redstone_lamp")
    k.put(1, 1, 2, "potted_azalea_bush")
    k.put(0, 1, 1, "lantern")
    k.put(4, 1, 1, "potted_fern")
    k.put(4, 1, 2, "omolife:name_plate[face=floor,facing=south]")
    return k


def terminal_nook():
    """A Terminal tucked between bookshelves under a low spruce ceiling."""
    k = Kit("terminal_nook", 5, 4, 3)
    k.fill(0, 0, 0, 4, 3, 0, "spruce_planks")
    for x in (0, 4):
        k.fill(x, 0, 1, x, 2, 2, "bookshelf")
        k.fill(x, 3, 1, x, 3, 2, "spruce_planks")
    k.fill(1, 3, 1, 3, 3, 2, "spruce_slab[type=bottom]")    # a full underside, so lanterns can hang
    k.fill(1, 0, 1, 3, 0, 1, "spruce_slab[type=top]")
    k.station(2, 1, 1, False, {"Name": "Terminal Nook", "Kind": "terminal",
                               "Tabs": [{"Name": "Shell", "Kind": "shell"}, {"Name": "Logs", "Kind": "shell"}]})
    k.put(1, 1, 1, "candle[candles=3,lit=true]")
    k.put(3, 1, 1, "potted_fern")
    k.put(1, 2, 2, "lantern[hanging=true]")
    k.put(3, 2, 2, "lantern[hanging=true]")
    k.put(2, 2, 1, "omolife:name_plate[face=wall,facing=south]")
    return k


def web_gallery():
    """Two 3x2 web walls, the Hermes Board and the Hermes docs, with name plates."""
    k = Kit("web_gallery", 9, 4, 3)
    k.fill(0, 0, 0, 8, 3, 0, "smooth_quartz")
    k.fill(0, 3, 0, 8, 3, 0, "quartz_bricks")
    k.wall(1, 1, 1, 3, 2, BOARD)
    k.wall(5, 1, 1, 3, 2, DOCS)
    k.sign(2, 0, 1, "", {"text": "Hermes Board", "color": "gold", "bold": True}, "every workspace, live")
    k.sign(6, 0, 1, "", {"text": "Hermes Docs", "color": "gold", "bold": True}, "pinned page")
    k.put(4, 0, 1, "potted_azalea_bush")
    for x in (0, 8):
        k.put(x, 0, 1, "lantern")
    k.fill(3, 0, 2, 5, 0, 2, "dark_oak_slab[type=bottom]")
    return k


def meeting_room():
    """Three walls, a 5x3 wall on a Hermes Computer, a table for six with a Status Lamp."""
    k = Kit("meeting_room", 9, 5, 8)
    k.fill(0, 0, 0, 8, 4, 0, "smooth_quartz")
    for x in (0, 8):
        k.fill(x, 0, 1, x, 4, 7, "smooth_quartz")
        k.fill(x, 1, 2, x, 2, 5, "glass_pane[north=true,south=true]")
    k.fill(0, 4, 0, 8, 4, 0, "quartz_bricks")
    k.wall(2, 2, 1, 5, 3)
    k.fill(2, 0, 1, 6, 0, 1, "dark_oak_slab[type=top]")
    k.put(4, 0, 1, "dark_oak_planks")
    k.station(4, 1, 1, True, {"Name": "Meeting Room", "Kind": "hermes",
                              "Tabs": [{"Name": "Hermes", "Kind": "hermes"}, {"Name": "Notes", "Kind": "shell"}]})
    k.put(3, 1, 1, "potted_flowering_azalea_bush")
    k.put(5, 1, 1, "potted_flowering_azalea_bush")
    k.fill(3, 0, 3, 5, 0, 5, "dark_oak_slab[type=top]")
    for z in (3, 4, 5):
        k.put(2, 0, z, "dark_oak_stairs[facing=west]")
        k.put(6, 0, z, "dark_oak_stairs[facing=east]")
    k.put(4, 1, 4, "omolife:status_lamp")
    for z in (1, 6):
        k.put(1, 3, z, "lantern")
        k.put(7, 3, z, "lantern")
        k.put(1, 2, z, "quartz_pillar")
        k.put(7, 2, z, "quartz_pillar")
    return k


def pocket_stand():
    """A shop-front for Omo Pockets: two on show, more in the barrel."""
    k = Kit("pocket_stand", 3, 3, 2)
    k.fill(0, 0, 0, 2, 2, 0, "spruce_planks")
    k.put(1, 0, 1, "barrel[facing=up]", {"id": "minecraft:barrel", "Items": [
        dict(pocket(True, "Field Notes"), Slot=Byte(0)), dict(pocket(False, "Pocket Shell"), Slot=Byte(1))]})
    k.put(0, 0, 1, "potted_fern")
    k.put(2, 0, 1, "lantern")
    k.frame(0, 1, 1, pocket(True, "Field Notes"))
    k.frame(2, 1, 1, pocket(False, "Pocket Shell"))
    k.sign(1, 1, 1, {"text": "Omo Pockets", "color": "gold", "bold": True}, "take one from", "the barrel", "")
    return k


if __name__ == "__main__":
    for make in (hermes_desk, terminal_nook, web_gallery, meeting_room, pocket_stand):
        make().save()
