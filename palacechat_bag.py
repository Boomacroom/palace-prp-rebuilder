"""
palacechat_bag.py - read and add props to PalaceChat 5's own prop bag.

PalaceChat keeps its bag in Documents/PalaceChat/PropBag.bundle/:

    Version              1 byte, 0x01
    PalaceChat.props     every stored prop, one record after another, no header
    PalaceChat.pids      the index: 16 bytes per prop, same order as .props
    PalaceChat.favs      the main "PalaceChat" category: index entries you see in the bag
    N-Name.favs          your other categories (N sets the order), same entry format
    Trash.favs           the trash category
    macro                saved macros (not touched here)

All integers are big-endian.

Index entry (.pids and .favs): u32 id, u32 crc, u32 offset into .props, u32 length.
.pids also holds props PalaceChat cached from other people; only the .favs files are
what shows in your bag.

Prop record (from PropM.editpropdata in PalaceChat 5.0.280, checked against a real bag):

    Str31 name           32 bytes, Pascal string
    i16 width, height    always 44, 44
    i16 h_offset, v_offset
    i16 0
    u16 flags
    data

data by format (flags):
    8-bit                ThePalace RLE, as in .prp files              crc = asset_crc(data)
    0x40 20-bit          4840 bytes, uncompressed                     crc = asset_crc(zlib(data))
    0x200 S20            4840 bytes, uncompressed                     crc = asset_crc(zlib(data))
    0x100 32-bit         7744 bytes RGBA, uncompressed                crc = asset_crc(zlib(data))
    0x400 PNG            S20 44x44 fallback + trailer + PNG           crc = asset_crc(zlib(S20 part))
    0x800 (with 0x400)   same, but the payload is an animated WebP

PNG/WebP trailer, after the 4840 S20 bytes: u32 payload length, i16 w, h, x, y,
i16 speed (WebP frame delay, 0 for PNG), u16 flags, then the payload. The record header
keeps h/v offset at 0 for these.

CRCs of props PalaceChat downloaded from servers are whatever the server sent, so they
don't always follow the rules above. Keep the original crc when importing a prop that
already has one: that's what lets PalaceChat find it when someone in a room wears it.

PalaceChat holds these files open and keeps the index in memory, so only write to a bag
while PalaceChat is closed.
"""
from __future__ import annotations

import datetime as dt
import glob
import os
import random
import re
import shutil
import struct
import subprocess
import sys
import zlib
from dataclasses import dataclass

import palace_props as pp
from palace_prp import asset_crc

PF_PNG = 0x0400
PF_ANIMATED = 0x0800
ENTRY = struct.Struct(">IIII")
HEADER = struct.Struct(">hhhhhH")
TRAILER = struct.Struct(">IhhhhhH")
FORMAT_BITS = pp.PF_20BIT | 0x80 | pp.PF_32BIT | pp.PF_S20BIT | PF_PNG | PF_ANIMATED
# PNG and animated WebP props share one layout: S20 44x44 thumbnail, TRAILER, image bytes.
# PalaceChat's advanced editor writes animated props with 0x0800 alone (no PNG bit).
PF_IMAGE = PF_PNG | PF_ANIMATED
PROP_MAX = 220          # PalaceChat's own editors fit props in 220x220


def default_bundle() -> str:
    return os.path.join(os.path.expanduser("~"), "Documents", "PalaceChat", "PropBag.bundle")


@dataclass
class Entry:
    id: int
    crc: int
    offset: int
    length: int

    def pack(self) -> bytes:
        return ENTRY.pack(self.id & 0xFFFFFFFF, self.crc & 0xFFFFFFFF, self.offset, self.length)


@dataclass
class BagProp:
    name: str
    flags: int
    data: bytes
    h_offset: int = 0
    v_offset: int = 0
    id: int | None = None
    crc: int | None = None

    def record(self) -> bytes:
        raw = (self.name or "").encode("latin1", "replace")[:31]
        name = bytes([len(raw)]) + raw + b"\0" * (31 - len(raw))
        return name + HEADER.pack(44, 44, self.h_offset, self.v_offset, 0, self.flags) + self.data

    def computed_crc(self) -> int:
        """The crc PalaceChat itself would give this prop."""
        if self.flags & PF_IMAGE:          # PropM.makepropdata: crc of zlib(S20 thumbnail)
            return asset_crc(zlib.compress(self.data[:pp.SIZE_20BIT]))
        if self.flags & FORMAT_BITS:
            return asset_crc(zlib.compress(self.data))
        return asset_crc(self.data)


def read_entries(path: str) -> list[Entry]:
    if not os.path.exists(path):
        return []
    d = open(path, "rb").read()
    if len(d) % 16:
        raise ValueError(f"{path}: size {len(d)} is not a multiple of 16")
    return [Entry(*ENTRY.unpack_from(d, i)) for i in range(0, len(d), 16)]


def parse_record(rec: bytes) -> BagProp:
    name = rec[1:1 + min(rec[0], 31)].decode("latin1")
    _w, _h, ho, vo, _z, flags = HEADER.unpack_from(rec, 32)
    return BagProp(name, flags, rec[44:], ho, vo)


def palacechat_running() -> bool | None:
    """True/False on Windows, None when we can't tell."""
    if sys.platform != "win32":
        return None
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq PalaceChat.exe"],
                             capture_output=True, text=True, timeout=10).stdout
        return "PalaceChat.exe" in out
    except Exception:
        return None


class Bag:
    def __init__(self, bundle: str | None = None):
        self.dir = bundle or default_bundle()
        if not os.path.isdir(self.dir):
            raise FileNotFoundError(f"no prop bag at {self.dir}")
        self.props_path = os.path.join(self.dir, "PalaceChat.props")
        self.pids_path = os.path.join(self.dir, "PalaceChat.pids")
        self.index = read_entries(self.pids_path)
        size = os.path.getsize(self.props_path)
        end = max((e.offset + e.length for e in self.index), default=0)
        if end != size:
            raise ValueError(f"index ends at {end} but PalaceChat.props is {size} bytes; "
                             "the bag looks damaged, let PalaceChat repair it first")

    # ---------------------------------------------------------------- reading
    def categories(self) -> dict[str, str]:
        """Category name -> .favs path. 'PalaceChat' is the main one."""
        out = {}
        for p in sorted(glob.glob(os.path.join(self.dir, "*.favs")), key=_category_sort):
            out[_category_name(p)] = p
        return out

    def entries(self, category: str = "PalaceChat") -> list[Entry]:
        return read_entries(self.categories()[category])

    def read(self, e: Entry) -> BagProp:
        with open(self.props_path, "rb") as f:
            f.seek(e.offset)
            p = parse_record(f.read(e.length))
        p.id, p.crc = e.id, e.crc
        return p

    def has(self, prop_id: int, crc: int) -> bool:
        return any(e.id == prop_id & 0xFFFFFFFF and e.crc == crc & 0xFFFFFFFF for e in self.index)

    # ---------------------------------------------------------------- writing
    def backup(self) -> str:
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = self.dir.rstrip("/\\") + f".bak-{stamp}"
        shutil.copytree(self.dir, dest)
        return dest

    def category_path(self, name: str, create: bool = True) -> str:
        cats = self.categories()
        if name in cats:
            return cats[name]
        if not create:
            raise KeyError(name)
        safe = re.sub(r'[\\/:*?"<>|]', "_", name).strip() or "Imported"
        numbers = [int(m.group(1)) for p in cats.values()
                   if (m := re.match(r"(\d+)-", os.path.basename(p)))]
        path = os.path.join(self.dir, f"{max(numbers, default=1) + 1}-{safe}.favs")
        open(path, "wb").close()
        return path

    def add(self, props: list[BagProp], category: str = "PalaceChat", at_start: bool = False,
            skip_existing: bool = True) -> tuple[list[Entry], int]:
        """Append props to the bag and list them in a category. Returns (new entries,
        number skipped because the same id and crc were already stored)."""
        known = {(e.id, e.crc): e for e in self.index}
        used_ids = {e.id for e in self.index}
        offset = os.path.getsize(self.props_path)
        blob, new, listed, skipped = bytearray(), [], [], 0
        for p in props:
            crc = (p.crc if p.crc is not None else p.computed_crc()) & 0xFFFFFFFF
            pid = p.id if p.id is not None else _new_id(crc, used_ids)
            pid &= 0xFFFFFFFF
            if skip_existing and (pid, crc) in known:
                skipped += 1
                listed.append(known[(pid, crc)])
                continue
            rec = p.record()
            e = Entry(pid, crc, offset + len(blob), len(rec))
            blob += rec
            new.append(e)
            listed.append(e)
            known[(pid, crc)] = e
            used_ids.add(pid)

        fav_path = self.category_path(category)
        favs = read_entries(fav_path)
        already = {(e.id, e.crc) for e in favs}
        add_favs = [e for e in listed if (e.id, e.crc) not in already]
        favs = add_favs + favs if at_start else favs + add_favs

        # props first, then the index, then the category: if anything fails part way the
        # bag still opens (extra bytes past the index are what PalaceChat repairs anyway).
        with open(self.props_path, "ab") as f:
            f.write(blob)
        with open(self.pids_path, "ab") as f:
            f.write(b"".join(e.pack() for e in new))
        tmp = fav_path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(b"".join(e.pack() for e in favs))
        os.replace(tmp, fav_path)
        self.index += new
        return new, skipped


def _category_name(path: str) -> str:
    base = os.path.basename(path)[:-5]
    m = re.match(r"\d+-(.*)", base)
    return m.group(1) if m else base


def _category_sort(path: str):
    base = os.path.basename(path)
    m = re.match(r"(\d+)-", base)
    if base == "PalaceChat.favs":
        return (0, 0, base)
    if base == "Trash.favs":
        return (2, 0, base)
    return (1, int(m.group(1)) if m else 0, base)


def _new_id(crc: int, used: set[int]) -> int:
    """PalaceChat's createpropid: crc xor a tick count, kept positive, unique in the bag."""
    while True:
        pid = (crc ^ random.getrandbits(31)) & 0x7FFFFFFF
        if pid and pid not in used:
            return pid


# ---------------------------------------------------------------- converters

def from_prp_prop(p) -> BagProp:
    """A palace_prp.Prop (classic .prp, little-endian, 20/32-bit data zlib'd) as a bag prop.
    Keeps id and crc so PalaceChat recognizes the prop when someone wears it."""
    data = p.data
    if p.flags & (pp.PF_20BIT | pp.PF_32BIT | pp.PF_S20BIT):
        data = zlib.decompress(data)
    return BagProp(p.name or "", p.flags & 0xFFFF, data, p.h_offset, p.v_offset, p.id, p.crc)


def encode_s20(rgba: bytes) -> bytes:
    """Inverse of palace_props.decode_s20bit: 5 bits each of R, G, B, A, 2 pixels per 5 bytes."""
    if len(rgba) != pp.RGBA_SIZE:
        raise ValueError("expected 44x44 RGBA")
    out = bytearray()
    q = lambda v: (v * 31 + 127) // 255
    for i in range(0, len(rgba), 8):
        v = 0
        for c in rgba[i:i + 8]:
            v = (v << 5) | q(c)
        out += v.to_bytes(5, "big")
    return bytes(out)


def from_image(path: str, name: str | None = None, flags: int = 0, x: int | None = None,
               y: int | None = None, max_size: int = PROP_MAX) -> BagProp:
    """Any image as a PalaceChat prop (needs Pillow). A still image becomes a PNG prop; an
    animated GIF / WebP / APNG becomes an animated WebP prop (flags 0x0800, the frame delay
    as its speed), like PalaceChat's advanced editor makes.

    Images bigger than max_size (220, what PalaceChat's editors use) are scaled down to
    fit. x, y place it relative to the avatar like the prop editor's offsets; left out,
    the prop is centered on the avatar the way PalaceChat centers its own. A 44x44 S20
    thumbnail goes in front, the same as PalaceChat writes, for clients that can't show
    the full image."""
    from io import BytesIO
    from PIL import Image, ImageSequence
    frames, durations = [], []
    with Image.open(path) as src:
        for fr in ImageSequence.Iterator(src):
            frames.append(fr.convert("RGBA"))
            durations.append(int(fr.info.get("duration") or src.info.get("duration") or 100))
    w, h = frames[0].size
    if max_size and max(w, h) > max_size:
        k = max_size / max(w, h)
        w, h = max(1, round(w * k)), max(1, round(h * k))
        frames = [f.resize((w, h), Image.LANCZOS) for f in frames]
    if x is None:
        x = int((44 - w) / 2)
    if y is None:
        y = int((44 - h) / 2)

    buf = BytesIO()
    if len(frames) > 1:
        # GIFs often say 0 or 10 ms per frame; browsers treat those as 100 ms, so do we
        durations = [d if d > 10 else 100 for d in durations]
        frames[0].save(buf, "WEBP", save_all=True, append_images=frames[1:],
                       duration=durations, loop=0, quality=90, method=4)
        f = (flags & ~FORMAT_BITS) | PF_ANIMATED
        speed = round(sum(durations) / len(durations))
    else:
        frames[0].save(buf, "PNG", optimize=True)
        f = (flags & ~FORMAT_BITS) | PF_PNG
        speed = 0
    payload = buf.getvalue()

    thumb = frames[0].copy()
    thumb.thumbnail((44, 44), Image.LANCZOS)
    canvas = Image.new("RGBA", (44, 44), (0, 0, 0, 0))
    canvas.paste(thumb, ((44 - thumb.width) // 2, (44 - thumb.height) // 2))
    s20 = encode_s20(canvas.tobytes())
    trailer = TRAILER.pack(len(payload), w, h, x, y, speed, f)
    nm = name or os.path.splitext(os.path.basename(path))[0]
    return BagProp(nm[:31], f, s20 + trailer + payload)


def png_payload(p: BagProp) -> tuple[dict, bytes] | None:
    """For a PNG/WebP prop: (trailer fields, payload bytes)."""
    if not p.flags & PF_IMAGE or len(p.data) < pp.SIZE_20BIT + TRAILER.size:
        return None
    n, w, h, x, y, speed, fl = TRAILER.unpack_from(p.data, pp.SIZE_20BIT)
    start = pp.SIZE_20BIT + TRAILER.size
    return ({"w": w, "h": h, "x": x, "y": y, "speed": speed, "flags": fl},
            p.data[start:start + n])


def to_rgba(p: BagProp) -> bytes:
    """44x44 RGBA for any bag prop (PNG props give their S20 thumbnail)."""
    ghost = bool(p.flags & pp.PF_GHOST)
    if p.flags & PF_IMAGE:
        return pp.decode_s20bit(p.data[:pp.SIZE_20BIT])
    fmt = pp.format_from_flags(p.flags)
    return pp.decode_8bit(p.data, ghost=ghost) if fmt is None else pp.decode(p.data, fmt, ghost=ghost)


# ---------------------------------------------------------------- command line

def main(argv=None):
    import argparse
    import palace_prp as prp

    ap = argparse.ArgumentParser(description="Look at or add to PalaceChat's prop bag.")
    ap.add_argument("--bag", default=None, help="PropBag.bundle folder (default: Documents/PalaceChat)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("info", help="categories, counts and formats")
    ex = sub.add_parser("export", help="write a category's props as PNGs")
    ex.add_argument("category")
    ex.add_argument("out_dir")
    imp = sub.add_parser("import-prp", help="add every prop in a .prp file")
    imp.add_argument("prp")
    imp.add_argument("--category", help="category to list them in (default: the file name)")
    img = sub.add_parser("import-images",
                         help="add image files as props (animated GIF/WebP stay animated)")
    img.add_argument("images", nargs="+")
    img.add_argument("--category", default="Imported")
    img.add_argument("--head", action="store_true", help="mark as head props")
    img.add_argument("--max-size", type=int, default=PROP_MAX,
                     help=f"scale bigger images down to fit this box (default {PROP_MAX}; 0 = never)")
    for s in (imp, img):
        s.add_argument("--at-start", action="store_true", help="put them at the top of the category")
        s.add_argument("--no-backup", action="store_true")
        s.add_argument("--force", action="store_true", help="write even if PalaceChat seems to be running")
    a = ap.parse_args(argv)
    bag = Bag(a.bag)

    if a.cmd == "info":
        print(bag.dir)
        print(f"{len(bag.index)} props stored ({os.path.getsize(bag.props_path) / 1e6:.1f} MB), "
              "including ones cached from other people")
        for name, path in bag.categories().items():
            print(f"  {name:20} {len(read_entries(path)):5}")
        return
    if a.cmd == "export":
        os.makedirs(a.out_dir, exist_ok=True)
        for e in bag.entries(a.category):
            p = bag.read(e)
            base = os.path.join(a.out_dir, f"{e.id}_{re.sub(r'[^A-Za-z0-9._-]+', '_', p.name)}")
            payload = png_payload(p)
            if payload:
                ext = ".webp" if p.flags & PF_ANIMATED else ".png"
                open(base + ext, "wb").write(payload[1])
            else:
                pp.save_png(to_rgba(p), base + ".png")
        print("wrote", a.out_dir)
        return

    running = palacechat_running()
    if running and not a.force:
        sys.exit("PalaceChat is running. Close it first: it keeps the bag open and would "
                 "overwrite or corrupt what we add.")
    if running is None and not a.force:
        print("Can't check whether PalaceChat is running from here; make sure it's closed.")
    if not a.no_backup:
        print("backup:", bag.backup())

    if a.cmd == "import-prp":
        props, _ = prp.read_prp(a.prp)
        cat = a.category or os.path.splitext(os.path.basename(a.prp))[0]
        items = [from_prp_prop(p) for p in props]
    else:
        cat = a.category
        items = [from_image(f, flags=pp.PF_HEAD if a.head else 0, max_size=a.max_size)
                 for f in a.images]
    new, skipped = bag.add(items, cat, at_start=a.at_start)
    print(f"added {len(new)} props to '{cat}' ({skipped} were already stored and just got listed)")


if __name__ == "__main__":
    main()
