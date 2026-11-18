"""Compare Frame.time (ms) against CONTROL module t (s) from a
cade_record meta file: least-squares fit module_t = a*frame_s + b.

Usage:
    py -3.12 tools/auto/analyze_clock_align.py out/cade/sp_archive_test.meta.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main():
    meta = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    samples = [(w, t / 1000.0) for w, t in meta["time_samples"]]
    ticks = meta["module_ticks"]
    pairs = []
    si = 0
    for wall, t in ticks:
        while si + 1 < len(samples) and samples[si + 1][0] <= wall:
            si += 1
        if si < len(samples):
            fw, fs = samples[si]
            if abs(fw - wall) < 0.35:
                pairs.append((fs, t))
    print(f"{len(pairs)} aligned pairs "
          f"(of {len(ticks)} ticks / {len(samples)} samples)")
    if len(pairs) < 5:
        for fs, t in pairs:
            print(f"  frame={fs:.3f} module={t:.3f}")
        return
    n = len(pairs)
    sx = sum(p[0] for p in pairs)
    sy = sum(p[1] for p in pairs)
    sxx = sum(p[0] * p[0] for p in pairs)
    sxy = sum(p[0] * p[1] for p in pairs)
    a = (n * sxy - sx * sy) / (n * sxx - sx * sx)
    b = (sy - a * sx) / n
    res = sorted(abs(a * x + b - y) for x, y in pairs)
    print(f"fit: module_t = {a:.4f} * frame_s + {b:+.3f}")
    print(f"residual: med={res[n // 2]:.3f}s p90={res[int(n * .9)]:.3f}s "
          f"max={res[-1]:.3f}s")
    print("first pairs:", [(round(x, 2), round(y, 2)) for x, y in pairs[:5]])
    print("last pairs:", [(round(x, 2), round(y, 2)) for x, y in pairs[-5:]])


if __name__ == "__main__":
    main()
