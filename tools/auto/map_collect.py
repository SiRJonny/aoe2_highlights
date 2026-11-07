"""Map-animation data collector: fastest runthrough with object snapshots.

Same skeleton as the highlight collect pass (touch -> UI start -> pipe ->
FASTEST until the replay ends), but instead of the event stream it asks
the module for map_mode and stores the per-second full-object snapshots
plus a terrain PNG rendered from the replay header:

    out/<tag>/positions.jsonl   meta row + objects rows (one per game-s)
    out/<tag>/map.png           minimap-style terrain image

The game must be running (bootstrap_row.py reboots + injects if needed).

Usage:
    py -3.12 tools\\auto\\map_collect.py --tag demo --touch "X.aoe2record"
        [--search TEXT] [--force-inject] [--max-minutes 40]
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from aoe2hl import capture, control, gameui, paths, terrain  # noqa: E402
from aoe2hl import pipe as drive  # noqa: E402
from aoe2hl import replays_ui  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True,
                    help="replay tag: out/<tag>/positions.jsonl + map.png")
    ap.add_argument("--touch", default=None,
                    help="replay file to touch (newest mtime -> list row 1);"
                         " also the terrain-image source")
    ap.add_argument("--search", default=None)
    ap.add_argument("--row", type=int, default=1)
    ap.add_argument("--force-inject", action="store_true",
                    help="unload + re-inject CONTROL first")
    ap.add_argument("--max-minutes", type=float, default=40)
    args = ap.parse_args()

    if args.force_inject:
        log = capture.log
        log("swapping CONTROL module ...")
        for attempt in range(3):
            if control.unload_module():
                break
            log(f"module still running after unload (attempt {attempt})")
        control.inject()
        time.sleep(4)
    if not control.control_running():
        control.inject()

    if args.touch:
        os.utime(args.touch)        # newest mtime -> row 1 in the UI
        capture.log(f"touched {args.touch}")
    search = args.search
    if not search and args.touch:
        # the DE replay search only matches the part of the name after
        # the @ sign ("MP Replay v101.103.57257.0 @2026.11.01 200634 (2)"
        # -> "2026.11.01 200634 (2)"), so search for exactly that
        stem = Path(args.touch).stem
        if "@" in stem:
            search = stem.split("@", 1)[1].strip()
            capture.log(f"search '{search}' (from @ in the file name)")
    if not replays_ui.run(args.row, search):
        capture.log("WARNING: UI start reported failure; still waiting "
                    "for the module pipe")
    time.sleep(4)
    handle = drive.connect(120)
    if not capture.wait_first_tick(handle, 150):
        capture.log("WARNING: module never streamed ticks; pipe may be dead")

    # objects rows on. NO pre-read: the module answers map_mode by
    # re-sending meta + tc_snap, and those rows are DATA (the acks are
    # skipped inside the collector by type).
    drive.send(handle, {"action": "map_mode", "on": True})

    tag_dir = paths.tag_dir(args.tag)
    pos_path = tag_dir / "positions.jsonl"
    last_t = capture.collect_positions(handle, pos_path, args.max_minutes)
    capture.log(f"positions -> {pos_path} (last t={last_t})")

    replay_path = args.touch
    if replay_path and Path(replay_path).exists():
        png, name = terrain.render_replay_png(replay_path,
                                              tag_dir / "map.png")
        names = terrain.player_info(replay_path)
        with open(pos_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"type": "map", "name": name}) + "\n")
            if names:
                fh.write(json.dumps({"type": "names", "names": names}) + "\n")
        capture.log(f"terrain -> {png} (map: {name}, "
                    f"{sum(1 for _ in names)} named players)")
    else:
        capture.log("WARNING: no replay file -- terrain image skipped "
                    "(webapp falls back to flat background)")

    # stop the replay once the data is in: leave the end screen (or quit
    # a still-running replay) and park the game at the replay list
    if replays_ui.wind_down():
        capture.log("parked at replay list")


if __name__ == "__main__":
    main()
