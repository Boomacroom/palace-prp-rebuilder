"""
site_to_prp.py - rebuild Palace .prp files from avatars.thepalace.app

    pip install pillow            (only needed for --sheet)
    python site_to_prp.py --list
    python site_to_prp.py --bag 95 --out bag95.prp --sheet

How it works: /api/props/{id}/raw gives each prop's unpacked 8-bit palette
indexes + alpha. We re-run ThePalace 3.6's row-by-row RLE encoder on them
and write a .prp the 3.6 client can load. For ~97% of props the result is
byte-identical to the original, proven by the CRC matching the one the site
stored. Props made by other tools encode differently; those still look the
same but get a fresh CRC (listed at the end).

Be nice to a volunteer project: it sleeps between requests (--delay).
"""
import argparse
import base64
import datetime as dt
import json
import sys
import time
import urllib.parse
import urllib.request

import palace_props as pp
import palace_prp as prp

UA = {"User-Agent": "palace-prp-rebuilder/0.1 (personal preservation)"}


class Site:
    def __init__(self, base, delay):
        self.base = base.rstrip("/")
        self.delay = delay

    def get(self, path):
        req = urllib.request.Request(self.base + path, headers=UA)
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read()
        time.sleep(self.delay)
        return json.loads(body)

    def bags(self):
        b = self.get("/api/bags")
        return b if isinstance(b, list) else b.get("bags", [])

    def bag_props(self, bag):
        seen, out = set(), []
        for fav in (1, 0):
            offset = 0
            while True:
                page = self.get(f"/api/props?bag={bag}&favorite={fav}&limit=200&offset={offset}")
                if not page:
                    break
                for p in page:
                    if p["id"] not in seen:
                        seen.add(p["id"])
                        out.append(p)
                if len(page) < 200:
                    break
                offset += 200
        return out


def to_timestamp(s):
    try:
        return int(dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
    except Exception:
        return 0


def build_prop(meta, raw):
    fmt_bits = pp.PF_20BIT | pp.PF_32BIT | pp.PF_S20BIT
    if raw.get("flags", 0) & fmt_bits:
        raise ValueError("not an 8-bit prop, skipping")
    idx = base64.b64decode(raw["indices"])
    alpha = base64.b64decode(raw["alpha"]) if raw.get("alpha") else None
    data = pp.encode_8bit(idx, alpha)
    return prp.Prop(
        id=raw.get("asset_id", meta["asset_id"]) & 0xFFFFFFFF,
        name=meta.get("name") or None,
        width=raw.get("width", 44), height=raw.get("height", 44),
        h_offset=raw.get("h_offset", 0), v_offset=raw.get("v_offset", 0),
        script_offset=0, flags=raw.get("flags", meta.get("flags", 0)),
        crc=prp.asset_crc(data),
        timestamp=to_timestamp(meta.get("created_at", "")),
        data=data,
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="https://avatars.thepalace.app")
    ap.add_argument("--list", action="store_true", help="list prop bags and exit")
    ap.add_argument("--bag", type=int)
    ap.add_argument("--out")
    ap.add_argument("--sheet", action="store_true", help="also write a contact sheet PNG")
    ap.add_argument("--delay", type=float, default=0.3)
    ap.add_argument("--favorites", choices=["all", "site"], default="all",
                    help="which props go in the prop bag PalaceChat imports: "
                         "all (default) or only ones the site marks as favorites")
    args = ap.parse_args()
    site = Site(args.base, args.delay)

    if args.list:
        for b in site.bags():
            print(f'{b["id"]:>4}  {b.get("prop_count", "?"):>5}  {b.get("filename", "")}')
        return
    if args.bag is None:
        ap.error("--bag is required (or --list)")
    out = args.out or f"bag{args.bag}.prp"

    metas = site.bag_props(args.bag)
    print(f"bag {args.bag}: {len(metas)} props")
    props, used_ids, mismatched, skipped = [], set(), [], []
    for n, m in enumerate(metas, 1):
        try:
            raw = site.get(f'/api/props/{m["id"]}/raw')
            p = build_prop(m, raw)
        except Exception as e:
            skipped.append((m["id"], str(e)))
            continue
        if p.id in used_ids:
            skipped.append((m["id"], f"duplicate asset id {p.id}"))
            continue
        used_ids.add(p.id)
        if (m.get("crc") or 0) & 0xFFFFFFFF != p.crc:
            mismatched.append((m["id"], p.id, p.name))
        props.append(p)
        if n % 25 == 0:
            print(f"  {n}/{len(metas)}")

    # PalaceChat's importer only brings in props listed in 'Fave' (your prop
    # bag); the rest of a .prp is just cache. Default: put every prop in it.
    if args.favorites == "all":
        favorites = [p.id for p in props]
    else:
        kept = {p.id for p in props}
        favorites = [m["asset_id"] & 0xFFFFFFFF for m in metas
                     if m.get("is_favorite") and (m["asset_id"] & 0xFFFFFFFF) in kept]
        favorites = list(dict.fromkeys(favorites))
    prp.write_prp(props, out, favorite_ids=favorites)
    print(f"prop bag (Fave) entries: {len(favorites) or 'none, using first prop'}")
    check, _ = prp.read_prp(out)
    assert len(check) == len(props) and all(p.crc_ok for p in check)
    print(f"wrote {out}: {len(props)} props, "
          f"{len(props) - len(mismatched)} byte-identical to the original (CRC match), "
          f"{len(mismatched)} re-encoded with a new CRC, {len(skipped)} skipped")
    for s in skipped:
        print("  skipped", *s)
    if args.sheet:
        sheet = out.rsplit(".", 1)[0] + "_sheet.png"
        prp.contact_sheet(props, sheet)
        print("wrote", sheet)


if __name__ == "__main__":
    main()
