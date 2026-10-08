#!/usr/bin/env python3
"""Build the "Omo Workshop" starter world in a running test game.

A small open-air atelier: warm sandstone and quartz, a copper aisle, a gold
winged crest. On the back wall, a Hermes Computer drives a 5x3 terminal wall;
a plain Terminal sits at its own desk; two web walls show the live Hermes
Board and the Hermes docs. Stations carry no workspace until first opened, so
every player's copy makes its own.

Usage (OmoTest running with the test driver, see tests/launch-test.sh):
    python3 worlds/build_workshop.py            # builds in the currently open world
Then quit to title so the save is flushed, and copy the save into worlds/.
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests"))
from gamectl import call  # noqa: E402

F = -61                      # floor surface; players stand at F + 1
G = F + 1


def cmd(c):
    out = call("cmd", {"command": c.lstrip("/")})
    if out.get("error"):
        print("!!", c, out)
    time.sleep(0.05)


def fill(x1, y1, z1, x2, y2, z2, block, mode=""):
    cmd(f"fill {x1} {y1} {z1} {x2} {y2} {z2} {block} {mode}".strip())


def put(x, y, z, block):
    cmd(f"setblock {x} {y} {z} {block}")


def wall(x1, x2, y1, y2, z_back=-9):
    """Hang a monitor wall on the back wall's south face, placed as the
    player: CC:T only joins monitors into one wall on a player placement."""
    cmd("item replace entity @s weapon.mainhand with omolife:monitor 64")
    cmd(f"tp @s {(x1 + x2) / 2 + 0.5} {G} {z_back + 4.5} 180 0")
    time.sleep(0.5)
    for y in range(y1, y2 + 1):
        for x in range(x1, x2 + 1):
            call("use", {"x": x, "y": y, "z": z_back, "face": "south"})
            time.sleep(0.15)
    cmd("item replace entity @s weapon.mainhand with air")


def build():
    for rule in ("advance_time false", "advance_weather false", "spawn_mobs false",
                 "spawn_monsters false", "keep_inventory true"):
        cmd(f"gamerule {rule}")
    cmd("time set 6000")
    cmd("weather clear")

    # ground: clear the old platform, lay a lawn, then the atelier floor
    fill(-16, G, -16, 16, G + 16, 14, "air")
    fill(-16, F, -16, 16, F, 14, "grass_block")
    fill(-9, F, -9, 9, F, 7, "smooth_sandstone")
    fill(-9, F, -9, 9, F, -9, "cut_sandstone")
    fill(-9, F, 7, 9, F, 7, "cut_sandstone")
    fill(-9, F, -9, -9, F, 7, "cut_sandstone")
    fill(9, F, -9, 9, F, 7, "cut_sandstone")
    fill(0, F, -6, 0, F, 7, "waxed_cut_copper")            # the aisle
    put(0, F, 7, "chiseled_sandstone")
    for x, z in ((-8, 6), (8, 6), (-8, -8), (8, -8)):
        put(x, F, z, "pearlescent_froglight")

    # back wall with a quartz frame; columns and beams
    fill(-9, G, -9, 9, G + 6, -9, "smooth_quartz")
    fill(-9, G + 7, -9, 9, G + 7, -9, "quartz_slab[type=bottom]")
    for x in (-9, 9):
        for z in (-9, -4, 1, 6):
            fill(x, G, z, x, G + 5, z, "quartz_pillar")
            put(x, G + 6, z, "chiseled_quartz_block")
        fill(x, G + 6, -8, x, G + 6, 6, "smooth_quartz_slab[type=top]", "keep")
    for z in (-4, 1, 6):
        for x in (-8, 8):
            put(x, G + 5, z, "lantern[hanging=true]")

    # gold winged crest above the hero screen
    for x, y in ((-5, 5), (-4, 5), (-3, 5), (-6, 6), (-5, 6), (-4, 6), (-3, 4), (-2, 4)):
        put(x, G + y, -9, "gold_block")
        put(-x, G + y, -9, "gold_block")
    put(0, G + 4, -9, "shroomlight")
    put(-1, G + 4, -9, "chiseled_quartz_block")
    put(1, G + 4, -9, "chiseled_quartz_block")

    # desks: stripped dark oak
    fill(-2, G, -7, 2, G, -7, "stripped_dark_oak_log[axis=x]")
    fill(5, G, -7, 7, G, -7, "stripped_dark_oak_log[axis=x]")
    fill(-7, G, -7, -5, G, -7, "stripped_dark_oak_log[axis=x]")

    # the hero: Hermes Computer in front of a 5x3 terminal wall
    wall(-2, 2, G + 1, G + 3)
    put(0, G + 1, -7, "omolife:hermes_computer[facing=south]")
    put(-2, G + 1, -7, "potted_azalea_bush")
    put(2, G + 1, -7, "lantern")

    # the Terminal: its own desk, its own screen, no agent
    put(6, G + 1, -7, "omolife:terminal[facing=south]")
    put(7, G + 1, -7, "potted_fern")
    put(5, G + 1, -7, "candle[candles=3,lit=true]")

    # web walls: the Hermes Board (right) and the Hermes docs (left)
    wall(5, 7, G + 3, G + 4)
    wall(-7, -5, G + 3, G + 4)
    put(-6, G + 1, -7, "bookshelf")
    put(-7, G + 1, -7, "potted_flowering_azalea_bush")
    put(-5, G + 1, -7, "flower_pot")

    # side shelves and greenery
    for x in (-8, 8):
        fill(x, G, -7, x, G + 1, -6, "bookshelf")
        put(x, G, -2, "moss_block")
        put(x, G + 1, -2, "azalea")
        put(x, G, 3, "moss_block")
        put(x, G + 1, 3, "flowering_azalea")

    # welcome sign at the aisle mouth
    put(-1, G, 6, "dark_oak_sign[rotation=0]" + '{front_text:{messages:["",'
        '{text:"Omo Space",color:"gold",bold:true},{text:"Hermes Workshop"},""]}}')
    put(1, G, 6, "dark_oak_sign[rotation=0]" + '{front_text:{messages:['
        '{text:"Right-click the"},{text:"Hermes Computer"},{text:"to start working."},'
        '{text:"Its work keeps running.",color:"dark_gray"}]}}')

    cmd(f"setworldspawn 0 {G} 4 180")
    cmd(f"tp @s 0.5 {G} 4.5 180 5")


def pin(x, y, z, url):
    """Pin a web page on a monitor wall through the monitor's own controls."""
    cmd(f"tp @s {x + 0.5} {G} {z + 3.5} 180 0")     # within reach, or the server ignores the click
    time.sleep(0.5)
    call("sneak", {"on": True})                     # a plain right-click takes remote control;
    time.sleep(0.4)                                  # (the server sees the crouch a few ticks later)
    call("use", {"x": x, "y": y, "z": z, "face": "south"})   # sneak + use opens the settings
    call("sneak", {"on": False})
    time.sleep(0.8)
    widgets = {w["text"]: w for w in call("widgets")["widgets"]}
    if "Web page" in widgets:
        w = widgets["Web page"]
        call("click", {"x": w["x"] + w["w"] / 2, "y": w["y"] + w["h"] / 2})
        time.sleep(0.4)
        widgets = {w["text"]: w for w in call("widgets")["widgets"]}
    box = widgets.get("Address")
    if box:
        call("click", {"x": box["x"] + 5, "y": box["y"] + box["h"] / 2})
        call("key", {"key": 65, "mods": 2})                     # select all, then replace
        call("type", {"text": url})
        pinbtn = widgets.get("Pin page")
        if pinbtn:
            call("click", {"x": pinbtn["x"] + pinbtn["w"] / 2, "y": pinbtn["y"] + pinbtn["h"] / 2})
    time.sleep(0.6)
    call("close")
    time.sleep(0.4)


if __name__ == "__main__":
    build()
    time.sleep(2)
    pin(6, G + 4, -8, "http://127.0.0.1:8780/board")
    pin(-6, G + 4, -8, "https://hermes-agent.nousresearch.com/docs/")
    cmd(f"tp @s 0.5 {G} 4.5 180 5")
    print("built; quit the game to flush the save before copying it")
