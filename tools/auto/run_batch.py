"""Collect the last N multiplayer replays from the savegame folder.

Enumerates MP Replay*.aoe2record (newest by mtime), derives a tag per
replay from the file name's @timestamp (mp_<YYYYMMDD>_<HHMMSS>), and
runs map_collect.py sequentially on each. Tags whose out/<tag> folder
already has positions.jsonl are skipped unless --redo.

Usage:
    py -3.12 tools\\auto\\run_batch.py [--count 10] [--max-minutes 1]
                                      [--redo] [--dry]
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "out"
COLLECT = ROOT / "tools" / "auto" / "map_collect.py"
SAVEGAME = (Path.home() / "Games" / "Age of Empires 2 DE" /
            "76561198014928708" / "savegame")


def tag_from_name(name):
    m = re.search(r"@(\d{4})\.(\d{2})\.(\d{2}) (\d{2})(\d{2})(\d{2})",
                  name)
    if not m:
        raise ValueError("no @timestamp in %r" % name)
    return "mp_%s%s%s_%s%s%s" % (m.group(1), m.group(2), m.group(3),
                                 m.group(4), m.group(5), m.group(6))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=10)
    ap.add_argument("--max-minutes", type=float, default=1)
    ap.add_argument("--redo", action="store_true",
                    help="collect again even if the tag already exists")
    ap.add_argument("--dry", action="store_true", help="list and stop")
    args = ap.parse_args()

    files = sorted(SAVEGAME.glob("MP Replay*.aoe2record"),
                   key=lambda f: f.stat().st_mtime, reverse=True)
    if not files:
        sys.exit("no MP Replay*.aoe2record under %s" % SAVEGAME)
    picks = []
    for f in files:
        try:
            tag = tag_from_name(f.name)
        except ValueError as exc:
            print("skip (name): %s" % exc, flush=True)
            continue
        picks.append((tag, f))
        if len(picks) >= args.count:
            break
    print("batch: %d replays" % len(picks), flush=True)
    for tag, f in picks:
        print("  %s <- %s" % (tag, f.name), flush=True)
    if args.dry:
        return

    for tag, f in picks:
        if (OUT / tag / "positions.jsonl").exists() and not args.redo:
            print("%s: already collected, skipping" % tag, flush=True)
            continue
        print("=== %s: collect start (%s) ===" % (tag, f.name), flush=True)
        rc = subprocess.run(
            [sys.executable, str(COLLECT), "--tag", tag,
             "--force-inject", "--touch", str(f),
             "--max-minutes", str(args.max_minutes)],
            cwd=str(ROOT)).returncode
        print("=== %s: collect done (rc=%d) ===" % (tag, rc), flush=True)
    # summary
    ok = [t for t, _ in picks
          if (OUT / t / "positions.jsonl").exists()]
    print("batch finished: %d/%d replays have positions.jsonl"
          % (len(ok), len(picks)), flush=True)


if __name__ == "__main__":
    main()
