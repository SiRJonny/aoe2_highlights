"""Automated replay start by driving the game UI (thin CLI over
aoe2hl.replays_ui).

    py -3.12 tools\\auto\\start_replay.py [--search <text>] [--row N]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from aoe2hl.replays_ui import main  # noqa: E402

if __name__ == "__main__":
    main()
