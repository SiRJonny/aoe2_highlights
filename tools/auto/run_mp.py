"""Run the priority-highlight pipeline end-to-end on 5 MP Replay games.

Per game:
 1. bootstrap_row.py --file <replay>   (touch + game reboot + inject —
    the replay list is cached per boot, so a touch only lands on a boot)
 2. record_events.py --tag mpN --touch <replay> --collect
    (fast pass; mgz annotator clamps the plan to the true file length)
 3. if the plan has segments: record_events.py --tag mpN --record
    (fog_off + chase + per-segment recording with audio)
 4. verify clips exist with sane sizes

Usage:
    py -3.12 tools\\auto\\run_mp.py            # all 5 games
    py -3.12 tools\\auto\\run_mp.py --only 2   # one game
    py -3.12 tools\\auto\\run_mp.py --collect-only   # no recording
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parents[1]
SAVEGAME = (Path.home() / "Games" / "Age of Empires 2 DE"
            / "76561198014928708" / "savegame")

GAMES = [
    "MP Replay v101.103.57257.0 @2026.11.02 190017 (4).aoe2record",
    "MP Replay v101.103.57257.0 @2026.11.02 183902 (2).aoe2record",
    "MP Replay v101.103.57257.0 @2026.11.01 211553 (4).aoe2record",
    "MP Replay v101.103.57257.0 @2026.11.01 205106 (2).aoe2record",
    "MP Replay v101.103.57257.0 @2026.11.01 200634 (2).aoe2record",
]


def run(cmd, timeout=3600):
    print(f"$ {' '.join(str(c) for c in cmd)}", flush=True)
    r = subprocess.run([str(c) for c in cmd], capture_output=True,
                       text=True, encoding="utf-8", errors="replace",
                       timeout=timeout)
    if r.stdout.strip():
        print(r.stdout.strip()[-2000:], flush=True)
    if r.returncode != 0 and r.stderr.strip():
        print(r.stderr.strip()[-800:], file=sys.stderr, flush=True)
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", type=int, default=None,
                    help="1-based game index from GAMES to run alone")
    ap.add_argument("--collect-only", action="store_true",
                    help="bootstrap + collect + plan, never record")
    args = ap.parse_args()
    order = [args.only] if args.only else range(1, len(GAMES) + 1)

    summary = {}
    for idx in order:
        name = GAMES[idx - 1]
        target = SAVEGAME / name
        if not target.exists():
            sys.exit(f"missing replay: {target}")
        tag = f"mp{idx}"
        print(f"\n=== game {idx}/5: {name}", flush=True)

        boot = run([sys.executable, HERE / "bootstrap_row.py",
                    "--file", target])
        if boot.returncode != 0:
            summary[tag] = "BOOTSTRAP FAILED"
            continue

        coll = run([sys.executable, HERE / "record_events.py",
                    "--tag", tag, "--touch", target, "--collect"],
                   timeout=2700)
        if coll.returncode != 0:
            summary[tag] = "COLLECT FAILED"
            continue
        plan = json.loads((ROOT / "out" / tag / "plan.json")
                          .read_text(encoding="utf-8"))
        n = len(plan.get("segments", []))
        if args.collect_only:
            summary[tag] = f"collect-only: {n} segments planned"
            continue
        if n == 0:
            summary[tag] = "no events, no video (valid)"
            continue

        rec = run([sys.executable, HERE / "record_events.py",
                   "--tag", tag, "--record"], timeout=3600)
        if rec.returncode != 0:
            summary[tag] = "RECORD FAILED"
            continue
        clips = sorted((ROOT / "out" / tag / "clips").glob("*.mp4"))
        sizes = [f"{c.name} {c.stat().st_size // 1024}kB" for c in clips]
        summary[tag] = f"{n} segments -> {len(clips)} clips: {sizes}"

    print("\n=== summary", flush=True)
    for tag, msg in summary.items():
        print(f"  {tag}: {msg}", flush=True)


if __name__ == "__main__":
    main()
