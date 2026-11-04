"""Offline resign/defeat annotation for a collected events jsonl.

Parses the replay body with mgz (no game needed): SYNC increments carry the
game clock in milliseconds; RESIGN actions between them are timestamped with
the accumulated clock. Appends {"type": "resign", "time": <game s>,
"player": <player number>} rows, idempotent (no duplicates on re-run).

Player numbering: the body uses 1-based player numbers (Gaia = 0), matching
the module's playerId rows.

Usage:
    py -3.12 tools\\auto\\annotate_resigns.py <replay.aoe2record> <events.jsonl>
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mgz import fast  # noqa: E402
from mgz.fast import header as fast_header  # noqa: E402


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


def main():
    replay_path, events_path = sys.argv[1], sys.argv[2]
    resigns, length_s = parse_resigns(replay_path)
    teams = parse_teams(replay_path)

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
    lines.append(json.dumps({"type": "replay_length", "time": length_s}))

    Path(events_path).write_text(
        "\n".join(lines) + "\n", encoding="utf-8")
    print(f"resigns in replay: {len(resigns)}, appended: {added}, "
          f"teams: {len(teams)}, length {length_s:.1f}s -> {events_path}")


if __name__ == "__main__":
    main()
