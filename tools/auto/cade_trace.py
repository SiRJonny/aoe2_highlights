"""Trace one archived patch: applies frames up to N with tracing and
prints the instruction trail + hex context on error.

Usage:
    py -3.12 tools/auto/cade_trace.py out/cade/sp_archive_test.xz [frame_no]
"""
from __future__ import annotations

import lzma
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from aoe2hl import cade, cadestate  # noqa: E402


def main():
    src = Path(sys.argv[1])
    upto = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    data = lzma.open(src, "rb").read()
    reg = cadestate.parse_registry()
    state = cadestate.State(reg)
    state.trace_enabled = True
    frames = []
    i = 0
    while i < len(data):
        (ln,) = struct.unpack("<I", data[i:i + 4])
        seq = data[i + 4:i + 4 + ln]
        i += 4 + ln
        frames.extend((f[0], f[1]) for f in cade.iter_frames(seq))
    print(f"{len(frames)} frames")
    t, patch = frames[upto]
    print(f"frame {upto} t={t} patch_len={len(patch)}")
    try:
        state.apply(patch)
    except Exception as e:
        print(f"ERROR after {len(state.trace)} traced instructions: {e}")
        for off, action, model, depth in list(state.trace)[-25:]:
            print(f"  @{off} action={action} model={model} depth={depth}")
        err_off = list(state.trace)[-1][0] if state.trace else 0
        lo, hi = max(0, err_off - 20), min(len(patch), err_off + 60)
        print("hex around failure:", patch[lo:hi].hex(" "))
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
