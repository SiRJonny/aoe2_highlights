"""One-shot controlled experiment: does the module actually apply commands
after a fresh reload?

Prints tick rate after set_speed FASTEST (NORMAL ~1.7 game-s/s of real
time, FASTEST >> that) and reports seek ack/pause for a small target.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import drive
import game

REPLAY = Path.home() / "Games" / "Age of Empires 2 DE" / "76561198" \
    "014928708" / "savegame" / "hl2 replay copy.aoe2record"


def read_ticks(handle, seconds):
    rows = drive.read_for(handle, seconds, quiet=True)
    ts = [r.get("t") or r.get("time") for r in rows
          if r.get("type") == "tick"]
    return rows, [t for t in ts if t]


def main():
    os.utime(REPLAY)
    print("touched", REPLAY.name, flush=True)
    subprocess.run([sys.executable, str(HERE / "start_replay.py")])
    handle = drive.connect(120)
    rows, ts = read_ticks(handle, 20)
    print(f"first ticks: {len(ts)} rows={len(rows)} "
          f"last_t={max(ts) if ts else None}", flush=True)
    if not ts:
        game.save_debug("exp_no_ticks")
        sys.exit("module not streaming")
    t_before = max(ts)

    drive.send(handle, {"action": "set_speed", "speedName": "FASTEST"})
    time.sleep(1)
    rows, ts = read_ticks(handle, 8)
    ack = [r.get("action") for r in rows if r.get("action")]
    print(f"speed ack: {ack}", flush=True)

    time.sleep(5)                       # let the speed settle
    rows, ts = read_ticks(handle, 8)
    t_after = max(ts) if ts else None
    if t_after is not None:
        rate = (t_after - t_before) / (1.0 + 8 + 5 + 8)
        print(f"tick rate ~ {rate:.1f} game-s per real-s "
              f"(NORMAL ~1.7; FASTEST should be much higher)", flush=True)
    else:
        print("no ticks after set_speed", flush=True)
    game.save_debug("exp_fastest_hud")

    drive.send(handle, {"action": "seek", "t": 200, "x": 100, "y": 100,
                        "pause": True, "camera": True})
    end = time.time() + 240
    settled = None
    while time.time() < end:
        rows, ts = read_ticks(handle, 2)
        for r in rows:
            if r.get("action") == "seek_reached":
                print("seek_reached:", {k: r.get(k)
                                        for k in ("t", "x", "y", "paused")},
                      flush=True)
                settled = "ack"
                break
            if r.get("type") == "tick":
                t = r.get("t") or r.get("time") or 0
                if t >= 200:
                    settled = f"tick t={t:.0f} (no ack)"
                    break
        if settled:
            break
        print("  chase t=", max(ts) if ts else None, flush=True)
    if not settled:
        print("seek did not settle within 240 s", flush=True)
        game.save_debug("exp_seek_no_settle")
    else:
        print("SEEK SETTLED:", settled, flush=True)
        # verify it really paused: no tick advance for 10 s
        rows, ts = read_ticks(handle, 10)
        t_final = max(ts) if ts else None
        print("after-pause ticks last t:", t_final, flush=True)


if __name__ == "__main__":
    main()
