#!/usr/bin/env python3
"""Generate AppIcon.icns for Claude Stats — the "Gridline" mark:
a white trend line over two faint gridlines with a green end dot, on black.

Pure stdlib: an analytic (anti-aliased) software rasterizer + a minimal PNG
writer, assembled into an .icns with macOS `iconutil`.  Run: python3 make_icon.py
"""

import os
import struct
import subprocess
import zlib
from math import hypot

HERE = os.path.dirname(os.path.abspath(__file__))
AA = 0.7  # edge feather in pixels

# colors (r, g, b) — phosphor-green terminal theme
BG    = (0, 0, 0)
GRID  = (18, 58, 40)
BASE  = (30, 92, 62)
LINE  = (43, 255, 158)
DOT   = (233, 255, 244)

# geometry, normalized inside the tile (0..1)
INSET = 0.22
RADIUS = 0.223
POLY = [(0.0, 0.74), (0.30, 0.58), (0.55, 0.64), (0.78, 0.34), (1.0, 0.16)]
GRIDS = [0.36, 0.64]
DOT_AT = (1.0, 0.16)


def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def seg_dist(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return hypot(px - ax, py - ay)
    t = clamp(((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy), 0.0, 1.0)
    return hypot(px - (ax + t * dx), py - (ay + t * dy))


class Canvas:
    def __init__(self, size):
        self.s = size
        self.buf = bytearray(size * size * 4)  # RGBA

    def coverage_fill(self, x0, y0, x1, y1, color, cov_fn):
        """Blend `color` over the box [x0,x1)x[y0,y1) using cov_fn(px,py)->0..1."""
        s = self.s
        x0 = max(0, int(x0)); y0 = max(0, int(y0))
        x1 = min(s, int(x1) + 1); y1 = min(s, int(y1) + 1)
        cr, cg, cb = color
        buf = self.buf
        for y in range(y0, y1):
            row = (y * s) * 4
            py = y + 0.5
            for x in range(x0, x1):
                c = cov_fn(x + 0.5, py)
                if c <= 0:
                    continue
                i = row + x * 4
                ia = 1.0 - c
                buf[i]   = int(buf[i] * ia + cr * c + 0.5)
                buf[i+1] = int(buf[i+1] * ia + cg * c + 0.5)
                buf[i+2] = int(buf[i+2] * ia + cb * c + 0.5)
                if c * 255 > buf[i+3]:
                    buf[i+3] = min(255, int(c * 255 + 0.5))


def render(size):
    cv = Canvas(size)
    m = INSET * size
    A = size - 2 * m
    X = lambda x: m + x * A
    Y = lambda y: m + y * A
    rad = RADIUS * size
    hw = size / 2.0

    # 1) black rounded-rect tile (alpha = rounded mask)
    def tile_cov(px, py):
        qx = abs(px - hw) - (hw - rad)
        qy = abs(py - hw) - (hw - rad)
        d = hypot(max(qx, 0.0), max(qy, 0.0)) - rad + min(max(qx, qy), 0.0)
        return clamp(0.5 - d / AA, 0.0, 1.0)
    cv.coverage_fill(0, 0, size, size, BG, tile_cov)

    # helper: stroke a horizontal line
    def hline(yn, color, thick):
        yy = Y(yn); half = max(thick * size / 2.0, 0.55)
        cv.coverage_fill(X(0) - 1, yy - half - AA, X(1) + 1, yy + half + AA, color,
                         lambda px, py: clamp((half - abs(py - yy)) / AA + 0.5, 0, 1)
                         if X(0) - 0.5 <= px <= X(1) + 0.5 else 0.0)

    for gy in GRIDS:
        hline(gy, GRID, 0.014)
    hline(1.0, BASE, 0.02)

    # 2) white trend polyline (round caps/joins via distance to whole path)
    pts = [(X(x), Y(y)) for x, y in POLY]
    half = max(0.0275 * size, 0.7)
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    segs = list(zip(pts, pts[1:]))

    def line_cov(px, py):
        d = min(seg_dist(px, py, a[0], a[1], b[0], b[1]) for a, b in segs)
        return clamp((half - d) / AA + 0.5, 0.0, 1.0)
    cv.coverage_fill(min(xs) - half - AA, min(ys) - half - AA,
                     max(xs) + half + AA, max(ys) + half + AA, LINE, line_cov)

    # 3) green end dot
    dcx, dcy = X(DOT_AT[0]), Y(DOT_AT[1])
    dr = 0.052 * size
    cv.coverage_fill(dcx - dr - AA, dcy - dr - AA, dcx + dr + AA, dcy + dr + AA, DOT,
                     lambda px, py: clamp((dr - hypot(px - dcx, py - dcy)) / AA + 0.5, 0, 1))
    return cv


def write_png(path, cv):
    s = cv.buf
    size = cv.s
    raw = bytearray()
    for y in range(size):
        raw.append(0)  # filter: none
        raw.extend(s[y * size * 4:(y + 1) * size * 4])

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data +
                struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff))
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", ihdr))
        f.write(chunk(b"IDAT", zlib.compress(bytes(raw), 9)))
        f.write(chunk(b"IEND", b""))


def main():
    iconset = os.path.join(HERE, "AppIcon.iconset")
    os.makedirs(iconset, exist_ok=True)
    # (base size, [(filename, pixel size), ...])
    specs = [
        ("icon_16x16.png", 16), ("icon_16x16@2x.png", 32),
        ("icon_32x32.png", 32), ("icon_32x32@2x.png", 64),
        ("icon_128x128.png", 128), ("icon_128x128@2x.png", 256),
        ("icon_256x256.png", 256), ("icon_256x256@2x.png", 512),
        ("icon_512x512.png", 512), ("icon_512x512@2x.png", 1024),
    ]
    cache = {}
    for fname, px in specs:
        if px not in cache:
            cache[px] = render(px)
            print("rendered", px)
        write_png(os.path.join(iconset, fname), cache[px])
    out = os.path.join(HERE, "AppIcon.icns")
    subprocess.run(["iconutil", "-c", "icns", iconset, "-o", out], check=True)
    print("wrote", out)


if __name__ == "__main__":
    main()
