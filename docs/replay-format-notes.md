# AoE2 DE Replay Highlights — Technical Notes

Short summary of what an `.aoe2record` file contains, what can be parsed
without running the game, what cannot, and how the capture pipeline works
around that. Verified against DE build 101.103.x (save version 68.0).

## Repo layout

    tools/auto/aoe2hl/        the reusable package (see below)
    tools/auto/*.py           thin CLIs over the package + tests + grid compositor
    tools/aoe2control/        AoE2Control launch config: settings.timeline.ini,
                              timeline_recorder (the injected Lua module)
    anchors/                  1920x1080 UI template crops for gameui
    replays/                  sample replays
    out/                      OUTPUT ONLY: highlights_<tag>.mp4 at the root,
                              one out/<tag>/ folder per replay for intermediates
                              (events.jsonl, plan.json, clips/, clips/grid_parts/)

### The `aoe2hl` package (`tools/auto/aoe2hl/`)

| module        | role |
|---------------|------|
| `paths`       | out-folder layout: `final_path`, `events_path`, `plan_path`, `clips_dir`, `grid_parts_dir`, `tags()` |
| `gameui`      | window focus, anchor template matching, SendInput clicks, debug screenshots |
| `pipe`        | named-pipe client for the injected Lua module (send, read_for, connect) |
| `replays_ui`  | drives the UI from any screen into a loaded replay (`run(row, search)`) |
| `control`     | headless AoE2Control injection (`inject()`, `control_running()`) |
| `capture`     | `collect` (stream rows to jsonl), `record_segment` (mss video + WASAPI loopback), `mux` (real-time itsscale), `fog_off`, `close_control_panel`, `wake`, `wait_first_tick`, `wait_seek_settled` |
| `planner`     | priority event selection → plan.json (`build(events, row, plan_path)`), 18 unit tests |
| `annotators`  | mgz-based resigns/teams/map + attack-order annotations appended to the event stream |
| `recorder`    | two-pass orchestration: collect → annotate → plan → record (`main()`, `record_plan`, `record_grid`, `reload_session`) |

The CLIs: `record_events.py` (both passes), `select_events.py` (planner),
`start_replay.py` (UI), `bootstrap_row.py` (touch + game reboot + inject),
`run_mp.py` (5-game end-to-end), `make_grid.py` (grid compositor),
`concat_clips.py` (per-replay concat). A spinoff app (e.g. an artificial map
animation) imports `aoe2hl.capture/pipe/gameui/replays_ui` directly instead of
shelling out.

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

Confirmed with the `mgz` Python parser (`pip install mgz`, fast parser; used
by `aoe2hl.annotators`).

**Header (start of game, one snapshot):**
- Map: id, size, seed, full terrain tile grid (enough to render a minimap image)
- Players: name, civ, color, team, profile id/rating (DE metadata), team positions
- Lobby: game type, difficulty, resources, rules, starting age, victory type
- All initial objects with type + position (units, buildings, TCs, gaia)

**Body (events, per operation with in-game timestamp `sequence`):**
- Age advances: `RESEARCH` of age techs (mgz computes `uptimes`)
- Buildings: `BUILD` (placements with x/y/z), `WALL`, `DELETE` (walls/farms too)
- Production: `DE_QUEUE` / `MULTIQUEUE` (unit_id + producer object_ids)
- Completions/transforms: `DE_TRANSFORM`, `SPECIAL`
- Military orders: `ORDER`, `MOVE`, `PATROL`, `DE_ATTACK_MOVE`, `STANCE`,
  `FORMATION` (positions + object_ids → can cluster army movements)
- Economy/transactions: `SELL`/`BUY`, `DE_TRIBUTE`, gather points
- Player actions: `RESIGN`, `DELETE` (own buildings), `STOP`, town bell,
  autoscout, flare, garrison/ungarrison
- Camera: `VIEWLOCK` — recorder's viewport position over time
- `CHAT` — JSON with channel (all/teams), message, platform icon markup
- Frame stats: `SYNC` per player: obj_count, dp_obj_count, dp_obj_ttl,
  total_res (~1 cadence) — decent out-of-game proxy for score/activity curves
- `POSTGAME` at end: final ratings/ranks (leaderboards), victory, modes

## NOT extractable without running the game

- **Unit deaths / kill attribution** — combat damage is simulated by the engine,
  never recorded per tick. `SYNC` counters lack kill deltas; a battle must be
  recreated from movement clusters + disappearance inference, or end-guessed
  from `POSTGAME` totals only (units_killed/lost per player, no timing).
- Actual state at time T: unit positions, HP, population, **resources, idle
  TCs, army sizes, field of view of each player**. Requires replaying
  deterministically inside the real engine or a compatible recreation.
- **Graphics, sound, animations** — no asset data in the replay.
- Any player's view except the recorder's.
- Terrain/object changes mid-game (trees cut down, converted units):
  implied by rules + commands, but only recoverable through engine simulation.

## Extraction tier 2: run the game (AoE2Control) — verified working

While the game runs (replay/spectate included), **AoE2Control** (https://github.com/aoe2control/AoE2Control,
v1.1.5, verified against game build 101.103.48987.0) injects a Lua engine into
the DE process. The `timeline_recorder` module (`tools/aoe2control/`) reads
real game state (fog-of-war off via **Modules See Everything**) and streams
JSON rows over the `\\.\pipe\AoE2_Highlights` named pipe to `aoe2hl.pipe`:

- per-sample census: villager/army population, deaths with last-known x/y
- building events: TC/castle/Krepost start + done (with builder id)
- resigns, tick (game time), replay_length, map name, teams
- engine-side actions: seek (with pause flag + speed), camera, pan tween,
  set_speed

Setup (order matters, everything is scriptable with `--headless`):

1. `bootstrap_row.py --file X.aoe2record` touches the replay (newest mtime →
   list row 1), reboots the game, injects CONTROL
   (`AoE2Control.exe --headless --override-settings tools/aoe2control/settings.timeline.ini
   --override-module tools/aoe2control/timeline_recorder`). The replay list is
   cached per game boot, so every game needs this restart cycle.
2. `aoe2hl.replays_ui` drives main menu → Load Game → Replays tab → row 1 →
   Load Replay. The module auto-starts at match start.

UI automation facts (all in `aoe2hl.gameui` + `aoe2hl.replays_ui`):

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
- Only list row 1 is supported (row heights vary, hover-scroll breaks
  detection); copy/touch the wanted replay to make it row 1.

Module lifecycle facts:

- Injection while on a menu is reliable; the engine logs `Ready` via stdout.
- `GetGameTime()` behaviour differs between menu and match; the module resets
  its sampling schedule when time goes backwards (match (re)load), otherwise
  it strands at a huge `nextSampleTime` and silently stops sampling.
- Module time is true replay time (the on-screen top bar can differ —
  cross-check with SYNC ticks when mapping highlights).
- Same-ID type swap in the foundation tracker (a deleted lot replaced by a new
  building in one sample) used to double-fire tc_fall/castle_fall; planner
  dedupes same-owner/same-tile falls in a 30-s window.
- The collect pipe client attaches ~t=36 of a fresh replay → one-shot start
  rows are lost; the planner derives the first TC from done/tc_build rows and
  the builder from tc_build / vill_pos streams.

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

## Highlight pipeline (two-pass, priority planner)

One command per game (`tools/auto/run_mp.py --only N`, or per-file via
`bootstrap_row.py --file X.aoe2record` then `record_events.py --tag T --touch X`):

1. **Per-game bootstrap** — `bootstrap_row.py` (see setup above; inject with
   `settings.timeline.ini`, **Update Interval=0.05** — see engine truths).
   Pipe verify must wait for a *loaded replay*, not the main menu.
2. **Collect pass** — whole replay at `FASTEST` (a 28-min match takes ~90 s);
   module rows (minus stats) → `out\<tag>\events.jsonl`. End = game time
   stalled 20 s ("time stalled" means the replay *ended*, expected).
3. **mgz annotation** (in-process via `aoe2hl.annotators`) — sums the file's
   `SYNC`-millis → true replay length (`last t` must match); appends resign
   ops, a `teams` row (`de.players` `team_id` — authoritative team
   membership), the `replay_map` row, and right-click attack tasks (`ORDER`
   ops).
4. **Priority planner** (`aoe2hl.planner`, 18 unit tests) → `out\<tag>\plan.json`:
   1 = lose ≥10% villagers in 15 s, 2 = lose ≥20% army in 15 s (destroyed
   buildings of that player count as 2 dead units), 3 = TC falling,
   4 = castle/Krepost falling, 5 = nomad first-TC grid (see below;
   castle_build DISABLED — the flag `CASTLE_BUILD_ENABLED=False` stays in the
   planner), 6 = resign (team-last; focus = densest 5-tile bucket of the
   team's deaths in the last 10 s), 7 = castle built near an enemy TC (camera
   at the castle↔TC midpoint), 8 = villager killed by an animal,
   9 = battle extension (death cascade every ≤2 s within 12 tiles; lowest
   prio, yields to other events, merges into its base). After minute 20 a
   death-based event needs ≥10 deaths. Resigned players are event-dead.
   Highest prio wins overlaps entirely; same prio merges; chronological;
   zero events → no video (valid).
5. **Record pass** — per segment in time order; the game NEVER stops
   moving: seeks send `pause=False` (settle lands camera+per-segment
   speed while running), no F3 pause between takes, wake only for pipe
   recovery, chases bump to FASTEST. Close segments (gap ≤ 20 game-s,
   ≤ 40 tiles) film the gap as an engine-side camera PAN (module `pan`
   tween) to the next tile instead of seek-teleporting. Muxes deferred
   to plan end so the inter-take gap stays filmed. Take = verify-
   advancing, film 30 fps mp4v + WASAPI loopback →
   `out\<tag>\clips\clip_<tag>_<NN>_<prio>_t<sec>.mp4`.
   Mux corrects to real time with `-itsscale` (writer fps ≠ capture loop
   pace; without it long takes play sped-up) + `atempo` audio compression.
   Fog: `Spectator Mode=true` opens replays in the observer panel; the flow
   clicks `replay_all_visible` after *every* load (fog resets per load).
   The CONTROL overlay is Shift-closed at pass start (anchor-verified
   `control_panel` template; toggles only when visible).
6. **Grid + concat** — `make_grid.py` composites per-player grid takes
   (max take duration, `tpad` last-frame padding, HUD kept in cells; the old
   --mask-hud path is gone). `concat_clips.py` concatenates each
   `out\<tag>\clips\` → `out\highlights_<tag>.mp4` (`-c copy`, no re-encode).

### Nomad first-TC grid (prio 5)

Detection: map-name match (nomad/*_ship map scripts) OR gameplay fallback
(unknown map name + no tc_snap row + TC start rows exist). One segment, prio 5,
FAST speed, span = game 0 → last TC done + 15 in-game s (`FIRST_TC_TAIL_S=15`).
Recording: one take PER PLAYER with a fresh paused reload each
(`reload_session`: UI start, F3 pause right at match start so the opening
isn't eaten, then seek+arm). Cameras are STATIONARY on each player's TC site
from game start (villagers walk in; no camera following). Takes land in
`out\<tag>\clips\grid_parts\`; `make_grid.py` composites 2×2 (4 players) or
3×3 (6 players: top 6 + bottom-middle minimap; 8 players: minimap last slot).

## Engine truths (each verified experimentally, mp1 debug cycle)

- Module seek with `pause=true` stops the game **exactly** at the target
  (clock frozen, camera moved). A seek-less replay plays through and still
  crosses the target — trust the seek receipt ack, not just `t ≥ target`.
- The engine **ignores `SetReplaySpeed`/`SetCameraPosition` while paused**
  but the module still acks them (ack ≠ applied). The seek handler must
  store `speedName` and the settle branch must apply it while running.
- `set_speed NORMAL` sent while the game runs applies. Speed ladder
  (game-s/wall-s): SLOW 0.85, NORMAL 1.69, FAST 6.76, FASTEST 11.46.
- F3 unpauses at the engine's *current* speed — if the settle left FASTEST,
  end-of-file events race into the end screen.
- Ticks arrive every ~10 game-s; sample rows every 1 game-s (~0.6 wall-s at
  NORMAL). Advancement guards must read sample rows.
- `wake()` (F3-till-ticks) ends unpaused → the take prelude must test
  advancement *before* pressing F3, else it pauses a running game and the
  toggle dance eats the window.
- Backward seek target (head already past it): window ahead → skip the seek
  and film onward; window passed → skip the segment.
- No-pause flow: the game is never parked between takes. Post-take F3
  pauses froze the module (the whole wake dance existed for that); with
  `pause=False` seeks the module stays alive and the prelude's
  running()-check passes instantly. wake() is reserved for pipe recovery.
- Pan transitions: gap ≤ 20 game-s + ≤ 40 tiles → the gap is FILMED as a
  camera glide instead of a seek jump cut; muxes are deferred so
  the gap is captured.
- Update cadence was the pan bottleneck: CONTROL runs module `Update()`
  every "Update Interval" seconds of GAME time (`[modules] Update
  Interval` in the settings ini, default 1 = once per game-second ≈
  1.7 Hz wall at NORMAL). Every pipe camera command or Lua tween can
  only apply on Update — measured: both stepped at ~2.5 Hz. Fix:
  `Update Interval=0.05` (docs: ini values can go as low as 0.01) →
  ~34 Hz wall; the 1 Hz census keeps its own schedule. With that, the
  engine-side `pan` action (module tween per Update, game-time-driven)
  glides; pipe-driven sends still coalesce in the IPC pump.
- End screen (`FINISHED REPLAYING GAME.`) freezes the module permanently —
  zero pipe rows, no F3 recovery. Guards skip such segments; a <10-frame
  junk take is deleted. Clamp `t1` to `replay_length − 0.5`. The very
  end-of-file resign (~t=3995 in one test game) is therefore unrecordable.
- PC suspend kills the CONTROL module pipe → full bootstrap (game reboot +
  inject) required, not a mere reconnect.
- mss BitBlt capture fails while the monitor is asleep — wake the display
  before recording.
- Commands sent to a paused module never arrive (send after F3); `seek`
  cannot rewind (t < target only); one IPC connection per replay session;
  ReadFile without pending data blocks (Peek first).

## Out-folder hygiene

`out/` root holds ONLY final videos (`highlights_<tag>.mp4`). Everything else
an intermediate. The one folder per replay (`out/<tag>/`) is disposable:
`events.jsonl` + `plan.json` rebuild with a collect pass, clips with a record
pass. Never park debug screenshots or csv/svg artifacts there (use
`%TEMP%\opencode`); `gameui.save_debug` writes to `out/auto_debug/`, which is
scratch and can be wiped any time.

## Map-animation webapp (spinoff, 2026-11)

An enlarged minimap-style animation of a whole replay: all buildings and
units in player colors, notable buildings (TC/castle/Krepost) as icons.
Same toolchain as the highlight pipeline (one FASTEST runthrough, same
package, same injected module); `out/<tag>/positions.jsonl` + `map.png`
are intermediates in the same per-replay folder.

- **Module**: the recorder streams an `objects` row per sample — columnar
  snapshots `[id, owner, unitType, x, y]` for buildings (completed +
  foundations), villagers and military, positions rounded to 0.1 tile,
  gaia skipped — but ONLY after the client sends
  `{"action":"map_mode","on":true}` (highlight collect must never enable
  it; the rows are big). `map_mode` also re-sends the `meta` row (players,
  map size, `typeNames` id→name) so a late-attaching client still gets it.
  `meta` now always carries `typeNames`.
- **Collect**: `map_collect.py --tag T --touch X.aoe2record` — touch → UI
  start → pipe → map_mode on → `capture.collect_positions` at FASTEST
  (keeps `meta`/`objects`/`tc_snap`, same 20-s stall end) →
  `out/<tag>/positions.jsonl`; then `aoe2hl.terrain.render_replay_png`
  renders the terrain from the replay's mgz header (full `mgz.summary`
  header parses with the 67.2/68.0 patch: `map.tiles` = {x, y, elevation,
  terrain_id}; palette in `terrain.py`, forest=13, water=1/3, grass=14/22)
  → `out/<tag>/map.png` + a `{"type":"map","name":...}` row appended to
  the jsonl.
- **Serve/play**: `map_app.py --tag T` (stdlib http.server, port 8787)
  serves `tools/auto/map_web/` statics, `/api/data/<tag>` (streamed jsonl)
  and `/api/map/<tag>` (png). Canvas frontend: player-colored squares
  (buildings), dots (military/villagers), icon glyphs for notable
  buildings, sample-to-sample interpolation, play/pause (space), seek
  slider (±1 s with arrows), 0.5–16× speed, player legend.
- **No-game fixture**: `map_demo.py` synthesizes `out/demo/` (3 players,
  scripted march + fight, synthetic terrain) to develop the webapp
  without the game.
- Later: stats side panels (the `stats` rows already carry age/resources
  per player — the module sends them regardless of map_mode; the webapp
  just ignores them for now).
