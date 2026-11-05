"""Measure the actual replay-speed ladder (experiment).

The top-bar label lies: during castle_build takes it read "Normal - 1.7"
while the game advanced ~6.6x. This probe reloads the touched replay,
then measures the real game-time advance rate for every ReplaySpeed
preset and every Game Speed Multiplier, so the planner can pick a true
"middle speedup" for castle_build.

Usage:
    py -3.12 tools\\auto\\speed_probe.py
"""
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import drive  # noqa: E402
import game  # noqa: E402
import record_events as rem  # noqa: E402


def rate(handle, seconds=8.0):
    """Game-s per wall-s from sample-row timestamps (excluding any jump
    bigger than 5 game-s between consecutive rows - seek artifacts)."""
    drive.read_now(handle)
    t0w = time.time()
    last_g = None
    pts = []
    end = time.time() + seconds
    while time.time() < end:
        for r in drive.read_now(handle):
            g = r.get("time")
            if g:
                g = float(g)
                if last_g is not None and 0 < g - last_g < 5.0:
                    pts.append((time.time(), g))
                last_g = g
    if len(pts) < 2:
        return None
    wall = pts[-1][0] - pts[0][0]
    game = pts[-1][1] - pts[0][1]
    return game / wall if wall > 1.0 else None


def main():
    game.focus_window()
    game.pyautogui.keyDown("delete")
    game.pyautogui.keyUp("delete")
    time.sleep(4)
    import pipeline  # noqa: F401  (guarded main)
    pipeline.inject()
    time.sleep(4)
    if not rem.start_replay(1, None):
        rem.log("WARNING: UI start reported failure")
    time.sleep(4)
    handle = drive.connect(120)
    if not rem.wait_first_tick(handle, 150):
        game.save_debug("speed_probe_no_tick")
        sys.exit("module never streamed ticks")
    time.sleep(2.0)                 # let the post-load catch-up settle

    trials = [
        ("SLOW", None),
        ("NORMAL", None),
        ("FAST", None),
        ("FASTEST", None),
        ("NORMAL", "Default"),
        ("NORMAL", 1.5),
        ("NORMAL", 2),
        ("NORMAL", 5),
        ("FAST", 2),
        ("NORMAL", None),
    ]
    for speed, mult in trials:
        drive.send(handle, {"action": "set_speed", "speedName": speed})
        time.sleep(0.6)
        if mult is not None:
            drive.send(handle, {"action": "set_game_speed", "v": mult})
            time.sleep(0.6)
        r = rate(handle)
        label = speed if mult is None else f"{speed} + mult {mult}"
        # read the ack for the multiplier call (errors surface there)
        rows = drive.read_now(handle)
        ack = [r2 for r2 in rows if r2.get("action") == "set_game_speed"]
        extra = ""
        if ack:
            extra = f" ack ok={ack[0].get('ok')} err={ack[0].get('err')}"
        print(f"{label:22s} -> {r if r is None else round(r, 2)} "
              f"game-s/wall-s{extra}", flush=True)
        if mult is not None:
            drive.send(handle, {"action": "set_game_speed",
                                "v": "Default"})
            time.sleep(0.4)


if __name__ == "__main__":
    main()