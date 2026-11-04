# Priority Highlight Pipeline (fog-free, MP replays)

Goal: for any `.aoe2record`, one fast data-collection pass, then a
priority-planned, non-overlapping segment recording — fog disabled — run
end-to-end on 5 "MP Replay" games. Zero-event games produce no video.

## Context (carried over from rows work)

- Fast collect pass exists: `record_events.py --collect` + module v2
  (`vill_pop`, `vill_death`, `bld_event` start/done/lost, `stats`, 1 Hz).
- Replay list is cached per game boot → per-game bootstrap = touch + game
  restart (`tools/auto/bootstrap_row.py`, uncommitted; pipe-verify must be
  relaxed — module pipe only exists after a replay loads, not at main menu).
- mgz SYNC-millis sum = module clock (cross-validated); use it to validate
  every collect (`last t` ≤ file length) before recording.
- Record hygiene: arm writer+audio BEFORE F3 unpause; mux via temp +
  `os.replace`; end screen freezes the module; seek cannot rewind.
- Yesterday's row-3 bootstrap run: game relaunched + CONTROL injected
  ("Ready") but module-pipe check was premature → fix in step 5.

## Final priority spec (user-confirmed; old prio 5 was a typo, removed)

| prio | event | window (game s) |
|---|---|---|
| 1 | a player loses ≥10% of their villagers within any 15 s | detected 15 s window ±2 |
| 2 | a player loses ≥20% of their army within any 15 s | detected 15 s window ±2 |
| 3 | Town Center falling | t−5 … t+2 |
| 4 | Castle falling | t−3 … t+4 (pads not user-specified — tunable constant) |
| 6 | resign/defeated: clip where their last building fell or last unit died | t−4 … t+2 |
| 7 | castle built (completed) within 20 tiles of an enemy TC | t−15 … t+5 (tunable) |

Overlap rule: when windows overlap, keep the highest-priority one and drop
the lower entirely (never trim). Same-priority overlaps merge into one
segment. Recording order = chronological; gaps skipped via seek.

## Steps

- [ ] 1. Fog off via Spectator Mode: set `Spectator Mode=true` in
      `tools/aoe2control/settings.timeline.ini` (keep `Modules See
      Everything=true`), swap settings on the running game (Delete
      force-inject + `pipeline.inject()`), load a replay, verify fog-free
      render via frame dump + module still streams. Fallback if UI/module
      breaks: document and record from involved player's view instead.
- [ ] 2. Module v3 — army events: add military-unit census (`army_pop`,
      per-player counts, UnitClass.MILITARY or equivalent class set) and
      `unit_death {time,id,player,x,y}` for military units (same
      last-known-position gap logic as `vill_death`); tag `bld_event` lost
      rows with `isTC` (name prefix TOWN_CENTER) and `isCastle` (already
      there). No behavior change to existing rows. Commit.
- [ ] 3. Resign/defeat annotation: offline mgz action parse (extend the
      `probe_replay.py` pattern) → `resign {time, player}` rows merged into
      the events jsonl after collect. Position resolved at plan time from
      that player's last `bld_event lost` / `unit_death`. Commit.
- [ ] 4. `select_events.py` v2 priority planner: events jsonl + mgz length →
      ordered plan. Census-based sliding 15 s windows for prio 1/2 (baseline
      = census at window start; ≥10% vill / ≥20% army loss), destroyed TC /
      castle from `bld_event lost`, resign from step 3, prio 7 from
      castle `bld_event done` vs enemy TC positions (`bld_event done`
      isTC, tile distance ≤ 20, different owner). Empty plan is valid.
      Unit test with a synthetic events fixture covering overlap
      drop/merge + zero-event case. Commit.
- [ ] 5. Bootstrap fix: drop premature module-pipe verify in
      `bootstrap_row.py` (CONTROL pipe check is enough at main menu);
      commit.
- [ ] 6. Record driver v2: run a whole plan in one replay load — chase to
      each segment (forward seek), arm capture before unpause, record,
      skip gaps via seek; backward target → `restart` action then chase.
      Reuses `clip_recorder.wait_seek_settled` + WASAPI loopback. Commit.
- [ ] 7. End-to-end on 5 "MP Replay" games: pick 5 newest `MP Replay *`
      in savegame; per game: utime the original file → `bootstrap_row.py`
      → collect (cross-check `last t` vs mgz SYNC length) → plan → record →
      verify clips (frame counts, AAC audio, frames at event t). Zero-event
      game: log "no events, no video". Commit per game.
- [ ] 8. Docs: update `docs/replay-format-notes.md` (spectator/fog,
      priority planner, per-game bootstrap, mgz length check) + final
      commit.

## Notes

- Prio 5 in the original request was a duplicate of prio 3 (user: typo,
  removed). Prio numbers keep their given values (1,2,3,4,6,7).
- Army loss baseline subtlety: census at window start can already be
  post-loss; sliding 15 s windows step 1 game s so the max-loss window is
  found regardless.
- `record_events.py` gains a game-agnostic mode for step 7 (explicit
  `--events/--plan/--out-dir/--touch` already exist; no row/copy semantics
  for MP originals — touched in place, mtime only).
- Children do not commit (git index contention); each step is reviewed and
  committed by the lead.
