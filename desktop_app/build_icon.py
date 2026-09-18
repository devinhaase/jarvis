"""
build_icon.py — generates desktop_app/icon.ico from ../webapp/icon.svg's design, reproduced
via ../icon_render.py (shared with android_app/build_icons.py — see that module's own
docstring for why this is Pillow-only rather than a native SVG rasterizer).

Run manually whenever webapp/icon.svg's design actually changes — not part of every build.py
run, since icon changes are rare and regenerating identical output on every build buys nothing.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))  # repo root, for icon_render.py
from icon_render import render_full  # noqa: E402

OUT_PATH = os.path.join(HERE, "icon.ico")

# Windows .ico conventionally bundles several sizes so the OS can pick the sharpest one for
# whatever context it's showing the icon in (taskbar, Start tile, Explorer list view, etc.)
# — a single-resolution .ico just gets blurrily scaled everywhere except its one native size.
SIZES = [16, 24, 32, 48, 64, 128, 256]


def main():
    base = render_full(256)  # largest size as the base image; Pillow derives the rest
    frames = [render_full(s) for s in SIZES if s != 256]
    base.save(OUT_PATH, format="ICO", sizes=[(s, s) for s in SIZES], append_images=frames)
    print(f"Wrote {OUT_PATH} ({', '.join(str(s) for s in SIZES)} px)")


if __name__ == "__main__":
    main()
