"""Two-pass highlight recorder (thin CLI over aoe2hl.recorder).

Architecture: run the full replay once at FASTEST speed to collect the
timeline (module rows -> events jsonl -> plan json), then restart the
replay and record each planned segment at NORMAL speed. Video = monitor
1 via mss (1920x1080 @30fps, mp4v), audio = WASAPI loopback (system
output, no mic), muxed with the imageio-ffmpeg binary.

Pass 1 stops when game time stalls 20 s (replay ended) or --max-minutes.
Pass 2: segments in time order (never seeking backwards) — close
segments film the gap as a camera PAN, far gaps seek at FASTEST with
the game left running. The nomad first_tc_grid segment films one take
PER PLAYER (fresh paused reload each) at FAST with stationary cameras
on the TC sites.

Usage:
    py -3.12 tools\\auto\\record_events.py --tag mp5 --touch <replay>
        [--collect] [--record]         (default: both passes)
        [--segs 1,2] [--fps 30] [--max-minutes 40]
        [--events PATH] [--plan PATH] [--out-dir PATH]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from aoe2hl.recorder import main  # noqa: E402

if __name__ == "__main__":
    main()
