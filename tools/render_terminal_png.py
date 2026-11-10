#!/usr/bin/env python3
"""Render captured terminal output to a PNG — a terminal screenshot.

    python3 tools/render_terminal_png.py --in /tmp/demo-output.txt \
        --out docs/images/world-sense-demo.png --title "World Sense demo — sh scripts/demo.sh"

The input must be the REAL output of a real run (scripts/demo.sh, the test
suite, ...). Nothing here invents output: it wraps lines, draws them in
DejaVu Sans Mono on a dark terminal background, and labels the title bar
with the command that produced them. Use it to refresh the screenshots in
docs/WORLD-SENSE-TRY-IT.md after re-running the command.
"""

import argparse
from PIL import Image, ImageDraw, ImageFont

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"


def main():
    ap = argparse.ArgumentParser(description="Render terminal output to PNG")
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--title", default="terminal")
    ap.add_argument("--font-size", type=int, default=15)
    ap.add_argument("--max-cols", type=int, default=100)
    args = ap.parse_args()

    with open(args.inp) as fh:
        raw = fh.read()
    lines = []
    for line in raw.splitlines():
        line = line.rstrip()
        while len(line) > args.max_cols:
            lines.append(line[:args.max_cols])
            line = line[args.max_cols:]
        lines.append(line)
    # Keep screenshots readable: cap at 60 lines.
    truncated = len(lines) > 60
    lines = lines[:60]

    font = ImageFont.truetype(FONT, args.font_size)
    bold = ImageFont.truetype(FONT_BOLD, args.font_size)
    ascent, descent = font.getmetrics()
    line_h = ascent + descent + 4
    pad = 16
    char_w = font.getlength("M")
    width = int(pad * 2 + char_w * args.max_cols)
    title_h = 36
    height = title_h + pad + line_h * len(lines) + pad + (line_h if truncated else 0)

    img = Image.new("RGB", (width, height), (24, 20, 16))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([0, 0, width, title_h], radius=8,
                           fill=(58, 48, 38))
    draw.ellipse([14, 13, 26, 25], fill=(229, 100, 90))
    draw.ellipse([32, 13, 44, 25], fill=(221, 167, 68))
    draw.ellipse([50, 13, 62, 25], fill=(111, 208, 140))
    draw.text((72, 9), args.title, font=bold, fill=(232, 225, 213))
    y = title_h + pad
    for line in lines:
        draw.text((pad, y), line, font=font, fill=(232, 225, 213))
        y += line_h
    if truncated:
        draw.text((pad, y), "... (output truncated for the screenshot; full text in the guide)",
                  font=font, fill=(160, 150, 135))
    img.save(args.out)
    print(f"wrote {args.out} ({width}x{height}, {len(lines)} lines)")


if __name__ == "__main__":
    main()
