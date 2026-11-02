"""Highlight candidate detection from a captured vill_pop.jsonl.

Rules (offline, replay-source independent):
  first_house     first house foundation of any player (best "opening drama")
  house_pressure  a player's pop reaches housing cap - 1 (cap = 4 + 5 * done
                  houses observed; marks pop-blocked villagers training)
  tc_progress     town center foundation start / done / lost
  bld_lost        any building foundation lost (canceled or destroyed)

Usage:
    py -3.12 tools\\auto\\highlight_candidates.py [--in out\\vill_pop.jsonl]
                                                  [--pad 8]   # clip padding s
                                                  [--out out\\highlights.json]
"""
import argparse
import json

HOUSE_POP = 5
START_POP = 4


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", default="out/vill_pop.jsonl")
    ap.add_argument("--pad", type=float, default=8.0,
                    help="candidate window padding in game seconds")
    ap.add_argument("--out", default="out/highlights.json")
    args = ap.parse_args()

    rows = [json.loads(l) for l in open(args.src, encoding="utf-8") if l.strip()]
    stats = [r for r in rows if r.get("type") == "stats"]
    bld = [r for r in rows if r.get("type") == "bld_event"]
    duration = stats[-1]["time"] if stats else 0.0

    houses_done = {p: 0 for p in range(1, 9)}
    caps = {}          # time -> {player: cap} only when cap changed (sparse)
    candidates = []

    def pop_of(stats_row, p):
        return stats_row["stats"][p][5]

    def cap_for(p):
        return START_POP + HOUSE_POP * houses_done[p]

    def add(kind, player, t, why):
        t0 = max(0.0, t - args.pad)
        t1 = min(duration, t + args.pad)
        candidates.append({"kind": kind, "player": player, "t": round(t, 1),
                           "t0": round(t0, 1), "t1": round(t1, 1),
                           "why": why})

    # fuse overlapping windows of the same player
    seen_kinds = set()

    def add_unique(kind, player, t, why):
        key = (kind, player, round(t / 30))
        if key in seen_kinds:
            return
        seen_kinds.add(key)
        add(kind, player, t, why)

    first_house = None
    for r in bld:
        t, p = r["time"], r["owner"]
        if r["event"] == "start" and r.get("isHouse"):
            if first_house is None:
                first_house = r
                add("first_house", p, t,
                    f"first house of the match at tile {r['x']}:{r['y']}")
            add_unique("house_start", p, t, "house foundation placed")
        elif r["event"] == "done" and r.get("isHouse"):
            houses_done[p] = houses_done.get(p, 0) + 1
            cap = cap_for(p)
            add_unique("house_done", p, t,
                       f"house finished, housing cap -> {cap}")
        elif r["event"] == "lost":
            kind = "tc_lost" if not r.get("isHouse") else "bld_lost"
            name = r.get("name", f"type {r['unitType']}")
            add(kind, p, t, f"{name} foundation lost at {r['x']}:{r['y']}")
        elif r["event"] == "done":
            label = r.get("name") or str(r["unitType"])
            add_unique("bld_done", p, t, f"{label} completed")

    # population pressure: pop at (cap - 1) or above while houses are up
    for r in stats:
        for p in range(1, 9):
            pop = pop_of(r, p)
            cap = cap_for(p)
            if pop >= cap:  # POP_CURRENT includes queued units: full = blocked
                add_unique("pop_block", p, r["time"],
                           f"pop {pop} at cap {cap} (villager production "
                           f"blocked)")

    # sort + fuse overlapping windows (keep earliest cause)
    candidates.sort(key=lambda c: (c["t0"], c["player"]))
    fused = []
    for c in candidates:
        if fused and c["player"] == fused[-1]["player"] \
                and c["t0"] <= fused[-1]["t1"]:
            fused[-1]["t1"] = max(fused[-1]["t1"], c["t1"])
            fused[-1]["kinds"] = sorted(set(fused[-1]["kinds"] + [c["kind"]]))
            if c["t"] < fused[-1]["t"]:
                fused[-1]["t"] = c["t"]
            continue
        c = dict(c)
        c["kinds"] = [c.pop("kind")]
        fused.append(c)

    tc_lost = [c for c in candidates if c["kind"] == "tc_lost"]
    print(f"duration {duration:.0f}s, {len(bld)} bld events, "
          f"{len(candidates)} raw candidates, {len(fused)} fused clips")
    for c in fused:
        print(f"  t={c['t']:7.1f} p{c['player']} {','.join(c['kinds']):24} "
              f"window {c['t0']:.0f}-{c['t1']:.0f}  {c['why']}")

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"duration": duration, "clips": fused,
                   "tc_lost_count": len(tc_lost)}, f, indent=2)
    print(f"-> {args.out}")


if __name__ == "__main__":
    main()
