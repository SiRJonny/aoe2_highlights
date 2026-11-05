"""Out-folder layout: final videos at the root, one folder per replay.

out/
  highlights_<tag>.mp4      final concatenated video (the ONLY files at
                            the out root)
  <tag>/
    events.jsonl            collected module rows + annotations
    plan.json               planner output for this replay
    clips/                  recorded takes clip_<tag>_NN_<kind>_t<sec>.mp4
    clips/grid_parts/       per-player takes later composited into a grid
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "out"


def tag_dir(tag):
    d = OUT / tag
    d.mkdir(parents=True, exist_ok=True)
    return d


def events_path(tag):
    return tag_dir(tag) / "events.jsonl"


def plan_path(tag):
    return tag_dir(tag) / "plan.json"


def clips_dir(tag):
    d = tag_dir(tag) / "clips"
    d.mkdir(parents=True, exist_ok=True)
    return d


def grid_parts_dir(tag):
    return clips_dir(tag) / "grid_parts"


def final_path(tag):
    return OUT / f"highlights_{tag}.mp4"


def tags():
    """All replay tags that have intermediates on disk."""
    return sorted(d.name for d in OUT.iterdir()
                  if d.is_dir() and (d / "clips").is_dir())
