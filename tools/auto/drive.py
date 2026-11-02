"""Send commands to timeline_recorder over the AoE2_Highlights pipe.

Usage:
    py -3.12 tools\\auto\\drive.py ping
    py -3.12 tools\\auto\\drive.py seek --t 106 --x 109 --y 117
    py -3.12 tools\\auto\\drive.py camera --x 109 --y 117
    py -3.12 tools\\auto\\drive.py pause | unpause | speed --v 3
    py -3.12 tools\\auto\\drive.py watch --seconds 30   (print rows)
"""
import argparse
import json
import sys
import time

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))

import game
import pywintypes
import win32file
import win32pipe

PIPE_NAME = r"\\.\pipe\AoE2_Highlights"


def connect(wait_s=60):
    import time
    deadline = time.time() + wait_s
    last = None
    while time.time() < deadline:
        try:
            handle = win32file.CreateFile(
                PIPE_NAME,
                win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                0, None, win32file.OPEN_EXISTING, 0, None)
            win32pipe.SetNamedPipeHandleState(
                handle, win32pipe.PIPE_READMODE_MESSAGE, None, None)
            return handle
        except pywintypes.error as exc:
            last = exc
            time.sleep(0.5)
    print(f"connect failed: {last}", file=sys.stderr)
    sys.exit(2)


def send(handle, payload, target_module="timeline_recorder"):
    msg = {"target": {"moduleName": target_module}, "payload": payload}
    win32file.WriteFile(handle, json.dumps(msg).encode("utf-8"))


def _parse_chunk(chunk, rows, quiet):
    for line in chunk.decode("utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            env = json.loads(line)
        except json.JSONDecodeError:
            continue
        payload = env.get("payload", env)
        if isinstance(payload, dict):
            payload.setdefault("module", env.get("source", {}).get(
                "settingsGroup"))
            rows.append(payload)
            if not quiet:
                print(json.dumps(payload))
                sys.stdout.flush()


def read_now(handle):
    """Drain whatever is pending on the pipe, without waiting."""
    rows = []
    while True:
        try:
            _state, _total, avail = win32pipe.PeekNamedPipe(handle, 0)
        except pywintypes.error as exc:
            if exc.winerror in (109, 233):   # pipe broken/closed
                return rows
            raise
        if avail == 0:
            return rows
        try:
            result, data = win32file.ReadFile(handle, 65536)
            chunk = data
            while result != 0:
                result, more = win32file.ReadFile(handle, 65536)
                chunk += more
        except pywintypes.error as exc:
            if exc.winerror in (109, 232, 233):
                return rows
            raise
        _parse_chunk(chunk, rows, quiet=True)


def read_for(handle, timeout_s, quiet=False):
    """Read rows for up to timeout_s without blocking past the deadline.

    PeekNamedPipe first: ReadFile itself would block indefinitely when no
    message is pending (the old hang)."""
    deadline = time.time() + timeout_s
    rows = []
    while time.time() < deadline:
        try:
            state, total, avail = win32pipe.PeekNamedPipe(handle, 0)
        except pywintypes.error as exc:
            if exc.winerror in (109, 233):   # pipe broken/closed
                break
            raise
        if avail == 0:
            time.sleep(0.2)
            continue
        try:
            result, data = win32file.ReadFile(handle, 65536)
            chunk = data
            while result != 0:
                result, more = win32file.ReadFile(handle, 65536)
                chunk += more
        except pywintypes.error as exc:
            if exc.winerror in (109, 232, 233):
                break
            raise
        _parse_chunk(chunk, rows, quiet)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=[
        "ping", "seek", "camera", "pause", "unpause", "speed", "watch",
        "probe"])
    ap.add_argument("--t", type=float, default=None)
    ap.add_argument("--x", type=float, default=None)
    ap.add_argument("--y", type=float, default=None)
    ap.add_argument("--v", type=float, default=None)
    ap.add_argument("--unit-type", type=float, default=None,
                    help="probe: UnitObjectType id to inspect")
    ap.add_argument("--seconds", type=float, default=30,
                    help="watch: seconds to stream")
    args = ap.parse_args()

    game.focus_window()
    handle = connect()

    if args.action == "watch":
        read_for(handle, args.seconds)
        return

    send(handle, {
        "action": args.action,
        "t": args.t, "x": args.x, "y": args.y, "v": args.v,
        "unitType": args.unit_type,
    })

    # read the ack (and whatever comes meanwhile)
    read_for(handle, 8)


if __name__ == "__main__":
    main()
