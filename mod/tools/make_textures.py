#!/usr/bin/env python3
"""Draw the Omo textures: Hermes Computer, Terminal, Omo Monitor, Omo Pocket.

The palette is the launcher's: warm cream and espresso, with gold for Hermes
and copper for the plain Terminal. Pixel art is written as character grids so
a texture can be read (and fixed) in this file; the PNGs are 16x16 RGBA made
with zlib/struct alone, no image library.

The screen must stay at x 2..13, y 2..11: CC:T overlays its blinking cursor
(computer_on / computer_blink) at fixed pixels inside that rectangle.

Monitors are CC:T's advanced-monitor tiles (connected textures) recoloured:
their warm frame ramp is mapped onto the cream ramp, greys are left alone.

Pockets are items (16x16, transparent around the device): an "off" face and
an "on" face whose screen shows a prompt, in the Hermes and Terminal palettes.

Builder blocks: the Slim Screen (grey; its frame finish tints it), the Name
Plate and the Status Lamp. Their models come from make_models.py.

  python3 mod/tools/make_textures.py
"""
import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "src/main/resources/assets/omolife/textures/block"
ITEMS = OUT.parent / "item"


def hexrgb(h):
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5)) + (255,)


def png(path, rows):
    raw = b"".join(b"\0" + bytes(c for px in row for c in px) for row in rows)
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    ihdr = struct.pack(">IIBBBBB", len(rows[0]), len(rows), 8, 6, 0, 0, 0)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, 9))
                     + chunk(b"IEND", b""))


def read_png(path):
    """Minimal reader for the 8-bit RGBA/RGB, non-interlaced PNGs CC:T ships."""
    data = path.read_bytes()
    pos, idat, w = 8, b"", 0
    while pos < len(data):
        n, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + n]
        if kind == b"IHDR":
            w, h, depth, ctype = struct.unpack(">IIBB", body[:10])
            assert depth == 8 and ctype in (2, 6), f"{path}: unsupported PNG"
            bpp = 4 if ctype == 6 else 3
        elif kind == b"IDAT":
            idat += body
        pos += 12 + n
    raw, stride, prev, rows = zlib.decompress(idat), w * bpp, bytearray(w * bpp), []
    for y in range(h):
        f, line = raw[y * (stride + 1)], bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for i in range(stride):
            a = line[i - bpp] if i >= bpp else 0
            b, c = prev[i], prev[i - bpp] if i >= bpp else 0
            if f == 1: line[i] = (line[i] + a) & 255
            elif f == 2: line[i] = (line[i] + b) & 255
            elif f == 3: line[i] = (line[i] + (a + b) // 2) & 255
            elif f == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        rows.append([tuple(line[x * bpp:x * bpp + bpp]) + ((255,) if bpp == 3 else ()) for x in range(w)])
        prev = line
    return rows


def grid(art, palette):
    rows = [r.replace(" ", "") for r in art.strip().splitlines()]
    assert len(rows) == 16 and all(len(r) == 16 for r in rows), "textures are 16x16"
    return [[(0, 0, 0, 0) if ch == "." else hexrgb(palette[ch]) for ch in r] for r in rows]


# --------------------------------------------------------------------------- Hermes Computer
HERMES = {
    "W": "#FBF7EE", "w": "#EFE8D8", "v": "#DDD3BE", "V": "#C4B89E",   # cream marble ramp
    "G": "#F7D774", "g": "#DDA744", "d": "#A8762A",                     # gold
    "k": "#221C17", "s": "#0E1116", "t": "#12221F",                     # bezel, screen, screen glow
    "L": "#6FF0C0",                                                     # ready light
}

HERMES_FRONT = """
W W W W W W W W W W W W W W W w
W g g g g g g g g g g g g g g v
W g s s s s s s s s s s s s d v
W g s s s s s s s s s s s s d v
W g s s s s s s s s s s s s d v
W g s s s s s s s s s s s s d v
W g s s s s s s s s s s s s d v
W g s s s s s s s s s s s s d v
W g s s s s s s s s s s s s d v
W g s s s s s s s s s s s t d v
W g s s s s s s s s s s t t d v
W g s s s s s s s s s s s s d v
W d d d d d d d d d d d d d d v
w w d G G g G G d w w w w w L v
w v w d g g g d w v w w w w v V
v V V V V V V V V V V V V V V V
"""

HERMES_SIDE = """
W W W W W W W W W W W W W W W w
W w w w w w w w w w v w w w w v
W w w w w w w w w w w w w w w v
W w g G G G G G G G G G g w w v
W g G d d d d d d d d g w w w v
W g G G G G G G G G g w w v w v
W w g d d d d d d g w w w w w v
W w w G G G G G g w w w w w w v
W w v w d d d g w w w w w w w v
W w w w w w w w w w w w v w w v
W w w w w w w w w w w w w w w v
W w w w w w w w w w w w w w w v
G G G G G G G G G G G G G G G g
g g g g g g g g g g g g g g g d
w w w w w w w w w w w w w w w v
v V V V V V V V V V V V V V V V
"""

HERMES_TOP = """
W W W W W W W W W W W W W W W w
W g g g g g g g g g g g g g g v
W g w w w w w w w w w w w w d v
W g w w w w w w w w v w w w d v
W g w w k k k k k k k k w w d v
W g w w w w w w w w w w w w d v
W g w v k k k k k k k k w w d v
W g w w w w w w w w w w w w d v
W g w w k k k k k k k k w w d v
W g w w w w w w w w w w v w d v
W g w w k k k k k k k k w w d v
W g w w w w w w w w w w w w d v
W g w w w w v w w w w w w w d v
W g w w w w w w w w w w w w d v
W d d d d d d d d d d d d d d v
v V V V V V V V V V V V V V V V
"""

# --------------------------------------------------------------------------- Terminal
TERMINAL = {
    "W": "#5A4A3E", "w": "#46392F", "v": "#382D25", "V": "#2A211B",   # espresso ramp
    "C": "#F2A06A", "c": "#C8703F", "e": "#8E4A2A",                     # copper
    "k": "#171310", "s": "#0B0F0C", "t": "#142018",
    "L": "#FFB347",                                                     # amber light
}

TERMINAL_FRONT = """
W W W W W W W W W W W W W W W w
W c c c c c c c c c c c c c c v
W c s s s s s s s s s s s s e v
W c s s s s s s s s s s s s e v
W c s s s s s s s s s s s s e v
W c s s s s s s s s s s s s e v
W c s s s s s s s s s s s s e v
W c s s s s s s s s s s s s e v
W c s s s s s s s s s s s s e v
W c s s s s s s s s s s s t e v
W c s s s s s s s s s s t t e v
W c s s s s s s s s s s s s e v
W e e e e e e e e e e e e e e v
w C w w k w k w k w k w w w L v
w w C C w w w w w w w w w w v V
v V V V V V V V V V V V V V V V
"""

TERMINAL_SIDE = """
W W W W W W W W W W W W W W W w
W C w w w w w w w w w w w w C v
W w w w w w w w w w w w w w w v
W w w k w k w k w k w k w w w v
W w w k w k w k w k w k w w w v
W w w k w k w k w k w k w w w v
W w w k w k w k w k w k w w w v
W w w k w k w k w k w k w w w v
W w w k w k w k w k w k w w w v
W w w w w w w w w w w w w w w v
W w w w w w w w w w w w w w w v
W w w w w w w w w w w w w w w v
W c c c c c c c c c c c c c c v
W w w w w w w w w w w w w w w v
W C w w w w w w w w w w w w C v
v V V V V V V V V V V V V V V V
"""

TERMINAL_TOP = """
W W W W W W W W W W W W W W W w
W C w w w w w w w w w w w w C v
W w w w w w w w w w w w w w w v
W w w v v v v v v v v v v w w v
W w w v k k k k k k k k v w w v
W w w v v v v v v v v v v w w v
W w w v k k k k k k k k v w w v
W w w v v v v v v v v v v w w v
W w w v k k k k k k k k v w w v
W w w v v v v v v v v v v w w v
W w w v k k k k k k k k v w w v
W w w v v v v v v v v v v w w v
W w w w w w w w w w w w w w w v
W w w w w w w w w w w w w w w v
W C w w w w w w w w w w w w C v
v V V V V V V V V V V V V V V V
"""


# --------------------------------------------------------------------------- Omo Pocket
# One device, two palettes. The screen (x 4..11, y 3..9) is dark when off;
# on, it shows a prompt in the accent colour. Same letters as the blocks:
# W/w/v/V body ramp, a/A/b the accent ramp, s screen, t glow, L the light.
POCKET_OFF = """
. . . . . . . . . . . . . . . .
. . W W W W W W W W W W W W . .
. . W a a a a a a a a a a v . .
. . W a s s s s s s s s b v . .
. . W a s s s s s s s s b v . .
. . W a s s s s s s s s b v . .
. . W a s s s s s s s s b v . .
. . W a s s s s s s s s b v . .
. . W a s s s s s s s t b v . .
. . W a s s s s s s t t b v . .
. . W b b b b b b b b b b v . .
. . W w w w w w w w w w w v . .
. . W w A a A w w w w L w v . .
. . W w w b w w w w w w w v . .
. . v V V V V V V V V V V V . .
. . . . . . . . . . . . . . . .
"""

# Lit pixels drawn over the off face: a ">" and a cursor.
POCKET_PROMPT = [(5, 4), (6, 5), (5, 6), (7, 6), (8, 6)]

POCKETS = {
    "pocket_hermes": {**HERMES, "a": HERMES["g"], "A": HERMES["G"], "b": HERMES["d"], "p": HERMES["G"]},
    "pocket_terminal": {**TERMINAL, "a": TERMINAL["c"], "A": TERMINAL["C"], "b": TERMINAL["e"], "p": TERMINAL["C"]},
}


def pocket(pal, lit):
    rows = grid(POCKET_OFF, pal)
    if lit:
        for x, y in POCKET_PROMPT:
            rows[y][x] = hexrgb(pal["p"])
    return rows


# --------------------------------------------------------------------------- monitors
CREAM_RAMP = [hexrgb(h) for h in ("#B6A88C", "#CFC3AA", "#E3D9C4", "#F1EADB", "#FBF7EE")]


def recolour_monitor(px):
    r, g, b, a = px
    lum = (r * 3 + g * 6 + b) / 10
    # Only the warm tan frame (luminance ~95..150) changes; grey screens and
    # already-cream pixels are left alone, so running this twice is harmless.
    if a == 0 or not (r - b > 18 and r >= g >= b) or not 90 <= lum <= 155:
        return px
    i = max(0, min(len(CREAM_RAMP) - 1, round((lum - 95) / 55 * (len(CREAM_RAMP) - 1))))
    return CREAM_RAMP[i]


# --------------------------------------------------------------------------- Omo Linker
# A wand, held like a tool: an espresso handle with a copper band, a gold
# head holding the mint "link" light, and a signal arc off its tip.
LINKER_PAL = {
    "k": "#2A211B", "w": "#5A4A3E", "C": "#F2A06A", "c": "#C8703F",   # handle, copper band
    "G": "#F7D774", "g": "#DDA744", "d": "#A8762A",                     # gold head
    "L": "#6FF0C0", "l": "#3FB58C", "W": "#FBF7EE",                     # link light, glint
    "s": "#F3EFE7",                                                     # signal
}

LINKER = """
. . . . . . . . . . . . . . . .
. . . . . . . . . . . s s . . .
. . . . . . . . . . . . . s . .
. . . . . . . . . g G G . . s .
. . . . . . . . g G W L g . s .
. . . . . . . . g L L l g d . .
. . . . . . . . d g l l g d . .
. . . . . . . . c d g g d . . .
. . . . . . . C c . d d . . . .
. . . . . . w c . . . . . . . .
. . . . . w k . . . . . . . . .
. . . . w k . . . . . . . . . .
. . . w k . . . . . . . . . . .
. . w k . . . . . . . . . . . .
. . k . . . . . . . . . . . . .
. . . . . . . . . . . . . . . .
"""


# --------------------------------------------------------------------------- builder blocks
# Slim Screen fronts, one per CC:T edge state: a 2 px bezel on every side not
# joined to another screen (u/d/l/r: joined above/below/left/right), open
# inside: the glass is its own face just behind, where the screen is drawn.
# Grey, because the frame finish tints the bezel (FrameFinish).
SLIM_STATES = ["none", "l", "r", "lr", "u", "d", "ud", "rd", "ld", "ru", "lu", "lrd", "rud", "lud", "lru", "lrud"]
BEZEL_OUT, BEZEL_IN, BEZEL_CORNER = hexrgb("#F4F4F4"), hexrgb("#D2D2D2"), hexrgb("#B8B8B8")
CLEAR = (0, 0, 0, 0)


def slim_front(state):
    top, bottom, left, right = "u" not in state, "d" not in state, "l" not in state, "r" not in state
    rows = [[CLEAR] * 16 for _ in range(16)]
    for y in range(16):
        for x in range(16):
            sides = sum((top and y < 2, bottom and y > 13, left and x < 2, right and x > 13))
            outer = (top and y == 0) or (bottom and y == 15) or (left and x == 0) or (right and x == 15)
            if sides:
                rows[y][x] = BEZEL_CORNER if sides == 2 and outer else BEZEL_OUT if outer else BEZEL_IN
    return rows


SLIM_GLASS = [[hexrgb("#101216")] * 16 for _ in range(16)]
SLIM_EDGE = [[BEZEL_IN if y in (0, 15) else hexrgb("#E2E2E2") for _ in range(16)] for y in range(16)]

# Name Plate: an espresso plaque in a gold rim (its face is x 1..14, y 5..10);
# the rest of the sheet is rim gold for its edges and wood for its stand.
WOOD = {"G": "#F7D774", "g": "#DDA744", "d": "#A8762A", "e": "#2B221C", "E": "#241C17",
        "w": "#4A3A2F", "W": "#3B2F27"}
NAME_PLATE = """
g g g g g g g g g g g g g g g g
g g g g g g g g g g g g g g g g
d d d d d d d d d d d d d d d d
g g g g g g g g g g g g g g g g
g g g g g g g g g g g g g g g g
g G G G G G G G G G G G G G g g
g G e e e e e e e e e e e d g g
g G e E E E E E E E E E e d g g
g G e E E E E E E E E E e d g g
g G e e e e e e e e e e e d g g
g d d d d d d d d d d d d d g g
g g g g g g g g g g g g g g g g
w w W w w w w W w w w w W w w w
W w w w W w w w w w W w w w W w
w w w W w w w w w W w w w w w W
w W w w w w W w w w w w W w w w
"""

# Status Lamp: an andon. Espresso wood for the frame; paper panes (x 4..11,
# y 3..12) with two crossbars, plain paper when off, lit in the state's
# colour otherwise (the lit models glow too).
LAMP_WOOD = "\n".join(["w w W w w w w W w w w w W w w w", "W w w w W w w w w w W w w w W w",
                       "w w w W w w w w w W w w w w w W", "w W w w w w W w w w w w W w w w"] * 4)
LAMP_PAPER = """
. . . . . . . . . . . . . . . .
. . . . . . . . . . . . . . . .
. . . . . . . . . . . . . . . .
. . . . a a a a a a a a . . . .
. . . . a b b b b b b a . . . .
. . . . a b c c c c b a . . . .
. . . . k k k k k k k k . . . .
. . . . a b c c c c b a . . . .
. . . . a b c c c c b a . . . .
. . . . k k k k k k k k . . . .
. . . . a b c c c c b a . . . .
. . . . a b b b b b b a . . . .
. . . . a a a a a a a a . . . .
. . . . . . . . . . . . . . . .
. . . . . . . . . . . . . . . .
. . . . . . . . . . . . . . . .
"""
# a pane edge, b between, c pane centre, k crossbar.
LAMP_GLOWS = {
    "off": {"a": "#DCD1BA", "b": "#E6DCC7", "c": "#EDE5D3", "k": "#3B2F27"},
    "idle": {"a": "#7FD79A", "b": "#A6E8B8", "c": "#CCF5D6", "k": "#2E5A3A"},
    "working": {"a": "#E8B54E", "b": "#F7D774", "c": "#FFF1B8", "k": "#6B4A1E"},
    "interrupted": {"a": "#E5645A", "b": "#F08A80", "c": "#FFB8AE", "k": "#5E2622"},
}


# The Name Plate as an item: the plaque head-on, a green dot and a name.
NAME_PLATE_ITEM = """
. . . . . . . . . . . . . . . .
. . . . . . . . . . . . . . . .
. . . . . . . . . . . . . . . .
. G G G G G G G G G G G G G G .
. G e e e e e e e e e e e e d .
. G e E E E E E E E E E E e d .
. G e E n n E c c c W c c e d .
. G e E n n E c c c c W c e d .
. G e E E E E E E E E E E e d .
. G e e e e e e e e e e e e d .
. d d d d d d d d d d d d d d .
. . . . . . . . . . . . . . . .
. . . . . . . . . . . . . . . .
. . . . . . . . . . . . . . . .
. . . . . . . . . . . . . . . .
. . . . . . . . . . . . . . . .
"""


def builder_blocks():
    for state in SLIM_STATES:
        png(OUT / f"slim_screen_{state}.png", slim_front(state))
    png(OUT / "slim_screen_edge.png", SLIM_EDGE)
    png(OUT / "slim_screen_glass.png", SLIM_GLASS)
    png(OUT / "name_plate.png", grid(NAME_PLATE, WOOD))
    png(ITEMS / "name_plate.png", grid(NAME_PLATE_ITEM, {**WOOD, "n": "#6FD08C", "c": "#F3EFE7", "W": "#B3A99C"}))
    png(OUT / "status_lamp_wood.png", grid(LAMP_WOOD, WOOD))
    for glow, pal in LAMP_GLOWS.items():
        png(OUT / f"status_lamp_{glow}.png", grid(LAMP_PAPER, pal))


def main():
    for name, art, pal in (
        ("hermes_computer_front", HERMES_FRONT, HERMES), ("hermes_computer_side", HERMES_SIDE, HERMES),
        ("hermes_computer_top", HERMES_TOP, HERMES), ("terminal_front", TERMINAL_FRONT, TERMINAL),
        ("terminal_side", TERMINAL_SIDE, TERMINAL), ("terminal_top", TERMINAL_TOP, TERMINAL),
    ):
        png(OUT / f"{name}.png", grid(art, pal))
    ITEMS.mkdir(exist_ok=True)
    for name, pal in POCKETS.items():
        png(ITEMS / f"{name}.png", pocket(pal, False))
        png(ITEMS / f"{name}_on.png", pocket(pal, True))
    png(ITEMS / "linker.png", grid(LINKER, LINKER_PAL))
    builder_blocks()
    for tile in sorted(OUT.glob("monitor_*.png")):
        png(tile, [[recolour_monitor(p) for p in row] for row in read_png(tile)])
    print(f"wrote textures to {OUT} and {ITEMS}")


if __name__ == "__main__":
    main()
