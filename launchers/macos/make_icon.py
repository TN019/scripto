#!/usr/bin/env python3
"""Generate AppIcon.iconset PNGs with the stdlib only (no Pillow).

The icon: indigo rounded square, two white "subtitle" bars at the bottom and
a white play triangle above them — recognizably a subtitle tool. Replace this
by dropping your own PNGs into the iconset before running iconutil.
"""

from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

BG_TOP = (99, 102, 241)      # indigo-500
BG_BOTTOM = (67, 56, 202)    # indigo-700
WHITE = (255, 255, 255)


def rounded_rect_mask(x: float, y: float, w: float, h: float, r: float, px: float, py: float) -> bool:
    if not (x <= px <= x + w and y <= py <= y + h):
        return False
    cx = min(max(px, x + r), x + w - r)
    cy = min(max(py, y + r), y + h - r)
    return (px - cx) ** 2 + (py - cy) ** 2 <= r * r or (
        x + r <= px <= x + w - r or y + r <= py <= y + h - r
    )


def render(size: int) -> bytes:
    s = size
    rows = []
    # geometry (fractions of size)
    pad = 0.06 * s
    radius = 0.22 * s
    tri_x0, tri_y0 = 0.30 * s, 0.24 * s      # play triangle bounding box
    tri_h = 0.28 * s
    tri_w = 0.30 * s
    bar1_y, bar2_y = 0.62 * s, 0.76 * s      # subtitle bars
    bar_h = 0.075 * s
    bar_r = bar_h / 2
    bar1_w, bar2_w = 0.56 * s, 0.38 * s
    bar_x = 0.22 * s

    for j in range(s):
        row = bytearray()
        row.append(0)  # PNG filter type
        for i in range(s):
            px, py = i + 0.5, j + 0.5
            if rounded_rect_mask(pad, pad, s - 2 * pad, s - 2 * pad, radius, px, py):
                t = j / s
                r = int(BG_TOP[0] + (BG_BOTTOM[0] - BG_TOP[0]) * t)
                g = int(BG_TOP[1] + (BG_BOTTOM[1] - BG_TOP[1]) * t)
                b = int(BG_TOP[2] + (BG_BOTTOM[2] - BG_TOP[2]) * t)
                a = 255
                # play triangle
                if tri_x0 <= px <= tri_x0 + tri_w and tri_y0 <= py <= tri_y0 + tri_h:
                    frac = (px - tri_x0) / tri_w
                    half = tri_h / 2 * (1 - frac)
                    mid = tri_y0 + tri_h / 2
                    if mid - half + tri_h / 2 * frac * 0 <= py <= mid + half:
                        if abs(py - mid) <= tri_h / 2 * (1 - frac):
                            r, g, b = WHITE
                # subtitle bars
                for by, bw in ((bar1_y, bar1_w), (bar2_y, bar2_w)):
                    if rounded_rect_mask(bar_x, by, bw, bar_h, bar_r, px, py):
                        r, g, b = WHITE
                row += bytes((r, g, b, a))
            else:
                row += b"\x00\x00\x00\x00"
        rows.append(bytes(row))

    raw = zlib.compress(b"".join(rows), 9)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", s, s, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", raw)
        + chunk(b"IEND", b"")
    )


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "AppIcon.iconset")
    out.mkdir(parents=True, exist_ok=True)
    sizes = {
        "icon_16x16.png": 16, "icon_16x16@2x.png": 32,
        "icon_32x32.png": 32, "icon_32x32@2x.png": 64,
        "icon_128x128.png": 128, "icon_128x128@2x.png": 256,
        "icon_256x256.png": 256, "icon_256x256@2x.png": 512,
        "icon_512x512.png": 512, "icon_512x512@2x.png": 1024,
    }
    for name, size in sizes.items():
        (out / name).write_bytes(render(size))
    print(f"iconset written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
