# Priority Highlight Pipeline v3 — event-model refinements (2026-11)

The base pipeline is done end-to-end on 5 MP replays (steps 1-8 of the v2
plan): per-game bootstrap, fast collect, mgz cross-check, priority planner,
no-pause record flow with pan transitions, per-replay concat. Architecture
and engine truths live in `docs/replay-format-notes.md`. This plan refines
the event model; current priorities: 1 vill_loss, 2 army_loss, 3 tc_fall,
4 castle_fall, 6 resign, 7 castle_near_tc, 8 vill_animal.

## Tasks

- [x] 1. Resigned players are event-dead: after a player's resign time,
       their unit deaths, building losses and census drops never
       trigger or extend events (post-resign deletions are cleanup,
       not battles). Resign FOCUS is the exception: the team's battle
       deaths count even if a member had already resigned.
- [x] 2. Pan glide window: PAN_MAX_GAP 10 -> 20 game-s.
- [x] 3. Battle extension: while at least 1 unit/building dies every 2 s
       within 12 tiles of a death-based event's tile, the take keeps
       rolling. The extension is prio 9 (lowest): it is dropped when any
       other event overlaps it ("interrupted"), and merged into its base
       segment when kept (verified live: mp5 has 3 extended takes).
- [x] 4. Late-game gate: after minute 30 (t > 1800) a death-based event
       needs at least 10 deaths to trigger.
- [x] 5. Buildings count as 2 dead units in army-loss events (same
       player, within 12 tiles, inside the 15-s detection window): the
       loss count, the late-game gate and the extension cadence all
       include them.
- [x] 6. Krepost counts as a castle everywhere: module `isCastle` flag
       covers KREPOST names (foundations + completed-building tracker),
       planner also falls back to the row name. Verified live: mp5's
       plan has 3 Krepost events.
- [x] 7. New event (prio 5, vacant slot) `castle_build`: a castle/Krepost
       foundation whose area is safe (no deaths within 12 tiles from
       30 s before start until done) is filmed from start to done sped
       up. The engine speed ladder has NO middle preset (measured:
       SLOW 0.85 / NORMAL 1.69 / FAST 6.76 / FASTEST 11.46 game-s per
       wall-s; the Game Speed Multiplier is inert during replays), so
       the take films at NORMAL and the mux compresses it 3x
       (itsscale + atempo) — a true middle speedup.
- [x] 8. castle_near_tc (prio 7) focuses the midpoint tile between the
       castle and the NEAREST enemy TC inside the 20-tile radius.
- [x] 9. Resign events (prio 6) fire only when the LAST player of a team
       resigns; focus = the densest 5-tile bucket of that team's deaths
       (buildings = 2 units) in the last 10 s before the resign;
       fallback = densest whole-game bucket, then the resigning
       player's base TC. Teams from the mgz DE header (`de.players`
       `team_id`; stance 4 = ally in the diplomacy matrix, confirmed on
       mp4/mp5 4v4s). mp4's two same-team resigns collapsed to one
       event; mp1's solo early resign still fires for its team.

Constants: NEARBY_TILES = 12, EXTEND_GAP = 2.0 s, LATE_GATE_T = 1200,
LATE_MIN_DEATHS = 10, BLD_DEATH_WEIGHT = 2, RESIGN_CLUSTER_S = 180.