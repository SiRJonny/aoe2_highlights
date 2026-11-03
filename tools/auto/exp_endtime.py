"""Measure the true end of the loaded replay: fresh load, FASTEST, no
seek commands at all. Prints the last game time reached before the
module stops emitting (FINISHED REPLAYING GAME)."""
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import drive

REPLAY = Path.home() / "Games" / "Age of Empires 2 DE" / "76561198" \
    "014928708" / "savegame" / "hl2 replay copy.aoe2record"


def main():
    os.utime(REPLAY)
    print("touched", REPLAY.name, flush=True)
    subprocess.run([sys.executable, str(HERE / "start_replay.py")])
    handle = drive.connect(120)
    end = time.time() + 60
    t_max = 0.0
    while time.time() < end and t_max == 0.0:
        for r in drive.read_for(handle, 3, quiet=True):
            if r.get("type") == "tick":
                t = r.get("t") or r.get("time") or 0
                t_max = max(t_max, t)
    print("module streaming, first t =", t_max, flush=True)

    drive.send(handle, {"action": "set_speed", "speedName": "FASTEST"})
    drive.read_for(handle, 3, quiet=True)

    # watch until 60 s with no new t (or 20 min hard cap)
    quiet_at = time.time() + 60
    hard_cap = time.time() + 20 * 60
    t_report = 0.0
    while time.time() < hard_cap and time.time() < quiet_at:
        for r in drive.read_for(handle, 3, quiet=True):
            if r.get("type") == "tick":
                t = r.get("t") or r.get("time") or 0
                if t > t_max:
                    t_max = t
                    quiet_at = time.time() + 60
                    if t - t_report > 240:
                        t_report = t
                        print(f"  t={t:.0f}", flush=True)
    print(f"REPLAY REACHED t={t_max:.0f} "
          f"({t_max / 60:.1f} game minutes) then stopped", flush=True)


if __name__ == "__main__":
    main()
