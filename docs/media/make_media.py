#!/usr/bin/env python3
"""Build the README images in docs/media from the raw screenshots.

    uv run --with pillow python docs/media/make_media.py

Everything here is derived from files already in the repo (docs/screenshots,
docs/demo/screenshots, docs/builders/images, docs/media/island-rooftop.jpg)
plus the launcher's own fonts and palette. Island source shots were re-exported
from the live world's Minecraft screenshots on 30 Sep 2026; no private monitor
content is included.
Private details (host paths, local model routing) are blurred, never re-typed.
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs/media"
SHOTS = ROOT / "docs/screenshots"
DEMO = ROOT / "docs/demo/screenshots"
BUILD = ROOT / "docs/builders/images"
FONTS = ROOT / "launcher/assets/fonts"

# The launcher / OmoStyle palette.
CREAM = (245, 240, 232)
INK = (26, 16, 8)
ESPRESSO = (43, 34, 28)
GOLD = (221, 167, 68)
MUTED = (179, 169, 156)


def font(name, size):
    return ImageFont.truetype(str(FONTS / name), size)


def save(img, name, quality=86):
    path = OUT / name
    img.convert("RGB").save(path, "JPEG", quality=quality, optimize=True, progressive=True)
    print(f"{name:28s} {img.size[0]}x{img.size[1]}  {path.stat().st_size // 1024} KB")


def blur(img, box, radius=6):
    region = img.crop(box).filter(ImageFilter.GaussianBlur(radius))
    img.paste(region, box)


def cover(img, w, h, focus_y=0.5):
    """Scale to cover w x h, crop around a vertical focus point."""
    s = max(w / img.width, h / img.height)
    img = img.resize((round(img.width * s), round(img.height * s)), Image.LANCZOS)
    x = (img.width - w) // 2
    y = min(max(0, round(img.height * focus_y - h / 2)), img.height - h)
    return img.crop((x, y, x + w, y + h))


def hero():
    """2400x1000 banner: a demo campus with the launcher's type."""
    W, H = 2400, 1000
    base = cover(Image.open(DEMO / "22_dusk_campus.jpg"), W, H, focus_y=0.43).convert("RGBA")
    # Left-to-right espresso wash so the type reads on any monitor.
    wash = Image.new("RGBA", (W, H))
    d = ImageDraw.Draw(wash)
    for x in range(W):
        a = int(245 * max(0.0, 1 - x / (W * 0.72)) ** 1.1)
        d.line([(x, 0), (x, H)], fill=(*ESPRESSO, a))
    for y in range(H):  # a soft floor so the bottom edge is not a hard crop
        a = int(120 * max(0, (y - H * 0.72) / (H * 0.28)) ** 1.6)
        d.line([(0, y), (W, y)], fill=(*ESPRESSO, a))
    img = Image.alpha_composite(base, wash)
    d = ImageDraw.Draw(img)
    x0 = 150
    eyebrow = font("IBMPlexMono-Medium.ttf", 40)
    label = "OMO SPACE"
    d.rounded_rectangle((x0 - 18, 232, x0 + eyebrow.getlength(label) + 42, 292), radius=30, fill=(*ESPRESSO, 215))
    d.text((x0, 238), label, font=eyebrow, fill=(*GOLD, 255))
    d.text((x0, 300), "Hermes in", font=font("CrimsonText-SemiBold.ttf", 150), fill=(*CREAM, 255))
    d.text((x0, 450), "Minecraft", font=font("CrimsonText-SemiBold.ttf", 150), fill=(*CREAM, 255))
    d.text((x0, 640), "Give your AI work a place in the world.",
           font=font("CrimsonText-Italic.ttf", 58), fill=(*CREAM, 235))
    d.line([(x0, 740), (x0 + 120, 740)], fill=(*GOLD, 255), width=4)
    d.text((x0, 770), "Real terminals · Hermes agents · live web monitors · sessions that survive",
           font=font("IBMPlexMono-Medium.ttf", 40), fill=(*CREAM, 225))
    save(img, "hero.jpg", quality=84)


def launcher():
    img = Image.open(SHOTS / "launcher.png").convert("RGB").crop((0, 0, 700, 611))
    blur(img, (180, 384, 530, 406), radius=5)  # host path + local model routing
    save(img, "launcher.jpg", quality=90)


def terminal_shots():
    # The station panel: header + tabs + terminal, no world chat around it.
    for src, name in (("t6-hermes-resumed.png", "hermes-station.jpg"),
                      ("t4-tabs.png", "terminal-tabs.jpg"),
                      ("t1-terminal.png", "terminal-plain.jpg")):
        img = Image.open(SHOTS / src).convert("RGB").crop((262, 44, 1338, 758))
        save(img, name, quality=88)


def gallery():
    """Uniform 16:9 tiles for the README gallery (and one wide collage)."""
    picks = {
        "world-campus.jpg": (DEMO / "02_aerial_campus.jpg", 0.55),
        "world-command-center.jpg": (DEMO / "04_hq_command_center.jpg", 0.45),
        "world-hq-desk.jpg": (DEMO / "05_hq_hero_desk.jpg", 0.5),
        "world-dev-room.jpg": (DEMO / "09_omo_dev_room.jpg", 0.5),
        "world-factory.jpg": (DEMO / "24_dusk_factory.jpg", 0.5),
        "world-pagoda.jpg": (DEMO / "19_ops_pagoda.jpg", 0.5),
        "world-torii.jpg": (DEMO / "13_torii_stair.jpg", 0.5),
        "world-lake.jpg": (DEMO / "23_dusk_pm_from_lake.jpg", 0.5),
        "kit-hermes-desk.jpg": (BUILD / "hermes-desk-working.jpg", 0.5),
        "kit-web-gallery.jpg": (BUILD / "kit-web-gallery.jpg", 0.5),
        "kit-meeting-room.jpg": (BUILD / "kit-meeting-room.jpg", 0.5),
        "kit-redstone.jpg": (BUILD / "redstone-bench.jpg", 0.5),
        "web-monitors.jpg": (SHOTS / "t8-webmonitors.png", 0.42),
    }
    for name, (src, fy) in picks.items():
        save(cover(Image.open(src).convert("RGB"), 1280, 720, fy), name, quality=84)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    hero()
    launcher()
    terminal_shots()
    gallery()
