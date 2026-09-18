"""
build_icons.py — generates Android launcher icon PNGs from ../webapp/icon.svg's design,
reproduced via ../icon_render.py (shared with desktop_app/build_icon.py — see that module's
own docstring for why this is Pillow-only, no native SVG rasterizer).

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
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))  # repo root, for icon_render.py
from icon_render import render_full, render_foreground  # noqa: E402

RES_DIR = os.path.join(HERE, "app", "src", "main", "res")

# The adaptive icon's background layer is a flat color rather than the full radial gradient
# render_full() draws — Android composites the background/foreground layers itself, and a
# flat color resource is the normal/expected shape for that layer (matches the deepest,
# most-background-looking tone in webapp/icon.svg's gradient).
ADAPTIVE_BG_COLOR = "#05090F"

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
            f'    <color name="ic_launcher_background">{ADAPTIVE_BG_COLOR}</color>\n'
            "</resources>\n"
        )
    print(f"Wrote {color_path}")


def main():
    for folder, size in DENSITIES.items():
        out_dir = os.path.join(RES_DIR, folder)
        os.makedirs(out_dir, exist_ok=True)
        img = render_full(size)
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
        fg = render_foreground(size)
        fg_path = os.path.join(out_dir, "ic_launcher_foreground.png")
        fg.save(fg_path, format="PNG")
        print(f"Wrote {fg_path} ({size}x{size})")
    _write_adaptive_icon_xml()


if __name__ == "__main__":
    main()
