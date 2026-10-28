# palace-prp-rebuilder

Rebuild classic Palace prop bags (`.prp` files) from the [Palace Prop Preservation Project](https://avatars.thepalace.app), so ThePalace 3.6 and PalaceChat can load them again.

The archive keeps every prop's pixels. This script fetches a bag's props, packs each one back up the way ThePalace 3.6 did, and writes a `.prp` file you can open in the old client or import into PalaceChat (File, Import Prop File, The Palace).

**No Python?** The same thing runs in your browser at [materialboi.com/tools/prp-converter](https://materialboi.com/tools/prp-converter/). Its files come out byte-identical to this script's.

The story of how this came together, including why PalaceChat kept rejecting files that looked perfect, is in [Rebuilding .prp files PalaceChat will actually import](https://materialboi.com/posts/rebuilding-prp-files/).

## Usage

Python 3.10 or newer. No dependencies, except Pillow for the optional contact sheet.

```
python site_to_prp.py --list                          # every bag in the archive
python site_to_prp.py --bag 95                        # writes bag95.prp
python site_to_prp.py --bag 95 --out mine.prp --sheet # plus a contact sheet PNG (pip install pillow)
```

Options:

- `--favorites all` (default) puts every prop in the file's prop bag. PalaceChat only imports the props listed there, so this is usually what you want. `--favorites site` lists only the props the archive marks as favorites.
- `--delay 0.3` is the pause after each request, in seconds. The archive is a volunteer project, so please leave it at 0.3 or higher.

### Straight into PalaceChat's bag

PalaceChat 5 keeps its own bag in `Documents\PalaceChat\PropBag.bundle`. Close PalaceChat, then:

```
python site_to_prp.py --bag 95 --palacechat          # .prp as usual, plus a new bag category
python palacechat_bag.py info                         # categories and counts
python palacechat_bag.py import-prp mine.prp          # any .prp, as its own category
python palacechat_bag.py import-images *.png *.gif --category "My art"   # full-color props
python palacechat_bag.py export PalaceChat out_dir    # a category back out as PNGs
```

`import-images` makes a still image a PNG prop and an animated GIF, WebP or APNG an
animated WebP prop, the same kind PalaceChat's advanced editor makes (flags 0x0800, frame
delay as the speed). Anything bigger than 220x220 is scaled to fit (`--max-size` to change,
0 to keep the size), and props are centered on the avatar the way PalaceChat centers its
own. `--head` marks them as head props.

Every write makes a copy of the bag first (`PropBag.bundle.bak-<time>`, next to it). Props
keep their original id and checksum, so PalaceChat recognizes them when someone in a room
wears the same prop. The bag format is documented at the top of `palacechat_bag.py`.

To look inside an existing `.prp`, `python palace_prp.py file.prp out_dir` writes every prop as a PNG plus a contact sheet (this one needs Pillow too).

## How it works

- `/api/props/{id}/raw` on the archive gives each prop's unpacked 8-bit palette indexes and alpha.
- `palace_props.py` re-runs ThePalace 3.6's row-by-row run-length encoder on them, and also decodes 8-bit, 20-bit, S20 and 32-bit props to RGBA.
- `palace_prp.py` reads and writes the `.prp` container: a Windows take on a Mac resource file, with a `Prop` resource per prop and the `Fave` resource that PalaceChat's importer needs.
- Each prop's checksum is compared with the one the archive stored. About 97% match exactly, which proves the rebuilt prop is byte-identical to the original. The rest were made by other tools that encoded differently; they look the same but get a fresh checksum. The script tells you how many of each.
- Props in the 20-bit, S20 and 32-bit formats are skipped, since this rebuilds 8-bit bags.
- After writing, the file is read back and every checksum is checked.

## Thanks

To the people behind the Palace Prop Preservation Project, for saving all of this in the first place.

## License

MIT. See [LICENSE](LICENSE).
