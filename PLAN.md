# Headless frames: strip in-game HUD from recorded replays

Goal: record replay takes with the game's own HUD (minimap, resource
bar, score, command panel, chat, notifications) not drawn - world
   visible behind it. Modular option, default off. Game window stays
   visible; mss capture unchanged. Optional custom HUD renders inside
   game so captured frames include aligned counts and minimap.

## Proven mechanism (build 101.103.57257, live-verified)

1. AoE2 DE HUD panels are data-driven: `<install>\widgetui\<panel>.json`
   (`{"Collection": {Name, ViewPort, Widgets: [{"Widget": {...,
   "ChildWidgets": [...]}}...]}}`, CRLF line endings, 2-space indent).
2. A local mod = a mirror folder under
   `%USERPROFILE%\Games\Age of Empires 2 DE\<profile>\mods\local\hl_nohud\`
   with `info.json` + `widgetui\<panel>` payload files.
3. WORKING DELIVERY: place the prepared folder under `local\` with NO
   hand-written mod-status.json entry. At boot the game SELF-REGISTERS
   the folder (its own `local//hl_nohud` entry, enabled, real payload
   CheckSum) and the session applies it safely. Hand-writing the entry
   ourselves + widgetui payload CRASHES the session at replay-load -
   even for byte-identical payloads (engine dies at session
   instantiation, `Age2CrashReport.txt`: SessionState=ReplayInGame,
   WorldTime=0, last widget dialogscrollanchor).
4. PAYLOAD TRANSFORM: byte-surgical `shrink` - keep every widget, set
   the collection ViewPort to 1x1 (four number swaps in the file text).
   All other bytes byte-identical to the game's file (CRLF/indent kept;
   json round-trips are FATAL - the engine rejects re-serialized files).
5. `commandpanel.json` uses the same full-tree 1x1 shrink as other panels.
   A partial tree preserving `ReplayButtons` crashed at replay startup.
   The replay bar and fog toggle therefore stay hidden.
6. Per-run state, all file-only, game must be DEAD during writes:
   - backup mod-status.json -> mod-status.json.bak once per run
   - solo: set every other entry Enabled=false (clean vanilla frames;
     user's UI/world mods would pollute recordings)
   - write payload folder; NEVER write our mod-status entry
   - park `logs\*\Age2CrashReport.txt` (rename) + kill BsSndRpt64.exe
     BEFORE launch - the game reads its own last crash report at boot
     and otherwise shows the "mods disabled" prompt + session-disables
     mods
   - after the run: kill game, restore mod-status from .bak (the game
     rewrites it on exit - restore must run AFTER the kill), then delete
     generated `local\hl_nohud` payload so next boot stays normal
7. Negative results (all live-tested, do not retry): `noui` chat cheat
   inert in replays (module SendChatMessage works, engine ignores it);
   full empty-Widgets crashes; json re-serialization crashes;
   subscribed-masquerade entries get dropped by the game.

## Tests / probes / tooling shipped

- `aoe2hl/nohud.py`: PANELS (16), MODES (empty/shrink/raw/none),
  _surgical_empty/_surgical_shrink/_surgical_keep_replay,
  generate_mod, enable (solo, channel; legacy synthetic-entry path),
  restore/entry/is_enabled. PREP-script pattern for self-registration
  runs (see tools/auto/probe_mod.py --prepared + one-shot prep script).
- `tools/auto/test_nohud.py`: 20+ tests (transform byte-fidelity,
  solo roundtrip, channel, commandpanel keep-replay).
- `tools/auto/probe_mod.py`: end-to-end live probe (boot -> replay ->
  HUD evidence PNGs + verdict.json, sanitize step, solo/only/skip/mode/
  channel/prepared flags, post-exit mod-status snapshot, restore).
- `capture.py wait_first_tick`: no more busy-spin on dead pipes.
- chat pipe command (timeline_recorder.main.lua + pipe.py) - kept for
  future use, inert for HUD.

## Steps (remaining work)

- [x] 4b-close: full 16-panel shrink + commandpanel keep-replay live run
      Standard full-panel shrink succeeded earlier (HUD gone, session
      healthy; fog toggle absent because commandpanel is collapsed).
      Attempt to keep only ReplayButtons (drops BackgroundRight,
      Buttons grid, FlightNameLabel) crashed at ReplayInGame/WorldTime=0.
      Keep-replay surgery parses and passes unit tests, but is NOT safe
      for the game; generate_mod reverted to standard 1x1 shrink for all
      panels. Research safer commandpanel edit that preserves complete
      widget structure while shrinking only unwanted subtrees.
- [x] 5. Recorder integration: `--no-hud` flag implemented on
      `recorder.py` (thin CLI `record_events.py` passes through). It
      applies only to pass 2; rejects `--reuse` because boot-time mod
      registration needs restart. Flow: kill -> backup+solo+payload
      self-registration prep -> crash-report sanitize -> launch/inject/
      replay -> HUD anchor verification -> record -> finally kill,
      restore status, delete folder. `nohud.prepare()` never writes an
      entry. Unit tests/compile pass; bounded live smoke recorded
      `out/recorder_nohud_smoke/clip_smoke_01_smoke_t126.mp4` (166 frames,
      6.2s audio), HUD anchor absent in clip frame, local payload removed,
      zero hl_nohud status entries after cleanup.
- [x] 6a. Custom HUD prototype: in-game Render API paints team villager
      totals + owner-colored castle/Krepost/TC dots. `overlay` pipe
      action and probe `--overlay` implemented. LIVE VALIDATED:
      two team-count text lines at top-center; three owner-colored
      5x5 building markers; full HUD stays gone; replay healthy. 6b
      adds a coarse terrain background under the markers.
- [x] 6b. Custom minimap background/terrain: render a useful map base
      behind the existing castle/TC markers (or retain a game-rendered
      map texture without restoring other HUD); current API exposes
      `GetMapWidth/Height`, `GetMapTile(x,y):GetTerrain()`, and
      `RenderRectFilled`, but no direct minimap bitmap call. Prototype a
      cached coarse tile grid (land/water/unknown colors) and draw icons
      in the same custom screen-space rectangle; avoid per-frame tile
      queries to keep Render/Update callbacks cheap. LIVE VALIDATED:
      224x224 custom map at bottom-right; 16x16 cached terrain sampling;
      land/water colors and three owner-colored 5x5 building markers
      rendered; HUD anchor remains absent; replay healthy.
- [x] 6c. Optional production recording flag `record_events.py
      --overlay` implemented; sends team/color header data, disables
      overlay after clips. LIVE VALIDATED together with `--no-hud`:
      168-frame clip; custom 224x224 terrain minimap pixels present;
      overlay disabled before normal game/mod cleanup. See
      `out/recorder_nohud_overlay_smoke/`.
- [x] 7. Docs: update `docs/replay-format-notes.md` - final mechanism,
      flag, ops rules (kill game before mods writes, crash-report
      parking, BugSplat kill, restore-after-kill + folder deletion),
      negative results. Added optional HUD-less recording section and CLI
      examples, documented custom overlay and panel/fog caveats.
- [x] 8. Replay rewind research: CaptureAge documents reverse playback
      and timeline jumps only through maximum game time already
      spectated. Exact implementation remains undocumented. Public CONTROL
      API exposes only positive `ReplaySpeed` values and no replay-time
      setter; our pipe seek is forward-only. No supported in-session
      rewind path found. Safe revisit fallback: reload, then fast-forward
      from start; this is not true rewind. Details and official links in
      `docs/replay-format-notes.md`.
- [x] 9. CadeRemote probe (game's built-in CaptureAge gRPC API, port
      4341 mTLS): built `tools/auto/aoe2hl/cade.py` (schema-free client,
      certs from LibreMatch) + `tools/auto/probe_cade.py`. LIVE
      VERIFIED: server runs from the main menu; Info (apiVersion 22);
      SetFogOfWar toggles the game-window fog (screenshot-verified);
      SetPerspective switches POV instantly; SetGameSpeed enum
      SLOW/NORMAL/FAST/FASTEST measured 0.83/1.68/5.95/6.26 game-s/s;
      LoadGame{LoadReplay.filePath} loads/restarts/switches replays with
      no UI driving (same-rec restart ~1-2 s); Pause works; Frames
      stream = full-state catch-up patch (~22 MB) + 8-17 KB per-tick
      deltas, time in ms, no reverse patches, streaming slows the sim.
      RestartAsReplay refused (status 9). Findings in
      `docs/replay-format-notes.md` ("CadeRemote" section).
- [x] 10. Recorder integration behind `--cade` (LIVE VALIDATED both
      passes): pass 1 collect via RPC replay load (full replay to
      t=1542, module ticks + plan built); pass 2 record via RPC load +
      RPC fog (clip minimap 19% dark = fog off, 412-frame clip muxed);
      pyautogui fallbacks retained. Files: `aoe2hl/cadeui.py`,
      recorder/capture/record_events changes.
- [ ] 11. Rewind option B (in progress): `cade_record.py` archives the
      Frames stream (~26 MB xz/wall-min at FASTEST) with module-clock
      alignment; `analyze_cade_frames.py` + `analyze_clock_align.py`
      done; WORKING: `cade_events.py` extracts the exact per-tick event
      stream from archives without any state model (damage, kills,
      tributes, train/research-done, chat w/ text - verified on both
      archives). `cadestate.py` delta applier + registry parser built;
      window-flip backtracking reaches ~27 kB of the full-state patch;
      full apiVersion-22 schema mining still OPEN (seed overrides in
      `docs/cade/schema_v22_overrides.json`; specs in `docs/cade/`).
      Mining diagnosis (2026-11-18): in dense full-state dumps a wrong
      scalar size drifts the stream for THOUSANDS of bytes before an
      invalid action byte appears, so local backtracking (96-instruction
      window + resume-at-choice) flips healthy instructions and thrashes;
      next attack is repeated-structure alignment (the dump contains
      thousands of identical blocks, e.g. 12-byte ResearchState records)
      or extracting the true v22 schema.
- [x] 13. Full-game archive + damage semantics pinned: `mp4_archive.xz`
      (30926 frames, t 0-6661 s, whole 22-min 8-player MP game, 67 MB
      xz) captured with the fixed resubscribe-on-deadline stream loop
      (`cade_record.py`); `cade_events.py` decode finalized - damage =
      {source_id, target_id, amount, hp_after (victim HP after the
      hit), counter}; chat taunt -1 when absent; NO entity_killed
      events exist in v22 (kills must come from the state model).
- [ ] 12. Later: per-player takes via `SetPerspective`; Oracle stream
      (modal dismissal); ChangeScreen/ChangeWindowState/SetDesiredGame
      probes; archive-first pipeline (pass 1 = archive, planner reads
      archive events: damage windows = battles, train/research done =
      exact timings); finish v22 state-model mining for kill
      detection.


## Notes

- User desktop 2x1920, game on RIGHT monitor. capture.py records mss
  `monitors[1]` (LEFT/primary?) - VERIFY before trusting recorded
  clips; the probe crops use window_rect() correctly.
- Module pipe dies at replay end (t~204 in test replay, winerror 233)
  - reconnect/wait patterns already tolerate it.
- The game auto-disabled 3 of the user's mods after early crash tests
  (Better Resource Panel and Idle Villager Icon, Fast Villager Defense
  Club, Immobile AI); restore-from-bak leaves them disabled; user can
  re-enable via in-game enable-all anytime (does not interfere).
- Session-side clean vanilla: solo mode also removes world-visual mods
  (plant remover, small trees, grids) from recordings - intentional.
- Implementation delegated to subagents on `opencode/glm-5.3-flash-free`;
  live game runs driven by the main agent on the user's desktop.
