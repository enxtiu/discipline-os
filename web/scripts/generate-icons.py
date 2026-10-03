#!/usr/bin/env python3
"""Generate the PWA icon set (no third-party dependencies).

The mark is a "discipline ring": a dark blue-gray rounded square, a soft cyan
progress arc, a violet remainder and an amber marker dot — the same visual
language as the rings on the Today screen.

Usage:
    python scripts/generate-icons.py [outdir]     # default: ./public/icons

Writes: icon-192.png, icon-512.png, maskable-512.png, apple-touch-icon.png,
icon-mono.svg and favicon.svg.
"""

from __future__ import annotations

import math
import os
import struct
import sys
import zlib
from pathlib import Path

# ---- palette (mirrors tailwind.config.js) ---------------------------------
INK_TOP = (0x0B, 0x12, 0x20)
INK_BOTTOM = (0x16, 0x21, 0x36)
TRACK = (0x1F, 0x2C, 0x45)
AQUA = (0x38, 0xE0, 0xC8)
VIOLET = (0x8B, 0x5C, 0xF6)
AMBER = (0xFB, 0xBF, 0x24)
MIST = (0x84, 0x92, 0xAB)

SS = 3  # supersampling factor


def _mix(a, b, t: float):
    t = max(0.0, min(1.0, t))
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _over(dst, src, alpha: float):
    alpha = max(0.0, min(1.0, alpha))
    return tuple(round(dst[i] * (1 - alpha) + src[i] * alpha) for i in range(3))


def _coverage(sdf: float, aa: float) -> float:
    """Signed distance -> coverage in 0..1 (``aa`` = antialias width)."""
    return max(0.0, min(1.0, 0.5 - sdf / aa))


def _rounded_rect_alpha(x: float, y: float, size: float, radius: float, aa: float = 1.0) -> float:
    """Coverage of a rounded square centred at (size/2, size/2) — rounded box SDF."""
    cx = cy = size / 2
    qx = abs(x - cx) - size / 2 + radius
    qy = abs(y - cy) - size / 2 + radius
    inside = min(max(qx, qy), 0.0)
    outside = math.hypot(max(qx, 0.0), max(qy, 0.0))
    return _coverage(inside + outside - radius, aa)


def _ring_coverage(x: float, y: float, cx: float, cy: float, r: float, w: float, aa: float = 1.0) -> float:
    """Coverage of a circular annulus (ring) of radius ``r`` and width ``w``."""
    sdf = abs(math.hypot(x - cx, y - cy) - r) - w / 2
    return _coverage(sdf, aa)


def render(size: int, maskable: bool = False) -> bytes:
    scale = 1.0 if not maskable else 0.78  # keep the mark inside the safe zone
    cx = cy = size / 2
    r = size * 0.30 * scale
    width = size * 0.075 * scale
    glow = width * 1.9

    big = size * SS
    aa = SS * 1.25  # antialias width, in supersampled units (~1 device px)
    start_angle = -90.0
    sweep = 252.0  # 70 % progress arc

    rows: list[bytes] = []
    for py in range(size):
        row = bytearray()
        for px in range(size):
            r_acc = g_acc = b_acc = 0.0
            a_acc = 0.0
            for sy in range(SS):
                for sx in range(SS):
                    x = px + (sx + 0.5) / SS
                    y = py + (sy + 0.5) / SS
                    xb = x * SS / SS * (big / size)
                    yb = y * SS / SS * (big / size)

                    cover = _rounded_rect_alpha(xb, yb, big, big * 0.23, aa)
                    if cover <= 0.0:
                        continue
                    colour = _mix(INK_TOP, INK_BOTTOM, y / size)
                    base_alpha = cover

                    # subtle inner ring for depth
                    inner = _ring_coverage(xb, yb, big / 2, big / 2, r * SS * 0.58, width * SS * 0.32, aa)
                    if inner > 0:
                        colour = _over(colour, MIST, inner * 0.22)

                    # glow + main ring
                    g = _ring_coverage(xb, yb, big / 2, big / 2, r * SS, glow * SS, aa * 3)
                    if g > 0:
                        colour = _over(colour, AQUA, g * 0.20)

                    track = _ring_coverage(xb, yb, big / 2, big / 2, r * SS, width * SS, aa)
                    if track > 0:
                        angle = math.degrees(math.atan2(yb - big / 2, xb - big / 2))
                        delta = (angle - start_angle) % 360
                        if delta <= sweep:
                            colour = _over(colour, AQUA, track)
                        else:
                            colour = _over(colour, VIOLET, track * 0.55)

                    # amber marker at the arc start (top)
                    mx = big / 2
                    my = big / 2 - r * SS
                    md = math.hypot(xb - mx, yb - my)
                    dot_r = width * SS * 0.62
                    if md <= dot_r:
                        colour = _over(colour, AMBER, max(0.0, 1.0 - (md / dot_r) ** 3))

                    r_acc += colour[0] * base_alpha
                    g_acc += colour[1] * base_alpha
                    b_acc += colour[2] * base_alpha
                    a_acc += base_alpha
            samples = SS * SS
            if a_acc <= 0:
                row += bytes((0, 0, 0, 0))
            else:
                row += bytes(
                    (
                        round(r_acc / a_acc),
                        round(g_acc / a_acc),
                        round(b_acc / a_acc),
                        round(255 * a_acc / samples),
                    )
                )
        rows.append(bytes(row))

    raw = b"".join(b"\x00" + row for row in rows)
    return _png(size, size, raw)


def _png(width: int, height: int, raw: bytes) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


FAVICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" role="img" aria-label="DisciplineOS">
  <rect width="64" height="64" rx="15" fill="#0B1220"/>
  <circle cx="32" cy="32" r="18" fill="none" stroke="#1F2C45" stroke-width="5"/>
  <circle cx="32" cy="32" r="18" fill="none" stroke="#38E0C8" stroke-width="5"
          stroke-linecap="round" stroke-dasharray="79.2 113.1" transform="rotate(-90 32 32)"/>
  <circle cx="32" cy="32" r="18" fill="none" stroke="#8B5CF6" stroke-width="5" stroke-opacity=".55"
          stroke-linecap="round" stroke-dasharray="33.9 158.4" stroke-dashoffset="-79.2" transform="rotate(-90 32 32)"/>
  <circle cx="32" cy="14" r="4" fill="#FBBF24"/>
</svg>
"""

MONO_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"
     stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
  <circle cx="12" cy="12" r="8.5"/>
  <path d="M12 3.5a8.5 8.5 0 0 1 7.4 4.3"/>
  <circle cx="12" cy="3.5" r="1.6" fill="currentColor" stroke="none"/>
</svg>
"""


def main(argv: list[str]) -> int:
    out = Path(argv[1] if len(argv) > 1 else "public/icons")
    out.mkdir(parents=True, exist_ok=True)

    targets = {
        "icon-192.png": (192, False),
        "icon-512.png": (512, False),
        "maskable-512.png": (512, True),
        "apple-touch-icon.png": (180, False),
    }
    for name, (size, maskable) in targets.items():
        data = render(size, maskable=maskable)
        (out / name).write_bytes(data)
        print(f"  {name:26} {size}x{size}  {len(data):>7} bytes")

    (out / "favicon.svg").write_text(FAVICON_SVG, encoding="utf-8")
    (out / "icon-mono.svg").write_text(MONO_SVG, encoding="utf-8")
    print("  favicon.svg / icon-mono.svg")

    # the SPA also needs the offline fallback page next to index.html
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
