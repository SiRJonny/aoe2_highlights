"""Per-row bootstrap: restart the game so a touched replay copy lands row 1.

The replay list is CACHED PER GAME BOOT: os.utime on a copy only reorders
the list when the game boots after the touch (mid-session touches are
ignored). For every row beyond the one that is already row 1 at boot:

 1. os.utime the row's replay copy (newest mtime -> row 1 in the UI list)
 2. kill the running AoE2 process (module/list state is useless anyway)
 3. relaunch AoE2 DE through Steam (-applaunch), wait for its window
 4. inject CONTROL with our settings + module (headless launcher waits
    for the game process itself)
 5. sanity: the CONTROL named pipe answers

Usage:
    py -3.12 tools\\auto\\bootstrap_row.py --row 3
Then run the collect/record as usual:
    py -3.12 tools\\auto\\record_events.py --row 3 --collect
"""
import argparse
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "aoe2control"))

import drive  # noqa: E402
import pipeline  # noqa: E402  (guarded main; inject() reused)

import win32gui  # noqa: E402

WINDOW_TITLE = "Age of Empires II: Definitive Edition"
GAME_EXE = "AoE2DE_s.exe"
STEAM_EXE = r"C:\Program Files (x86)\Steam\steam.exe"
STEAM_APPID = "813780"


def touch_target(row):
    """Same profile rule as record_events: pre-made hl<N> replay copy."""
    cand = Path.home() / "Games" / "Age of Empires 2 DE"
    for d in cand.glob("*/savegame"):
        hit = d / f"hl{row} replay copy.aoe2record"
        if hit.exists():
            import os
            os.utime(hit)
            print(f"touched {hit}", flush=True)
            return hit
    sys.exit(f"no 'hl{row} replay copy.aoe2record' found under {cand}")


def killed_before_boot(row_target):
    """True when a kill+relaunch is required for row_target to be row 1.

    We cannot read the cached list from outside, so the cheap proxy is:
    the file list is rescanned per boot, but mtime ordering means the
    NEWEST copy wins. If our target is already newer than every other
    hl copy, the running session's list already has it on top ... but the
    list was fixed at boot, so only a real scan order known at boot counts.
    Conservative: always reboot unless --noreboot."""
    return True


def kill_game():
    r = subprocess.run(["taskkill", "/IM", GAME_EXE, "/F"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(f"game not running ({r.returncode})", flush=True)
        return
    print("killed running game", flush=True)
    # BugSplat reporter may have been spawned by an earlier crash
    subprocess.run(["taskkill", "/IM", "BsSndRpt64.exe", "/F"],
                   capture_output=True, text=True)
    time.sleep(4)


def launch_game():
    subprocess.Popen([STEAM_EXE, "-applaunch", STEAM_APPID],
                     creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    print("steam -applaunch sent", flush=True)


def wait_window(timeout=300):
    """Wait for the game's top-level window, then let the engine settle."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if win32gui.FindWindow(None, WINDOW_TITLE):
            print(f"game window up ({time.time() - t0:.0f}s)", flush=True)
            time.sleep(12)          # engine intro -> main menu settle
            return True
        time.sleep(2)
    sys.exit(f"game window not seen within {timeout}s")


def inject_and_verify():
    # the launcher's own readiness check needs the main menu up; wait for
    # its anchor and then retry injection a few times anyway
    import game
    if game.wait_anchor("single_player_button", timeout=240):
        print("main menu ready", flush=True)
    for attempt in range(4):
        try:
            pipeline.inject()       # sys.exits on launcher failure; caught
        except SystemExit as e:
            print(f"launcher exit: {e}", flush=True)
        if pipeline.control_running():
            break
        print(f"inject attempt {attempt + 1} failed; retry in 20s",
              flush=True)
        time.sleep(20)
    else:
        sys.exit("CONTROL never injected")
    print("waiting for module pipe ...", flush=True)
    handle = drive.connect(180)
    rows = drive.read_for(handle, 5, quiet=True)
    print("pipe alive, first rows:", [r.get("type") for r in rows],
          flush=True)


t0 = time.time()

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--row", type=int, required=True)
    ap.add_argument("--noreboot", action="store_true",
                    help="touch + inject only (boot order already right)")
    args = ap.parse_args()

    touch_target(args.row)
    if not args.noreboot:
        kill_game()
        launch_game()
        wait_window()
    inject_and_verify()
    print(f"bootstrap done ({time.time() - t0:.0f}s): "
          f"run record_events.py --row {args.row} --collect", flush=True)
