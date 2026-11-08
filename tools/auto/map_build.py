"""Build a static bundle of the map webapp for GitHub Pages.

Copies tools/auto/map_web/ statics into dist/ and, for every collected
replay, writes:
    dist/data/<tag>.jsonl   (out/<tag>/positions.jsonl)
    dist/map/<tag>.png      (out/<tag>/map.png)
plus dist/tags.json ({tags: [...]}, newest first). app.js already falls
back to those static paths when /api/* fails, so the bundle works with
no server - just point GitHub Pages (or any static host) at dist/.

Usage:
    py -3.12 tools\\auto\\map_build.py [--out dist] [--tag TAG] [--gzip]
"""
import argparse
import gzip
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEB = Path(__file__).resolve().parent / "map_web"
OUT = ROOT / "out"

STATICS = ("index.html", "app.js", "icons.js", "hp.js", "style.css",
           "icons_test.html")


def _has_players(f: Path) -> bool:
    """Skip replays whose mgz header failed to parse: they carry no
    names row (flat map, no player names). It lands last in the stream,
    so scan the file tail."""
    try:
        with open(f, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - 65536))
            tail = fh.read(65536).decode("utf-8", "replace")
        i = tail.find('"type": "names"')
        if i < 0:
            return False
        row = tail[max(0, tail.rfind("\n", 0, i)) + 1:
                   tail.find("\n", i)].strip()
        return bool(json.loads(row).get("names"))
    except Exception:
        pass
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "dist"),
                    help="output folder (default: <repo>/dist)")
    ap.add_argument("--tag", action="append",
                    help="include only this replay (repeatable)")
    ap.add_argument("--gzip", action="store_true",
                    help="compress JSONL data for static hosting")
    args = ap.parse_args()
    dist = Path(args.out).resolve()
    dist.mkdir(parents=True, exist_ok=True)
    for name in STATICS:
        shutil.copy2(WEB / name, dist / name)
    shutil.copytree(WEB / "ages", dist / "ages", dirs_exist_ok=True)

    candidates = [d for d in OUT.iterdir() if d.is_dir()]
    if args.tag:
        by_tag = {d.name: d for d in candidates}
        missing = [tag for tag in args.tag if tag not in by_tag]
        if missing:
            ap.error("unknown replay tag(s): " + ", ".join(missing))
        candidates = [by_tag[tag] for tag in dict.fromkeys(args.tag)]

    tags = []   # newest first (positions.jsonl mtime)
    for d in candidates:
        pos = d / "positions.jsonl"
        if not pos.exists() or not _has_players(pos):
            continue
        tags.append(d.name)
        (dist / "data").mkdir(exist_ok=True)
        suffix = ".jsonl.gz" if args.gzip else ".jsonl"
        data_path = dist / "data" / (d.name + suffix)
        if args.gzip:
            with pos.open("rb") as source, gzip.open(
                    data_path, "wb", compresslevel=9) as target:
                shutil.copyfileobj(source, target)
        else:
            shutil.copy2(pos, data_path)
        mp = d / "map.png"
        if mp.exists():
            (dist / "map").mkdir(exist_ok=True)
            shutil.copy2(mp, dist / "map" / (d.name + ".png"))
    tags.sort(key=lambda t: (OUT / t / "positions.jsonl").stat().st_mtime,
              reverse=True)
    (dist / "tags.json").write_text(
        json.dumps({"tags": tags}, indent=1) + "\n", encoding="utf-8")
    (dist / ".nojekyll").touch()
    print("dist: %d replays, %d statics -> %s" % (len(tags), len(STATICS),
                                                  dist))


if __name__ == "__main__":
    main()
