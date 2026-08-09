"""
build_icon.py — generates desktop_app/icon.ico from ../webapp/icon.svg.

Windows has no sips/iconutil equivalent (that's macOS-only, per Tier 5's own troubleshooting
note in task.md), and a real SVG rasterizer (cairosvg/resvg) needs a native library that
isn't guaranteed present on a fresh machine — exactly the kind of "invisible until it bites"
dependency this whole project has been trying to avoid. webapp/icon.svg is simple enough
(a rounded-rect fill plus one centered glyph, no gradients/paths/images) to reproduce
pixel-for-pixel with Pillow's own drawing primitives instead of parsing SVG at all — zero
extra native dependencies, and it stays exactly in sync with icon.svg's actual fill color/
corner radius/glyph values, which are copied from that file below, not eyeballed.

Known, deliberate limitation, stated plainly rather than glossed over: if icon.svg's design
ever grows into something more complex than "rounded rect + one glyph" (a real illustration,
gradients, multiple shapes), this script won't reproduce it — regenerate icon.ico by hand at
that point (export a PNG at 512x512 from wherever the new design lives, then convert with
Pillow's own Image.save(..., format="ICO") or an online converter) rather than trying to
extend this into a general SVG parser.

Run manually whenever webapp/icon.svg's design actually changes — not part of every build.py
run, since icon changes are rare and regenerating identical output on every build buys nothing.
"""

import os

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_PATH = os.path.join(HERE, "icon.ico")

# Copied directly from ../webapp/icon.svg — keep these in sync if that file's design changes.
FILL_COLOR = "#0a84ff"
GLYPH = "J"
GLYPH_COLOR = "#ffffff"
CORNER_RADIUS_FRACTION = 36 / 192  # rx="36" on a 192x192 viewBox

# Windows .ico conventionally bundles several sizes so the OS can pick the sharpest one for
# whatever context it's showing the icon in (taskbar, Start tile, Explorer list view, etc.)
# — a single-resolution .ico just gets blurrily scaled everywhere except its one native size.
SIZES = [16, 24, 32, 48, 64, 128, 256]


def _find_a_bold_font(size: int):
    # No bundled font ships with Pillow — fall back through a few fonts Windows always has,
    # so this works on a fresh machine without needing a font file checked into the repo.
    candidates = [
        r"C:\Windows\Fonts\segoeuib.ttf",  # Segoe UI Bold — closest match to the SVG's stack
        r"C:\Windows\Fonts\arialbd.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()  # last resort — still produces a valid, if plainer, icon


def _render_at(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    radius = round(size * CORNER_RADIUS_FRACTION)
    draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=FILL_COLOR)

    # Binary-search the largest font size that keeps the glyph comfortably inside the
    # square, rather than hardcoding a fraction that looks right at one size and wrong at
    # the others — icon.svg's font-size=110 on a 192px box (~57%) is itself just one
    # reference point, and Pillow/PIL font metrics don't scale identically to a browser's.
    lo, hi = 1, size
    best_font = _find_a_bold_font(max(lo, 1))
    while lo <= hi:
        mid = (lo + hi) // 2
        font = _find_a_bold_font(mid)
        bbox = draw.textbbox((0, 0), GLYPH, font=font)
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        if w <= size * 0.62 and h <= size * 0.62:
            best_font = font
            lo = mid + 1
        else:
            hi = mid - 1

    bbox = draw.textbbox((0, 0), GLYPH, font=best_font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (size - w) / 2 - bbox[0]
    y = (size - h) / 2 - bbox[1]
    draw.text((x, y), GLYPH, font=best_font, fill=GLYPH_COLOR)
    return img


def main():
    base = _render_at(256)  # largest size as the base image; Pillow derives the rest
    frames = [_render_at(s) for s in SIZES if s != 256]
    base.save(OUT_PATH, format="ICO", sizes=[(s, s) for s in SIZES], append_images=frames)
    print(f"Wrote {OUT_PATH} ({', '.join(str(s) for s in SIZES)} px)")


if __name__ == "__main__":
    main()
