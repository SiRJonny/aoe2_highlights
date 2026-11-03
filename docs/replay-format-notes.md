# AoE2 DE Replay Format — Investigation Notes

Short summary of what an `.aoe2record` file contains, what can be parsed without
running the game, and what cannot. Verified against the two test files in
`replays/` (DE build 101.103.x, save version 68.0).

## How the file works

An `.aoe2record` file is **not a video**. It is:

1. **Header** — a zlib compressed snapshot of the *initial* game state:
   terrain (full map), starting objects (units, buildings, positions), players
   (names, civs, teams, color, rating), lobby settings, map id/seed,
   viewport of the recording player.
2. **Body** — a deterministic command stream (~ match length). Ops:
   `SYNC` (per-frame: checksum + per-player object/resource counters ~1/s),
   `VIEWLOCK` (recorder's camera position), `ACTION`, `CHAT`, `POSTGAME`.

The game replays by loading the header and applying each command with the same
engine rules. Everything is deterministic; bodies from this family survived
15+ years of parser compatibility (AoK `.mgl` → AoC `.mgx` → `.aoe2record`),
only the header changes per DE patch.

Sources (do not copy specs locally, link instead):

- Parser + schema roadmap: https://github.com/happyleavesaoc/aoc-mgz
- Body action wire format (AoC base, still DE-relevant): https://github.com/stefan-kolb/aoc-mgx-format
- Legacy AoC format doc: https://web.archive.org/web/20090215065209/http://members.at.infoseek.co.jp/aocai/mgx_format.html
- DE/Genie data & engine internals: https://github.com/SiegeEngineers/genie-rs and the openage docs
- Community tool catalog: https://github.com/ustacode/aoe2-apis

## Extractable without running the game

Confirmed working on both test replays with the `mgz` Python parser
(`pip install mgz`, fast parser, `tools/probe_replay.py` / `probe_actions.py`).

**Header (start of game, one snapshot):**
- Map: id, size, seed, full terrain tile grid (enough to render a minimap image)
- Players: name, civ, color, team, profile id/rating (DE metadata), team positions
- Lobby: game type, difficulty, resources, rules, starting age, victory type
- All initial objects with type + position (units, buildings, TCs, gaia)

**Body (events, per operation with in-game timestamp `sequence`):**
- Age advances: `RESEARCH` of age techs (mgz computes `uptimes`)
- Buildings: `BUILD` (placements with x/y/z), `WALL`, `DELETE` (walls/farms too)
- Production: `DE_QUEUE` / `MULTIQUEUE` (unit_id + producer object_ids) →
  villager/military build orders (bug: earlier releases dropped payloads, PR #146)
- Completions/transforms: `DE_TRANSFORM`, `SPECIAL`
- Military orders: `ORDER`, `MOVE`, `PATROL`, `DE_ATTACK_MOVE`, `STANCE`,
  `FORMATION` (positions + object_ids → can cluster army movements)
- Economy/transactions: `SELL`/`BUY`, `DE_TRIBUTE`, gather points
- Player actions: `RESIGN`, `DELETE` (own buildings), `STOP`, town bell,
  autoscout, flare, garrison/ungarrison
- Camera: `VIEWLOCK` — recorder's viewport position over time (= recorder
  minimap POV)
- `CHAT` — JSON with channel (all/teams), message, platform icon markup
- Frame stats: `SYNC` per player: obj_count, dp_obj_count, dp_obj_ttl,
  total_res (~1 cadence) — decent out-of-game proxy for score/activity curves
- `POSTGAME` at end: final ratings/ranks (leaderboards), victory, modes

**Derived, no simulation needed:** opening style, TC/castle placement
(`BUILD`/`DE_TRANSFORM` to castle ids), age-up timeline, attack-move clusters
(approximate "battle" hotspots), resign order, chat mining, recorder attention
(from VIEWLOCK).

## NOT extractable without running the game

- **Unit deaths / kill attribution** — combat damage is simulated by the engine,
  never recorded per tick. `SYNC` counters lack kill deltas; a battle must be
  recreated from movement clusters + disappearance inference, or end-guessed
  from `POSTGAME` totals only (units_killed/lost per player, no timing).
- Actual state at time T: unit positions, HP, population, **resources, idle
  TCs, army sizes, field of view of each player**. Requires replaying
  deterministically inside the real engine or a compatible recreation
  (openage-grade effort).
- **Graphics, sound, animations** — no asset data in the replay. Visual
  reproduction requires the game client (or full sprite atlas extraction +
  renderer).
- Any player's view except the recorder's.
- Terrain/object changes mid-game (e.g. trees cut down, converted units):
  implied by rules + commands, but only recoverable through engine simulation.

## Extraction tier 2: run the game (AoE2Control) — verified working

While the game runs (replay/spectate included), **AoE2Control** (https://github.com/aoe2control/AoE2Control,
v1.1.5, verified against game build 101.103.48987.0) injects a Lua engine into
the DE process. Modules read real game state (fog-of-war can be disabled with
the **Modules See Everything** setting) and send JSON through Windows named
pipes (`IPC.*`) to a Python client. This turns every state we could not get
from the file alone into a stream:

- villager population per player (verified: `tools/aoe2control/vill_pop_recorder`,
  1 sample/s, JSONL → CSV + SVG via `villpop_to_csv.py`)
- full object snapshots: `GetObjectsPtr()` (id/type/position/owner/alive) →
  unit positions, army movements, deaths — the battle and castle-fall timelines
- `GetObjectChanges()` → created/destroyed/damaged per second (kill timing)
- chat, ratings, tiles, placement queries, paths

Setup record (order matters, everything is scriptable with `--headless`):

1. Run the pipe client `pipe_client.py` (waits for the module pipe `AoE2_Highlights`).
2. `AoE2Control.exe --headless --override-settings tools/aoe2control/settings.villpop.ini
   --override-module tools/aoe2control/vill_pop_recorder` → status `Ready`.
3. Start the replay — the module auto-starts at match start (`SyncEnabled=true`).

### Automated capture (validated end-to-end)

One command from any postgame/menu screen:

    py -3.12 tools/auto/pipeline.py --row 1 [--search <text>]

Pipeline: skip inject if CONTROL running → start `pipe_client.py` (background,
writes `out/vill_pop.jsonl`) → `start_replay.py` drives the UI → wait for the
`done` row → convert to timestamped CSV + SVG
(`villpop_<date>_<time>.csv/svg`). Validated run: **348 samples, t=0.4–347.4 s
(full replay, auto-attached at match start), 8 players.**

UI automation facts (all in `tools/auto/game.py` + `start_replay.py`):

- Several game buttons ignore instantaneous synthetic clicks; a raw
  SendInput click with a ~0.12 s hold (`game.press_click`) works everywhere
  (also used for tab switches). Plain pyautogui clicks get swallowed.
- `SetForegroundWindow` from a background process needs an ALT-key unlock
  first; NEVER `SW_RESTORE` the game window (it un-maximizes it).
- After Load Game, the savegame list loads slowly; the `-AUTOSAVE-` row badge
  only appears once loaded and is the reliable gate (the spinner is not).
- The Replays tab header is the only reliable "we are on the Replays tab"
  check; the search bar exists on both tabs.
- Postgame resume flows: statistics page → **Return to Replays** button goes
  straight back to the replay list; "FINISHED REPLAYING GAME." screen →
  **Leave Map**. Anchor-based branching on the buttons works for both.
- Anchor templates are 1920×1080 crops; the game must be maximized/fullscreen.

Module lifecycle facts:

- CONTROL exits/loses attach when the game closes a session if idle too
  long — re-inject (`AoE2Control.exe --headless ...`) and reload after
  pressing the unload key (`Delete`) in game. Injection while on a menu is
  reliable; the engine logs `Ready` via stdout.
- `GetGameTime()` behaviour differs between menu and match; the module must
  reset its sampling schedule when time goes backwards (match (re)load),
  otherwise it strands at a huge `nextSampleTime` and silently stops
  sampling (the `tick` debug rows in `vill_pop_recorder` make this visible).
- The module's `End()` writes a `done` row with the final game time; the
  postgame summary's "Time" equals the module's max `time`, so module time is
  true replay time (the on-screen top bar can differ — cross-check with SYNC
  ticks when mapping highlights).

Gotchas learned (cost us a full debug cycle):

- Module entry file MUST be named `<module>.main.lua` inside a same-named
  folder; anything else is silently ignored (0 updates, no pipe).
- CONTROL keeps settings in memory and rewrites `settings.ini` on start;
  disk edits are lost unless passed via `--override-settings` at launch.
- To apply config changes: unload (in-game menu, `Delete`) → relaunch →
  replay. Attach-time determines timeline coverage; replay start clients
  sample from the beginning only if attached before match start.
- Data flow proven across the whole session: 456 samples, 335–790 s,
  8 players, written live to `out/vill_pop.jsonl`.

## Compatibility notes (current build)

- Header broken in released `mgz` 1.8.51 for save versions 67.2/68.0 (build
  101.103.x): two extra words before the AI count + an extra per-player
  string. Fix available, unmerged: https://github.com/happyleavesaoc/aoc-mgz/pull/147
  (saved as `tools/mgz-67.2-header.patch`, applied locally to verify.
  Header + body now parse both test files to EOF; full construct header
  (`mgz.summary`/`model`) still fails — `initial` section needs more work).
- Body action schema is stable through this patch — no body changes needed.
- DE_QUEUE payloads need PR #146 fix to be visible.
- Format bends per game update — track `mgz` upstream tests
  (`tests/recs/de-*`) rather than duplicating specs.

## Incremental plan toward auto-highlights

## Highlight pipeline (two-pass, 2026-11)

Architecture: **run full replay once at FASTEST to collect the timeline ->
event jsonl -> select main events -> record the events at NORMAL speed.**

Per replay row (`N` = replay list row):

1. `py -3.12 tools\auto\record_events.py --row N --force-inject`
   - swap module in (Delete key + headless injector)
   - pass 1 `collect`: whole replay at `FASTEST`; module rows (minus stats)
     to `out\events_rowN.jsonl`; ends when game time stalls 20 s (replay end)
   - `select_events.py` builds `out\events_plan_rowN.json`:
     earliest *finished* castle -> full-build clip (start..done + pad);
     top-2 10 s windows of villager deaths in one 12-tile location cell
   - pass 2 `record`: UI-restarts the replay, then per segment in time
     order (never seek backwards): F3 -> seek (pause+camera) -> F3 +
     `set_speed NORMAL` -> monitor-1 mss video (30 fps mp4v) + WASAPI
     loopback audio (no mic) -> stop on tick >= t1+pad -> F3 pause;
     ffmpeg mux (imageio-ffmpeg binary) -> `out\clips_rowN\clip_...mp4`

Module rows used: `bld_event` (`isCastle` now), `vill_death` (1 Hz
villager-id diff; death at last known x/y, +-1 s / +-1 tile), `tick`.
`vill_death` also fires for fishing ships and never for spawn+death gaps
inside one sample interval. Castle `done` uses the owner-building-at-tile
fallback (`UnitClass.BUILDING`) when no completed-type name mapping exists.

Gotchas that stay true: commands hit a paused module never arrive (send
after F3), `seek` cannot rewind (t < target only), one IPC connection per
replay session, ReadFile without pending data blocks (Peek first).


1. **Parse timeline** — fast parser + PR fixes → normalized JSON per match:
   ages/builder orders/maps/chat/resigns + activity graph from SYNC.
2. **Derive highlight candidates** — rule-based scoring over the timeline:
   concentrated BUILD/attack-move clusters = fights (approximate), castle
   placements, age-ups, king kills (Regicide), base wipes (DELETEs), unique
   moments (flares, chat reaction spikes). Output: timestamps; no rendering.
3. **Render** — the only place the game is needed: launch DE in replay mode on
   the host machine, seek/demo to parsed timestamps, capture (OBS replay
   buffer or CaptureAge). Parsed JSON is the trigger layer, video is the
   capture layer.
4. **Extras** — token/frame alignment, statistical scores (AoE2Insights-style
   graphs), storage of parsed timelines.

Decision recorded: highlights are generated "data-first" (parse → timestamp →
record), not by watching pixels.
