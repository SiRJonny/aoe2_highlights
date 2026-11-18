"""Analyze a captured CadeRemote Frames sample (.xz of length-prefixed
FrameSequence messages).

Usage:
    py -3.12 tools/auto/analyze_cade_frames.py out/probe_cade/frames_sample_fastest.xz
"""
from __future__ import annotations

import lzma
import struct
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from aoe2hl import cade  # noqa: E402


def main():
    src = Path(sys.argv[1])
    data = lzma.open(src, "rb").read()
    print(f"{src}: {len(data) / 1e6:.1f} MB raw")
    i = 0
    n = len(data)
    seqs = []
    while i < n:
        (ln,) = struct.unpack("<I", data[i:i + 4])
        seqs.append(data[i + 4:i + 4 + ln])
        i += 4 + ln
    print(f"{len(seqs)} FrameSequence messages")

    times = []
    patch_sizes = Counter()
    rev_sizes = Counter()
    events = Counter()
    cmds = Counter()
    sends = []
    skipped = Counter()
    for seq in seqs:
        send_t = None
        for num, wt, val in cade._fields(seq):
            if num == 2 and wt == 0:
                send_t = val
            elif num == 1 and wt == 2:
                t = 0
                for fnum, fwt, fval in cade._fields(val):
                    if fnum == 1 and fwt == 0:
                        t = fval
                    elif fnum == 2 and fwt == 2:
                        patch_sizes[len(fval) // 1024] += 1
                    elif fnum == 4 and fwt == 2:
                        rev_sizes[len(fval)] += 1
                    elif fnum == 3 and fwt == 2:
                        events[_event_kind(fval)] += 1
                    elif fnum == 6 and fwt == 2:
                        cmds[_cmd_kind(fval)] += 1
                    elif fnum == 7 and fwt == 0:
                        skipped[fval] += 1
                times.append(t)
        if send_t is not None:
            sends.append(send_t)

    print(f"frames: {len(times)}; time head={times[:4]} tail={times[-4:]}")
    dts = [b - a for a, b in zip(times, times[1:])]
    neg = sum(1 for d in dts if d < 0)
    zero = sum(1 for d in dts if d == 0)
    print(f"time deltas: neg={neg} zero={zero} "
          f"min={min(dts)} max={max(dts)} mean={sum(dts) / len(dts):.1f} ms")
    print("patch KB histogram (top):",
          sorted(patch_sizes.items(), key=lambda kv: -kv[1])[:10])
    print("patch sizes: min", min(k for k in patch_sizes),
          "max", max(k for k in patch_sizes), "KB")
    print("reverse patch sizes:", dict(list(rev_sizes.items())[:5]),
          f"(total {sum(rev_sizes.values())})")
    print("timeStepsSkipped:", dict(skipped))
    print("sendingWorldTime head/tail:", sends[0], sends[-1])
    print("events:", dict(events.most_common(8)))
    print("commands:", dict(cmds.most_common(10)))


def _event_kind(ev: bytes) -> str:
    for num, wt, val in cade._fields(ev):
        # Event oneof: 1 EntityKilled .. 12 NotificationWonder
        kinds = {1: "EntityKilled", 2: "MarketTransaction", 3: "Tribute",
                 4: "PlayerChat", 5: "CombatNotificationSound", 6: "Damage",
                 7: "MonkConversion", 8: "NotificationTrainDone",
                 9: "NotificationResearchDone",
                 10: "NotificationFarmOrFishTrapDied",
                 11: "NotificationWonder"}
        return kinds.get(num, f"event{num}")
    return "empty"


def _cmd_kind(cmd: bytes) -> str:
    for num, wt, val in cade._fields(cmd):
        if num >= 1000:
            return f"cmd{num}"
        return f"field{num}"
    return "empty"


if __name__ == "__main__":
    main()
