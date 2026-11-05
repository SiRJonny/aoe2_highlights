"""Priority-based highlight planner for one replay (select_events v3).

Input:  a module events jsonl (vill_pop / army_pop / vill_death /
        unit_death / bld_event / tc_snap / resign / teams / replay_length
        rows).
Output: a plan json, chronological:

    {"row": N, "source": "...", "segments": [...]}

Each segment: {kind, prio, t0, t1, pb, pa, speedName, x, y, info} — t0/t1
are the RAW event window; pb/pa are the per-segment pads applied at
record time (record window = t0-pb .. t1+pa). speedName overrides the
replay speed for that take (castle_build films at FAST).

Priorities:
    1  a player loses >=10% of their villagers within any 15 s   (win ±2)
    2  a player loses >=20% of their army within any 15 s        (win ±2;
       destroyed buildings of that player count as 2 dead units)
    3  Town Center falling                                       (t-5 .. t+2)
    4  Castle/Krepost falling                                    (t-3 .. t+6)
    5  castle/Krepost built in a safe area (no deaths within 12 tiles
       from 30 s before start until done) -> film the construction and
       compress 3x at mux (the engine ladder NORMAL 1.69 / FAST 6.76
       has no middle)                                           (start..done ±2)
    6  resign: only the LAST resign of a team fires; focus = densest
       5-tile bucket of that team's deaths in the last 10 s     (t-4 .. t+2)
    7  castle/Krepost built within 20 tiles of an enemy TC; camera
       focuses the midpoint between castle and that (nearest) TC
                                                                 (t-15 .. t+5)
    8  villager killed by an animal                             (t-2 .. t+3)
    9  battle extension: while >=1 unit/building dies every 2 s within
       12 tiles of a death-based event, the take keeps rolling; the
       extension yields to every other event (dropped when interrupted,
       merged into its base when kept)

Resigned players are event-dead: after a player's resign time their
deaths, building losses and census drops never trigger or extend
events. After minute 30 (t > 1800) a death-based event needs >= 10
deaths to trigger.

Overlap rule: candidates are kept in priority order; a candidate
overlapping an already-kept (higher-priority) segment is DROPPED
entirely. Same-priority overlaps merge. The final plan is
chronological; gaps are skipped by seeking at record time. Zero events
-> empty plan -> no video.

Usage:
    py -3.12 tools\\auto\\select_events.py out\\events_mp1.jsonl
        [--out out\\events_plan_mp1.json] [--row N]
"""
import argparse
import bisect
import json
import sys
from pathlib import Path

LOSS_WINDOW = 15.0     # s, prio 1/2 detection window
VILL_LOSS_FRAC = 0.10
ARMY_LOSS_FRAC = 0.20
CASTLE_NEAR_TC_TILES = 20.0
NEARBY_TILES = 12.0    # "nearby area" for extensions + building losses
EXTEND_GAP = 2.0       # s: deaths at least this often keep a battle going
LATE_GATE_T = 1800.0   # after minute 30 ...
LATE_MIN_DEATHS = 10   # ... a death-based event needs >=10 deaths
BLD_DEATH_WEIGHT = 2   # a destroyed building counts as 2 dead units
RESIGN_FOCUS_S = 10.0  # look this far back for the resign focus
FOCUS_BUCKET = 5.0     # tile bucket size for the densest-death focus
RESIGN_CLUSTER_S = 180.0  # fallback team detection without a teams row
CASTLE_BUILD_SAFE_S = 30.0  # deaths-free span checked before start
PRIO_CASTLE_BUILD = 5  # the vacated slot; now used by the nomad grid
CASTLE_BUILD_ENABLED = False   # safe-castle recordings disabled for now
PRIO_FIRST_TC_GRID = 5         # nomad first-TC grid (same free slot)
FIRST_TC_TAIL_S = 4.0          # film this long past the last TC done
NOMAD_MAP_PAT = ("nomad", "migration")
PRIO_EXTEND = 9        # battle extension: lowest, yields to everything

PADS = {               # per priority (pb, pa) in game seconds
    1: (2.0, 2.0),
    2: (2.0, 2.0),
    3: (5.0, 2.0),
    4: (3.0, 6.0),     # +2 s tail: the castle-fall animation runs long
    5: (2.0, 2.0),
    6: (4.0, 2.0),
    7: (15.0, 5.0),
    8: (2.0, 3.0),     # villager killed by animal (pads not user-specified)
    9: (2.0, 2.0),     # extensions merge into their base; pads spare
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


def resigned_at(rows):
    """{pid: first resign time} — a player is event-dead after this."""
    out = {}
    for r in rows:
        if r.get("type") == "resign" and r.get("player") is not None:
            t = r.get("time")
            pid = r["player"]
            if pid not in out or (t is not None and t < out[pid]):
                out[pid] = t if t is not None else out.get(pid)
    return out


def teams_of(rows):
    """{pid: team_id} from the annotator's teams row, or {} when absent."""
    for r in rows:
        if r.get("type") == "teams" and isinstance(r.get("teams"), dict):
            return {int(k): int(v) for k, v in r["teams"].items()}
    return {}


def is_castle_row(r):
    """Castle OR Krepost (same event category, user rule).

    EXACT name match only: DE type names encode the age
    (UNIVERSITY_CASTLE_AGE, MONASTERY_CASTLE_AGE_BASE), so a substring
    match catches monasteries/universities built in the Castle Age.
    The module's isCastle flag is ignored for the same reason (older
    collects carry the polluted flag)."""
    return (r.get("name") or "").upper() in ("CASTLE", "KREPOST")


def death_feed(rows, resigned):
    """Sorted death feed [(t, x, y, pid, weight)] for extension chains,
    focuses and building-loss incorporation.

    Deaths of resigned players are excluded entirely (post-resign
    deletions are cleanup, not battles). A lost building counts as
    BLD_DEATH_WEIGHT dead units."""
    feed = []
    for r in rows:
        t = r.get("time")
        x, y = r.get("x"), r.get("y")
        if t is None or x is None or y is None:
            continue
        rt = r.get("type")
        if rt in ("vill_death", "unit_death"):
            pid = r.get("player")
            if pid is not None and pid in resigned \
                    and resigned[pid] is not None and t > resigned[pid]:
                continue
            feed.append((t, x, y, pid, 1))
        elif rt == "bld_event" and r.get("event") == "lost":
            pid = r.get("owner")
            if pid is not None and pid in resigned \
                    and resigned[pid] is not None and t > resigned[pid]:
                continue
            feed.append((t, x, y, pid, BLD_DEATH_WEIGHT))
    feed.sort(key=lambda d: d[0])
    return feed


def feed_nearby(feed, t_a, t_b, x, y, radius=NEARBY_TILES, pids=None):
    """Deaths in the feed inside a time span, tile radius and optional
    player set."""
    return [d for d in feed
            if t_a <= d[0] <= t_b
            and abs(d[1] - x) <= radius and abs(d[2] - y) <= radius
            and (pids is None or d[3] in pids)]


def extend_chain(feed, t_start, x, y):
    """Battle extension: walk the sorted feed forward from t_start; while
    at least one death occurs every EXTEND_GAP seconds within
    NEARBY_TILES, the chain (and the take) keeps rolling. Returns the
    last chained death time (t_start when nothing chains)."""
    times = [d[0] for d in feed]
    i = bisect.bisect_right(times, t_start)
    last_t = t_start
    while i < len(feed):
        d = feed[i]
        if d[0] > last_t + EXTEND_GAP:
            break           # sorted: every later death is past the gap too
        if abs(d[1] - x) <= NEARBY_TILES and abs(d[2] - y) <= NEARBY_TILES:
            last_t = d[0]
        i += 1
    return last_t


def focus_cluster(feed, pids, t_a, t_b):
    """Densest FOCUS_BUCKET-tile death bucket of `pids` in a time span:
    returns (x, y, weight) or None. Buildings weigh BLD_DEATH_WEIGHT."""
    buckets = {}
    for t, x, y, pid, w in feed:
        if pid not in pids or not (t_a <= t <= t_b):
            continue
        key = (int(x // FOCUS_BUCKET), int(y // FOCUS_BUCKET))
        b = buckets.setdefault(key, [0.0, 0.0, 0.0, 0.0])  # w, sx, sy, n
        b[0] += w
        b[1] += x
        b[2] += y
        b[3] += 1
    if not buckets:
        return None
    best = max(buckets.values(), key=lambda b: b[0])
    return (best[1] / best[3], best[2] / best[3], best[0])


def build_candidates(rows):
    """All priority candidates as plan-shaped dicts (before overlap solve)."""
    cands = []
    resigned = resigned_at(rows)
    feed = death_feed(rows, resigned)

    def late_gate(t, deaths):
        # after minute 30 a death-based event needs >=10 deaths
        return t <= LATE_GATE_T or deaths >= LATE_MIN_DEATHS

    # prio 1: villager losses
    for c in loss_candidates(rows, "vill_pop", VILL_LOSS_FRAC, min_base=10):
        rt = resigned.get(c["pid"])
        if rt is not None and c["t_min"] > rt:
            continue        # census drop after the resign: cleanup
        if not late_gate(c["t_min"], c["drop"]):
            continue
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
        base_ref = cands[-1]
        ext_end = extend_chain(feed, c["t_min"], pos[0], pos[1])
        if ext_end > c["t_min"] + 0.5:
            cands.append({"kind": "battle_extend", "prio": PRIO_EXTEND,
                          "t0": c["t_min"], "t1": ext_end,
                          "x": pos[0], "y": pos[1],
                          "base_ref": base_ref,
                          "info": {"base_kind": "vill_loss",
                                   "base_t0": c["t_first"],
                                   "player": c["pid"]}})

    # prio 2: army losses — destroyed buildings of the same player count
    # as BLD_DEATH_WEIGHT dead units each (same window, within 12 tiles)
    for c in loss_candidates(rows, "army_pop", ARMY_LOSS_FRAC, min_base=5):
        rt = resigned.get(c["pid"])
        if rt is not None and c["t_min"] > rt:
            continue
        pos = deaths_position(rows, "unit_death", c["pid"],
                              c["t_first"], c["t_min"])
        if not pos:
            pos = deaths_position(rows, "vill_death", c["pid"],
                                  c["t_first"], c["t_min"])
        if not pos:
            continue
        near_blds = feed_nearby(feed, c["t_first"], c["t_first"] + LOSS_WINDOW,
                                pos[0], pos[1], pids={c["pid"]})
        bld_n = sum(1 for d in near_blds if d[4] == BLD_DEATH_WEIGHT)
        bld_w = bld_n * BLD_DEATH_WEIGHT
        lost_total = c["drop"] + bld_w
        if not late_gate(c["t_min"], lost_total):
            continue
        cands.append({"kind": "army_loss", "prio": 2,
                      "t0": c["t_first"], "t1": c["t_min"],
                      "x": pos[0], "y": pos[1],
                      "info": {"player": c["pid"],
                               "lost": lost_total,
                               "units": c["drop"], "buildings": bld_n,
                               "bld_units": bld_w,
                               "of": c["base"],
                               "frac": round(c["frac"], 3)}})
        base_ref = cands[-1]
        ext_end = extend_chain(feed, c["t_min"], pos[0], pos[1])
        if ext_end > c["t_min"] + 0.5:
            cands.append({"kind": "battle_extend", "prio": PRIO_EXTEND,
                          "t0": c["t_min"], "t1": ext_end,
                          "x": pos[0], "y": pos[1],
                          "base_ref": base_ref,
                          "info": {"base_kind": "army_loss",
                                   "base_t0": c["t_first"],
                                   "player": c["pid"]}})

    # prio 3 / 4: TC and castle/Krepost destruction (completed buildings)
    for r in rows:
        if r.get("type") != "bld_event" or r.get("event") != "lost":
            continue
        rt = resigned.get(r.get("owner"))
        if rt is not None and r.get("time") is not None and r["time"] > rt:
            continue        # a resigned player's buildings are cleanup
        if r.get("isTC"):
            cands.append({"kind": "tc_fall", "prio": 3,
                          "t0": r["time"], "t1": r["time"],
                          "x": r["x"], "y": r["y"],
                          "info": {"owner": r.get("owner"),
                                   "name": r.get("name")}})
        elif is_castle_row(r):
            cands.append({"kind": "castle_fall", "prio": 4,
                          "t0": r["time"], "t1": r["time"],
                          "x": r["x"], "y": r["y"],
                          "info": {"owner": r.get("owner"),
                                   "name": r.get("name")}})

    # prio 5: castle/Krepost built in a SAFE area -> film the whole
    # construction sped up at FAST (middle speed, not FASTEST). Safety
    # proxy: no deaths within 12 tiles from 30 s before start until done
    # (a raid on the builders would kill someone nearby).
    dones_by_id = {}
    for r in rows:
        if r.get("type") == "bld_event" and r.get("event") == "done":
            dones_by_id.setdefault(r.get("id"), r)
    if CASTLE_BUILD_ENABLED:
        for r in rows:
            if r.get("type") != "bld_event" or r.get("event") != "start" \
                    or not is_castle_row(r) or r.get("time") is None:
                continue
            done = dones_by_id.get(r.get("id"))
            if not done or done.get("time") is None \
                    or done["time"] < r["time"]:
                continue        # never completed (or stale id reuse)
            t0, t1 = r["time"], done["time"]
            if feed_nearby(feed, t0 - CASTLE_BUILD_SAFE_S, t1,
                           r["x"], r["y"]):
                continue        # contested ground: not a safe build
            # FAST = 6.76x (measured ladder): the user-chosen construction
            # speedup; the take settles at FAST and plays wall-real
            cands.append({"kind": "castle_build", "prio": PRIO_CASTLE_BUILD,
                          "t0": t0, "t1": t1,
                          "x": r["x"], "y": r["y"], "speedName": "FAST",
                          "info": {"owner": r.get("owner"),
                                   "name": r.get("name"),
                                   "safe": True}})

    # prio 5 (nomad maps): the first-TC grid. Nomad maps start TC-less;
    # every player walks their villagers to a spot and builds. ONE grid
    # segment covers the whole phase (game start -> last TC done +
    # FIRST_TC_TAIL_S): the record flow films it once PER PLAYER (fresh
    # replay reload each time — the takes share the same game-time span,
    # so they composite into a grid: 2x2 for 4 players, 3x3 for 6/8 with
    # an enlarged minimap cell), at FAST.
    map_name = next((r.get("name", "") for r in rows
                     if r.get("type") == "replay_map"), "")
    is_nomad = any(p in map_name.lower() for p in NOMAD_MAP_PAT)
    if not is_nomad and not map_name:
        # gameplay fallback for UNKNOWN map names only (mp5's custom
        # lobby leaves de.rms_filename empty): a nomad start means
        # NOBODY has a TC at match start (no tc_snap rows at all) while
        # TCs get built later. A known non-nomad name is trusted.
        has_tc_snap = any(r.get("type") == "tc_snap" for r in rows)
        has_later_tc = any(r.get("type") == "bld_event" and r.get("isTC")
                           for r in rows)
        is_nomad = not has_tc_snap and has_later_tc
    if is_nomad:
        roster = set()
        for r in rows:
            if r.get("type") == "teams" and isinstance(r.get("teams"), dict):
                roster = {int(k) for k in r["teams"]}
                break
        starts = {}   # pid: first isTC start row
        dones = {}    # (pid, id): first done row
        for r in rows:
            if r.get("type") != "bld_event" or not r.get("isTC"):
                continue
            pid = r.get("owner")
            if r.get("event") == "start":
                if pid is not None and pid not in starts:
                    starts[pid] = r
            elif r.get("event") == "done":
                key = (pid, r.get("id"))
                if key not in dones:
                    dones[key] = r
        # vill_pos feed for builder selection: per player, the villager
        # closest to the foundation when it appears
        vpos = {}
        for r in rows:
            if r.get("type") == "vill_pos" and r.get("time") is not None:
                vpos.setdefault(round(r["time"], 3), []).extend(
                    r.get("positions") or [])
        players = []
        for pid in sorted(roster):
            st = starts.get(pid)
            if not st or st.get("time") is None or st.get("x") is None:
                continue        # no TC built (died before building one)
            done = dones.get((pid, st.get("id")))
            t_done = done["time"] if done and done.get("time") else None
            if t_done is None:
                continue
            best = None
            for t in sorted(vpos):
                if t > st["time"]:
                    break
                for v in vpos[t]:
                    if v.get("player") == pid and v.get("x") is not None:
                        d = abs(v["x"] - st["x"]) + abs(v["y"] - st["y"])
                        if best is None or d < best[0]:
                            best = (d, v.get("id"))
            players.append({"pid": pid,
                            "builder_id": best[1] if best else None,
                            "tc_x": st["x"], "tc_y": st["y"],
                            "t_start": st["time"], "t_done": t_done})
        if players:
            t1 = max(p["t_done"] for p in players) + FIRST_TC_TAIL_S
            cands.append({"kind": "first_tc_grid", "prio": PRIO_FIRST_TC_GRID,
                          "t0": 0.0, "t1": t1,
                          "x": players[0]["tc_x"], "y": players[0]["tc_y"],
                          "speedName": "FAST",
                          "info": {"players": players, "nomad": True,
                                   "map": map_name}})

    # prio 6: resign — only the LAST resign of a team fires. Focus = the
    # densest 5-tile bucket of that team's deaths in the last 10 s
    # (buildings weigh 2); fallback = densest bucket over the whole game,
    # then the resigning player's last destroyed building / last death.
    tcs = tc_positions(rows)
    teams = teams_of(rows)
    resigns = [(r["time"], r.get("player")) for r in rows
               if r.get("type") == "resign" and r.get("time") is not None]
    if teams:
        by_team = {}
        for t, pid in resigns:
            by_team.setdefault(teams.get(pid), []).append((t, pid))
        groups = []
        for team, members in by_team.items():
            # the focus covers the WHOLE team's deaths, not only the
            # resigning members'
            roster = sorted(p for p, tid in teams.items() if tid == team)
            groups.append((team, roster, max(members)))
    else:
        # no teams row: cluster consecutive resigns (a team defeat is a
        # burst of resigns; a solo quit stands alone)
        groups = []
        cluster = []
        for t, pid in sorted(resigns):
            if cluster and t - cluster[-1][0] > RESIGN_CLUSTER_S:
                groups.append((None, [p for _, p in cluster],
                               max(cluster)))
                cluster = []
            cluster.append((t, pid))
        if cluster:
            groups.append((None, [p for _, p in cluster], max(cluster)))
    for team, pids, (t, last_pid) in groups:
        pids = set(pids)
        # the focus counts the team's battle deaths even if a member had
        # already resigned (task 1 only bars post-resign CLEANUP events)
        all_feed = death_feed(rows, {})
        focus = focus_cluster(all_feed, pids, t - RESIGN_FOCUS_S, t)
        if not focus:
            focus = focus_cluster(all_feed, pids, 0.0, t)
        if focus:
            pos = (focus[0], focus[1], focus[2])
        else:
            pos = deaths_position(rows, "vill_death", last_pid, 0.0, t)
            alt = deaths_position(rows, "unit_death", last_pid, 0.0, t)
            if alt and (not pos or alt[2] > pos[2]):
                pos = alt
        if not pos:
            # no deaths at all: focus the resigning player's base (TC)
            for t_from, t_to, tx, ty in tcs.get(last_pid, []):
                if t_from <= t and (t_to is None or t_to > t) \
                        and tx is not None:
                    pos = (tx, ty, 0)
                    break
        if not pos:
            continue
        cands.append({"kind": "resign", "prio": 6,
                      "t0": t, "t1": t,
                      "x": pos[0], "y": pos[1],
                      "info": {"player": last_pid,
                               "team": team,
                               "team_pids": sorted(pids),
                               "resigned": len([1 for rt, rp in resigns
                                                if rp in pids])}})

    # prio 7: castle/Krepost built within 20 tiles of an enemy TC — the
    # camera focuses the MIDPOINT between the castle and the nearest
    # qualifying enemy TC
    dones = dones_by_id
    for cid, r in dones.items():
        rt = resigned.get(r.get("owner"))
        if rt is not None and r.get("time") is not None and r["time"] > rt:
            continue
        if not is_castle_row(r):
            continue
        enemies = [pid for pid in tcs if pid not in (r.get("owner"), 0, None)]
        near = []
        for pid in enemies:
            for t_from, t_to, tx, ty in tcs[pid]:
                if t_from <= r["time"] and (t_to is None or t_to > r["time"]) \
                        and tx is not None:
                    d = ((tx - r["x"]) ** 2 + (ty - r["y"]) ** 2) ** 0.5
                    if d <= CASTLE_NEAR_TC_TILES:
                        near.append((pid, round(d, 1), tx, ty))
        if near:
            pid, d, tx, ty = min(near, key=lambda n: n[1])
            cands.append({"kind": "castle_near_tc", "prio": 7,
                          "t0": r["time"], "t1": r["time"],
                          "x": (r["x"] + tx) / 2, "y": (r["y"] + ty) / 2,
                          "info": {"owner": r.get("owner"),
                                   "enemy_tcs": [(n[0], n[1])
                                                 for n in near[:4]],
                                   "midpoint_of": pid,
                                   "tc_dist": d}})

    # prio 8 (bottom): villager killed by an animal. Attribution is
    # module-side: gaia aggressive animal near the death (kill
    # attribution is engine-simulated, not in the file). Closest suspect
    # wins: an enemy unit/building suspect closer than the animal means
    # the animal was a bystander (verified: a boar 3 tiles from a kill
    # whose real suspect was a militia 0.57 tiles away). Prey animals
    # (boar/elephant/...) only attack once damaged — the provocation can
    # be anyone's (hunt click, auto-attacking military), not necessarily
    # the victim's.
    PREY = ("BOAR", "ELEPHANT", "RHINO", "DEER", "CROCODILE", "OSTRICH")
    for r in rows:
        if r.get("type") != "vill_death" or not r.get("by_animal") \
                or r.get("x") is None:
            continue
        rt = resigned.get(r.get("player"))
        if rt is not None and r.get("time") is not None and r["time"] > rt:
            continue        # resigned player's villager: cleanup
        a_d = abs(r.get("animal_x", 0) - r["x"]) \
            + abs(r.get("animal_y", 0) - r["y"])
        if r.get("by_player") and r.get("by_player_dist", 1e9) <= a_d:
            continue
        if r.get("by_bld") and r.get("by_bld_dist", 1e9) <= a_d:
            continue
        name = r["by_animal"]
        cands.append({"kind": "vill_animal", "prio": 8,
                      "t0": r["time"], "t1": r["time"],
                      "x": r["x"], "y": r["y"],
                      "info": {"player": r.get("player"),
                               "animal": name,
                               "provoked": any(p in name for p in PREY)}})
    return cands


def overlaps(a, b):
    # compare padded windows (what will actually be filmed)
    a0, a1 = a["t0"] - PADS[a["prio"]][0], a["t1"] + PADS[a["prio"]][1]
    b0, b1 = b["t0"] - PADS[b["prio"]][0], b["t1"] + PADS[b["prio"]][1]
    return a0 < b1 and b0 < a1


def is_own_base(base, ext):
    """True when `base` is the base segment that spawned the `ext`
    extension candidate (extensions must not be dropped by their own
    base's padded window)."""
    return base.get("kind") == ext.get("info", {}).get("base_kind") \
        and abs(base["t0"] - ext["info"].get("base_t0", -1.0)) < 0.5


def solve_overlaps(cands):
    """Higher priority wins entirely; same priority merges. Extensions
    (prio 9) only yield to OTHER events — they merge into their base
    when they survive, and die with it when the base was dropped."""
    cands.sort(key=lambda c: (c["prio"], c["t0"]))
    kept = []
    for c in cands:
        same = [k for k in kept if k["prio"] == c["prio"]
                and overlaps(k, c)]
        higher = [k for k in kept if k["prio"] < c["prio"]
                  and overlaps(k, c)
                  and not (c["prio"] == PRIO_EXTEND and is_own_base(k, c))]
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
        final.append(k)
    # merge surviving extensions into their base segment: the take just
    # keeps rolling; an extension whose base was dropped dies with it
    bases = [k for k in final if k["prio"] != PRIO_EXTEND]
    out = []
    for k in final:
        if k["prio"] == PRIO_EXTEND:
            base = k.pop("base_ref", None)
            if base is not None and any(b is base for b in bases):
                base["t1"] = max(base["t1"], k["t1"])
                base["info"]["extended"] = True
            continue
        out.append(k)
    for k in out:
        pb, pa = PADS[k["prio"]]
        k.setdefault("pb", pb)
        k.setdefault("pa", pa)
        k.setdefault("speedName", "NORMAL")
    return sorted(out, key=lambda c: c["t0"])


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
        speed = "" if s.get("speedName", "NORMAL") == "NORMAL" \
            else f" @ {s['speedName']}"
        print(f"p{s['prio']} {s['kind']:15s} t={s['t0']:.0f}..{s['t1']:.0f} "
              f"+({s['pb']},{s['pa']}) tile {s['x']:.0f}:{s['y']:.0f}"
              f"{speed} "
              f"{json.dumps(s['info'])[:80]}")
    print(f"plan: {out_path} ({len(segs)} segments)")


if __name__ == "__main__":
    main()
