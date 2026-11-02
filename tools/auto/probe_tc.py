"""One-shot TC-probe experiment: reinject module, replay, seek to a failed
town center, ask the module what sits at that tile.

The treaty replay labels TOWN_CENTER foundations as `lost` while they most
likely complete normally (GetObjectById on the foundation id returns nil).
This probe seeks to t=150 (just after p2's TC "lost" at t=141.4, tile
111:158, unitType 621), pauses there, and runs `probe`.
"""
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parents[1]

sys.path.insert(0, str(HERE))
import game  # noqa: E402
import drive  # noqa: E402  (tools/auto/drive.py)
import pywintypes  # noqa: E402

PY = sys.executable


def run(label, script, *a):
    print(f"== {label}")
    r = subprocess.run([PY, script, *a], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    print(r.stdout.strip() or r.stderr.strip())
    if r.returncode != 0:
        sys.exit(f"{label} failed ({r.returncode})")


def main():
    if "--inject" in sys.argv:
        print("== injecting CONTROL (module swap)")
        game.focus_window()
        game.pyautogui.keyDown("delete")
        game.pyautogui.keyUp("delete")
        time.sleep(4)
        import pipeline  # noqa: F401  (guarded main)
        pipeline.inject()
        time.sleep(4)

    # fresh replay with the probing module
    run("start replay", str(HERE / "start_replay.py"), "--row", "1")

    # let the match settle a moment, then seek (fast-forward + pause + cam)
    time.sleep(4)
    handle = drive.connect(120)
    drive.send(handle, {"action": "seek", "t": 150, "x": 111, "y": 158,
                        "pause": True, "camera": True})
    print("== seek sent (t=150, tile 111:158, pause)")
    deadline = time.time() + 240
    reached = False
    while time.time() < deadline and not reached:
        rows = drive.read_for(handle, 5)
        for r in rows:
            if r.get("action") == "seek_reached":
                reached = True
        if not reached and rows and rows[-1].get("time", 0) > 148:
            reached = True
    game.save_debug("probe_seek_state")
    print("== seek settled:", reached)

    # probe: what does the engine know about the TC foundation type (621)?
    drive.send(handle, {"action": "probe", "unitType": 621})
    got = False
    for _ in range(4):
        for r in drive.read_for(handle, 4):
            if r.get("action") == "probe":
                print("PROBE:", drive.json.dumps(r, indent=2))
                got = True
        if got:
            break
    if not got:
        print("no probe_result (paused game freezes module updates; "
              "unpause with F3 first)")
        game.pyautogui.press("f3")
        time.sleep(1.5)
        for r in drive.read_for(handle, 4):
            if r.get("action") == "probe":
                print("PROBE:", drive.json.dumps(r, indent=2))
                got = True
    game.save_debug("probe_final")


if __name__ == "__main__":
    main()
