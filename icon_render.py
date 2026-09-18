"""
icon_render.py — shared Pillow-only renderer for the "arc reactor" icon design (see
webapp/icon.svg, the actual source of truth this reproduces pixel-for-pixel in the same
"no native SVG rasterizer" spirit android_app/build_icons.py and desktop_app/build_icon.py
already committed to — see either file's own docstring for why). Previously each of those
two scripts had its own near-duplicate render function for the old flat "rounded-rect + J
glyph" design; this design has gradients/rings/glow that are much more error-prone to keep
duplicated in sync by hand across two files, so it's factored out once here instead.

Both build scripts import this and call render_full()/render_foreground() — see their own
files for how the result maps to each platform's actual icon format.
"""

import math
from PIL import Image, ImageDraw, ImageFilter

# Colors copied directly from webapp/icon.svg — keep in sync if that file's design changes.
BG_STOPS = [(0.0, (18, 34, 54)), (0.55, (10, 20, 32)), (1.0, (5, 9, 15))]  # #122236 -> #0a1420 -> #05090f
CORE_STOPS = [(0.0, (255, 255, 255)), (0.45, (127, 212, 255)), (1.0, (11, 58, 92))]  # #ffffff -> #7fd4ff -> #0b3a5c
RING_OUTER = (191, 232, 255)   # #bfe8ff
RING_MIDDLE = (127, 212, 255)  # #7fd4ff
CORNER_COLOR = (191, 232, 255)


def _lerp(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _stop_color(stops, t):
    t = max(0.0, min(1.0, t))
    for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
        if t0 <= t <= t1:
            local = (t - t0) / (t1 - t0) if t1 > t0 else 0
            return _lerp(c0, c1, local)
    return stops[-1][1]


def _radial_gradient(size: int, stops, center=(0.5, 0.5)) -> Image.Image:
    """Approximates an SVG radialGradient by evaluating stop_color per-pixel against
    normalized distance from `center` (fraction of size) — fine at these icon resolutions,
    no need for a real gradient-mesh renderer."""
    img = Image.new("RGB", (size, size))
    px = img.load()
    cx, cy = center[0] * size, center[1] * size
    max_r = size * 0.75  # matches SVG r="85%"/"50%" roughly against the corner-to-center span
    for y in range(size):
        for x in range(size):
            d = math.hypot(x - cx, y - cy) / max_r
            px[x, y] = _stop_color(stops, d)
    return img


def _rounded_mask(size: int, radius_fraction: float) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(mask)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=round(size * radius_fraction), fill=255)
    return mask


def _draw_dashed_circle(draw, cx, cy, r, color, width, dash_deg=14, gap_deg=16):
    angle = 0.0
    while angle < 360:
        start = angle
        end = min(angle + dash_deg, 360)
        draw.arc([cx - r, cy - r, cx + r, cy + r], start, end, fill=color, width=width)
        angle += dash_deg + gap_deg


def _draw_corner_ticks(draw, size, color, width):
    m = size * 0.125   # margin, matches SVG's 24/192
    L = size * 0.083    # tick arm length, matches SVG's 16/192
    for sx, sy in ((1, 1), (-1, 1), (1, -1), (-1, -1)):
        ox = m if sx > 0 else size - m
        oy = m if sy > 0 else size - m
        # vertical arm
        draw.line([ox, oy, ox, oy + sy * L], fill=color, width=width)
        # horizontal arm
        draw.line([ox, oy, ox + sx * L, oy], fill=color, width=width)


def render_full(size: int, corner_radius_fraction: float = 36 / 192, corners: bool = True) -> Image.Image:
    """The complete icon on its own rounded-rect background — used for legacy (pre-API-26)
    Android launcher icons, the desktop .ico, and anywhere else a single flat image (no OS
    masking/adaptive layering) is what's actually needed."""
    bg = _radial_gradient(size, BG_STOPS, center=(0.35, 0.18)).convert("RGBA")
    mask = _rounded_mask(size, corner_radius_fraction)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(bg, (0, 0), mask)

    draw = ImageDraw.Draw(canvas)
    cx = cy = size / 2
    w = max(1, round(size * 2 / 192))

    if corners:
        _draw_corner_ticks(draw, size, CORNER_COLOR, w)

    draw.ellipse([cx - size * 58 / 192, cy - size * 58 / 192, cx + size * 58 / 192, cy + size * 58 / 192],
                 outline=RING_OUTER, width=w)
    _draw_dashed_circle(draw, cx, cy, size * 46 / 192, RING_MIDDLE, w)

    _paste_core(canvas, cx, cy, size * 30 / 192)
    return canvas


def render_foreground(size: int, safe_zone_fraction: float = 66 / 108) -> Image.Image:
    """Transparent-background version, scaled to fit Android's adaptive-icon safe zone —
    just the rings/core/corners with no background rect, since the adaptive icon's
    background layer (a flat color, see values/ic_launcher_background.xml) shows through
    everywhere else. Content is scaled down by safe_zone_fraction so OEM icon masks
    (circle, squircle, rounded square, ...) never clip it."""
    inner = round(size * safe_zone_fraction)
    core_img = render_full(inner, corners=False)
    # render_full draws its own rounded-rect background; redo without it by re-rendering
    # just the foreground elements on transparent instead.
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    cx = cy = size / 2
    scale = inner / 192
    w = max(1, round(size * 2 / 192 * safe_zone_fraction))

    draw.ellipse([cx - 58 * scale, cy - 58 * scale, cx + 58 * scale, cy + 58 * scale],
                 outline=RING_OUTER, width=w)
    _draw_dashed_circle(draw, cx, cy, 46 * scale, RING_MIDDLE, w)
    _paste_core(canvas, cx, cy, 30 * scale)
    return canvas


def _paste_core(canvas: Image.Image, cx: float, cy: float, r: float):
    """The glowing core: a radial-gradient disc plus a soft blurred halo underneath it,
    approximating the SVG's feGaussianBlur glow filter."""
    pad = round(r * 1.8)
    glow_size = round(r * 2) + pad * 2
    core_grad = _radial_gradient(round(r * 2), CORE_STOPS).convert("RGBA")
    disc_mask = Image.new("L", (round(r * 2), round(r * 2)), 0)
    ImageDraw.Draw(disc_mask).ellipse([0, 0, round(r * 2) - 1, round(r * 2) - 1], fill=255)

    glow_layer = Image.new("RGBA", (glow_size, glow_size), (0, 0, 0, 0))
    glow_layer.paste(core_grad, (pad, pad), disc_mask)
    glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(radius=max(1, round(r * 0.35))))

    canvas.alpha_composite(glow_layer, (round(cx - glow_size / 2), round(cy - glow_size / 2)))

    disc_rgba = Image.new("RGBA", (round(r * 2), round(r * 2)), (0, 0, 0, 0))
    disc_rgba.paste(core_grad, (0, 0), disc_mask)
    canvas.alpha_composite(disc_rgba, (round(cx - r), round(cy - r)))
