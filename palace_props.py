"""
palace_props.py - decode Palace props (44x44) into RGBA.

Reconstructed from PalaceChat 5.0.280's StringtoProp.DLL (sha256 4c5c2f99...),
the RealBasic/Xojo plugin that exports:
    Decode8bitProp(theData as String, alpha as Integer = 0) as MemoryBlock
    StringtoPalaceProp(theData as String, len as Integer, alpha as Integer) as MemoryBlock

Formats (all decode to 44x44 = 1936 pixels):
    8-bit   run-length coded indexes into a 256 color palette
    20-bit  5 bytes per 2 pixels: 6 bits R, G, B + 2 bits alpha
    S20     5 bytes per 2 pixels: 5 bits R, G, B, A
    32-bit  4 bytes per pixel, RGBA

The 20/S20/32 decoders expect data that is already inflated. In the client that
happens before the plugin is called (zlib1.dll sits next to the exe).

Differences from the DLL, on purpose:
    * output is RGBA (the DLL writes BGRA for Windows bitmaps)
    * input sizes are checked; the DLL copies into a fixed global buffer
      with no length check
    * palette index 0 is pure white by default. PalaceChat's table has fefefe
      there; pass faithful_palette=True to get that back.
"""

from __future__ import annotations

import math
import struct
from enum import IntEnum

WIDTH = HEIGHT = 44
PIXELS = WIDTH * HEIGHT          # 1936
RGBA_SIZE = PIXELS * 4           # 7744, same as the DLL's 0x1e40 output
SIZE_20BIT = PIXELS // 2 * 5     # 4840
SIZE_32BIT = PIXELS * 4          # 7744


class PropFormat(IntEnum):
    """The DLL's format selector (the misnamed `len` argument)."""
    S20BIT = 1
    BIT20 = 2
    BIT32 = 3


# Prop flag bits. These come from the classic Palace protocol, not from the
# DLL, so treat them as "known from docs, not yet confirmed in PalaceChat".
PF_HEAD = 0x0002
PF_GHOST = 0x0004
PF_RARE = 0x0008
PF_ANIMATE = 0x0010
PF_BOUNCE = 0x0020
PF_20BIT = 0x0040
PF_32BIT = 0x0100
PF_S20BIT = 0x0200


# --- palette ---------------------------------------------------------------
# 1024 bytes read straight out of StringtoProp.DLL at 0x407008.
# Stored as B, G, R, A per entry (Windows byte order).
_PALETTE_BGRA_HEX = (
    "fefefeffffffccffffff99ffffff66ffffff33ffffff00ffffdfffffffdfccffffdf99ff"
    "ffdf66ffffdf33ffffdf00ffffbfffffffbfccffffbf99ffffbf66ffffbf33ffffbf00ff"
    "ff9fffffff9fccffff9f99ffff9f66ffff9f33ffff9f00ffff7fffffff7fccffff7f99ff"
    "ff7f66ffff7f33ffff7f00ffff5fffffff5fccffff5f99ffff5f66ffff5f33ffff5f00ff"
    "ff3fffffff3fccffff3f99ffff3f66ffff3f33ffff3f00ffff1fffffff1fccffff1f99ff"
    "ff1f66ffff1f33ffff1f00ffff00ffffff00ccffff0099ffff0066ffff0033ffff0000ff"
    "eeeeeeffddddddffccccccffbbbbbbffaaffffffaaffccffaaff99ffaaff66ffaaff33ff"
    "aaff00ffaadfffffaadfccffaadf99ffaadf66ffaadf33ffaadf00ffaabfffffaabfccff"
    "aabf99ffaabf66ffaabf33ffaabf00ffaaaaaaffaa9fffffaa9fccffaa9f99ffaa9f66ff"
    "aa9f33ffaa9f00ffaa7fffffaa7fccffaa7f99ffaa7f66ffaa7f33ffaa7f00ffaa5fffff"
    "aa5fccffaa5f99ffaa5f66ffaa5f33ffaa5f00ffaa3fffffaa3fccffaa3f99ffaa3f66ff"
    "aa3f33ffaa3f00ffaa1fffffaa1fccffaa1f99ffaa1f66ffaa1f33ffaa1f00ffaa00ffff"
    "aa00ccffaa0099ffaa0066ffaa0033ffaa0000ff999999ff888888ff777777ff666666ff"
    "55ffffff55ffccff55ff99ff55ff66ff55ff33ff55ff00ff55dfffff55dfccff55df99ff"
    "55df66ff55df33ff55df00ff55bfffff55bfccff55bf99ff55bf66ff55bf33ff55bf00ff"
    "559fffff559fccff559f99ff559f66ff559f33ff559f00ff557fffff557fccff557f99ff"
    "557f66ff557f33ff557f00ff555fffff555fccff555f99ff555f66ff555f33ff555f00ff"
    "555555ff553fffff553fccff553f99ff553f66ff553f33ff553f00ff551fffff551fccff"
    "551f99ff551f66ff551f33ff551f00ff5500ffff5500ccff550099ff550066ff550033ff"
    "550000ff444444ff333333ff222222ff111111ff00ffffff00ffccff00ff99ff00ff66ff"
    "00ff33ff00ff00ff00dfffff00dfccff00df99ff00df66ff00df33ff00df00ff00bfffff"
    "00bfccff00bf99ff00bf66ff00bf33ff00bf00ff009fffff009fccff009f99ff009f66ff"
    "009f33ff009f00ff007fffff007fccff007f99ff007f66ff007f33ff007f00ff005fffff"
    "005fccff005f99ff005f66ff005f33ff005f00ff003fffff003fccff003f99ff003f66ff"
    "003f33ff003f00ff001fffff001fccff001f99ff001f66ff001f33ff001f00ff0000ffff"
    "0000ccff000099ff000066ff000033ff000000ff000000ff000000ff000000ff000000ff"
    "000000ff000000ff000000ff000000ff000000ff000000fff0f0f0ffe0e0e0ffd0d0d0ff"
    "c0c0c0ffb0b0b0ffa0a0a0ff808080ff707070ff606060ff505050ff404040ff303030ff"
    "202020ff101010ff080808ff000000ff"
)


def _build_palette(faithful: bool) -> list[tuple[int, int, int]]:
    raw = bytes.fromhex(_PALETTE_BGRA_HEX)
    assert len(raw) == 1024
    pal = [(raw[i * 4 + 2], raw[i * 4 + 1], raw[i * 4]) for i in range(256)]
    if not faithful:
        pal[0] = (255, 255, 255)
    return pal


PALETTE = _build_palette(faithful=False)           # RGB, index 0 = white
PALETTE_PALACECHAT = _build_palette(faithful=True)  # RGB, index 0 = fefefe


# --- scaling, matching the DLL's x87 math -----------------------------------
# Each channel is (masked bits) * K, optionally / alpha_div, then floored.
# K values are the doubles at 0x408008..0x408068 in the DLL.
_K = {
    "a20": 255 / 48,       # 0x408008
    "r6": 255 / 252,       # 0x408010
    "g6": 255 / 1008,      # 0x408018
    "b6": 255 / 4032,      # 0x408020
    "x6": 255 / 63,        # 0x408028
}
_K_S20 = [struct.unpack("<d", bytes.fromhex(h))[0] for h in (
    "74ce39e79c73703f",  # 0x408030  ~255/63488
    "74ce39e79c73c03f",  # 0x408038  ~255/1984
    "74ce39e79c731040",  # 0x408040  ~255/62
    "74ce39e79c73e03f",  # 0x408048  ~255/496
    "74ce39e79c73b03f",  # 0x408050  ~255/3968
    "74ce39e79c730040",  # 0x408058  ~255/124
    "74ce39e79c73d03f",  # 0x408060  ~255/992
    "74ce39e79c732040",  # 0x408068  ~255/31
)]
# The 20-bit ones are stored as 0cc3300cc330.... doubles; Python's 255/n
# produces the same bit patterns, checked in the tests.


def _scale(value: int, k: float, div: int = 1) -> int:
    # Double precision multiply then floor. Windows runs 32-bit processes
    # with the x87 in 53-bit mode, so this matches the DLL. In 64-bit
    # extended mode a few 20-bit values (85/170/255) come out one lower.
    v = value * k
    if div != 1:
        v = v / div
    return int(math.floor(v))


# --- decoders --------------------------------------------------------------

def decode_8bit(data: bytes, ghost: bool = False, faithful_palette: bool = False) -> bytes:
    """Decode the classic RLE 8-bit prop format to RGBA.

    Control byte: high nibble = transparent pixels to skip,
                  low nibble  = literal palette indexes that follow.
    Stops after 1936 pixels or when input runs out.
    ghost=True mirrors the DLL's alpha>=2 path: opaque pixels get alpha 0x7f.
    """
    pal = PALETTE_PALACECHAT if faithful_palette else PALETTE
    out = bytearray(RGBA_SIZE)          # starts fully transparent, like the DLL
    opaque = 0x7F if ghost else 0xFF
    remaining = PIXELS
    pos = 0                              # output pixel
    i = 0                                # input byte
    n = len(data)
    while i < n and remaining > 0:
        ctrl = data[i]
        i += 1
        skip, lit = ctrl >> 4, ctrl & 0x0F
        remaining -= skip + lit
        if remaining < 0:
            break
        pos += skip
        for _ in range(lit):
            if i >= n:                   # DLL would read junk here; we stop
                break
            r, g, b = pal[data[i]]
            i += 1
            o = pos * 4
            out[o:o + 4] = bytes((r, g, b, opaque))
            pos += 1
    return bytes(out)


def decode_20bit(data: bytes, alpha_div: int = 1) -> bytes:
    """20-bit: per pixel 6 bits R, G, B and 2 bits alpha, 2 pixels per 5 bytes."""
    _check(data, SIZE_20BIT, "20-bit")
    out = bytearray(RGBA_SIZE)
    k = _K
    for p in range(PIXELS // 2):
        b0, b1, b2, b3, b4 = data[p * 5:p * 5 + 5]
        o = p * 8
        # pixel A
        out[o + 0] = _scale(b0 & 0xFC, k["r6"])
        out[o + 1] = _scale(((b0 << 8) | b1) & 0x3F0, k["g6"])
        out[o + 2] = _scale(((b1 << 8) | b2) & 0xFC0, k["b6"])
        out[o + 3] = _scale(((b1 << 8) | b2) & 0x30, k["a20"], alpha_div)
        # pixel B
        out[o + 4] = _scale(((b2 << 8) | b3) & 0xFC0, k["b6"])
        out[o + 5] = _scale(b3 & 0x3F, k["x6"])
        out[o + 6] = _scale(b4 & 0xFC, k["r6"])
        out[o + 7] = ((b4 & 0x03) * 0x55) // alpha_div
    return bytes(out)


def decode_s20bit(data: bytes, alpha_div: int = 1) -> bytes:
    """S20: per pixel 5 bits each of R, G, B, A, 2 pixels per 5 bytes."""
    _check(data, SIZE_20BIT, "S20")
    out = bytearray(RGBA_SIZE)
    k = _K_S20
    for p in range(PIXELS // 2):
        b0, b1, b2, b3, b4 = data[p * 5:p * 5 + 5]
        o = p * 8
        # pixel A
        out[o + 0] = _scale((b0 & 0xF8) << 8, k[0])
        out[o + 1] = _scale(((b0 << 8) | b1) & 0x7C0, k[1])
        out[o + 2] = _scale(b1 & 0x3E, k[2])
        out[o + 3] = _scale(((b1 << 8) | b2) & 0x1F0, k[3], alpha_div)
        # pixel B
        out[o + 4] = _scale(((b2 << 8) | b3) & 0xF80, k[4])
        out[o + 5] = _scale(b3 & 0x7C, k[5])
        out[o + 6] = _scale(((b3 << 8) | b4) & 0x3E0, k[6])
        out[o + 7] = _scale(b4 & 0x1F, k[7], alpha_div)
    return bytes(out)


def decode_32bit(data: bytes, alpha_div: int = 1) -> bytes:
    """32-bit: straight RGBA, alpha integer-divided by alpha_div."""
    _check(data, SIZE_32BIT, "32-bit")
    if alpha_div == 1:
        return bytes(data)
    out = bytearray(data)
    for i in range(3, RGBA_SIZE, 4):
        out[i] //= alpha_div
    return bytes(out)


def decode(data: bytes, fmt: int | PropFormat | None, ghost: bool = False) -> bytes:
    """One entry point. fmt=None means 8-bit; otherwise a PropFormat.
    ghost halves alpha the same way PalaceChat does."""
    if fmt is None:
        return decode_8bit(data, ghost=ghost)
    div = 2 if ghost else 1
    fmt = PropFormat(fmt)
    if fmt is PropFormat.S20BIT:
        return decode_s20bit(data, div)
    if fmt is PropFormat.BIT20:
        return decode_20bit(data, div)
    return decode_32bit(data, div)


def format_from_flags(flags: int) -> PropFormat | None:
    """Map Palace prop flags to a format. Unverified against PalaceChat."""
    if flags & PF_S20BIT:
        return PropFormat.S20BIT
    if flags & PF_32BIT:
        return PropFormat.BIT32
    if flags & PF_20BIT:
        return PropFormat.BIT20
    return None


def encode_8bit(indices: bytes, alpha: bytes | None = None) -> bytes:
    """Inverse of decode_8bit, matching ThePalace 3.6 byte for byte (checked
    on 99 props from a real .prp). Each 44 pixel row is coded on its own:
    up to 15 transparent pixels to skip, then up to 15 literal indexes,
    repeated until the row ends. A pixel is transparent when alpha is 0."""
    if len(indices) != PIXELS:
        raise ValueError(f"expected {PIXELS} indexes, got {len(indices)}")
    opaque = (lambda p: True) if alpha is None else (lambda p: alpha[p] != 0)
    out = bytearray()
    for y in range(HEIGHT):
        x = 0
        row = y * WIDTH
        while x < WIDTH:
            skip = 0
            while x < WIDTH and not opaque(row + x) and skip < 15:
                skip += 1
                x += 1
            lits = bytearray()
            while x < WIDTH and opaque(row + x) and len(lits) < 15:
                lits.append(indices[row + x])
                x += 1
            out.append((skip << 4) | len(lits))
            out += lits
    return bytes(out)


# --- helpers ---------------------------------------------------------------

def indices_to_rgba(indices: bytes, alpha: bytes | None = None,
                    faithful_palette: bool = False) -> bytes:
    """Already-unpacked 8-bit data (one index per pixel), like the
    avatars.thepalace.app /raw endpoint returns."""
    if len(indices) != PIXELS:
        raise ValueError(f"expected {PIXELS} indexes, got {len(indices)}")
    pal = PALETTE_PALACECHAT if faithful_palette else PALETTE
    out = bytearray(RGBA_SIZE)
    for p, idx in enumerate(indices):
        a = 255 if alpha is None else alpha[p]
        if a == 0:
            continue                     # fully transparent stays 0,0,0,0
        r, g, b = pal[idx]
        out[p * 4:p * 4 + 4] = bytes((r, g, b, a))
    return bytes(out)


def to_image(rgba: bytes):
    """RGBA bytes -> PIL Image (needs Pillow)."""
    from PIL import Image
    return Image.frombytes("RGBA", (WIDTH, HEIGHT), rgba)


def save_png(rgba: bytes, path: str) -> None:
    to_image(rgba).save(path)


def _check(data: bytes, expected: int, name: str) -> None:
    if len(data) != expected:
        raise ValueError(f"{name} prop data must be {expected} bytes after inflate, got {len(data)}")
