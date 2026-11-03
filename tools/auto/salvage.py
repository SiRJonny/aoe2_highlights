"""Salvage pass: continue the currently-live paused replay session and
record the remaining highlight segment(s).

Flow: wake (F3 until ticks) -> seek target (pause+camera) -> F3 +
NORMAL -> record take. Uses record_events' shared machinery so clip
naming, audio capture and muxing are identical to the normal pass.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))

import drive
import game
import record_events as rec


def main():
    plan_path = ROOT / "out" / "events_plan_row2.json"
    seg_idx = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    all_segs = plan["segments"]
    seg = all_segs[seg_idx - 1]
    out_dir = ROOT / "out" / f"clips_row{plan['row']}"
    out_dir.mkdir(parents=True, exist_ok=True)
    name = (f"clip_row{plan['row']}_{seg_idx:02d}_{seg['kind']}"
            f"_t{int(seg['t0'])}.mp4")
    out_path = out_dir / name

    handle = drive.connect(30)
    if not rec.wait_first_tick(handle, 20):
        print("module not ticking (frozen?) -> wake", flush=True)
    if not rec.wake(handle):
        sys.exit("module never unfroze; aborting")
    t0p = seg["t0"] - plan.get("pad", 2.0)
    x, y = int(seg["x"]), int(seg["y"])
    print(f"seek to t={t0p} @ tile {x}:{y}", flush=True)
    drive.send(handle, {"action": "seek", "t": t0p, "x": x, "y": y,
                        "pause": True, "camera": True})
    res = rec.wait_seek_settled(handle, t0p, max_s=300,
                                progress=lambda t: print(
                                    f"  chase {t:.0f}/{t0p:.0f}",
                                    flush=True))
    if res is not True:
        sys.exit(f"seek did not settle ({res!r})")
    game.pyautogui.press("f3")
    drive.send(handle, {"action": "camera", "x": x, "y": y})
    drive.send(handle, {"action": "set_speed", "speedName": "NORMAL"})
    drive.read_for(handle, 2, quiet=True)
    rec.record_segment(handle, seg, plan.get("pad", 2.0), out_path, 30)
    print(f"done -> {out_path}", flush=True)


if __name__ == "__main__":
    main()
