#!/usr/bin/env python3
"""Write the builder blocks' block states, block models and item models.

The Slim Screen follows CC:T's monitor: one model per edge state (which
sides join another screen), turned by facing and orientation exactly as CC:T
turns its monitors, and tinted by the frame finish (tintindex 0). The Name
Plate has a model per attachment (wall, floor, ceiling); the Status Lamp one
per glow, standing or hanging, and its lit paper glows (light_emission).

  python3 mod/tools/make_models.py      (textures: make_textures.py)
"""
import json
from pathlib import Path

ASSETS = Path(__file__).resolve().parent.parent / "src/main/resources/assets/omolife"
SLIM_STATES = ["none", "l", "r", "lr", "u", "d", "ud", "rd", "ld", "ru", "lu", "lrd", "rud", "lud", "lru", "lrud"]
FINISHES = {"cream": 0xFBF7EE, "espresso": 0x4A3A2F, "gold": 0xE8B54E, "copper": 0xD07A48,
            "black": 0x2C2927, "white": 0xFFFFFF}                    # as FrameFinish
GLOWS = ["off", "idle", "working", "interrupted"]
Y_ROT = {"north": 0, "east": 90, "south": 180, "west": 270}


def dumps(value, indent=""):
    """JSON with anything that fits on a line on one line, so a model reads element by element."""
    inline = json.dumps(value)
    if len(indent) + len(inline) <= 110 or not isinstance(value, (dict, list)):
        return inline
    inner = indent + " "
    items = ([f"{inner}{json.dumps(k)}: {dumps(v, inner)}" for k, v in value.items()] if isinstance(value, dict)
             else [inner + dumps(v, inner) for v in value])
    ends = "{}" if isinstance(value, dict) else "[]"
    return ends[0] + "\n" + ",\n".join(items) + "\n" + indent + ends[1]


def write(rel, data):
    path = ASSETS / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps(data) + "\n")


def face(tex, uv, cull=None, tint=False):
    f = {"uv": uv, "texture": tex}
    if cull:
        f["cullface"] = cull
    if tint:
        f["tintindex"] = 0
    return f


def box(frm, to, tex, uvs=None, cull=None, **extra):
    """An element with all six faces on `tex`; uv defaults to the box's own extent."""
    x0, y0, z0 = frm
    x1, y1, z1 = to
    auto = {"north": [16 - x1, 16 - y1, 16 - x0, 16 - y0], "south": [x0, 16 - y1, x1, 16 - y0],
            "west": [z0, 16 - y1, z1, 16 - y0], "east": [16 - z1, 16 - y1, 16 - z0, 16 - y0],
            "up": [x0, z0, x1, z1], "down": [x0, 16 - z1, x1, 16 - z0]}
    faces = {d: face(tex, (uvs or {}).get(d, auto[d]), (cull or {}).get(d)) for d in auto}
    return {"from": frm, "to": to, "faces": faces, **extra}


# --------------------------------------------------------------------------- Slim Screen
def slim_screen():
    edge = "#edge"
    # The bezel is the cut-out front of the panel; the glass is a face a
    # quarter pixel behind it, so the screen drawn on the glass never fights it.
    base = {"parent": "block/block", "textures": {"particle": "#edge", "glass": "omolife:block/slim_screen_glass"},
            # In inventories it shows its face, turned a little, like a picture of a TV.
            "display": {"gui": {"rotation": [15, 200, 0], "translation": [0, 0, 0], "scale": [0.75, 0.75, 0.75]},
                        "fixed": {"rotation": [0, 180, 0], "translation": [0, 0, -3], "scale": [0.6, 0.6, 0.6]}},
            "elements": [{"from": [0, 0, 13.25], "to": [16, 16, 16],
                          "faces": {"north": face("#glass", [0, 0, 16, 16])}}, {
        "from": [0, 0, 13], "to": [16, 16, 16],
        "faces": {"north": face("#front", [0, 0, 16, 16], tint=True),
                  "south": face(edge, [0, 0, 16, 16], "south", True),
                  "east": face(edge, [0, 0, 3, 16], tint=True), "west": face(edge, [13, 0, 16, 16], tint=True),
                  "up": face(edge, [0, 13, 16, 16], tint=True), "down": face(edge, [0, 0, 16, 3], tint=True)}}]}
    write("models/block/slim_screen_base.json", base)
    for s in SLIM_STATES:
        write(f"models/block/slim_screen_{s}.json", {"parent": "omolife:block/slim_screen_base", "textures": {
            "front": f"omolife:block/slim_screen_{s}", "edge": "omolife:block/slim_screen_edge"}})
    variants = {}
    for facing, y in Y_ROT.items():
        for orientation, x in (("north", 0), ("up", 270), ("down", 90)):
            for s in SLIM_STATES:
                v = {"model": f"omolife:block/slim_screen_{s}"}
                if x:
                    v["x"] = x
                if y:
                    v["y"] = y
                variants[f"facing={facing},orientation={orientation},state={s}"] = v
    write("blockstates/slim_screen.json", {"variants": variants})
    # The item wears its finish: the block_state component picks the tint.
    def tinted(colour):
        return {"type": "minecraft:model", "model": "omolife:block/slim_screen_none",
                "tints": [{"type": "minecraft:constant", "value": 0xFF000000 | colour}]}
    write("items/slim_screen.json", {"model": {
        "type": "minecraft:select", "property": "minecraft:block_state", "block_state_property": "finish",
        "cases": [{"when": f, "model": tinted(c)} for f, c in FINISHES.items()],
        "fallback": tinted(FINISHES["cream"])}})


# --------------------------------------------------------------------------- Name Plate
def name_plate():
    t = "#plate"
    front = [1, 5, 15, 11]
    rim = {"up": [1, 5, 15, 6], "down": [1, 10, 15, 11], "east": [14, 5, 15, 11], "west": [1, 5, 2, 11],
           "south": [1, 0, 15, 6]}
    def plaque(y0, z0):
        return box([1, y0, z0], [15, y0 + 6, z0 + 1], t, {"north": front, **rim})
    wood = {d: [3, 12, 13, 16] for d in ("north", "south", "east", "west", "up", "down")}
    models = {
        "wall": [box([1, 5, 15], [15, 11, 16], t, {"north": front, **rim}, {"south": "south"})],
        "floor": [plaque(1, 7.5), box([3, 0, 6], [13, 1, 10], t, wood, {"down": "down"})],
        "ceiling": [plaque(8, 7.5), box([3.5, 14, 7.75], [4, 16, 8.25], t, wood, {"up": "up"}),
                    box([12, 14, 7.75], [12.5, 16, 8.25], t, wood, {"up": "up"})],
    }
    for name, elements in models.items():
        write(f"models/block/name_plate_{name}.json", {"parent": "block/block", "textures": {
            "plate": "omolife:block/name_plate", "particle": "omolife:block/name_plate"}, "elements": elements})
    variants = {}
    for facing, y in Y_ROT.items():
        variants[f"face=wall,facing={facing}"] = {"model": "omolife:block/name_plate_wall", **({"y": y} if y else {})}
        # On a desk or a ceiling the plate faces whoever placed it: away from `facing`.
        yf = (y + 180) % 360
        for f in ("floor", "ceiling"):
            variants[f"face={f},facing={facing}"] = {"model": f"omolife:block/name_plate_{f}", **({"y": yf} if yf else {})}
    write("blockstates/name_plate.json", {"variants": variants})
    write("items/name_plate.json", {"model": {"type": "minecraft:model", "model": "omolife:item/name_plate"}})
    write("models/item/name_plate.json", {"parent": "minecraft:item/generated",
                                          "textures": {"layer0": "omolife:item/name_plate"}})


# --------------------------------------------------------------------------- Status Lamp
def status_lamp():
    wood, paper = "#wood", "#paper"
    for glow in GLOWS:
        lit = glow != "off"
        for hanging in (False, True):
            dy = 2 if hanging else 0
            def at(y0, y1):
                return y0 + dy, y1 + dy
            b0, b1 = at(0, 1)
            p0, p1 = at(1, 11)
            c0, c1 = at(11, 12)
            f0, f1 = at(12, 13)
            pane = {d: [4, 3, 12, 13] for d in ("north", "south", "east", "west")}
            lamp = box([4, p0, 4], [12, p1, 12], paper, pane)
            del lamp["faces"]["up"], lamp["faces"]["down"]
            if lit:
                lamp["light_emission"] = 15
                lamp["shade"] = False
            elements = [box([3, b0, 3], [13, b1, 13], wood, cull=None if hanging else {"down": "down"}), lamp]
            elements += [box([x, p0, z], [x + 1, p1, z + 1], wood) for x in (3, 12) for z in (3, 12)]
            elements += [box([3, c0, 3], [13, c1, 13], wood), box([6, f0, 6], [10, f1, 10], wood)]
            if hanging:
                elements.append(box([7.5, 15, 7.5], [8.5, 16, 8.5], wood, cull={"up": "up"}))
            name = f"status_lamp{'_hanging' if hanging else ''}_{glow}"
            write(f"models/block/{name}.json", {"parent": "block/block", "display": {
                "gui": {"rotation": [30, 225, 0], "translation": [0, 1, 0], "scale": [0.8, 0.8, 0.8]}}, "textures": {
                "wood": "omolife:block/status_lamp_wood", "paper": f"omolife:block/status_lamp_{glow}",
                "particle": "omolife:block/status_lamp_wood"}, "elements": elements})
    write("blockstates/status_lamp.json", {"variants": {
        f"glow={g},hanging={str(h).lower()}": {"model": f"omolife:block/status_lamp{'_hanging' if h else ''}_{g}"}
        for g in GLOWS for h in (False, True)}})
    write("items/status_lamp.json", {"model": {"type": "minecraft:model", "model": "omolife:block/status_lamp_working"}})


if __name__ == "__main__":
    slim_screen()
    name_plate()
    status_lamp()
    print(f"wrote builder block models to {ASSETS}")
