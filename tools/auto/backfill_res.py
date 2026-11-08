"""Backfill res (gold/stone) rows into already-collected positions.jsonl.

Older collections only read the raw object buffer once and never wrote
a res row, so the map webapp shows no gold or stone for them. For every
tag in out/ that has objects_raw.jsonl but no res row in positions.jsonl,
this inserts a res row (t = the fish row's time) right after it, the
same place capture._read_objects_raw puts it today. No game interaction,
pure file patching.

Usage:
    py -3.12 tools\\auto\\backfill_res.py
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "out"
GOLD, STONE = 66, 102


def main():
    for d in sorted(OUT.iterdir()):
        pos = d / "positions.jsonl"
        raw = d / "objects_raw.jsonl"
        if not pos.exists() or not raw.exists():
            continue
        objs = [json.loads(line) for line in raw.read_text(encoding="utf-8")
                .splitlines() if line.strip()]
        res = {"type": "res", "time": None,
               "g": [[x, y] for _id, ut, x, y, _p, alive in objs
                     if ut == GOLD and alive],
               "s": [[x, y] for _id, ut, x, y, _p, alive in objs
                     if ut == STONE and alive]}
        if not (res["g"] or res["s"]):
            print("%-22s no gold/stone in buffer, skip" % d.name)
            continue
        lines = pos.read_text(encoding="utf-8").splitlines(keepends=True)
        if any('"type": "res"' in l for l in lines):
            print("%-22s already has res row" % d.name)
            continue
        fish_t = None
        out = []
        for l in lines:
            if fish_t is None and '"type": "fish"' in l:
                fish_t = json.loads(l).get("time")
                out.append(json.dumps(res) + "\n")
            out.append(l)
        tmp = pos.with_suffix(".jsonl.tmp")
        tmp.write_text("".join(out), encoding="utf-8")
        tmp.replace(pos)
        print("%-22s res row (gold %d, stone %d, t=%s)"
              % (d.name, len(res["g"]), len(res["s"]), fish_t))


if __name__ == "__main__":
    main()
