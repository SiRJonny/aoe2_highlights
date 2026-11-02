"""Pipe client for an AoE2Control module.

Reads JSON envelopes from the module's named pipe (or the raw RMS/endpoint
style newline stream) and appends each row to a JSONL file.

Usage while the replay plays:
    py -3.12 tools\\aoe2control\\pipe_client.py [--pipe AoE2_Highlights] [--out out\\vill_pop.jsonl]

Stop with Ctrl+C. Rows are flushed to disk immediately.
"""
import argparse
import json
import sys
import time
from pathlib import Path

try:
    import pywintypes
    import win32file
    import win32pipe

    HAVE_PYWIN32 = True
except ImportError:
    HAVE_PYWIN32 = False


def connect(pipe_name, timeout=0):
    """Returns a pipe handle in message-read mode, retrying while the server isn't up."""
    deadline = time.time() + timeout
    while True:
        try:
            handle = win32file.CreateFile(
                pipe_name,
                win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                0,
                None,
                win32file.OPEN_EXISTING,
                0,
                None,
            )
        except pywintypes.error as exc:
            # 2: ERROR_FILE_NOT_FOUND (pipe doesn't exist yet)
            # 231: ERROR_PIPE_BUSY
            if exc.winerror in (2, 231) and (timeout == 0 or time.time() < deadline):
                time.sleep(0.5)
                continue
            raise
        win32pipe.SetNamedPipeHandleState(handle, win32pipe.PIPE_READMODE_MESSAGE, None, None)
        return handle


def read_writer(out_path):
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    handle = out_path.open("a", encoding="utf-8")
    return handle


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pipe", default="AoE2_Highlights")
    ap.add_argument("--out", default="out/vill_pop.jsonl")
    ap.add_argument("--wait", type=float, default=5.0, help="seconds to wait for pipe to appear")
    args = ap.parse_args()

    if not HAVE_PYWIN32:
        print("pip install pywin32 first", file=sys.stderr)
        sys.exit(2)
    if args.pipe.startswith("\\\\"):
        pipe_name = args.pipe
    else:
        pipe_name = r"\\.\pipe\{}".format(args.pipe)

    out = read_writer(args.out)
    print(f"listening on {pipe_name} -> {args.out}", flush=True)
    handle = connect(pipe_name, timeout=args.wait)

    n = 0
    try:
        while True:
            # message-mode read: one whole envelope per call
            try:
                result, data = win32file.ReadFile(handle, 4096)
            except pywintypes.error as exc:
                if exc.winerror in (109, 232):  # broken pipe / server gone
                    break
                raise
            chunk = data
            while result != 0:  # 234 (ERROR_MORE_DATA) = message continues
                try:
                    result, more = win32file.ReadFile(handle, 4096)
                except pywintypes.error as exc:
                    if exc.winerror in (109, 232):
                        result = 0
                        chunk = b""
                        break
                    raise
                chunk += more
            for line in chunk.decode("utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                env = json.loads(line)
                payload = env.get("payload", env)
                if isinstance(payload, dict):
                    src = env.get("source", {})
                    if src.get("settingsGroup"):
                        payload.setdefault("module", src["settingsGroup"])
                    out.write(json.dumps(payload) + "\n")
                    out.flush()
                    n += 1
                    if n % 10 == 0:
                        print(f"{n} rows", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        out.close()
        try:
            win32file.CloseHandle(handle)
        except Exception:
            pass
        print(f"saved {n} rows -> {args.out}")


if __name__ == "__main__":
    main()
