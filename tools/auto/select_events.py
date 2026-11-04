"""Priority-based highlight planner for one replay (select_events v2).

Input:  a module events jsonl (vill_pop / army_pop / vill_death /
        unit_death / bld_event / tc_snap / resign / replay_length rows).
Output: a plan json, chronological:

    {"row": N, "source": "...", "segments": [...]}

Each segment: {kind, prio, t0, t1, pb, pa, x, y, info} — t0/t1 are the RAW
event window; pb/pa are the per-segment pads applied at record time
(record window = t0-pb .. t1+pa).

Priorities (user spec; old prio 5 was a typo, removed):
    1  a player loses >=10% of their villagers within any 15 s   (win ±2)
    2  a player loses >=20% of their army within any 15 s        (win ±2)
    3  Town Center falling                                       (t-5 .. t+2)
    4  Castle falling                                            (t-3 .. t+4)
    6  resign/defeat: last building fell / unit died             (t-4 .. t+2)
    7  castle built within 20 tiles of an enemy TC               (t-15 .. t+5)

Overlap rule: candidates are kept in priority order; a candidate overlapping
an already-kept (higher-priority) segment is DROPPED entirely. Same-priority
overlaps merge into one segment. The final plan is chronological; gaps are
skipped by seeking at record time. Zero events -> empty plan -> no video.

Usage:
    py -3.12 tools\\auto\\select_events.py out\\events_mp1.jsonl
        [--out out\\events_plan_mp1.json] [--row N]
"""
import argparse
import json
import sys
from pathlib import Path

LOSS_WINDOW = 15.0     # s, prio 1/2 detection window
VILL_LOSS_FRAC = 0.10
ARMY_LOSS_FRAC = 0.20
CASTLE_NEAR_TC_TILES = 20.0

PADS = {               # per priority (pb, pa) in game seconds
    1: (2.0, 2.0),
    2: (2.0, 2.0),
    3: (5.0, 2.0),
    4: (3.0, 4.0),
    6: (4.0, 2.0),
    7: (15.0, 5.0),
    8: (2.0, 3.0),     # villager killed by animal (pads not user-specified)
}


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


def census_series(rows, rtype):
    """Per-player {pid: [(t, count), ...]} from vill_pop/army_pop rows.

    counts arrives either as {pid: n} or as an array (Lua numeric keys ->
    JSON array, position 0 = player 1; module playerIds are 1-based)."""
    series = {}
    for r in rows:
        if r.get("type") != rtype or "counts" not in r:
            continue
        t = r.get("time")
        if t is None:
            continue
        counts = r["counts"]
        if isinstance(counts, list):
            items = ((i + 1, v) for i, v in enumerate(counts))
        else:
            items = counts.items()
        for k, v in items:
            pid = int(k)
            series.setdefault(pid, []).append((t, v))
    for pid in series:
        series[pid].sort()
    return series


def max_drop_in_windows(points, frac, window, min_base):
    """Candidates where the count falls by >=frac*start within any window.

    points: [(t, count)] sorted. The interesting interval is
    [t_first_drop .. t_min]: from the first time the count falls below the
    window-start baseline to its minimum inside the window. Returns
    [{t_first, t_min, drop, base, frac}] de-duplicated per interval."""
    cands = []
    ts = [p[0] for p in points]
    cs = [p[1] for p in points]
    n = len(ts)
    seen = set()
    for i in range(n):
        base = cs[i]
        if base < min_base:
            continue
        need = frac * base
        j = i
        first_drop = None
        best_drop, best_t = 0, None
        while j < n and ts[j] < ts[i] + window:
            if cs[j] < base and first_drop is None:
                first_drop = ts[j]
            drop = base - cs[j]
            if drop > best_drop:
                best_drop, best_t = drop, ts[j]
            j += 1
        if first_drop is not None and best_t is not None \
                and best_drop >= need:
            key = (first_drop, best_t)
            if key not in seen:
                seen.add(key)
                cands.append({"t_first": first_drop, "t_min": best_t,
                              "drop": best_drop, "base": base,
                              "frac": best_drop / base})
    return cands


def loss_candidates(rows, rtype, frac, min_base):
    """Prio 1/2 candidate windows (one per distinct loss event, greedy)."""
    series = census_series(rows, rtype)
    cands = []
    for pid, pts in series.items():
        wins = max_drop_in_windows(pts, frac, LOSS_WINDOW, min_base)
        # greedy: strongest loss first, drop candidates overlapping an
        # already-taken interval for the SAME player
        wins.sort(key=lambda w: (-w["frac"], w["t_first"]))
        taken = []
        for w in wins:
            if any(w["t_first"] <= u["t_min"] and u["t_first"] <= w["t_min"]
                   for u in taken):
                continue
            taken.append(w)
        for w in taken:
            cands.append({"pid": pid, **w})
    return cands


def deaths_position(rows, rtype, pid, t_a, t_b):
    """Mean death position of a player's units of some death-row type."""
    hits = [r for r in rows
            if r.get("type") == rtype
            and (r.get("player") == pid)
            and t_a <= r.get("time", -1) <= t_b
            and r.get("x") is not None]
    if not hits:
        return None
    return (sum(r["x"] for r in hits) / len(hits),
            sum(r["y"] for r in hits) / len(hits),
            len(hits))


def tc_positions(rows):
    """TC timeline: {pid: [(t_from, t_to_or_None, x, y)]} from tc_snap +
    bld_event done/lost rows (isTC). Used for the prio-7 proximity test."""
    tcs = {p: [] for p in set()}

    def add(pid, t, x, y, gone=None):
        tcs.setdefault(pid, []).append([t, gone, x, y])

    for r in rows:
        if r.get("type") == "tc_snap":
            add(r.get("owner"), 0.0, r.get("x"), r.get("y"))
        elif r.get("type") == "bld_event" and r.get("isTC"):
            if r.get("event") == "done":
                add(r.get("owner"), r.get("time"), r.get("x"), r.get("y"))
            elif r.get("event") == "lost":
                # mark the nearest open TC of that owner destroyed
                t = r.get("time")
                pid, x, y = r.get("owner"), r.get("x"), r.get("y")
                best = None
                for entry in tcs.get(pid, []):
                    if entry[1] is None and entry[2] is not None:
                        d = abs(entry[2] - x) + abs(entry[3] - y)
                        if best is None or d < best[0]:
                            best = (d, entry)
                if best:
                    best[1][1] = t
    return tcs


def tc_alive_at(tcs, pid, t, x, y, tol=3.0):
    for t_from, t_to, tx, ty in tcs.get(pid, []):
        if t_from <= t and (t_to is None or t_to > t) \
                and tx is not None and abs(tx - x) <= tol and abs(ty - y) <= tol:
            return True
    return False


def build_candidates(rows):
    """All priority candidates as plan-shaped dicts (before overlap solve)."""
    cands = []

    # prio 1: villager losses
    for c in loss_candidates(rows, "vill_pop", VILL_LOSS_FRAC, min_base=10):
        pos = deaths_position(rows, "vill_death", c["pid"],
                              c["t_first"], c["t_min"])
        if not pos:
            continue
        cands.append({"kind": "vill_loss", "prio": 1,
                      "t0": c["t_first"], "t1": c["t_min"],
                      "x": pos[0], "y": pos[1],
                      "info": {"player": c["pid"],
                               "lost": c["drop"], "of": c["base"],
                               "frac": round(c["frac"], 3)}})

    # prio 2: army losses
    for c in loss_candidates(rows, "army_pop", ARMY_LOSS_FRAC, min_base=5):
        pos = deaths_position(rows, "unit_death", c["pid"],
                              c["t_first"], c["t_min"])
        if not pos:
            pos = deaths_position(rows, "vill_death", c["pid"],
                                  c["t_first"], c["t_min"])
        if not pos:
            continue
        cands.append({"kind": "army_loss", "prio": 2,
                      "t0": c["t_first"], "t1": c["t_min"],
                      "x": pos[0], "y": pos[1],
                      "info": {"player": c["pid"],
                               "lost": c["drop"], "of": c["base"],
                               "frac": round(c["frac"], 3)}})

    # prio 3 / 4: TC and castle destruction (completed buildings)
    for r in rows:
        if r.get("type") != "bld_event" or r.get("event") != "lost":
            continue
        if r.get("isTC"):
            cands.append({"kind": "tc_fall", "prio": 3,
                          "t0": r["time"], "t1": r["time"],
                          "x": r["x"], "y": r["y"],
                          "info": {"owner": r.get("owner"),
                                   "name": r.get("name")}})
        elif r.get("isCastle"):
            cands.append({"kind": "castle_fall", "prio": 4,
                          "t0": r["time"], "t1": r["time"],
                          "x": r["x"], "y": r["y"],
                          "info": {"owner": r.get("owner"),
                                   "name": r.get("name")}})

    # prio 6: resign/defeat — clip where their last building fell or unit died
    tcs = tc_positions(rows)
    for r in rows:
        if r.get("type") != "resign":
            continue
        t, pid = r["time"], r.get("player")
        # prefer the LAST destroyed building; fall back to the last death
        last_bld = None
        for rr in rows:
            if rr.get("type") == "bld_event" and rr.get("event") == "lost" \
                    and rr.get("owner") == pid and rr.get("time") is not None \
                    and rr["time"] <= t and rr.get("x") is not None:
                if last_bld is None or rr["time"] > last_bld["time"]:
                    last_bld = rr
        if last_bld:
            pos = (last_bld["x"], last_bld["y"], 1)
        else:
            pos = deaths_position(rows, "vill_death", pid, 0.0, t)
            alt = deaths_position(rows, "unit_death", pid, 0.0, t)
            if alt and (not pos or alt[2] > pos[2]):
                pos = alt
        if not pos:
            continue
        cands.append({"kind": "resign", "prio": 6,
                      "t0": t, "t1": t,
                      "x": pos[0], "y": pos[1],
                      "info": {"player": pid}})

    # prio 7: castle built within 20 tiles of an enemy TC
    dones = {}
    for r in rows:
        if r.get("type") == "bld_event" and r.get("event") == "done" \
                and r.get("isCastle"):
            dones.setdefault(r["id"], r)
    for cid, r in dones.items():
        enemies = [pid for pid in tcs if pid not in (r.get("owner"), 0, None)]
        near = []
        for pid in enemies:
            for t_from, t_to, tx, ty in tcs[pid]:
                if t_from <= r["time"] and (t_to is None or t_to > r["time"]) \
                        and tx is not None:
                    d = ((tx - r["x"]) ** 2 + (ty - r["y"]) ** 2) ** 0.5
                    if d <= CASTLE_NEAR_TC_TILES:
                        near.append((pid, round(d, 1)))
        if near:
            cands.append({"kind": "castle_near_tc", "prio": 7,
                          "t0": r["time"], "t1": r["time"],
                          "x": r["x"], "y": r["y"],
                          "info": {"owner": r.get("owner"),
                                   "enemy_tcs": near[:4]}})

    # prio 8 (bottom): villager killed by an animal. Attribution is
    # module-side: gaia-owned aggressive animal within ~3 tiles of the
    # death (kill attribution is engine-simulated, not in the file).
    for r in rows:
        if r.get("type") == "vill_death" and r.get("by_animal") \
                and r.get("x") is not None:
            cands.append({"kind": "vill_animal", "prio": 8,
                          "t0": r["time"], "t1": r["time"],
                          "x": r["x"], "y": r["y"],
                          "info": {"player": r.get("player"),
                                   "animal": r.get("by_animal")}})
    return cands


def overlaps(a, b):
    # compare padded windows (what will actually be filmed)
    a0, a1 = a["t0"] - PADS[a["prio"]][0], a["t1"] + PADS[a["prio"]][1]
    b0, b1 = b["t0"] - PADS[b["prio"]][0], b["t1"] + PADS[b["prio"]][1]
    return a0 < b1 and b0 < a1


def solve_overlaps(cands):
    """Higher priority wins entirely; same priority merges. Then chrono."""
    cands.sort(key=lambda c: (c["prio"], c["t0"]))
    kept = []
    for c in cands:
        same = [k for k in kept if k["prio"] == c["prio"]
                and overlaps(k, c)]
        higher = [k for k in kept if k["prio"] < c["prio"]
                  and overlaps(k, c)]
        if higher:
            continue
        if same:
            base = same[0]
            base["t0"] = min(base["t0"], c["t0"])
            base["t1"] = max(base["t1"], c["t1"])
            for k in kept:
                if k["prio"] == c["prio"] and k is not base \
                        and overlaps(k, base):
                    base["t0"] = min(base["t0"], k["t0"])
                    base["t1"] = max(base["t1"], k["t1"])
                    k["t0"] = base["t0"]
                    k["t1"] = base["t1"]
            continue
        kept.append(c)
    # drop same-prio duplicates produced by merging
    seen, final = set(), []
    for k in sorted(kept, key=lambda c: (c["t0"], c["t1"])):
        key = (k["prio"], k["t0"], k["t1"])
        if key in seen:
            continue
        seen.add(key)
        pb, pa = PADS[k["prio"]]
        k["pb"], k["pa"] = pb, pa
        final.append(k)
    return final


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("events")
    ap.add_argument("--out", default=None)
    ap.add_argument("--row", type=int, default=None)
    args = ap.parse_args()

    rows = load_events(args.events)
    length = next((r["time"] for r in rows
                   if r.get("type") == "replay_length"), None)

    cands = build_candidates(rows)
    if length is not None:
        cands = [c for c in cands if c["t0"] <= length]
        for c in cands:
            c["t1"] = min(c["t1"], length)
    segs = solve_overlaps(cands)

    plan = {
        "row": args.row,
        "source": args.events,
        "replay_length": length,
        "segments": segs,
    }
    out_path = args.out or str(
        Path(args.events).with_name("events_plan_"
                                    + Path(args.events).stem + ".json"))
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(plan, fh, indent=2)

    if not segs:
        print("no events selected (zero events is valid: no video)")
    for s in segs:
        print(f"p{s['prio']} {s['kind']:15s} t={s['t0']:.0f}..{s['t1']:.0f} "
              f"+({s['pb']},{s['pa']}) tile {s['x']:.0f}:{s['y']:.0f} "
              f"{json.dumps(s['info'])[:80]}")
    print(f"plan: {out_path} ({len(segs)} segments)")


if __name__ == "__main__":
    main()
