#!/usr/bin/env python3
"""Generate the .icns app icon without external assets."""
from __future__ import annotations

import math
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
SIZE = 1024


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def main() -> int:
    img = Image.new("RGBA", (SIZE, SIZE), (5, 9, 8, 255))
    draw = ImageDraw.Draw(img, "RGBA")

    # Deep, slightly green-black field with a soft mint halo.
    glow = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    for radius, alpha in [(480, 22), (360, 22), (240, 30), (150, 38)]:
        gd.ellipse((512 - radius, 430 - radius, 512 + radius, 430 + radius),
                   fill=(78, 220, 145, alpha))
    glow = glow.filter(ImageFilter.GaussianBlur(90))
    img = Image.alpha_composite(img, glow)
    draw = ImageDraw.Draw(img, "RGBA")

    # Inner rounded-square plate.
    plate = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    pd = ImageDraw.Draw(plate)
    pd.rounded_rectangle((68, 68, 956, 956), radius=220, fill=(11, 20, 16, 255))
    pd.rounded_rectangle((68, 68, 956, 956), radius=220, outline=(155, 246, 193, 35), width=3)
    img = Image.alpha_composite(img, plate)
    draw = ImageDraw.Draw(img, "RGBA")

    # Build the R mark as a mask so the mint gradient stays inside the glyph.
    mask = Image.new("L", (SIZE, SIZE), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle((292, 258, 704, 536), radius=132, fill=255)
    md.rectangle((292, 258, 414, 766), fill=255)
    md.rounded_rectangle((414, 356, 618, 458), radius=50, fill=0)
    md.polygon([(530, 496), (658, 496), (766, 766), (620, 766)], fill=255)
    md.rounded_rectangle((292, 720, 766, 766), radius=22, fill=255)

    mint_top = (210, 255, 229)
    mint_bottom = (84, 211, 145)
    gradient = Image.new("RGBA", (SIZE, SIZE))
    gdraw = ImageDraw.Draw(gradient)
    for y in range(SIZE):
        t = y / SIZE
        color = lerp(mint_top, mint_bottom, t)
        gdraw.line((0, y, SIZE, y), fill=color + (255,))
    img.paste(gradient, (0, 0), mask)
    draw = ImageDraw.Draw(img, "RGBA")

    # Signal dot.
    draw.ellipse((742, 196, 846, 300), fill=(155, 246, 193, 255))
    draw.ellipse((728, 182, 860, 314), outline=(155, 246, 193, 55), width=3)
    draw.ellipse((764, 218, 824, 278), fill=(10, 19, 15, 255))

    # Fine scan line detail near the bottom.
    for i, x in enumerate(range(312, 736, 36)):
        alpha = 70 if i % 2 == 0 else 28
        draw.rounded_rectangle((x, 824, x + 16, 830), radius=3, fill=(155, 246, 193, alpha))

    # Rounded mask to make Finder/Dock corners clean.
    rounded = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(rounded).rounded_rectangle((0, 0, SIZE - 1, SIZE - 1), radius=218, fill=255)
    img.putalpha(rounded)

    build_dir = ROOT / "dist" / "icon-build"
    build_dir.mkdir(parents=True, exist_ok=True)
    master = build_dir / "AppIcon-1024.png"
    img.save(master)
    iconset = build_dir / "AppIcon.iconset"
    iconset.mkdir(exist_ok=True)

    mapping = {
        "icon_16x16.png": 16,
        "icon_16x16@2x.png": 32,
        "icon_32x32.png": 32,
        "icon_32x32@2x.png": 64,
        "icon_128x128.png": 128,
        "icon_128x128@2x.png": 256,
        "icon_256x256.png": 256,
        "icon_256x256@2x.png": 512,
        "icon_512x512.png": 512,
        "icon_512x512@2x.png": 1024,
    }
    for name, px in mapping.items():
        target = iconset / name
        if px == SIZE:
            img.save(target)
        else:
            resized = img.resize((px, px), Image.LANCZOS)
            resized.save(target)

    icns = build_dir / "AppIcon.icns"
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(icns)], check=True)
    print(icns)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
