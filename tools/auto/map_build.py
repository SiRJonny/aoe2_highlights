"""Build a static bundle of the map webapp for GitHub Pages.

Copies tools/auto/map_web/ statics into dist/ and, for every collected
replay, writes:
    dist/data/<tag>.jsonl   (out/<tag>/positions.jsonl)
    dist/map/<tag>.png      (out/<tag>/map.png)
plus dist/tags.json ({tags: [...]}, newest first). app.js already falls
back to those static paths when /api/* fails, so the bundle works with
no server - just point GitHub Pages (or any static host) at dist/.

Usage:
    py -3.12 tools\\auto\\map_build.py [--out dist]
"""
import argparse
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEB = Path(__file__).resolve().parent / "map_web"
OUT = ROOT / "out"

STATICS = ("index.html", "app.js", "icons.js", "hp.js", "style.css",
           "icons_test.html")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "dist"),
                    help="output folder (default: <repo>/dist)")
    args = ap.parse_args()
    dist = Path(args.out).resolve()
    dist.mkdir(parents=True, exist_ok=True)
    for name in STATICS:
        shutil.copy2(WEB / name, dist / name)
    shutil.copytree(WEB / "ages", dist / "ages", dirs_exist_ok=True)

    tags = []   # newest first (positions.jsonl mtime)
    for d in OUT.iterdir():
        pos = d / "positions.jsonl"
        if not d.is_dir() or not pos.exists():
            continue
        tags.append(d.name)
        (dist / "data").mkdir(exist_ok=True)
        shutil.copy2(pos, dist / "data" / (d.name + ".jsonl"))
        mp = d / "map.png"
        if mp.exists():
            (dist / "map").mkdir(exist_ok=True)
            shutil.copy2(mp, dist / "map" / (d.name + ".png"))
    tags.sort(key=lambda t: (OUT / t / "positions.jsonl").stat().st_mtime,
              reverse=True)
    (dist / "tags.json").write_text(
        json.dumps({"tags": tags}, indent=1) + "\n", encoding="utf-8")
    print("dist: %d replays, %d statics -> %s" % (len(tags), len(STATICS),
                                                  dist))


if __name__ == "__main__":
    main()
