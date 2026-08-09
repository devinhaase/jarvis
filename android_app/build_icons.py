"""
build_icons.py — generates Android launcher icon PNGs from ../webapp/icon.svg, same
reasoning and same reproduce-with-Pillow approach as desktop_app/build_icon.py (see that
file's docstring for why: the source icon is simple enough — one rounded-rect fill, one
centered glyph — to redraw pixel-for-pixel rather than pull in a native SVG rasterizer).

Generates two things:
  1. The legacy (non-adaptive) mipmap-*/ic_launcher.png set — needed for the app to build
     and install with a real icon at all (Tier 7), and as the fallback Android itself uses
     on API < 26.
  2. The adaptive icon (Tier 11, Play Store expects this): a solid-color background layer
     (just a color resource, no image needed) plus a foreground-only glyph PNG per density,
     sized within the standard 66dp "safe zone" of the 108dp adaptive-icon canvas — content
     outside that zone gets inconsistently cropped across OEM icon mask shapes (circle,
     squircle, rounded square, ...), so the glyph is deliberately smaller/more centered here
     than in the legacy full-bleed icon.

Run manually whenever webapp/icon.svg's design changes — not part of every Gradle build.
"""

import os

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
RES_DIR = os.path.join(HERE, "app", "src", "main", "res")

FILL_COLOR = "#0a84ff"
GLYPH = "J"
GLYPH_COLOR = "#ffffff"
CORNER_RADIUS_FRACTION = 36 / 192

# Android's standard density buckets for a launcher icon, in px — mirrors what
# `android:icon="@mipmap/ic_launcher"` expects to find one of, picked by density at runtime.
DENSITIES = {
    "mipmap-mdpi": 48,
    "mipmap-hdpi": 72,
    "mipmap-xhdpi": 96,
    "mipmap-xxhdpi": 144,
    "mipmap-xxxhdpi": 192,
}

# Adaptive icon canvas is 108dp regardless of legacy icon size (extra room is what lets the
# OS mask/parallax it into a circle, squircle, etc. without clipping real content) — same
# density scale factor as DENSITIES above, just a bigger dp base (108 vs 48).
ADAPTIVE_DENSITIES = {folder: round(size * 108 / 48) for folder, size in DENSITIES.items()}
SAFE_ZONE_FRACTION = 66 / 108  # Android's documented adaptive-icon safe zone


def _find_a_bold_font(size: int):
    candidates = [r"C:\Windows\Fonts\segoeuib.ttf", r"C:\Windows\Fonts\arialbd.ttf"]
    for path in candidates:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _render_at(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    radius = round(size * CORNER_RADIUS_FRACTION)
    draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=FILL_COLOR)

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


def _render_foreground_at(size: int) -> Image.Image:
    """Glyph only, transparent background, fit within SAFE_ZONE_FRACTION of the canvas —
    the background layer (a flat color, see values/ic_launcher_background.xml) shows
    through everywhere else. Same bold-font-size search as _render_at, just against the
    smaller safe-zone box instead of the full canvas."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    safe = size * SAFE_ZONE_FRACTION

    lo, hi = 1, size
    best_font = _find_a_bold_font(max(lo, 1))
    while lo <= hi:
        mid = (lo + hi) // 2
        font = _find_a_bold_font(mid)
        bbox = draw.textbbox((0, 0), GLYPH, font=font)
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        if w <= safe * 0.75 and h <= safe * 0.75:
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


def _write_adaptive_icon_xml():
    xml = """<?xml version="1.0" encoding="utf-8"?>
<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">
    <background android:drawable="@color/ic_launcher_background" />
    <foreground android:drawable="@mipmap/ic_launcher_foreground" />
</adaptive-icon>
"""
    out_dir = os.path.join(RES_DIR, "mipmap-anydpi-v26")
    os.makedirs(out_dir, exist_ok=True)
    for name in ("ic_launcher.xml", "ic_launcher_round.xml"):
        path = os.path.join(out_dir, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(xml)
        print(f"Wrote {path}")

    values_dir = os.path.join(RES_DIR, "values")
    os.makedirs(values_dir, exist_ok=True)
    color_path = os.path.join(values_dir, "ic_launcher_background.xml")
    with open(color_path, "w", encoding="utf-8") as f:
        f.write(
            '<?xml version="1.0" encoding="utf-8"?>\n'
            "<resources>\n"
            f'    <color name="ic_launcher_background">{FILL_COLOR.upper()}</color>\n'
            "</resources>\n"
        )
    print(f"Wrote {color_path}")


def main():
    for folder, size in DENSITIES.items():
        out_dir = os.path.join(RES_DIR, folder)
        os.makedirs(out_dir, exist_ok=True)
        img = _render_at(size)
        out_path = os.path.join(out_dir, "ic_launcher.png")
        img.save(out_path, format="PNG")
        # Round variant — same source image; Android picks whichever the launcher/device
        # actually wants (round icon masks on some OEM skins) rather than us guessing.
        round_path = os.path.join(out_dir, "ic_launcher_round.png")
        img.save(round_path, format="PNG")
        print(f"Wrote {out_path} ({size}x{size})")

    # Adaptive icon (Tier 11) — API 26+ prefers this over the legacy PNGs above whenever
    # both are present; the legacy set stays as the < 26 fallback, not dead weight.
    for folder, size in ADAPTIVE_DENSITIES.items():
        out_dir = os.path.join(RES_DIR, folder)
        os.makedirs(out_dir, exist_ok=True)
        fg = _render_foreground_at(size)
        fg_path = os.path.join(out_dir, "ic_launcher_foreground.png")
        fg.save(fg_path, format="PNG")
        print(f"Wrote {fg_path} ({size}x{size})")
    _write_adaptive_icon_xml()


if __name__ == "__main__":
    main()
