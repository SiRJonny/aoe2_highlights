"""One-command capture pipeline.

1. start the pipe client (background, waits for AoE2_Highlights)
2. inject CONTROL with settings + module overrides (only when not running)
3. drive the game UI to start a replay
4. block until the module reports `done`, then run the converter

Usage:
    py -3.12 tools\\auto\\pipeline.py [--search <text>] [--row N] [--max-minutes 60]
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / "AoE2Control_1.1.5" / "AoE2Control.exe"
SETTINGS_OVERRIDE = ROOT / "tools" / "aoe2control" / "settings.villpop.ini"
MODULE_OVERRIDE = ROOT / "tools" / "aoe2control" / "vill_pop_recorder"
REPLAY_SCRIPT = ROOT / "tools" / "auto" / "start_replay.py"
CLIENT_SCRIPT = ROOT / "tools" / "aoe2control" / "pipe_client.py"
CONVERTER = ROOT / "tools" / "aoe2control" / "villpop_to_csv.py"

OUT_JSONL = ROOT / "out" / "vill_pop.jsonl"


def control_running() -> bool:
    script = (
        "import win32file, pywintypes\n"
        "try:\n"
        "    h = win32file.CreateFile('\\\\\\\\.\\\\pipe\\\\AoE2ControlRmsIdeV1',"
        " 0x80000000 | 0x40000000, 0, None, 3, 0, None)\n"
        "    print('yes')\n"
        "except pywintypes.error:\n"
        "    print('no')\n"
    )
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    return out.stdout.strip() == "yes"


def inject() -> None:
    if control_running():
        print("CONTROL already running; skip injection")
        return
    args = [
        str(LAUNCHER), "--headless", "--timeout-ms", "180000",
        "--override-settings", str(SETTINGS_OVERRIDE),
        "--override-module", str(MODULE_OVERRIDE),
    ]
    print("injecting CONTROL ...")
    result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    print(result.stdout.strip())
    if result.returncode != 0:
        sys.exit(f"injection failed, exit {result.returncode}")


def wait_for_done(max_seconds: float) -> dict:
    """Poll the client output jsonl for a `done` row; return merged stats."""
    deadline = time.time() + max_seconds
    seen = 0
    last_text = 0
    while time.time() < deadline:
        if OUT_JSONL.exists():
            recs = []
            for line in OUT_JSONL.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    recs.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            done = [r for r in recs if r.get("type") == "done"]
            sample = [r for r in recs if r.get("type") == "vill_pop"]
            if done:
                return {
                    "samples": len(sample),
                    "rows_total": len(recs),
                    "first_time": sample[0].get("time") if sample else None,
                    "last_time": sample[-1].get("time") if sample else None,
                    "last_counts": sample[-1].get("counts") if sample else None,
                }
            if len(sample) != seen:
                seen = len(sample)
                now = time.time()
                if now - last_text > 60:
                    last_text = now
                    t = sample[-1].get("time", 0) if sample else 0
                    print(f"  sampling went on, {seen} samples, game t={t:.0f}s")
        time.sleep(2)
    sys.exit(f"no `done` row within {max_seconds}s")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--search", default=None)
    ap.add_argument("--row", type=int, default=1)
    ap.add_argument("--max-minutes", type=float, default=60)
    args = ap.parse_args()

    OUT_JSONL.unlink(missing_ok=True)

    inject()

    print("starting pipe client ...")
    client = subprocess.Popen(
        [sys.executable, str(CLIENT_SCRIPT), "--out", str(OUT_JSONL), "--wait", "300"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
    )

    replay_cmd = [sys.executable, str(REPLAY_SCRIPT)]
    if args.search:
        replay_cmd += ["--search", args.search]
    if args.row != 1:
        replay_cmd += ["--row", str(args.row)]
    print("driving UI to start replay ...")
    ui = subprocess.run(replay_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    print(ui.stdout.strip())
    if ui.returncode != 0:
        client.terminate()
        sys.exit(f"UI flow failed at step above (see out/auto_debug)")

    print("waiting for replay to finish (samples live in out/vill_pop.jsonl)")
    stats = wait_for_done(max_seconds=args.max_minutes * 60)
    client.terminate()
    print("done:", json.dumps(stats))

    stem = time.strftime("villpop_%Y%m%d_%H%M%S")
    conv = subprocess.run(
        [sys.executable, str(CONVERTER), "--stem", stem],
        capture_output=True, text=True)
    print(conv.stdout.strip(), conv.stderr.strip())


if __name__ == "__main__":
    main()
