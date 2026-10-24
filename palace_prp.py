"""
palace_prp.py - read Palace .prp prop files (as written by ThePalace.exe 3.6 on Windows).

Layout (all little-endian), a Windows take on a Mac resource file:

    header   u32 data_offset, u32 data_length, u32 map_offset, u32 map_length
    map      u32 type_count, u32 ref_count, u32 ?, u32 type_list_off,
             u32 ref_list_off, u32 name_list_off      (offsets relative to map)
    types    12 bytes each: 4cc (byte reversed, 'porP' = 'Prop'), u32 count, u32 first_ref
    refs     32 bytes each: u32 id, u32 0, u32 data_off (rel. data_offset), u32 length,
             u32 timestamp, i32 name_off (-1 = none), u32 0, u32 crc
    names    pascal strings (length byte + text)

Each 'Prop' resource: u16 width, u16 height, i16 h_offset, i16 v_offset,
u16 script_offset, u16 flags, then pixel data (8-bit RLE for flags without
PF_20BIT/PF_32BIT/PF_S20BIT).

crc = asset_crc(pixel data after the 12-byte header). From FUN_0040c6e9:
seed 0xd9216290, rotate left 1, xor byte. Verified 99/99 on a real file.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

import palace_props as pp

U32 = 0xFFFFFFFF


def asset_crc(data: bytes) -> int:
    c = 0xD9216290
    for b in data:
        c = (((c << 1) | (c >> 31)) & U32) ^ b
    return c


@dataclass
class Prop:
    id: int
    name: str | None
    width: int
    height: int
    h_offset: int
    v_offset: int
    script_offset: int
    flags: int
    crc: int
    timestamp: int
    data: bytes            # pixel data after the 12-byte header

    @property
    def crc_ok(self) -> bool:
        return asset_crc(self.data) == self.crc

    def rgba(self) -> bytes:
        fmt = pp.format_from_flags(self.flags)
        ghost = bool(self.flags & pp.PF_GHOST)
        if fmt is None:
            return pp.decode_8bit(self.data, ghost=ghost)
        import zlib
        return pp.decode(zlib.decompress(self.data), fmt, ghost=ghost)


def read_prp(path: str) -> tuple[list[Prop], dict[str, bytes]]:
    """Returns (props, other_resources_by_type_and_id)."""
    d = open(path, "rb").read()
    data_off, data_len, map_off, map_len = struct.unpack_from("<4I", d, 0)
    if data_off + data_len != map_off or map_off + map_len != len(d):
        raise ValueError("header sizes don't add up, not a .prp?")
    ntypes, nrefs, _, type_off, ref_off, name_off = struct.unpack_from("<6I", d, map_off)

    ref_type = {}
    for i in range(ntypes):
        tag, count, first = struct.unpack_from("<4sII", d, map_off + type_off + i * 12)
        for r in range(first, first + count):
            ref_type[r] = tag[::-1].decode("latin1")

    props, other = [], {}
    for i in range(nrefs):
        rid, _, off, ln, ts, noff, _, crc = struct.unpack_from("<IIIIIiII", d, map_off + ref_off + i * 32)
        blob = d[data_off + off: data_off + off + ln]
        if ref_type.get(i) != "Prop":
            other[f"{ref_type.get(i)}:{rid}"] = blob
            continue
        name = None
        if noff >= 0:
            p = map_off + name_off + noff
            name = d[p + 1: p + 1 + d[p]].decode("latin1")
        w, h, hx, vy, so, fl = struct.unpack_from("<HHhhHH", blob, 0)
        props.append(Prop(rid, name, w, h, hx, vy, so, fl, crc, ts, blob[12:]))
    return props, other


def fave_blob(props: list[Prop], favorite_ids) -> bytes:
    """'Fave' resource: (u32 prop id, u32 crc) pairs, no header."""
    by_id = {p.id: p for p in props}
    out = bytearray()
    for i in favorite_ids:
        if i in by_id:
            out += struct.pack("<II", i, by_id[i].crc)
    return bytes(out)


def write_prp(props: list[Prop], path: str, extra: list[tuple[str, int, bytes]] = (),
              favorite_ids=None) -> None:
    """Write a .prp that ThePalace 3.6 and PalaceChat can read.

    PalaceChat's importer (PropWindow.importOldprops) throws an IO error unless
    a 'Fave' resource exists, so one is always written: favorite_ids if given,
    else the first prop. Pass a ('Fave', id, data) in extra to supply your own.
    """
    props = [Prop(**{**p.__dict__, "crc": asset_crc(p.data)}) for p in props]
    if not any(t == "Fave" for t, _, _ in extra):
        fav = fave_blob(props, favorite_ids or [])
        if not fav and props:
            fav = fave_blob(props, [props[0].id])
        extra = list(extra) + [("Fave", 128, fav)]
    entries = []   # (type, id, blob, name, timestamp, crc)
    for p in props:
        hdr = struct.pack("<HHhhHH", p.width, p.height, p.h_offset, p.v_offset,
                          p.script_offset, p.flags)
        entries.append(("Prop", p.id, hdr + p.data, p.name, p.timestamp, asset_crc(p.data)))
    for tag, rid, blob in extra:
        entries.append((tag, rid, blob, None, 0, 0))

    # group by type, keep order within a type
    order = []
    for e in entries:
        if e[0] not in order:
            order.append(e[0])
    entries.sort(key=lambda e: order.index(e[0]))

    data = bytearray()
    names = bytearray()
    refs = bytearray()
    for tag, rid, blob, name, ts, crc in entries:
        name_off = -1
        if name:
            raw = name.encode("latin1", "replace")[:255]
            name_off = len(names)
            names += bytes([len(raw)]) + raw
        refs += struct.pack("<IIIIIiII", rid, 0, len(data), len(blob), ts, name_off, 0, crc)
        data += blob

    types = bytearray()
    first = 0
    for tag in order:
        count = sum(1 for e in entries if e[0] == tag)
        types += struct.pack("<4sII", tag.encode("latin1")[::-1], count, first)
        first += count

    type_off = 24
    ref_off = type_off + len(types)
    name_off = ref_off + len(refs)
    map_ = struct.pack("<6I", len(order), len(entries), len(names), type_off, ref_off, name_off)
    map_ += types + refs + names

    header = struct.pack("<4I", 16, len(data), 16 + len(data), len(map_))
    with open(path, "wb") as f:
        f.write(header + data + map_)


def contact_sheet(props: list[Prop], path: str, cols: int = 10, scale: int = 2) -> None:
    from PIL import Image, ImageDraw
    cell = 44 * scale + 8
    rows = (len(props) + cols - 1) // cols
    sheet = Image.new("RGBA", (cols * cell, rows * (cell + 14)), (40, 40, 48, 255))
    draw = ImageDraw.Draw(sheet)
    for n, p in enumerate(props):
        x, y = (n % cols) * cell + 4, (n // cols) * (cell + 14) + 4
        img = pp.to_image(p.rgba()).resize((44 * scale, 44 * scale), Image.NEAREST)
        sheet.alpha_composite(img, (x, y))
        label = (p.name or str(p.id))[:14]
        draw.text((x, y + 44 * scale + 1), label, fill=(220, 220, 220, 255))
    sheet.save(path)


if __name__ == "__main__":
    import os
    import sys
    if len(sys.argv) < 2:
        print("usage: python palace_prp.py file.prp [out_dir]")
        sys.exit(1)
    props, other = read_prp(sys.argv[1])
    out = sys.argv[2] if len(sys.argv) > 2 else "prp_out"
    os.makedirs(out, exist_ok=True)
    bad = [p.id for p in props if not p.crc_ok]
    print(f"{len(props)} props, {len(bad)} CRC failures, other resources: {list(other)}")
    for p in props:
        pp.save_png(p.rgba(), os.path.join(out, f"{p.id}.png"))
    contact_sheet(props, os.path.join(out, "_contact_sheet.png"))
    print("wrote", out)
