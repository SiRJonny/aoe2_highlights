"""Apply an archived CadeRemote Frames stream (cade_record.py output) to
a Python state tree and print summaries.

Usage:
    py -3.12 tools/auto/cade_apply.py out/cade/sp_archive_test.xz [--every 2000]
"""
from __future__ import annotations

import lzma
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from aoe2hl import cadestate  # noqa: E402


def sequences(data: bytes):
    i = 0
    n = len(data)
    while i < n:
        (ln,) = struct.unpack("<I", data[i:i + 4])
        seq = data[i + 4:i + 4 + ln]
        i += 4 + ln
        for num, wt, val in cadestate_state_fields(seq):
            pass
        yield seq


def cadestate_state_fields(seq):
    from aoe2hl import cade
    return cade._fields(seq)


def frames_of(seq: bytes):
    from aoe2hl import cade
    for t, patch, rev, nev, ncmd in cade.iter_frames(seq):
        yield t, patch


cade_frames = frames_of


def summarize(state: cadestate.State, label: str):
    world = state.world()
    wt = world[1].get(0)
    print(f"[{label}] world time {wt} ms "
          f"({wt / 1000:.1f}s), entities {len(world[1].get(1, {}))}, "
          f"map {world[1].get(7)}x{world[1].get(8)}")
    players = world[1].get(2, {})
    for pid, pref in sorted(players.items()):
        p = state._node(pref)
        name = p[1].get(1, "?")
        attr_ref = p[1].get(2)
        res = ""
        if attr_ref is not None:
            a = state._node(attr_ref)[1]
            res = (f" food={a.get(0, 0):.0f} wood={a.get(1, 0):.0f} "
                   f"gold={a.get(3, 0):.0f} stone={a.get(2, 0):.0f} "
                   f"pop={a.get(11, 0):.0f}/{a.get(4, 0):.0f}")
        print(f"  p{pid} {name!r} civ={p[1].get(5)} "
              f"resigned={p[1].get(13)}{res}")


def main():
    src = Path(sys.argv[1])
    every = int(sys.argv[sys.argv.index("--every") + 1]) if "--every" in sys.argv else 0
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])
    data = lzma.open(src, "rb").read()
    print(f"{src}: {len(data) / 1e6:.1f} MB raw")
    reg = cadestate.parse_registry()
    print(f"registry: {len(reg)} models, "
          f"{sum(len(m.fields) for m in reg.values())} fields")
    state = cadestate.State(reg)
    override_path = Path("docs/cade/schema_v22_overrides.json")
    if override_path.exists():
        state.load_overrides(override_path)
        print(f"loaded {len(state.schema)} schema overrides")
    frames = []
    i = 0
    n = len(data)
    while i < n:
        (ln,) = struct.unpack("<I", data[i:i + 4])
        seq = data[i + 4:i + 4 + ln]
        i += 4 + ln
        frames.extend((f[0], f[1]) for f in cade_frames(seq))
    print(f"{len(frames)} frames")
    if limit:
        frames = frames[:limit]

    t0 = time.time()
    if not override_path.exists():
        print("mining v22 schema (chronological discover+apply on a "
              "scratch state)...")
        mining = cadestate.State(reg)
        for k, (t, patch) in enumerate(frames, 1):
            mining.discover(patch)
            mining.apply(patch)
            if k % 2000 == 0:
                print(f"  mined {k} frames ({time.time() - t0:.0f}s, "
                      f"{len(mining.learned)} learned fields, "
                      f"{mining.stats['retries']} retries)")
        mining.save_overrides(override_path)
        print(f"schema saved -> {override_path} "
              f"({len(mining.learned)} learned fields, "
              f"{mining.stats['retries']} retries, {time.time() - t0:.0f}s)")
        state.load_overrides(override_path)

    t0 = time.time()
    n_frame = 0
    errors = 0
    for t, patch in frames:
        n_frame += 1
        try:
            state.apply(patch)
        except Exception as e:
            errors += 1
            if errors <= 3:
                print(f"frame {n_frame} t={t}: APPLY FAILED: {e}")
                raise
        if every and n_frame % every == 0:
            summarize(state, f"frame {n_frame}")
    print(f"applied {n_frame} frames "
          f"in {time.time() - t0:.1f}s ({errors} errors)")
    summarize(state, "final")


if __name__ == "__main__":
    main()
