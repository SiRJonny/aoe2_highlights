"""aoe2hl: reusable AoE2 DE replay-capture toolkit.

Modules:
  paths       out-folder layout: final videos at the out root, one
              folder per replay for all intermediates
  gameui      window focus, anchor template matching, synthetic clicks
  pipe        named-pipe client for the injected timeline_recorder
              Lua module (send actions, read row streams)
  replays_ui  drive the game UI from any screen into a loaded replay
  control     headless AoE2Control injection (hosts the Lua module)
  capture     timeline streaming (collect), screen+audio takes
              (record_segment), real-time muxing (mux)
  planner     priority event selection -> segment plan (plan.json)
  annotators  replay-file annotations (resigns, teams, map, attack
              orders) appended to the event stream
  recorder    the two-pass highlight recorder orchestration

The tools/auto CLIs are thin wrappers over these modules. A spinoff app
(e.g. an artificial map animation) imports the same primitives instead
of shelling out:

    import sys; sys.path.insert(0, "<repo>/tools/auto")
    from aoe2hl import capture, paths, pipe, replays_ui
"""
import sys
from pathlib import Path

_AUTO = str(Path(__file__).resolve().parent.parent)
if _AUTO not in sys.path:
    sys.path.insert(0, _AUTO)
