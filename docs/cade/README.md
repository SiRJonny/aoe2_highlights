# CadeRemote reference material

Protocol reverse-engineering references for the game's built-in
CaptureAge gRPC API (see `docs/replay-format-notes.md`, section
"CadeRemote").

- `model.rs`, `model2.rs`, `patcher.rs`, `path.rs`, `selector.rs`,
  `references.rs`, `document.rs` - copies of the LibreMatch
  delta-play-replay Rust specs (github.com/librematch/
  delta-play-replay, AGPL-3.0). Used as a protocol/model description
  only; `tools/auto/aoe2hl/cadestate.py` parses `model.rs` at runtime
  to build the state-model registry.
- `schema_v22_overrides.json` - learned corrections for the live
  apiVersion-22 schema on top of the 2021 `model.rs` spec (seeded by
  hand, extendable via the `cadestate` miner).
