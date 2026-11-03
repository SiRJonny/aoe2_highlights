"""Record a highlight clip from the running replay via the module seek.

Flow: inject (optional) -> start replay -> seek to t0 (pause + camera at
the event tile) -> optionally press Shift to close the CONTROL panel ->
unpause at normal speed -> record monitor frames to mp4 until game time
reaches t1 -> pause.

The game clock advances 1:1 at normal speed, so the clip covers game
seconds directly (long builds may take a while).

Usage:
    py -3.12 tools\\auto\\clip_recorder.py --t0 100 --t1 140 --x 109 --y 117
        [--out out\\clip_first_house.mp4] [--fps 30] [--no-gui]
        [--search term] [--row 1] [--force-inject]

Seconds are game time; window padding should cover the whole event
(first house: start t=107.4, done t=133.4 -> --t0 100 --t1 140 covers it).
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
OUT = HERE.parents[1] / "out"

sys.path.insert(0, str(HERE))
import cv2  # noqa: E402
import mss  # noqa: E402
import numpy as np  # noqa: E402

import drive  # noqa: E402
import game  # noqa: E402


def screen_size():
    with mss.mss() as sct:
        m = sct.monitors[1]
        return m["width"], m["height"]


def wait_seek_settled(handle, t0, max_s=300, progress=None, check_end=None):
    """Wait until the module acks the seek (or a tick passes t0).

    Returns True (settled via ack or tick >= t0), "end" (the replay-over
    screen appeared), "pipe" (stream went quiet >45 s mid-chase) or
    False (max_s elapsed). Tick rows carry the game time under "t"
    (older rows under "time"). Checking a tick passing t0 matters when
    the seek command was dropped: without it this waits out max_s
    while the replay tail plays and the game hits FINISHED REPLAYING
    GAME. At FASTEST the chase from a fresh load to a late t needs
    several minutes; max_s must exceed that."""
    deadline = time.time() + max_s
    seen_t = -1.0
    last_end_check = 0.0
    last_data = time.time()
    while time.time() < deadline:
        rows = drive.read_for(handle, 2, quiet=True)
        if rows:
            last_data = time.time()
        for r in rows:
            if r.get("action") == "seek_reached":
                return True
            if r.get("type") == "tick":
                t = r.get("t") or r.get("time") or 0
                if t >= t0:
                    return True
                if progress and t - seen_t > 120:
                    seen_t = t
                    progress(t)
        if check_end and time.time() - last_end_check > 20:
            last_end_check = time.time()
            if check_end():
                return "end"
        if time.time() - last_data > 45:
            return "pipe"
        time.sleep(0.2)
    return False


def record(handle, out_path, t1, fps, gui):
    """Record monitor 1 while the module streams ticks until game t1."""

    game.focus_window()
    time.sleep(0.5)
    if not gui:
        # close the CONTROL menu overlay (Shift)
        game.pyautogui.keyDown("shift")
        time.sleep(0.12)
        game.pyautogui.keyUp("shift")
        time.sleep(0.8)

    # normal replay speed (module set), then unpause via F3: a module
    # command cannot unpause because the module only runs while unpaused
    drive.send(handle, {"action": "set_speed", "v": None})
    drive.read_for(handle, 1, quiet=True)
    game.pyautogui.press("f3")
    time.sleep(1)

    t0_time = None
    for r in drive.read_for(handle, 2):
        if r.get("type") == "tick":
            t0_time = r.get("t")
            break
    print(f"recording from game t={t0_time} to {t1}: {out_path}")

    w, h = screen_size()
    writer = cv2.VideoWriter(str(out_path), cv2.VideoWriter_fourcc(
        *"mp4v"), fps, (w, h))
    if not writer.isOpened():
        sys.exit("VideoWriter failed to open")

    game.focus_window()
    time.sleep(0.4)
    frames = 0
    t_cur = t0_time or 0
    last_tick_at = time.time()
    unpause_retried = False
    frame_interval = 1.0 / fps
    next_frame = time.time()
    with mss.mss() as sct:
        mon = sct.monitors[1]
        while t_cur < t1:
            for r in drive.read_now(handle):
                if r.get("type") == "tick" and r.get("t"):
                    if r["t"] != t_cur:
                        last_tick_at = time.time()
                    t_cur = r["t"]
            if time.time() - last_tick_at > 8:
                if not unpause_retried:
                    # module never started steaming: game is still paused
                    game.focus_window()
                    game.pyautogui.press("f3")
                    unpause_retried = True
                    time.sleep(1)
                    last_tick_at = time.time()
                else:
                    print(f"STALL: no ticks for 16 s, aborting at t={t_cur}"
                          f" ({frames} frames recorded)")
                    break
            next_frame += frame_interval
            img = np.array(sct.grab(mon))
            writer.write(cv2.cvtColor(img, cv2.COLOR_BGRA2BGR))
            frames += 1
            sleep = next_frame - time.time()
            if sleep > 0:
                time.sleep(sleep)
    writer.release()
    # leave the replay paused behind us
    game.focus_window()
    time.sleep(0.3)
    game.pyautogui.press("f3")
    print(f"recorded frames={frames}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--t0", type=float, required=True)
    ap.add_argument("--t1", type=float, required=True)
    ap.add_argument("--x", type=float, default=None)
    ap.add_argument("--y", type=float, default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--fps", type=float, default=30)
    ap.add_argument("--no-gui", action="store_true",
                    help="close the CONTROL panel before recording")
    ap.add_argument("--search", default=None)
    ap.add_argument("--row", type=int, default=1)
    ap.add_argument("--force-inject", action="store_true")
    ap.add_argument("--resume", action="store_true",
                    help="reuse a running replay session (skip UI start)")
    args = ap.parse_args()

    OUT.mkdir(exist_ok=True)
    name = args.out or str(OUT / (
        f"clip_house_{int(args.t0)}_{int(args.t1)}"
        + ("_nogui.mp4" if args.no_gui else "_gui.mp4")))

    if args.force_inject:
        import pipeline  # noqa: F401  (guarded main)
        pipeline.inject()

    if not args.resume:
        print("starting replay ...")
        ui_cmd = [sys.executable, str(HERE / "start_replay.py")]
        if args.search:
            ui_cmd += ["--search", args.search]
        ui_cmd += ["--row", str(args.row)]
        ui = subprocess.run(ui_cmd, capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
        print(ui.stdout.strip())
        if ui.returncode != 0:
            sys.exit("UI flow failed (see out/auto_debug)")

    time.sleep(4)
    handle = drive.connect(120)
    drive.send(handle, {"action": "seek", "t": args.t0, "x": args.x,
                        "y": args.y, "pause": True, "camera": True})
    # a running module answers rows; a paused/static one answers nothing at
    # all -- on --resume we trust the earlier settle (rows stopped flowing)
    settled = True
    if not args.resume:
        for _ in range(3):
            if drive.read_now(handle):
                settled = None
                break
            time.sleep(0.5)
        if settled is None:
            settled = wait_seek_settled(handle, args.t0)
    if not settled:
        sys.exit("module did not settle at t0 (and no module output seen)")
    record(handle, name, args.t1, args.fps, not args.no_gui)


if __name__ == "__main__":
    main()
