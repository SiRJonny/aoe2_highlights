"""Replay-file annotations appended to the collected event stream.

Resigns (with team ids + map script name + true replay length) and the
ORDER right-click ops (attack_order rows: who tasked units onto what,
when, where). Both are idempotent: re-running rewrites the jsonl with
deduped rows.
"""
import json
from pathlib import Path

from mgz import fast
from mgz.fast import header as fast_header


def parse_teams(replay_path):
    """{player_number: team_id} from the DE header (de.players).

    team_id is the authoritative team membership (locked teams); FFA
    games give every player a distinct team. Returns {} when absent."""
    teams = {}
    with open(replay_path, "rb") as d:
        h = fast_header.parse(d)
    for p in (h.get("de") or {}).get("players") or []:
        try:
            teams[int(p["number"])] = int(p["team_id"])
        except (KeyError, TypeError, ValueError):
            continue
    return teams


def parse_map_name(replay_path):
    """The map script filename from the DE header (nomad detection)."""
    with open(replay_path, "rb") as d:
        h = fast_header.parse(d)
    return str((h.get("de") or {}).get("rms_filename") or "")


def parse_resigns(replay_path):
    """Returns a sorted list of {time, player} for every RESIGN action."""
    resigns = []
    total_ms = 0
    with open(replay_path, "rb") as d:
        eof = Path(replay_path).stat().st_size
        fast_header.parse(d)
        fast.meta(d)
        while d.tell() < eof:
            try:
                o = fast.operation(d)
            except Exception:
                break               # truncated body / postgame tail
            opname = o[0].name
            if opname == "SYNC":
                total_ms += o[1][0]
            elif opname == "ACTION":
                action_type, payload = o[1]
                if action_type is not None and action_type.name == "RESIGN":
                    resigns.append({
                        "time": round(total_ms / 1000, 3),
                        "player": payload.get("player_id"),
                    })
            elif opname == "POSTGAME":
                break
    return resigns, total_ms / 1000.0


def annotate_resigns(replay_path, events_path):
    resigns, length_s = parse_resigns(replay_path)
    teams = parse_teams(replay_path)
    map_name = parse_map_name(replay_path)
    existing = set()
    lines = []
    if Path(events_path).exists():
        for line in Path(events_path).read_text(
                encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                lines.append(line)
                continue
            if row.get("type") == "resign":
                existing.add((round(row["time"], 3), row.get("player")))
                lines.append(line)
            elif row.get("type") == "teams":
                continue            # re-emitted below
            elif row.get("type") == "replay_map":
                continue            # re-emitted below
            elif row.get("type") == "replay_length":
                continue            # re-computed below
            else:
                lines.append(line)

    added = 0
    for r in resigns:
        key = (r["time"], r["player"])
        hit = any(abs(k[0] - key[0]) < 0.5 and k[1] == key[1]
                  for k in existing)
        if not hit:
            lines.append(json.dumps({"type": "resign", "time": r["time"],
                                     "player": r["player"]}))
            added += 1
    if teams:
        lines.append(json.dumps({"type": "teams", "teams": teams}))
    if map_name:
        lines.append(json.dumps({"type": "replay_map", "name": map_name}))
    lines.append(json.dumps({"type": "replay_length", "time": length_s}))

    Path(events_path).write_text(
        "\n".join(lines) + "\n", encoding="utf-8")
    print(f"resigns in replay: {len(resigns)}, appended: {added}, "
          f"teams: {len(teams)}, length {length_s:.1f}s -> {events_path}")


def parse_orders(replay_path):
    """Returns a sorted list of attack_order dicts + the replay length s.

    Dedupe: one row per (player, target_id) — the first time that player
    tasked units onto that object. Repeat clicks on the same target are
    noise; re-tasking a DIFFERENT object is a new row."""
    orders = {}
    seen_keys = set()
    total_ms = 0
    with open(replay_path, "rb") as d:
        eof = Path(replay_path).stat().st_size
        fast_header.parse(d)
        fast.meta(d)
        while d.tell() < eof:
            try:
                o = fast.operation(d)
            except Exception:
                break               # truncated body / postgame tail
            opname = o[0].name
            if opname == "SYNC":
                total_ms += o[1][0]
            elif opname == "ACTION":
                action_type, payload = o[1]
                if action_type is None:
                    continue
                if action_type.name == "ORDER":
                    tid = payload.get("target_id")
                    if not tid:
                        continue    # ground click, no object
                    key = (payload.get("player_id"), tid)
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    orders[key] = {
                        "time": round(total_ms / 1000, 3),
                        "player": payload.get("player_id"),
                        "units": [int(u) for u in
                                  (payload.get("object_ids") or [])],
                        "target_id": int(tid),
                        "x": payload.get("x"),
                        "y": payload.get("y"),
                    }
                elif action_type.name == "POSTGAME":
                    break
    return sorted(orders.values(), key=lambda r: r["time"]), \
        total_ms / 1000.0


def annotate_orders(replay_path, events_path):
    orders, length_s = parse_orders(replay_path)
    existing = set()
    lines = []
    if Path(events_path).exists():
        for line in Path(events_path).read_text(
                encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                lines.append(line)
                continue
            if row.get("type") == "attack_order":
                existing.add((row.get("player"), row.get("target_id")))
                continue            # re-emitted below from the parse
            elif row.get("type") == "replay_length":
                continue
            lines.append(line)

    added = 0
    for r in orders:
        key = (r["player"], r["target_id"])
        if key in existing:
            continue
        lines.append(json.dumps({"type": "attack_order", **r}))
        added += 1
    lines.append(json.dumps({"type": "replay_length", "time": length_s}))

    Path(events_path).write_text(
        "\n".join(lines) + "\n", encoding="utf-8")
    print(f"attack orders in replay: {len(orders)}, appended: {added}, "
          f"length {length_s:.1f}s -> {events_path}")
