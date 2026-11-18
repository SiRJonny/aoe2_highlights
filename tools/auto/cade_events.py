"""Extract the event/command stream from a CadeRemote Frames archive.

No state model needed: Frame.time (ms) + the Event/Command oneofs are
decoded directly from the wire. Output is JSONL, one record per event
(or command with --commands), plus optional per-frame time index.

Usage:
    py -3.12 tools/auto/cade_events.py out/cade/sp_archive_test.xz \
        [-o events.jsonl] [--commands] [--times]
"""
from __future__ import annotations

import json
import lzma
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

EVENT_NAMES = {
    1: "entity_killed", 2: "market_transaction", 3: "tribute",
    4: "player_chat", 5: "combat_notification_sound", 6: "damage",
    7: "monk_conversion", 8: "notification_train_done",
    9: "notification_research_done",
    10: "notification_farm_or_fish_trap_died", 11: "notification_wonder",
}

# field names known from the LibreMatch proto (v1); v22 keeps these
# plus adds Damage/MonkConversion/Notification* whose field layouts are
# decoded generically (f1..fN).
EVENT_FIELDS = {
    "entity_killed": {1: "id", 2: "killer_id"},
    "market_transaction": {1: "player_id", 2: "attribute_type",
                           3: "attribute_before", 4: "gold_before",
                           5: "attribute_exchanged", 6: "gold_rate",
                           7: "gold_exchanged"},
    "tribute": {1: "sender_id", 2: "receiver_id", 3: "attribute_type",
                4: "sender_value_before", 5: "sender_value_change",
                6: "receiver_value_before", 7: "receiver_value_change"},
    "player_chat": {1: "player_id", 2: "comm_player_id", 3: "channel",
                    4: "text", 5: "taunt"},
    "combat_notification_sound": {1: "receiver_id", 2: "type",
                                  3: "attacked_object_id"},
}


def decode_value(wt: int, raw: bytes):
    if wt == 0:
        return int.from_bytes(raw, "little", signed=True)
    if wt == 1:
        import struct as _s
        return _s.unpack("<d", raw)[0]
    if wt == 5:
        import struct as _s
        return _s.unpack("<f", raw)[0]
    try:
        txt = raw.decode("utf-8")
        if all(32 <= ord(c) < 127 or c in "\n\r\t" for c in txt):
            return txt
    except UnicodeDecodeError:
        pass
    return raw.hex()


def fields_of(data: bytes):
    """(num, wiretype, raw_value) with raw varint/bytes kept as bytes."""
    i = 0
    n = len(data)
    while i < n:
        key = 0
        shift = 0
        while i < n:
            b = data[i]
            i += 1
            key |= (b & 0x7F) << shift
            shift += 7
            if not b & 0x80:
                break
        num, wt = key >> 3, key & 7
        if wt == 0:
            v = 0
            shift = 0
            start = i
            while i < n:
                b = data[i]
                i += 1
                v |= (b & 0x7F) << shift
                shift += 7
                if not b & 0x80:
                    break
            yield num, wt, data[start:i]
        elif wt == 1:
            yield num, wt, data[i:i + 8]
            i += 8
        elif wt == 2:
            ln = 0
            shift = 0
            while i < n:
                b = data[i]
                i += 1
                ln |= (b & 0x7F) << shift
                shift += 7
                if not b & 0x80:
                    break
            yield num, wt, data[i:i + ln]
            i += ln
        elif wt == 5:
            yield num, wt, data[i:i + 4]
            i += 4
        else:
            return


def main():
    src = Path(sys.argv[1])
    out = None
    if "-o" in sys.argv:
        out = Path(sys.argv[sys.argv.index("-o") + 1])
    want_cmds = "--commands" in sys.argv
    want_times = "--times" in sys.argv
    data = lzma.open(src, "rb").read()
    i = 0
    n = len(data)
    fh = open(out, "w", encoding="utf-8") if out else None

    def emit(rec):
        print(json.dumps(rec), file=fh or sys.stdout)

    n_seq = n_frame = n_ev = n_cmd = 0
    last_t = 0
    try:
        while i < n:
            (ln,) = struct.unpack("<I", data[i:i + 4])
            seq = data[i + 4:i + 4 + ln]
            i += 4 + ln
            n_seq += 1
            for fnum, fwt, fval in fields_of(seq):
                if fnum != 1 or fwt != 2:
                    continue     # Frame entries (field 2 = sending time)
                t = 0
                evs = []
                cmds = []
                for gnum, gwt, gval in fields_of(fval):
                    if gnum == 1 and gwt == 0:
                        t = int.from_bytes(gval, "little")
                    elif gnum == 3 and gwt == 2:
                        evs.append(gval)
                    elif gnum == 6 and gwt == 2:
                        cmds.append(gval)
                n_frame += 1
                last_t = t
                if want_times and n_frame % 50 == 0:
                    emit({"kind": "time", "t_ms": t, "frame": n_frame})
                for ev in evs:
                    emit(decode_event(t, ev))
                    n_ev += 1
                if want_cmds:
                    for cm in cmds:
                        emit({"kind": "command", "t_ms": t,
                              "wire": cm.hex()})
                        n_cmd += 1
    finally:
        if fh:
            fh.close()
    print(f"{src.name}: {n_seq} sequences, {n_frame} frames, "
          f"last t={last_t} ms; events {n_ev}, commands {n_cmd}"
          f"{' -> ' + str(out) if out else ''}")


def decode_event(t_ms: int, ev: bytes) -> dict:
    for num, wt, raw in fields_of(ev):
        name = EVENT_NAMES.get(num, f"event{num}")
        rec = {"kind": name, "t_ms": t_ms}
        field_names = EVENT_FIELDS.get(name, {})
        for fnum, fwt, fval in fields_of(raw):
            key = field_names.get(fnum, f"f{fnum}")
            rec[key] = decode_value(fwt, fval)
        return rec
    return {"kind": "empty", "t_ms": t_ms}


if __name__ == "__main__":
    main()
