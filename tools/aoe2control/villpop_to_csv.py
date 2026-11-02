"""Convert vill_pop_recorder JSONL into per-player CSV + SVG timeline.

Usage:
    py -3.12 tools\\aoe2control\\villpop_to_csv.py [--in out\\vill_pop.jsonl]
        [--out-dir out\\]

Reads {"type":"vill_pop","time":<s>,"counts":[v1..v8]} rows (index i = player i+1)
and writes:
    out/vill_pop.csv      time, p1..p8 header, one row per sample
    out/vill_pop.svg      polyline chart, one line per player
"""
import argparse
import csv
import json
import sys
from pathlib import Path

COLORS = ["#e6194b", "#3cb44b", "#4363d8", "#f58231",
          "#911eb4", "#46f0f0", "#f032e6", "#bcf60c"]


def read_rows(src):
    rows = []
    for line in Path(src).open(encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        if rec.get("type") != "vill_pop":
            continue
        counts = rec.get("counts")
        time = rec.get("time")
        if counts is None or time is None:
            continue
        if isinstance(counts, dict):
            counts = [counts.get(str(i), counts.get(i, 0)) for i in range(max(counts))]
        rows.append((float(time), counts))
    rows.sort(key=lambda r: r[0])
    return rows


def write_csv(rows, dest, nplayers):
    with dest.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["time_s"] + [f"p{i}" for i in range(1, nplayers + 1)])
        for time, counts in rows:
            writer.writerow([f"{time:.1f}"] + list(counts[:nplayers]))


def write_svg(rows, dest, nplayers):
    width, height, margin = 900, 380, 40
    times = [t for t, _ in rows]
    if not times:
        dest.write_text("", encoding="utf-8")
        return
    t0, t1 = min(times), max(times)
    flat = [v for _, counts in rows for v in counts[:nplayers]]
    v0, v1 = min(flat), max(flat)
    if t1 == t0:
        t1 = t0 + 1
    if v1 == v0:
        v1 = v0 + 1

    def X(t):
        return margin + (t - t0) / (t1 - t0) * (width - 2 * margin)

    def Y(v):
        return height - margin - (v - v0) / (v1 - v0) * (height - 2 * margin)

    parts = [
        f"<svg xmlns='http://www.w3.org/2000/svg' width='{width}' height='{height}' "
        "style='background:white'>",
        f"<text x='{margin}' y='{20}' font-family='sans-serif' font-size='13'>"
        f"villager population t={t0:.0f}s..{t1:.0f}s</text>",
    ]
    for value in _ticks(v0, v1):
        parts.append(
            f"<text x='{6}' y='{Y(value) + 4}' font-family='sans-serif' font-size='11'>{value}</text>",
        )
        parts.append(
            f"<line x1='{margin}' y1='{Y(value)}' x2='{width - margin}' y2='{Y(value)}' "
            "stroke='#dddddd'/>",
        )
    for index in range(nplayers):
        points = " ".join(
            f"{X(t):.1f},{Y(counts[index]):.1f}" for t, counts in rows
            if len(counts) > index
        ).replace(" ", " ")
        parts.append(
            f"<polyline fill='none' stroke='{COLORS[index]}' stroke-width='1.6' points='{points}'/>"
        )
    for index in range(nplayers):
        y = 32 + 14 * index
        parts.append(
            f"<rect x='{width - 110}' y='{y - 9}' width='12' height='9' fill='{COLORS[index]}'/>"
            f"<text x='{width - 92}' y='{y}' font-family='sans-serif' font-size='11'>p{index + 1}</text>"
        )
    parts.append("</svg>")
    dest.write_text("\n".join(parts), encoding="utf-8")


def _ticks(v0, v1):
    step = max(1, (v1 - v0) // 6)
    value = v0 - v0 % step
    out = []
    while value <= v1:
        out.append(value)
        value += step
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", default="out/vill_pop.jsonl")
    ap.add_argument("--out-dir", default="out")
    ap.add_argument("--stem", default="vill_pop",
                    help="output file base name (vill_pop.csv / vill_pop.svg)")
    args = ap.parse_args()

    rows = read_rows(args.src)
    if not rows:
        print("no vill_pop rows found in", args.src, file=sys.stderr)
        sys.exit(1)
    nplayers = max(len(c) for _, c in rows)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(rows, out_dir / f"{args.stem}.csv", nplayers)
    write_svg(rows, out_dir / f"{args.stem}.svg", nplayers)
    print(f"{len(rows)} samples, t="
          f"{rows[0][0]:.0f}..{rows[-1][0]:.0f}s, {nplayers} players -> "
          f"{out_dir / (args.stem + '.csv')} + {out_dir / (args.stem + '.svg')}")


if __name__ == "__main__":
    main()
