"""Composite the per-player first-TC grid takes into one grid video.

Layout (user spec):
  4 players  -> 2x2 grid, one player per cell (960x540 each)
  6 players  -> 3x3 grid, top 6 cells = players, bottom-middle = the
                game minimap enlarged, bottom corners black
  8 players  -> 3x3 grid, 8 player cells, last (bottom-right) cell =
                the game minimap enlarged

Each input take covers the SAME game-time span (the record flow reloads
the replay per player), so the cells are time-synced. The minimap cell
is a crop of the game minimap from the first take. Cells keep their HUD
(user choice).

Usage:
    py -3.12 tools\\auto\\make_grid.py --players 8 --out out\\grid.mp4 ^
        out\\clips_mp5\\clip_mp5_01_first_tc_grid_p0_t0.mp4 [...]
"""
import argparse
import subprocess
import sys
from pathlib import Path

import imageio_ffmpeg


def probe_duration(ff, path):
    pr = subprocess.run([ff, "-i", str(path)], capture_output=True,
                        text=True)
    for l in pr.stderr.splitlines():
        if "Duration:" in l:
            import re
            m = re.search(r"(\d+):(\d+):([\d.]+)", l)
            return (int(m.group(1)) * 3600 + int(m.group(2)) * 60
                    + float(m.group(3)))
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--players", type=int, required=True,
                    choices=(4, 6, 8))
    ap.add_argument("--out", required=True)
    ap.add_argument("--minimap-from", type=int, default=0,
                    help="index of the input take whose minimap feeds "
                         "the minimap cell (default: first)")
    ap.add_argument("inputs", nargs="+")
    args = ap.parse_args()
    if len(args.inputs) != args.players:
        sys.exit(f"need exactly {args.players} takes, "
                 f"got {len(args.inputs)}")
    ff = imageio_ffmpeg.get_ffmpeg_exe()

    if args.players == 4:
        cell = (960, 540)
        rows, cols = 2, 2
    else:
        cell = (640, 360)
        rows, cols = 3, 3

    durations = [probe_duration(ff, p) for p in args.inputs]
    if any(d is None for d in durations):
        sys.exit("could not probe all inputs")
    dur = min(durations)          # grid is time-synced; cut at shortest

    cmd = [ff, "-y"]
    for p in args.inputs:
        cmd += ["-i", str(p)]
    n = len(args.inputs)
    mm_from = args.minimap_from
    need_black = (args.players == 6)
    if need_black:
        cmd += ["-f", "lavfi", "-i",
                f"color=c=black:s={cell[0]}x{cell[1]}:d={dur:.3f}"]

    # per-cell scaling; labels appear in xstack input order = row-major
    # layout order (xstack binds inputs by label order)
    fc = []
    if args.players == 4:
        order = ["[c0]", "[c1]", "[c2]", "[c3]"]
    elif args.players == 6:
        order = ["[c0]", "[c1]", "[c2]",
                 "[c3]", "[c4]", "[c5]", "[bl0]", "[mm]", "[bl1]"]
    else:
        order = ["[c0]", "[c1]", "[c2]",
                 "[c3]", "[c4]", "[c5]",
                 "[c6]", "[c7]", "[mm]"]
    for i in range(n):
        fc.append(f"[{i}:v]fps=30,scale={cell[0]}:{cell[1]}[c{i}]")
    # minimap: crop the game minimap (bottom-right) from the chosen take
    # and enlarge it into the cell (4p layout has no minimap cell)
    if args.players != 4:
        fc.append(f"[{mm_from}:v]fps=30,crop=420:200:1498:878,"
                  f"scale={cell[0]}:{cell[1]}[mm]")
    if need_black:
        fc.append(f"[{black_idx}:v]fps=30,trim=duration={dur:.3f},"
                  f"setpts=PTS-STARTPTS,split=2[bl0][bl1]")
    layout = []
    x, y = 0, 0
    h0, w0 = cell[1], cell[0]
    for pos in range(len(order)):
        if pos and pos % cols == 0:
            x = 0
            y += h0
        elif pos:
            x += w0
        layout.append(f"{x}_{y}")
    fc.append("".join(order))
    fc.append(f"xstack=inputs={len(order)}:layout="
              + "|".join(layout)
              + ":fill=black[v]")
    full = ";".join(fc)
    cmd += ["-filter_complex", full, "-map", "[v]",
            "-t", f"{dur:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            str(args.out)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr[-1500:])
        sys.exit("grid encode failed")
    print(f"grid -> {args.out} ({args.players} players, {dur:.1f} s)")


if __name__ == "__main__":
    main()