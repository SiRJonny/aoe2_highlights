"""Select the highlight segments of one replay from the captured events.

Input:  a module events jsonl (rows: bld_event / vill_death / tick ...)
Output: a plan json, sorted by time:

    {"row": 2, "source": "out/events_row2.jsonl", "segments": [...]}

Castle segment:  full build, start->done (+/-- padding applied at record
time). Only castles whose `done` arrived count as "actually finished";
the earliest finished one is selected per game.
Villager segment: the two 10 s windows with the most villager deaths in
one location (12-tile grid cell), any player, windows never overlapping.

Usage:
    py -3.12 tools\\auto\\select_events.py out\\events_row2.jsonl \\
        [--out out\\events_plan_row2.json] [--max-castles 1]
        [--death-windows 2] [--min-deaths 2]
"""
import argparse
import json
import math
import sys
from pathlib import Path

CELL = 12.0          # grid cell size (tiles) for "one location"
WINDOW = 10.0        # seconds per villager-death window
PAD = 2.0            # seconds padded before/after, at record time


def load_events(path):
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return rows


def castles(rows, max_castles):
    """First finished castles: earliest starts whose done arrived."""
    starts = {}
    dones = {}
    for r in rows:
        if r.get("type") != "bld_event" or not r.get("isCastle"):
            continue
        t = r.get("time")
        if t is None:
            continue
        if r.get("event") == "start":
            starts.setdefault(r["id"], r)
        elif r.get("event") == "done":
            dones.setdefault(r["id"], r)
    finished = []
    for cid, s in starts.items():
        d = dones.get(cid)
        if d:
            finished.append(s)
    finished.sort(key=lambda r: r["time"])
    segs = []
    for s in finished[:max_castles]:
        segs.append({
            "kind": "castle",
            "t0": s["time"],
            "t1": dones[s["id"]]["time"],
            "x": s["x"], "y": s["y"],
            "info": {"id": s["id"], "owner": s["owner"],
                     "name": s.get("name")},
        })
    return segs


def _best_window(times):
    """Best [t, t+WINDOW) covering the most deaths: slide window over
    the sorted times (best = the longest two-pointer run)."""
    best = (0, 0.0)             # (count, t0)
    j = 0
    for i, t in enumerate(times):
        if j < i:
            j = i
        while j + 1 < len(times) and times[j + 1] < t + WINDOW:
            j += 1
        # inclusive run times[i..j] within [t, t+WINDOW)
        cnt = j - i + 1
        if cnt > best[0]:
            best = (cnt, t)
    return best


def villager_windows(rows, want, min_deaths):
    """Top-N 10 s windows of villager deaths in one grid cell."""
    deaths = [r for r in rows if r.get("type") == "vill_death"]
    deaths.sort(key=lambda r: r["time"])
    cells = {}
    for r in deaths:
        key = (math.floor(r["x"] / CELL), math.floor(r["y"] / CELL))
        cells.setdefault(key, []).append((r["time"], r["x"], r["y"], r))

    cands = []
    for key, items in cells.items():
        times = [it[0] for it in items]
        cnt, t0 = _best_window(times)
        if cnt >= min_deaths:
            win = [it for it in items if t0 <= it[0] < t0 + WINDOW]
            cands.append({
                "count": cnt,
                "t0": t0,
                "t1": t0 + WINDOW,
                "x": sum(it[1] for it in win) / len(win),
                "y": sum(it[2] for it in win) / len(win),
                "info": {"players": sorted({it[3].get("player")
                                            for it in win}),
                         "deaths": cnt},
            })
    cands.sort(key=lambda c: (c["count"], c["t0"]))

    picked = []
    for c in cands:
        if len(picked) >= want:
            break
        # windows must not overlap in time
        if any(c["t0"] < p["t1"] and p["t0"] < c["t1"] for p in picked):
            continue
        picked.append(c)
    return [{"kind": "vill_deaths", "t0": c["t0"], "t1": c["t1"],
             "x": c["x"], "y": c["y"], "info": c["info"]}
            for c in picked]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("events")
    ap.add_argument("--out", default=None)
    ap.add_argument("--max-castles", type=int, default=1)
    ap.add_argument("--death-windows", type=int, default=2)
    ap.add_argument("--min-deaths", type=int, default=2)
    ap.add_argument("--row", type=int, default=None)
    args = ap.parse_args()

    rows = load_events(args.events)
    segs = castles(rows, args.max_castles)
    for s in villager_windows(rows, args.death_windows, args.min_deaths):
        segs.append(s)

    # monotonic recording plan: never seek backwards
    segs.sort(key=lambda s: (s["t0"], s["t1"]))

    plan = {
        "row": args.row,
        "source": args.events,
        "pad": PAD,
        "segments": segs,
    }
    out_path = args.out or str(
        Path(args.events).with_name("events_plan_"
                                    + Path(args.events).stem + ".json"))
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(plan, fh, indent=2)

    kinds = [s["kind"] for s in segs]
    if not kinds:
        print("no events selected (no finished castle and/or no villager "
              "deaths >= min threshold)")
    for s in segs:
        print(f"{s['kind']:12s} t={s['t0']:.0f}..{s['t1']:.0f} "
              f"tile {s['x']:.0f}:{s['y']:.0f}")
    print(f"plan: {out_path} ({len(segs)} segments)")


if __name__ == "__main__":
    main()
