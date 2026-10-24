"""Pictures the suite draws itself, with nothing but ``zlib``: rectangles, circles, triangles, lines and text in a 5x7 font.

The vision questions are made of pictures like these (CLEVR-style: shapes and text whose content is known exactly),
because a photograph has no ground truth a program can check. A picture is described by what was drawn into it, so the
SHA-256 of its *pixels* identifies it: the PNG file itself depends on the zlib version, the pixels do not.
"""

from __future__ import annotations

import hashlib
import struct
import zlib

Color = tuple[int, int, int]

# 5 columns by 7 rows. Capitals, digits and the punctuation a receipt or a label needs.
_GLYPHS: dict[str, tuple[str, ...]] = {
    " ": (".....",) * 7,
    "0": (".###.", "#...#", "#..##", "#.#.#", "##..#", "#...#", ".###."),
    "1": ("..#..", ".##..", "..#..", "..#..", "..#..", "..#..", ".###."),
    "2": (".###.", "#...#", "....#", "...#.", "..#..", ".#...", "#####"),
    "3": (".###.", "#...#", "....#", "..##.", "....#", "#...#", ".###."),
    "4": ("...#.", "..##.", ".#.#.", "#..#.", "#####", "...#.", "...#."),
    "5": ("#####", "#....", "####.", "....#", "....#", "#...#", ".###."),
    "6": ("..##.", ".#...", "#....", "####.", "#...#", "#...#", ".###."),
    "7": ("#####", "....#", "...#.", "..#..", ".#...", ".#...", ".#..."),
    "8": (".###.", "#...#", "#...#", ".###.", "#...#", "#...#", ".###."),
    "9": (".###.", "#...#", "#...#", ".####", "....#", "...#.", ".##.."),
    "A": (".###.", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"),
    "B": ("####.", "#...#", "#...#", "####.", "#...#", "#...#", "####."),
    "C": (".###.", "#...#", "#....", "#....", "#....", "#...#", ".###."),
    "D": ("####.", "#...#", "#...#", "#...#", "#...#", "#...#", "####."),
    "E": ("#####", "#....", "#....", "####.", "#....", "#....", "#####"),
    "F": ("#####", "#....", "#....", "####.", "#....", "#....", "#...."),
    "G": (".###.", "#...#", "#....", "#.###", "#...#", "#...#", ".###."),
    "H": ("#...#", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"),
    "I": (".###.", "..#..", "..#..", "..#..", "..#..", "..#..", ".###."),
    "J": ("..###", "...#.", "...#.", "...#.", "...#.", "#..#.", ".##.."),
    "K": ("#...#", "#..#.", "#.#..", "##...", "#.#..", "#..#.", "#...#"),
    "L": ("#....", "#....", "#....", "#....", "#....", "#....", "#####"),
    "M": ("#...#", "##.##", "#.#.#", "#.#.#", "#...#", "#...#", "#...#"),
    "N": ("#...#", "##..#", "#.#.#", "#..##", "#...#", "#...#", "#...#"),
    "O": (".###.", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."),
    "P": ("####.", "#...#", "#...#", "####.", "#....", "#....", "#...."),
    "Q": (".###.", "#...#", "#...#", "#...#", "#.#.#", "#..#.", ".##.#"),
    "R": ("####.", "#...#", "#...#", "####.", "#.#..", "#..#.", "#...#"),
    "S": (".####", "#....", "#....", ".###.", "....#", "....#", "####."),
    "T": ("#####", "..#..", "..#..", "..#..", "..#..", "..#..", "..#.."),
    "U": ("#...#", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."),
    "V": ("#...#", "#...#", "#...#", "#...#", "#...#", ".#.#.", "..#.."),
    "W": ("#...#", "#...#", "#...#", "#.#.#", "#.#.#", "##.##", "#...#"),
    "X": ("#...#", "#...#", ".#.#.", "..#..", ".#.#.", "#...#", "#...#"),
    "Y": ("#...#", "#...#", ".#.#.", "..#..", "..#..", "..#..", "..#.."),
    "Z": ("#####", "....#", "...#.", "..#..", ".#...", "#....", "#####"),
    ".": (".....", ".....", ".....", ".....", ".....", ".##..", ".##.."),
    ",": (".....", ".....", ".....", ".....", ".##..", "..#..", ".#..."),
    ":": (".....", ".##..", ".##..", ".....", ".##..", ".##..", "....."),
    "-": (".....", ".....", ".....", "#####", ".....", ".....", "....."),
    "+": (".....", "..#..", "..#..", "#####", "..#..", "..#..", "....."),
    "/": ("....#", "....#", "...#.", "..#..", ".#...", "#....", "#...."),
    "%": ("##..#", "##.#.", "...#.", "..#..", ".#...", ".#.##", "#..##"),
    "=": (".....", ".....", "#####", ".....", "#####", ".....", "....."),
    "(": ("...#.", "..#..", ".#...", ".#...", ".#...", "..#..", "...#."),
    ")": (".#...", "..#..", "...#.", "...#.", "...#.", "..#..", ".#..."),
    "!": ("..#..", "..#..", "..#..", "..#..", "..#..", ".....", "..#.."),
    "?": (".###.", "#...#", "....#", "...#.", "..#..", ".....", "..#.."),
}
GLYPH_W, GLYPH_H = 5, 7


class Canvas:
    def __init__(self, width: int, height: int, background: Color = (255, 255, 255)) -> None:
        self.width, self.height = width, height
        self._buf = bytearray(bytes(background) * (width * height))

    def set(self, x: int, y: int, color: Color) -> None:
        if 0 <= x < self.width and 0 <= y < self.height:
            i = (y * self.width + x) * 3
            self._buf[i : i + 3] = bytes(color)

    def rect(self, x: int, y: int, w: int, h: int, color: Color) -> None:
        x0, x1 = max(0, x), min(self.width, x + w)
        if x1 <= x0:
            return
        row = bytes(color) * (x1 - x0)
        for yy in range(max(0, y), min(self.height, y + h)):
            start = (yy * self.width + x0) * 3
            self._buf[start : start + len(row)] = row

    def frame(self, x: int, y: int, w: int, h: int, color: Color, thickness: int = 1) -> None:
        self.rect(x, y, w, thickness, color)
        self.rect(x, y + h - thickness, w, thickness, color)
        self.rect(x, y, thickness, h, color)
        self.rect(x + w - thickness, y, thickness, h, color)

    def circle(self, cx: int, cy: int, r: int, color: Color) -> None:
        for yy in range(cy - r, cy + r + 1):
            for xx in range(cx - r, cx + r + 1):
                if (xx - cx) ** 2 + (yy - cy) ** 2 <= r * r:
                    self.set(xx, yy, color)

    def triangle(self, p1: tuple[int, int], p2: tuple[int, int], p3: tuple[int, int], color: Color) -> None:
        xs, ys = [p1[0], p2[0], p3[0]], [p1[1], p2[1], p3[1]]

        def side(a: tuple[int, int], b: tuple[int, int], x: int, y: int) -> int:
            return (b[0] - a[0]) * (y - a[1]) - (b[1] - a[1]) * (x - a[0])

        for yy in range(min(ys), max(ys) + 1):
            for xx in range(min(xs), max(xs) + 1):
                d = (side(p1, p2, xx, yy), side(p2, p3, xx, yy), side(p3, p1, xx, yy))
                if all(v >= 0 for v in d) or all(v <= 0 for v in d):
                    self.set(xx, yy, color)

    def line(self, x0: int, y0: int, x1: int, y1: int, color: Color, thickness: int = 1) -> None:
        dx, dy = abs(x1 - x0), -abs(y1 - y0)
        sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
        err = dx + dy
        while True:
            self.rect(x0 - thickness // 2, y0 - thickness // 2, thickness, thickness, color)
            if x0 == x1 and y0 == y1:
                break
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x0 += sx
            if e2 <= dx:
                err += dx
                y0 += sy

    def text_width(self, value: str, scale: int = 1) -> int:
        return len(value) * (GLYPH_W + 1) * scale - scale if value else 0

    def text(self, x: int, y: int, value: str, color: Color, scale: int = 1) -> None:
        for char in value:
            if char not in _GLYPHS:
                raise ValueError(f"the picture font has no glyph for {char!r}")
        cursor = x
        for char in value:
            for row, pattern in enumerate(_GLYPHS[char]):
                for col, bit in enumerate(pattern):
                    if bit == "#":
                        self.rect(cursor + col * scale, y + row * scale, scale, scale, color)
            cursor += (GLYPH_W + 1) * scale

    def pixels_sha256(self) -> str:
        return hashlib.sha256(f"{self.width}x{self.height}:".encode() + bytes(self._buf)).hexdigest()

    def png(self) -> bytes:
        def chunk(kind: bytes, body: bytes) -> bytes:
            return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

        stride = self.width * 3
        raw = b"".join(b"\x00" + bytes(self._buf[y * stride : (y + 1) * stride]) for y in range(self.height))
        return (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", self.width, self.height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b"")
        )
