"""Two-pass highlight recorder.

Architecture: run the full replay once at FASTEST speed to collect the
timeline (module rows -> events jsonl -> plan json), then restart the
replay and record each planned segment at NORMAL speed. Video = monitor
1 via mss (1920x1080 @30fps, mp4v), audio = WASAPI loopback (system
output, no mic), muxed with the imageio-ffmpeg binary.

Pass 1 stops when game time stalls 20 s (replay ended) or --max-minutes.
Pass 2 restarts the replay via the UI driver, then, segments in time
order (never seeking backwards):
    F3 wake/unpause -> seek (t0-pad, pause on arrival, camera at tile)
    -> set NORMAL -> record video+audio until tick >= t1+pad -> F3 pause

The CONTROL panel stays open (GUI-only takes; the Shift-close toggle is
unreliable).

Usage:
    py -3.12 tools\\auto\\record_events.py --row 2 [--search TEXT]
        [--collect] [--record]          (default: both passes)
        [--events out\\events_row2.jsonl] [--plan out\\events_plan_row2.json]
        [--fps 30] [--max-minutes 40] [--out-dir out\\clips_row2]
"""
import argparse
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parents[1]

sys.path.insert(0, str(HERE))
import cv2  # noqa: E402
import numpy as np  # noqa: E402

import drive  # noqa: E402
import game  # noqa: E402
from clip_recorder import wait_seek_settled  # noqa: E402

STALL_S = 20.0         # no module time advance -> pass/segment ends
FFMPEG = None          # resolved lazily (imageio_ffmpeg)


def log(msg):
    print(msg, flush=True)


# --- pass 1: collect -------------------------------------------------


def collect(handle, events_path, max_minutes):
    """Stream module rows at FASTEST to events_path until time stalls."""
    drive.send(handle, {"action": "set_speed", "speedName": "FASTEST"})
    drive.read_for(handle, 3, quiet=True)

    deadline = time.time() + max_minutes * 60
    last_t = None
    last_advance = time.time()
    kept = 0
    with open(events_path, "w", encoding="utf-8") as fh:
        while time.time() < deadline:
            for r in drive.read_for(handle, 5, quiet=True):
                t = r.get("time")
                if t and (last_t is None or t > last_t):
                    last_t = t
                    last_advance = time.time()
                if r.get("type") == "stats":
                    continue        # big rows, not needed for selection
                fh.write(json.dumps(r) + "\n")
                kept += 1
            if last_t and last_t > 60 and time.time() - last_advance > STALL_S:
                log(f"collect: time stalled at t={last_t:.0f} -> replay "
                    f"-ended, {kept} rows")
                break
    return last_t


# --- audio -----------------------------------------------------------


def capture_loopback(stop_evt, wav_path):
    """Record the default system output (WASAPI loopback) to a wav."""
    import sounddevice as sd
    import soundfile as sf

    out_idx = sd.default.device[1]
    info = sd.query_devices(out_idx)
    sr = int(info["default_samplerate"])
    ch = min(2, info["max_output_channels"])
    try:
        extra = sd.WasapiSettings(loopback=True)
    except Exception:
        extra = None
    frames = []

    def cb(indata, fframes, _t, _status):
        frames.append(indata.copy())

    try:
        with sd.InputStream(samplerate=sr, channels=ch, device=out_idx,
                            dtype="float32", extra_settings=extra,
                            callback=cb, blocksize=0):
            while not stop_evt.is_set():
                time.sleep(0.05)
    except Exception as exc:            # noqa: BLE001 (best-effort audio)
        log(f"audio: capture failed ({exc}); video-only segment")
        return

    if frames:
        import numpy as np
        data = np.concatenate(frames)
        sf.write(wav_path, data, sr)
        log(f"audio: {len(data) / sr:.1f} s -> {wav_path}")


def mux(video_path, wav_path, out_path):
    global FFMPEG
    if FFMPEG is None:
        import imageio_ffmpeg
        FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [FFMPEG, "-y", "-i", str(video_path), "-i", str(wav_path),
           "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", str(out_path)]
    subprocess.run(cmd, capture_output=True)
    if out_path.exists() and out_path.stat().st_size > 0:
        video_path.unlink(missing_ok=True)
        wav_path.unlink(missing_ok=True)
        return True
    return False


# --- pass 2: record --------------------------------------------------


def record_segment(handle, seg, pad, out_path, fps):
    """Record one segment: already-seeked & running; stops on tick."""
    t1p = seg["t1"] + pad
    stop = threading.Event()
    wav_path = out_path.with_suffix(".audio.wav")
    audio = threading.Thread(target=capture_loopback,
                             args=(stop, wav_path), daemon=True)
    audio.start()

    writer = cv2.VideoWriter(str(out_path), cv2.VideoWriter_fourcc(
        *"mp4v"), fps, (1920, 1080))
    if not writer.isOpened():
        stop.set()
        sys.exit("VideoWriter failed to open")

    t_cur = 0.0
    last_advance = time.time()
    frames = 0
    frame_interval = 1.0 / fps
    next_frame = time.time()
    with __import__("mss").mss() as sct:
        mon = sct.monitors[1]
        while t_cur < t1p:
            for r in drive.read_now(handle):
                t = r.get("time")
                if t and t > t_cur:
                    t_cur = t
                    last_advance = time.time()
            if time.time() - last_advance > STALL_S:
                log(f"record: time stalled at t={t_cur:.0f} (replay may "
                    f"have ended); stopping segment early")
                break
            next_frame += frame_interval
            if next_frame < time.time():
                next_frame = time.time()
            img = np.array(sct.grab(mon))
            writer.write(cv2.cvtColor(img, cv2.COLOR_BGRA2BGR))
            frames += 1
            sleep = next_frame - time.time()
            if sleep > 0:
                time.sleep(sleep)
    writer.release()
    stop.set()
    audio.join(timeout=5)

    game.focus_window()
    time.sleep(0.3)
    game.pyautogui.press("f3")          # pause the replay again
    time.sleep(1)
    if mux(out_path, wav_path, out_path):
        log(f"segment {out_path.name}: {frames} frames, done t={t_cur:.1f}")
    else:
        log(f"segment {out_path.name}: mux failed, video-only kept")


def record_plan(handle, plan, out_dir, fps):
    segs = plan["segments"]
    if not segs:
        log("plan has no segments; nothing to record")
        return
    out_dir.mkdir(parents=True, exist_ok=True)
    pad = plan.get("pad", 2.0)
    for i, seg in enumerate(segs):
        t0p = seg["t0"] - pad
        x, y = int(seg["x"]), int(seg["y"])
        name = (f"clip_row{plan.get('row', '?')}_{i + 1:02d}_{seg['kind']}"
                f"_t{int(seg['t0'])}.mp4")
        out_path = out_dir / name
        log(f"segment {i + 1}/{len(segs)} {seg['kind']}: "
            f"t={t0p:.0f}..{seg['t1'] + pad:.0f} tile {x}:{y}")
        drive.send(handle, {"action": "seek", "t": t0p, "x": x, "y": y,
                            "pause": True, "camera": True})
        if not wait_seek_settled(handle, t0p, max_s=900):
            log("  seek never settled; skipping segment")
            continue
        # awake? no: arrival paused the game and froze the module. F3
        # unpauses, then set normal speed right away.
        game.pyautogui.press("f3")
        time.sleep(0.5)
        drive.send(handle, {"action": "set_speed", "speedName": "NORMAL"})
        drive.read_for(handle, 2, quiet=True)
        record_segment(handle, seg, pad, out_path, fps)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--row", type=int, required=True,
                    help="which replay profile to run (1-9); resolves to a "
                         "touch/copy file ('hl<N> replay copy.aoe2record') "
                         "that is stamped newest so the UI loads it as "
                         "list row 1")
    ap.add_argument("--search", default=None)
    ap.add_argument("--collect", action="store_true")
    ap.add_argument("--record", action="store_true")
    ap.add_argument("--events", default=None)
    ap.add_argument("--plan", default=None)
    ap.add_argument("--fps", type=float, default=30)
    ap.add_argument("--max-minutes", type=float, default=40)
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--force-inject", action="store_true",
                    help="unload the running CONTROL (Delete) and inject "
                         "the current module before pass 1")
    ap.add_argument("--touch", default=None,
                    help="replay file to os.utime just before pass 1 "
                         "(pushes its copy to row 1 of the list)")
    args = ap.parse_args()

    do_collect = args.collect or not (args.collect or args.record)
    do_record = args.record or not (args.collect or args.record)

    tag = f"row{args.row}"
    events_path = Path(args.events or ROOT / "out" / f"events_{tag}.jsonl")
    plan_path = Path(args.plan or ROOT / "out" / f"events_plan_{tag}.json")
    out_dir = Path(args.out_dir or ROOT / "out" / f"clips_{tag}")

    if do_collect:
        log("== pass 1: collect (replay at full speed)")
        if args.touch is None:
            # profiles: pre-made copy 'hl<N> replay copy.aoe2record'
            cand = Path.home() / "Games" / "Age of Empires 2 DE"
            for d in cand.glob("*/savegame"):
                hit = d / f"hl{args.row} replay copy.aoe2record"
                if hit.exists():
                    args.touch = str(hit)
                    break
        if args.touch:
            import os
            os.utime(args.touch)        # newest mtime -> row 1 in the UI
            log(f"touched {args.touch}")
        if args.force_inject:
            log("swapping CONTROL module ...")
            game.focus_window()
            game.pyautogui.keyDown("delete")
            game.pyautogui.keyUp("delete")
            time.sleep(4)
            import pipeline  # noqa: F401  (guarded main)
            pipeline.inject()
            time.sleep(4)
        if not start_replay(args.row, args.search):
            sys.exit("UI start failed")
        time.sleep(4)
        handle = drive.connect(120)
        t_end = collect(handle, events_path, args.max_minutes)
        log(f"events -> {events_path} (last t={t_end})")
        plan = build_plan(events_path, args.row, plan_path)
    else:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))

    if do_record and plan.get("segments"):
        log("== pass 2: record segments (replay at normal speed)")
        if not start_replay(args.row, args.search):
            sys.exit("UI start failed")
        time.sleep(4)
        handle = drive.connect(120)
        record_plan(handle, plan, out_dir, args.fps)


def start_replay(row, search):
    row = 1                            # copies/touch always land on row 1
    ui_cmd = [sys.executable, str(HERE / "start_replay.py")]
    if search:
        ui_cmd += ["--search", search]
    ui_cmd += ["--row", str(row)]
    ui = subprocess.run(ui_cmd, capture_output=True, text=True,
                        encoding="utf-8", errors="replace")
    log(ui.stdout.strip())
    return ui.returncode == 0


def build_plan(events_path, row, plan_path):
    sel_cmd = [sys.executable, str(HERE / "select_events.py"),
               str(events_path), "--out", str(plan_path), "--row", str(row)]
    sel = subprocess.run(sel_cmd, capture_output=True, text=True,
                         encoding="utf-8", errors="replace")
    log(sel.stdout.strip())
    if sel.returncode != 0:
        print(sel.stderr, file=sys.stderr)
        return {"row": row, "segments": []}
    return json.loads(plan_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
