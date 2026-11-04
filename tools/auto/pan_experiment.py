"""Camera-pan smoothness experiment.

Reloads the touched replay, re-injects the module (the running one may
predate the `pan` action), then films two pans of 15 tiles each:

  phase A (0-4 s):  Python-side pipe sends at 30 Hz — the current method.
                    Camera commands apparently batch in the module IPC
                    pump (only the last of a batch renders), so this
                    should show ~4-5 visible steps.
  phase B (4-14 s): ONE `pan` command — the module interpolates
                    SetCameraPosition on EVERY engine Update, which
                    should glide.

Writes out/pan_test.mp4 and prints a per-frame motion profile per phase
(uniform low diffs = smooth glide; bursts separated by zeros = steps).
Watch the clip to verify which half glides.

Usage:
    py -3.12 tools\\auto\\pan_experiment.py
"""
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import cv2  # noqa: E402
import numpy as np  # noqa: E402

import drive  # noqa: E402
import game  # noqa: E402
import record_events as rem  # noqa: E402

A = (80.0, 80.0)                 # pan start tile
B = (95.0, 80.0)                 # pan end tile (15 tiles east)
DUR_A = 4.0                      # s, phase A (python sends)
DUR_B = 10.0                     # s, phase B (module tween)
OUT = rem.ROOT / "out" / "pan_test.mp4"


def main():
    # fresh module: the `pan` action must exist engine-side
    game.focus_window()
    game.pyautogui.keyDown("delete")
    game.pyautogui.keyUp("delete")
    time.sleep(4)
    import pipeline  # noqa: F401  (guarded main)
    pipeline.inject()
    time.sleep(4)

    if not rem.start_replay(1, None):
        rem.log("WARNING: UI start reported failure; continuing")
    time.sleep(4)
    handle = drive.connect(120)
    if not rem.wait_first_tick(handle, 150):
        game.save_debug("pan_exp_no_tick")
        sys.exit("module never streamed ticks")
    rem.close_control_panel()
    rem.fog_off()
    drive.send(handle, {"action": "set_speed", "speedName": "NORMAL"})
    drive.read_now(handle)
    game.focus_window()
    time.sleep(0.5)
    drive.send(handle, {"action": "camera", "x": A[0], "y": A[1]})
    drive.read_now(handle)
    time.sleep(2.0)                  # rest on the start tile

    writer = cv2.VideoWriter(str(OUT), cv2.VideoWriter_fourcc(*"mp4v"),
                             30, (1920, 1080))
    if not writer.isOpened():
        sys.exit("VideoWriter failed to open")
    t0 = time.time()
    last_send = 0.0
    phase_b_sent = False
    with __import__("mss").mss() as sct:
        mon = sct.monitors[1]
        next_frame = time.time()
        while time.time() - t0 < DUR_A + DUR_B:
            ph_t = time.time() - t0
            if ph_t < DUR_A:
                # phase A: python-side interpolation at 30 Hz
                if time.time() - last_send >= 0.033:
                    f = ph_t / DUR_A
                    drive.send(handle, {"action": "camera",
                                        "x": round(A[0] + (B[0] - A[0]) * f, 3),
                                        "y": round(A[1] + (B[1] - A[1]) * f, 3)})
                    last_send = time.time()
            elif not phase_b_sent:
                # phase B: ONE command; the module tweens every Update
                drive.send(handle, {"action": "pan", "x0": A[0], "y0": A[1],
                                    "x1": B[0], "y1": B[1],
                                    "seconds": DUR_B})
                drive.read_now(handle)
                rem.log("phase B: module pan command sent")
                phase_b_sent = True
            drive.read_now(handle)   # drain acks so the pipe stays clear
            img = np.array(sct.grab(mon))
            writer.write(cv2.cvtColor(img, cv2.COLOR_BGRA2BGR))
            next_frame += 1 / 30
            if next_frame < time.time():
                next_frame = time.time()
            sleep = next_frame - time.time()
            if sleep > 0:
                time.sleep(sleep)
    writer.release()

    cap = cv2.VideoCapture(str(OUT))
    diffs = []
    prev = None
    while True:
        ok, img = cap.read()
        if not ok:
            break
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        if prev is not None:
            diffs.append(float(np.mean(cv2.absdiff(prev, g))))
        prev = g
    cap.release()
    na, nb = int(DUR_A * 30), int(DUR_B * 30)
    for name, seg in (("A py-30Hz", diffs[:na]),
                      ("B module", diffs[na:na + nb])):
        if not seg:
            continue
        moving = sum(1 for d in seg if d > 2.0)
        # zero-runs between movement = discrete steps
        runs = 0
        in_run = False
        for d in seg:
            if d <= 1.0 and not in_run:
                runs += 1
                in_run = True
            elif d > 2.0:
                in_run = False
        print(f"phase {name}: frames={len(seg)} moving={moving} "
              f"mean={sum(seg) / len(seg):.2f} zero-runs={runs}")
    print(f"clip -> {OUT}  (A = python sends 0-4 s, B = module pan 4-14 s)")


if __name__ == "__main__":
    main()