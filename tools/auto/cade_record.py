"""Archive the CadeRemote Frames stream of a replay (CA-recording-like).

Streams FrameSequence messages into an .xz archive of length-prefixed
messages while the replay plays (usually chased at FASTEST). Optionally
aligns the archive against the CONTROL module's tick clock (t seconds)
so Frame.time (ms) can be mapped onto the event timeline.

Usage:
    py -3.12 tools/auto/cade_record.py --replay <path> --out <file.xz> \
        [--align] [--max-seconds 600] [--quiet-seconds 20]

Stop conditions: replay end (quiet stream), --max-seconds, Ctrl+C.
"""
from __future__ import annotations

import argparse
import json
import lzma
import struct
import sys
import threading
import time
from pathlib import Path

import grpc

sys.path.insert(0, str(Path(__file__).parent))

from aoe2hl import cade, cadeui, pipe as drive  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--align", action="store_true",
                    help="also record CONTROL module ticks for clock "
                         "alignment (module must be injected)")
    ap.add_argument("--max-seconds", type=float, default=600)
    ap.add_argument("--quiet-seconds", type=float, default=20,
                    help="no frames for this long => replay ended")
    ap.add_argument("--speed", default="FASTEST",
                    choices=["SLOW", "NORMAL", "FAST", "FASTEST"])
    args = ap.parse_args()

    cadeui.configure(True, args.replay)
    c = cadeui.client()
    log = print

    log(f"connecting module pipe ({'align' if args.align else 'no align'})")
    handle = drive.connect(30) if args.align else None
    if args.align and handle is None:
        sys.exit("no module pipe; cannot align")

    log(f"loading {Path(args.replay).name} via CadeRemote")
    if not cadeui.load_replay(args.replay):
        sys.exit("LoadReplay refused")

    # wait for the first frame (replay still loading); a paused/quiet
    # stream must not kill the archive loop, so wrap retries here
    got_first = False
    wait_end = time.time() + 180
    while time.time() < wait_end and not got_first:
        try:
            for seq in c.frames_raw(b"", timeout=20.0):
                if any(True for _ in cade.iter_frames(seq)):
                    got_first = True
                break
        except Exception:
            pass
        time.sleep(1.0)
    if not got_first:
        sys.exit("no frames after the load; aborting")

    cadeui.speed(args.speed)
    if args.align:
        handle = drive.connect(120) or handle
        if handle is None:
            sys.exit("module pipe did not come up after the load")

    ticks = []      # (wall, module_t) from the CONTROL module
    stop = threading.Event()

    def module_reader():
        last_t = None
        while not stop.is_set():
            rows = drive.read_for(handle, 1.0, quiet=True)
            for r in rows:
                t = r.get("t") or r.get("time")
                if r.get("type") == "tick" and t:
                    t = float(t)
                    if last_t is None or t != last_t:
                        ticks.append((time.time(), t))
                        last_t = t
    if args.align:
        threading.Thread(target=module_reader, daemon=True).start()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    meta_path = out.with_suffix(".meta.json")
    n_seq = n_frame = 0
    bytes_raw = 0
    samples = []    # (wall, frame_ms) every 25 frames
    t0 = time.time()
    quiet_last = time.time()
    comp = lzma.LZMAFile(open(out, "wb"), "wb", preset=6)
    import grpc

    def finish(reason):
        log(reason)
        raise SystemExit

    try:
        idle_deadlines = 0
        while True:
            got_frames_here = 0
            try:
                stream = c.frames_raw(
                    b"", timeout=args.quiet_seconds + 10)
                for seq in stream:
                    now = time.time()
                    quiet_last = now
                    idle_deadlines = 0
                    msg = struct.pack("<I", len(seq)) + seq
                    comp.write(msg)
                    bytes_raw += len(msg)
                    n_seq += 1
                    for t, patch, rev, nev, ncmd in cade.iter_frames(seq):
                        n_frame += 1
                        got_frames_here += 1
                        if n_frame % 25 == 0:
                            samples.append((now, t))
                    if now - t0 > args.max_seconds:
                        log(f"max-seconds {args.max_seconds} reached")
                        raise StopIteration
            except StopIteration:
                break
            except grpc.RpcError as e:
                if e.code() != grpc.StatusCode.DEADLINE_EXCEEDED:
                    raise
                # no message for quiet_seconds+10: replay ended, OR the
                # stream needs a re-subscribe (the RPC deadline is the
                # whole call). Two idle rounds in a row => end.
                idle_deadlines += 1
                log(f"stream idle ({idle_deadlines} rounds, "
                    f"{n_frame} frames so far)")
                if idle_deadlines >= 2:
                    log("stream quiet => replay end")
                    break
    except KeyboardInterrupt:
        log("interrupted")
    finally:
        stop.set()
        comp.close()

    meta = {
        "replay": str(args.replay),
        "speed": args.speed,
        "started": t0,
        "wall_seconds": time.time() - t0,
        "sequences": n_seq,
        "frames": n_frame,
        "raw_bytes": bytes_raw,
        "xz_bytes": out.stat().st_size,
        "time_samples": samples,
        "module_ticks": ticks[-5000:],
    }
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    log(f"archived {n_seq} sequences / {n_frame} frames "
        f"({bytes_raw / 1e6:.0f} MB raw -> {out.stat().st_size / 1e6:.0f} MB xz) "
        f"in {time.time() - t0:.0f}s")
    log(f"meta -> {meta_path}")

    if args.align and ticks:
        # compare module t (s) with the nearest frame sample (ms)
        errs = []
        si = 0
        for wall, t in ticks[::20]:
            while si + 1 < len(samples) and samples[si + 1][0] <= wall:
                si += 1
            if si < len(samples):
                fw, ft = samples[si]
                if abs(fw - wall) < 1.0:
                    errs.append(abs(ft / 1000.0 - t))
        if errs:
            errs.sort()
            n = len(errs)
            log(f"clock alignment |frame_ms/1000 - module_t| over {n} pairs: "
                f"med={errs[n // 2]:.3f}s p90={errs[int(n * 0.9)]:.3f}s "
                f"max={errs[-1]:.3f}s")


if __name__ == "__main__":
    main()
