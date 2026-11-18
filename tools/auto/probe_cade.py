"""Live probe for AoE2DE's built-in CadeRemote gRPC server (port 4341).

Run while the game is up. Subcommands map 1:1 to single RPC experiments
so results can be observed on screen (and via screenshot) between calls.

Usage:
    py -3.12 tools/auto/probe_cade.py port
    py -3.12 tools/auto/probe_cade.py info
    py -3.12 tools/auto/probe_cade.py pause 1|0
    py -3.12 tools/auto/probe_cade.py fog 1|0
    py -3.12 tools/auto/probe_cade.py persp <playerId>
    py -3.12 tools/auto/probe_cade.py speed <float|enum index> [--watch N]
    py -3.12 tools/auto/probe_cade.py frames <seconds> [outfile.xz]
    py -3.12 tools/auto/probe_cade.py watch <seconds>
    py -3.12 tools/auto/probe_cade.py restart
    py -3.12 tools/auto/probe_cade.py loadreplay <path>
    py -3.12 tools/auto/probe_cade.py cancel
    py -3.12 tools/auto/probe_cade.py access
    py -3.12 tools/auto/probe_cade.py shot <tag>
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from aoe2hl import cade  # noqa: E402


def log(*a):
    print(*a, flush=True)


def port():
    out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True).stdout
    hits = [l for l in out.splitlines() if ":4341" in l]
    print("\n".join(hits) if hits else "no :4341 listener")


def shot(tag: str):
    import mss
    out_dir = Path("out/probe_cade")
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / f"{tag}_{int(time.time())}.png"
    with mss.mss() as s:
        s.shot(mon=1, output=str(p))
    log("shot ->", p)


def _new() -> cade.Cade:
    t0 = time.time()
    c = cade.Cade()
    log(f"connected to {c.target} in {time.time() - t0:.1f}s")
    return c


def only_tag(args: list[str]) -> int | None:
    if "--tag" in args:
        return int(args[args.index("--tag") + 1])
    return None


def _resp(label: str, r):
    if isinstance(r, bytes):
        log(f"[{label}] {len(r)}B", cade.pb_dump(r) if r else "(empty)")
    else:
        log(f"[{label}] {r.decode(errors='replace')}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    cmd = sys.argv[1]
    args = sys.argv[2:]

    if cmd == "port":
        port()
        return 0
    if cmd == "shot":
        shot(args[0])
        return 0

    c = _new()
    try:
        if cmd == "info":
            r = c.info()
            _resp("Info", r)
        elif cmd == "pause":
            _resp(f"Pause({args[0]})", c.pause(args[0] == "1"))
        elif cmd == "fog":
            _resp(f"SetFogOfWar({args[0]})", c.set_fog(args[0] == "1"))
        elif cmd == "persp":
            _resp(f"SetPerspective({args[0]})", c.set_perspective(int(args[0])))
        elif cmd == "speed":
            val = args[0]
            only = None
            if "--enc" in args:
                only = (args[args.index("--enc") + 1],)
            kind = "float" if "." in val else "varint"
            encs = {kind: float(val) if kind == "float" else int(val)}
            for kind, v in encs.items():
                for label, r in c.set_game_speed(v, encodings=only or ("f1_float", "f1_double", "f1_varint")).items():
                    _resp(f"SetGameSpeed({kind} {v}) {label}", r)
            if "--watch" in args:
                secs = float(args[args.index("--watch") + 1])
                times = c.watch_time(secs)
                log(f"frames in {secs}s: {len(times)}; last times {times[-5:]}")
        elif cmd == "speedtest":
            import struct as _s
            def rate(secs=2.0):
                t = c.watch_time(secs)
                if len(t) < 2:
                    return 0.0, 0
                return (t[-1] - t[0]) / 1000.0 / secs, len(t)
            cands = []
            for v in (0, 1, 2, 3):
                cands.append((f"f1_varint_{v}", cade.pb_int(1, v)))
            cands.append(("f1_float_1.0", cade.pb_float(1, 1.0)))
            cands.append(("f1_float_13.52", cade.pb_float(1, 13.52)))
            base, n0 = rate()
            log(f"baseline rate {base:.3f} game-s/s ({n0} frames)")
            for label, payload in cands:
                r = c.unary(cade.SPEED, payload)
                a, na = rate()
                log(f"{label}: resp={cade.pb_dump(r) if r else '(empty)'} "
                    f"rate {a:.3f} ({na} frames)")
        elif cmd == "speedscan":
            import struct as _s
            cands = []
            for v in (0, 1, 2, 3):
                cands.append((f"f1_varint_{v}", cade.pb_int(1, v)))
            for v in (1.0, 4.0, 13.52):
                cands.append((f"f1_float_{v}", cade.pb_float(1, v)))
                cands.append((f"f1_double_{v}", cade.pb_double(1, v)))
            for v in (1.0, 13.52):
                cands.append((f"f2_float_{v}", cade.pb_float(2, v)))
            cands.append(("f2_varint_3", cade.pb_int(2, 3)))
            cands.append(("f3_varint_3", cade.pb_int(3, 3)))
            for label, payload in cands:
                try:
                    r = c.unary(cade.SPEED, payload)
                except grpc.RpcError as e:  # noqa: F821
                    log(label, "ERR", e.code().name, e.details())
                    continue
                log(label, "->", cade.pb_dump(r) if r else "(empty)")
        elif cmd == "watch":
            times = c.watch_time(float(args[0]))
            log(f"frame times sample: head={times[:5]} tail={times[-5:]} "
                f"n={len(times)}")
        elif cmd == "revscan":
            import struct as _s
            import lzma
            for fnum in range(4, 9):
                req = cade.pb_bool(fnum, True)
                rev_total = 0
                n = 0
                end = time.time() + 3
                try:
                    for seq in c.frames_raw(req, timeout=secs + 10):
                        for t, patch, rev, nev, ncmd in cade.iter_frames(seq):
                            n += 1
                            rev_total += len(rev)
                        if time.time() > end:
                            break
                except grpc.RpcError as e:
                    log(f"f{fnum}=true: ERR {e.code().name}")
                    continue
                log(f"f{fnum}=true: frames={n} reverse_bytes={rev_total}")
        elif cmd == "frames":
            secs = float(args[0])
            out = Path(args[1]) if len(args) > 1 else None
            req = b""
            if "--noparticles" in args:
                req += cade.pb_bool(3, True)
            if "--nocommands" in args:
                req += cade.pb_bool(2, True)
            if "--res" in args:
                spec = args[args.index("--res") + 1]
                cats = {"TIME_STEP": 0, "LOW_PRIORITY": 1, "SLEEPING_UNIT": 2,
                        "PARTICLES": 3, "GAME_OPTIONS": 4,
                        "PARTICLES_SIMULATION_OUTPUT": 5}
                for ent in spec.split(","):
                    k, v = ent.split("=")
                    req += cade.pb_msg(1, cade.pb_int(1, cats[k]) + cade.pb_int(2, int(v)))
            end = time.time() + secs
            n_seq = n_frame = 0
            bytes_in = 0
            patch_b = rev_b = 0
            events = cmds = 0
            fileobj = open(out, "wb") if out else None
            import lzma
            comp = lzma.LZMAFile(fileobj, "wb", preset=6) if fileobj else None
            import struct as _s
            for seq in c.frames_raw(req, timeout=secs + 10):
                n_seq += 1
                msg = _s.pack("<I", len(seq)) + seq
                bytes_in += len(msg)
                if comp:
                    comp.write(msg)
                for t, patch, rev, nev, ncmd in cade.iter_frames(seq):
                    n_frame += 1
                    patch_b += len(patch)
                    rev_b += len(rev)
                    events += nev
                    cmds += ncmd
                if time.time() > end:
                    break
            if comp:
                comp.close()
            if fileobj:
                fileobj.close()
                log(f"saved {out} ({out.stat().st_size} B xz)")
            log(f"{n_seq} sequences, {n_frame} frames in {secs}s; "
                f"wire {bytes_in / secs:.0f} B/s; patch {patch_b / max(1, n_frame):.0f} "
                f"B/frame; reverse {rev_b / max(1, n_frame):.0f} B/frame; "
                f"events {events}; commands {cmds}")
        elif cmd == "restart":
            for label, r in c.load_game_probe("restart", tag=only_tag(args)).items():
                _resp(f"LoadGame {label} RestartAsReplay", r)
        elif cmd == "cancel":
            for label, r in c.load_game_probe("cancel", tag=only_tag(args)).items():
                _resp(f"LoadGame {label} Cancel", r)
        elif cmd == "loadreplay":
            for label, r in c.load_game_probe("loadreplay", args[0],
                                              tag=only_tag(args[1:])).items():
                _resp(f"LoadGame {label} LoadReplay({args[0]})", r)
        elif cmd == "access":
            _resp("AccessFlag", c.access_flag())
        else:
            print(__doc__)
            return 2
    finally:
        c.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
