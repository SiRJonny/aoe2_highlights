"""Priority event selection (thin CLI over aoe2hl.planner).

    py -3.12 tools\\auto\\select_events.py <events.jsonl> [--out plan.json]
        [--row N]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from aoe2hl.planner import main  # noqa: E402

if __name__ == "__main__":
    main()
