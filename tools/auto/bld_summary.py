"""Summarize bld_event rows from a vill_pop.jsonl capture.

Usage:
    py -3.12 tools\\auto\\bld_summary.py [--in out\\vill_pop.jsonl]
"""
import argparse
import json


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", default="out/vill_pop.jsonl")
    args = ap.parse_args()

    rows = [json.loads(l) for l in open(args.src, encoding="utf-8") if l.strip()]
    bld = [r for r in rows if r.get("type") == "bld_event"]
    print("bld events:", len(bld))
    for r in bld:
        name = r.get("name") or "?"
        print(f"t={r['time']:6.1f} {r['event']:5} owner p{r['owner']} "
              f"{name} (id={r.get('id')}, {r['x']}:{r['y']})")

    stats = [r for r in rows if r.get("type") == "stats"]
    ages = [[row[0] for row in r["stats"][1:]] for r in stats]
    prev = None
    print("\nage timeline (players 1..8):")
    for r, a in zip(stats, ages):
        if a != prev:
            print(f"  t={r['time']:6.1f} {a}")
            prev = a

    houses = [r for r in bld if r.get("isHouse")]
    print(f"\nhouse events: {len(houses)} of {len(bld)}")


if __name__ == "__main__":
    main()
