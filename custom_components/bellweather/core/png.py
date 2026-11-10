"""Read one pixel of a PNG without Pillow.

Enough for map tiles: 8-bit RGB, RGBA and palette images, not interlaced.
"""

from __future__ import annotations

import struct
import zlib

from .safety import MAX_PNG_PIXELS

_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class PngError(ValueError):
    pass


def _rows(data: bytes, last_row: int | None, expect: tuple[int, int] | None = None):
    """Header fields and unfiltered rows 0..last_row. The decompressed size
    is capped at what the header promises, so a crafted file cannot expand
    without limit."""
    if not data.startswith(_SIGNATURE):
        raise PngError("not a PNG")
    pos = len(_SIGNATURE)
    width = height = depth = colour = interlace = None
    palette: bytes = b""
    alpha: bytes = b""
    idat = bytearray()
    while pos + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[pos : pos + 8])
        body = data[pos + 8 : pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            if len(body) != 13:
                raise PngError("bad header")
            width, height, depth, colour, _, _, interlace = struct.unpack(">IIBBBBB", body)
        elif kind == b"PLTE":
            palette = body
        elif kind == b"tRNS":
            alpha = body
        elif kind == b"IDAT":
            idat += body
        elif kind == b"IEND":
            break
    if width is None or depth != 8 or interlace != 0:
        raise PngError("unsupported PNG layout")
    if width * height > MAX_PNG_PIXELS or width <= 0 or height <= 0:
        raise PngError("image too large")
    if expect is not None and (width, height) != expect:
        raise PngError("unexpected image size")
    bpp = {2: 3, 3: 1, 6: 4}.get(colour)
    if bpp is None:
        raise PngError(f"unsupported colour type {colour}")
    if last_row is None:
        last_row = height - 1
    if not 0 <= last_row < height:
        raise PngError("row outside the image")
    stride = width * bpp
    expected = height * (stride + 1)
    inflater = zlib.decompressobj()
    raw = inflater.decompress(bytes(idat), expected + 1)
    if len(raw) > expected or inflater.unconsumed_tail:
        raise PngError("more image data than the header allows")
    if len(raw) < (last_row + 1) * (stride + 1):
        raise PngError("truncated image")
    previous = bytearray(stride)
    rows: list[bytearray] = []
    for r in range(last_row + 1):
        start = r * (stride + 1)
        filt = raw[start]
        line = raw[start + 1 : start + 1 + stride]
        row = bytearray(stride)
        for i in range(stride):
            a = row[i - bpp] if i >= bpp else 0
            b = previous[i]
            c = previous[i - bpp] if i >= bpp else 0
            if filt == 0:
                pred = 0
            elif filt == 1:
                pred = a
            elif filt == 2:
                pred = b
            elif filt == 3:
                pred = (a + b) // 2
            elif filt == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
            else:
                raise PngError(f"bad filter {filt}")
            row[i] = (line[i] + pred) & 0xFF
        rows.append(row)
        previous = row
    return width, height, colour, bpp, palette, alpha, rows


def _rgba(px: bytes | bytearray, colour: int, palette: bytes, alpha: bytes) -> tuple[int, int, int, int]:
    if colour == 6:
        return px[0], px[1], px[2], px[3]
    if colour == 2:
        return px[0], px[1], px[2], 255
    index = px[0]
    if index * 3 + 3 > len(palette):
        raise PngError("palette index out of range")
    r, g, b = palette[index * 3 : index * 3 + 3]
    a = alpha[index] if index < len(alpha) else 255
    return r, g, b, a


def pixel(data: bytes, x: int, y: int, expect: tuple[int, int] | None = None) -> tuple[int, int, int, int]:
    """RGBA of the pixel at (x, y)."""
    width, _, colour, bpp, palette, alpha, rows = _rows(data, y, expect)
    if not 0 <= x < width:
        raise PngError("pixel outside the image")
    return _rgba(rows[y][x * bpp : x * bpp + bpp], colour, palette, alpha)


def image(data: bytes, expect: tuple[int, int] | None = None) -> tuple[int, int, list[list[tuple[int, int, int, int]]]]:
    """All pixels as RGBA rows: (width, height, rows)."""
    width, height, colour, bpp, palette, alpha, rows = _rows(data, None, expect)
    return width, height, [
        [_rgba(row[x * bpp : x * bpp + bpp], colour, palette, alpha) for x in range(width)] for row in rows
    ]
