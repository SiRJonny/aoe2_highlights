"""Offline attack-order annotation for a collected events jsonl.

Parses the replay body with mgz (no game needed): player right-clicks land
as ORDER ops carrying player_id, object_ids (units tasked), target_id (the
object ordered onto) and the target x/y. Timestamps come from the SYNC
clock (millis accumulation), same as annotate_resigns. Appends
{"type": "attack_order", "time", "player", "units", "target_id", "x", "y"}
rows, idempotent on re-run (dedupe key = (player, target_id) first task).

ORDER is a generic right-click: the target can be an enemy (attack), a
gaia animal (hunt/attack), own unit/building (garrison, repair, reform) —
the file has no ownership, so caller-side joins resolve intent. Rows with
empty object_ids (camera/repeat clicks) are kept but flagged units=[].

Usage:
    py -3.12 tools\\auto\\annotate_orders.py <replay.aoe2record> <events.jsonl>
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mgz import fast  # noqa: E402
from mgz.fast import header as fast_header  # noqa: E402


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


def main():
    replay_path, events_path = sys.argv[1], sys.argv[2]
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


if __name__ == "__main__":
    main()