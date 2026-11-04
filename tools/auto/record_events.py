"""Two-pass highlight recorder.

Architecture: run the full replay once at FASTEST speed to collect the
timeline (module rows -> events jsonl -> plan json), then restart the
replay and record each planned segment at NORMAL speed. Video = monitor
1 via mss (1920x1080 @30fps, mp4v), audio = WASAPI loopback (system
output, no mic), muxed with the imageio-ffmpeg binary.

Pass 1 stops when game time stalls 20 s (replay ended) or --max-minutes.
Pass 2 restarts the replay via the UI driver, then, segments in time
order (never seeking backwards):
    seek (t0-pad; NO pause on arrival — the game NEVER stops moving:
    camera+NORMAL land while running) -> record video+audio until
    t1+pad -> next segment immediately (no F3 pause between takes).
Close segments (<=10 game-s gap, <=40 tiles) film the gap as a camera
PAN to the next tile instead of seek-teleporting; far gaps seek at
FASTEST with the game left running.
The CONTROL overlay is Shift-closed at pass start (anchor-verified).

Usage:
    py -3.12 tools\\auto\\record_events.py --row 2 [--search TEXT]
        [--collect] [--record]          (default: both passes)
        [--events out\\events_row2.jsonl] [--plan out\\events_plan_row2.json]
        [--fps 30] [--max-minutes 40] [--out-dir out\\clips_row2]
"""
import argparse
import json
import os
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
PAN_MAX_GAP = 10.0     # max game-s between takes for a pan transition
PAN_MAX_TILES = 40.0   # max tile distance for a pan transition
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


def _pick_wasapi_output():
    """A working WASAPI output device for loopback capture."""
    import sounddevice as sd
    try:
        name = sd.query_devices(sd.default.device[1])["name"]
    except Exception:
        name = ""
    devs = sd.query_devices()
    hostapis = {i: h["name"] for i, h in enumerate(sd.query_hostapis())}
    wasapi_outs = [(i, d) for i, d in enumerate(devs)
                   if d["max_output_channels"]
                   and hostapis.get(d["hostapi"], "").endswith("WASAPI")]
    def names_match(a, b):
        a, b = (a or "").strip(), (b or "").strip()
        return a == b or (a.startswith(b.split("(")[0].strip())
                          or b.startswith(a.split("(")[0].strip()))
    for i, d in wasapi_outs:            # default output first (names may
        if names_match(d["name"], name):  # differ slightly per hostapi)
            return i, d
    if wasapi_outs:
        return wasapi_outs[0]
    return None, None


def _pick_loopback():
    """Loopback device mirroring the default WASAPI output, or None."""
    try:
        import pyaudiowpatch as pyaudio
    except ImportError:
        return None
    p = pyaudio.PyAudio()
    try:
        api = p.get_host_api_info_by_type(pyaudio.paWASAPI)
        d = p.get_device_info_by_index(api["defaultOutputDevice"])
        if not d.get("isLoopbackDevice"):
            for lb in p.get_loopback_device_info_generator():
                if d["name"].split(" (")[0] in lb["name"]:
                    d = lb
                    break
        if not d.get("isLoopbackDevice"):
            lb = next(iter(p.get_loopback_device_info_generator()), None)
            if lb is None:
                return None
            d = lb
        return p, d
    except Exception:
        p.terminate()
        return None


def capture_loopback(stop_evt, wav_path):
    """Record the default system output (WASAPI loopback) to a wav."""
    picked = _pick_loopback()
    if picked is None:
        log("audio: no WASAPI loopback device; video-only segment")
        return
    import pyaudiowpatch as pyaudio
    p, dev = picked
    frames = []
    stream = p.open(format=pyaudio.paInt16,
                    channels=int(dev["maxInputChannels"]),
                    rate=int(dev["defaultSampleRate"]), input=True,
                    input_device_index=dev["index"], frames_per_buffer=512)
    try:
        while not stop_evt.is_set():
            frames.append(stream.read(512, exception_on_overflow=False))
    finally:
        stream.stop_stream()
        stream.close()
        p.terminate()
    if frames:
        import wave
        data = b"".join(frames)
        with wave.open(str(wav_path), "wb") as w:
            w.setnchannels(int(dev["maxInputChannels"]))
            w.setsampwidth(2)
            w.setframerate(int(dev["defaultSampleRate"]))
            w.writeframes(data)
        log(f"audio: {len(data)//4 / int(dev['defaultSampleRate']):.1f} s "
            f"-> {wav_path.name}")


def mux(video_path, wav_path, out_path, audio_ss=None, audio_span=None):
    global FFMPEG
    if FFMPEG is None:
        import imageio_ffmpeg
        FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
    tmp = out_path.with_suffix(".merged.mp4")
    # the wav is armed before the video writer and joined after it, so it
    # is longer on both ends — trim it to the video's wall span and cap
    # the output at the shorter stream (else the tail shows a frozen
    # last frame over trailing audio: a ~2 s "pause" at clip end)
    cmd = [FFMPEG, "-y", "-i", str(video_path)]
    if audio_ss is not None:
        cmd += ["-ss", f"{max(0.0, audio_ss):.3f}"]
        if audio_span is not None:
            cmd += ["-t", f"{max(0.0, audio_span):.3f}"]
    cmd += ["-i", str(wav_path), "-c:v", "copy", "-c:a", "aac",
            "-b:a", "128k", "-shortest", str(tmp)]
    proc = subprocess.run(cmd, capture_output=True)
    if tmp.exists() and tmp.stat().st_size > 0:
        os.replace(tmp, out_path)
        wav_path.unlink(missing_ok=True)
        if video_path != out_path:
            video_path.unlink(missing_ok=True)
        return True
    if proc.returncode != 0:
        log(f"mux stderr: {proc.stderr.decode('utf-8', 'replace')[-400:]}")
    return False


# --- pass 2: record --------------------------------------------------


def close_control_panel():
    """Hide the CONTROL overlay (Shift toggle) so takes show only gameplay.

    Idempotent: toggles only when the panel anchor is visible. The
    overlay sits at a fixed screen position, so the template match is
    reliable; the earlier 'unreliable' verdict came from blind toggles
    (double-toggle left it open). Verified anchor: 0.96+ across games."""
    if not game.hit_anchor("control_panel"):
        log("control panel: already hidden")
        return
    game.focus_window()
    time.sleep(0.3)
    for _ in range(2):
        game.pyautogui.keyDown("shift")
        time.sleep(0.12)
        game.pyautogui.keyUp("shift")
        time.sleep(0.8)
        if not game.hit_anchor("control_panel"):
            log("control panel: closed")
            return
    log("control panel: STILL visible (takes will show it)")


def fog_off():
    """Disable fog in the spectator replay panel (Spectator Mode=true).

    Fog is ON at every replay load. Detector: the minimap area goes dark
    under fog (calibrated: 73% dark pixels with fog vs 27% without).
    Clicks the 'All Visible' checkbox only when needed."""
    import cv2
    m = game._mon()
    mm = game.dump_screen()[900 - m["top"]:1060 - m["top"],
                            1630 - m["left"]:1910 - m["left"]]
    dark = (mm.mean(axis=2) < 60).mean() * 100
    if dark < 50:
        log(f"fog already off (minimap dark {dark:.0f}%)")
        return
    hit = game.locate_anchor("replay_all_visible", threshold=0.80)
    if not hit:
        game.save_debug("fail_all_visible_anchor")
        log("WARNING: All Visible anchor not found; recording with fog")
        return
    game.focus_window()
    time.sleep(0.3)
    game.press_click(hit[0], hit[1])
    time.sleep(2)
    log(f"fog off clicked (was {dark:.0f}% dark)")


def record_segment(handle, seg, pb, pa, out_path, fps, max_t=None,
                   pan=None, mux_queue=None):
    """Record one segment. The game is NEVER paused around takes.

    The seek settle branch lands CAMERA + NORMAL replay speed while the
    game is still RUNNING (engine-side; the chase proves SetReplaySpeed
    is accepted then) and with pause=False it does not pause at all —
    so this function only verifies advancement (running()) and presses
    F3 only when the game is actually frozen. No speed command here:
    sent while paused the engine IGNORES it while the module acks it
    anyway (that misleading ack cost two takes).
    pan=(x0, y0, from_t, until_t): film the lead-in as a camera GLIDE
    from the previous take's tile — while game time runs from from_t to
    until_t the camera interpolates toward the segment tile (close
    segments get a pan instead of a jump cut; the game keeps moving).
    Returns (replay-head time where the take ended, frames written);
    the ffmpeg mux is DEFERRED via mux_queue so the gap between takes
    stays filmed. max_t: replay length — the stop time is clamped just
    under it so end-of-game segments never film the FINISHED screen."""
    x, y = int(seg["x"]), int(seg["y"])
    t0p = seg["t0"] - pb
    t1p = seg["t1"] + pa
    if max_t is not None:
        t1p = min(t1p, max_t - 0.5)
    stopped_head = 0.0
    stop = threading.Event()
    wav_path = out_path.with_suffix(".audio.wav")
    t_audio0 = time.time()              # wav starts here — trimmed in mux
    audio = threading.Thread(target=capture_loopback,
                             args=(stop, wav_path), daemon=True)
    audio.start()

    writer = cv2.VideoWriter(str(out_path), cv2.VideoWriter_fourcc(
        *"mp4v"), fps, (1920, 1080))
    if not writer.isOpened():
        stop.set()
        sys.exit("VideoWriter failed to open")

    game.focus_window()
    time.sleep(0.3)

    def at_end():
        return game.locate_anchor("leave_map_button") is not None

    def running(window_s=4.0):
        """True once module rows show game time ADVANCING.

        Sample rows (vill_pop/stats/...) arrive every 1 game-s (~0.6 wall-s
        at NORMAL); ticks only every ~10 game-s — too sparse for a short
        window at NORMAL. Constant-t ticks while paused fail the distinct
        test, so a frozen game still reads as not-running."""
        seen = -1.0
        end = time.time() + window_s
        while time.time() < end:
            for r in drive.read_now(handle):
                g = r.get("time")
                if not g and r.get("type") == "tick":
                    g = r.get("t")
                if not g:
                    continue
                g = float(g)
                if g > seen + 0.5:
                    if seen >= 0:
                        return True   # second distinct value: advancing
                    seen = g
        return False

    if at_end():
        game.save_debug("end_before_take")
        log("  end screen already up before the take; skipping segment")
        writer.release()
        stop.set()
        audio.join(timeout=5)
        return 0.0, 0

    # The game should already be RUNNING (no-pause flow); the toggle
    # loop only fires when it is actually frozen. F3 on a running game
    # would PAUSE it and eat the window (cost a take once).
    unpaused = running(1.5)
    for toggle in range(3):
        if unpaused or at_end():
            break
        game.pyautogui.press("f3")       # settle paused it; a failed
        time.sleep(0.4)                  # pause means this pauses first
        unpaused = running()
    if unpaused:
        # the settle already landed NORMAL while running (verified
        # engine-side at 1.69x); re-send anyway — harmless if applied,
        # curative if the settle speedName path was skipped. The camera
        # is re-issued here too unless panning: the pan glide owns the
        # camera until the window starts.
        drive.send(handle, {"action": "set_speed", "speedName": "NORMAL"})
        if pan is None:
            drive.send(handle, {"action": "camera", "x": x, "y": y})
        drive.read_now(handle)
    if not unpaused:
        game.save_debug("never_unpaused")
        log("  no advancing module rows after F3; skipping segment")
        writer.release()
        stop.set()
        audio.join(timeout=5)
        return 0.0, 0
    drive.read_now(handle)              # drain leftovers

    pan_x0, pan_y0, pan_from_t, pan_until = (pan if pan
                                             else (0.0, 0.0, 0.0, -1.0))
    if pan:
        drive.send(handle, {"action": "camera", "x": pan_x0, "y": pan_y0})
    # wall-clock glide: the old version interpolated from game-time rows
    # (1 Hz) -> ~6 discrete camera jumps = stutter. Wall-driven at 30 Hz
    # (handleCommands drains all pending msgs every engine Update) the
    # steps are small -> visually smooth.
    pan_wall = max(0.3, (pan_until - pan_from_t) / 1.7)
    pan_t0 = None
    pan_done = pan is None
    last_pan = 0.0

    t_cur = 0.0
    last_advance = time.time()
    frames = 0
    t_vid0 = None                       # wall time of first/last frame
    t_vid1 = None
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
            if not pan_done and time.time() - last_pan >= 0.033:
                now = time.time()
                if pan_t0 is None:
                    pan_t0 = now
                f = (now - pan_t0) / pan_wall
                if t_cur >= pan_until:
                    f = 1.0             # window reached: snap to target
                f = min(1.0, max(0.0, f))
                drive.send(handle, {"action": "camera",
                                    "x": round(pan_x0 + (x - pan_x0) * f, 2),
                                    "y": round(pan_y0 + (y - pan_y0) * f, 2)})
                last_pan = now
                if f >= 1.0:
                    pan_done = True
            if time.time() - last_advance > STALL_S:
                log(f"record: time stalled at t={t_cur:.0f} (replay may "
                    f"have ended); stopping segment early")
                break
            if frames % 15 == 0 and \
                    game.locate_anchor("leave_map_button") is not None:
                log("record: replay end screen reached; stopping segment")
                break
            next_frame += frame_interval
            if next_frame < time.time():
                next_frame = time.time()
            img = np.array(sct.grab(mon))
            writer.write(cv2.cvtColor(img, cv2.COLOR_BGRA2BGR))
            if t_vid0 is None:
                t_vid0 = time.time()
            t_vid1 = time.time()
            frames += 1
            sleep = next_frame - time.time()
            if sleep > 0:
                time.sleep(sleep)
    writer.release()
    stop.set()
    audio.join(timeout=5)

    stopped_head = t_cur
    if frames < 10:
        log(f"  take produced only {frames} frames; removing junk clip")
        out_path.unlink(missing_ok=True)
        out_path.with_suffix(".audio.wav").unlink(missing_ok=True)
        return stopped_head, frames
    log(f"segment {out_path.name}: {frames} frames, done t={t_cur:.1f}")
    if mux_queue is not None and t_vid0 is not None:
        # trim the wav to the video's exact wall span (armed early, joined
        # late — otherwise the tail is a frozen frame over trailing audio)
        mux_queue.append((out_path, wav_path, t_vid0 - t_audio0,
                          t_vid1 - t_vid0))
    return stopped_head, frames


def wake(handle, timeout=25):
    """Unpause a frozen module: F3 until ticks flow again.

    Blind F3 presses are fragile (a miss leaves the game paused and a
    double restarts the tail unbaited); instead toggle F3 and check
    after each press whether the module emits ticks. Ends unpaused.
    """
    end = time.time() + timeout
    while time.time() < end:
        game.focus_window()
        time.sleep(0.3)
        game.pyautogui.press("f3")
        time.sleep(0.5)
        rows = list(drive.read_for(handle, 2.0, quiet=True))
        if any(r.get("type") == "tick" for r in rows):
            return True
    return False


def wait_first_tick(handle, timeout_s=120):
    """Block until the module actually streams ticks (pipe alive)."""
    end = time.time() + timeout_s
    while time.time() < end:
        rows = drive.read_for(handle, 3, quiet=True)
        if any(r.get("type") == "tick" for r in rows):
            return True
    return False


def record_plan(handle, plan, out_dir, fps, segs=None, reuse=False):
    all_segs = plan["segments"]
    if not all_segs:
        log("plan has no segments; nothing to record")
        return
    wanted = set(segs or range(1, len(all_segs) + 1))
    segs = [s for i, s in enumerate(all_segs, 1) if i in wanted]
    out_dir.mkdir(parents=True, exist_ok=True)
    pad = plan.get("pad", 2.0)
    if not reuse:
        # fresh load: the game runs at NORMAL out of the box; race to
        # the first seek target at full speed instead (~12 min saved)
        drive.send(handle, {"action": "set_speed",
                            "speedName": "FASTEST"})
        acks = [r.get("action") for r in drive.read_for(handle, 3,
                                                        quiet=True)]
        log(f"speed ack: {acks or 'NONE (pipe dead?)'}")
        fog_off()
    mux_queue = []
    t_cur_last = None                   # replay head after the last take
    last_cam = None                     # camera tile of the last GOOD take
    close_control_panel()
    for k, seg in enumerate(segs):
        if reuse:
            # externally managed session: may sit paused/frozen. The
            # normal flow leaves the game RUNNING between takes, so no
            # wake there (a blind F3 would pause a running game).
            if not wake(handle):
                log("  module never unfroze; skipping segment")
                continue
        idx_plan = next(j + 1 for j, s in enumerate(all_segs) if s is seg)
        pb = seg.get("pb", pad)
        pa = seg.get("pa", pad)
        t0p = seg["t0"] - pb
        t_head = t_cur_last if t_cur_last is not None else 0.0
        # lead-in: chase this far BEFORE the window; the clip films from
        # the settle point on, so the lead is spare footage that absorbs
        # the ~1-3 game-s between settle and the first captured frame
        LEAD = 10.0
        t_target = max(t0p - LEAD, 1.0)
        x, y = int(seg["x"]), int(seg["y"])
        tag = plan.get("tag") or f"row{plan.get('row', '?')}"
        name = (f"clip_{tag}_{idx_plan:02d}_"
                f"{seg['kind']}_t{int(seg['t0'])}.mp4")
        out_path = out_dir / name
        log(f"segment {k + 1}/{len(segs)} {seg['kind']}: "
            f"t={t0p:.0f}..{seg['t1'] + pa:.0f} tile {x}:{y}")

        def chase_log(t, t_target=t_target):
            log(f"  chase {t:.0f}/{t_target:.0f}" if t >= 0
                else "  replay over while chasing (needs reload)")

        def replay_over():
            return game.locate_anchor("leave_map_button") is not None

        # PAN transition: the previous take just ended close to this
        # window (<=10 game-s gap, <=40 tiles) — keep the game running
        # and film the gap as a camera glide instead of seek-teleporting
        pan = None
        gap = t0p - t_head
        if last_cam is not None and 0 <= gap <= PAN_MAX_GAP \
                and abs(last_cam[0] - x) + abs(last_cam[1] - y) \
                <= PAN_MAX_TILES:
            pan = (last_cam[0], last_cam[1], t_head, t0p)
            log(f"  pan transition: {gap:.1f} game-s gap, "
                f"{abs(last_cam[0] - x) + abs(last_cam[1] - y):.0f} tiles")
        elif t_cur_last is not None and t_target < t_head - 1.0:
            # head already past the target: window ahead -> film onward,
            # window passed -> skip
            if t_head < seg["t1"] + pa - 1.0:
                log(f"  no seek: head at t={t_head:.0f} past target; "
                    f"filming onward")
            else:
                log("  window already behind the replay head; skipping")
                last_cam = None
                continue
        else:
            settled = False
            attempt = 0
            while attempt < 2 and not settled:
                attempt += 1
                drive.read_now(handle)      # stale seek_reached acks must
                # pause=False: the game NEVER stops — settle lands
                # camera+NORMAL while running and keeps moving
                drive.send(handle, {"action": "seek", "t": t_target, "x": x,
                                    "y": y, "pause": False, "camera": True,
                                    "speedName": "NORMAL"})
                res = wait_seek_settled(handle, t_target, max_s=1500,
                                        progress=chase_log,
                                        check_end=replay_over)
                if res is True:
                    settled = True
                    break
                if res == "pipe":
                    log("  pipe went quiet mid-chase; reconnecting")
                    handle = drive.connect(30)
                    if not wake(handle):
                        log("  module still dead after reconnect; skipping")
                        break
                elif res == "end":
                    log("  replay finished during chase; reload needed, "
                        "skipping segment")
                    last_cam = None
                    break
                else:
                    # game keeps running (pause=False); just re-send the
                    # seek — a blind F3 wake here would pause a running
                    # game at chase speed
                    log(f"  seek attempt {attempt} did not settle; retrying")
            if not settled:
                log("  seek never settled; skipping segment")
                last_cam = None
                continue
            if game.locate_anchor("leave_map_button") is not None:
                # settled "True" can still mean the module paused right as
                # the replay transitioned to the end screen (end-of-file
                # events)
                game.save_debug("settled_at_end")
                log("  end screen up after settle; skipping segment")
                last_cam = None
                continue
        t_cur_last, frames = record_segment(
            handle, seg, pb, pa, out_path, fps,
            max_t=plan.get("replay_length"), pan=pan, mux_queue=mux_queue)
        last_cam = (x, y) if frames >= 10 else None
    for out_path, wav_path, audio_ss, audio_span in mux_queue:
        if mux(out_path, wav_path, out_path, audio_ss=audio_ss,
               audio_span=audio_span):
            log(f"muxed {out_path.name}")
        else:
            log(f"mux FAILED for {out_path.name}; video-only kept")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--row", type=int, default=None,
                    help="which replay profile to run (1-9); resolves to a "
                         "touch/copy file ('hl<N> replay copy.aoe2record') "
                         "that is stamped newest so the UI loads it as "
                         "list row 1")
    ap.add_argument("--tag", default=None,
                    help="game-agnostic run tag (e.g. mp1); overrides --row "
                         "for events/plan/clip naming; with --touch + "
                         "--search the target replay is arbitrary")
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
    ap.add_argument("--segs", default=None,
                    help="record pass: comma list of 1-based segment "
                         "indexes (default all)")
    ap.add_argument("--reuse", action="store_true",
                    help="record pass: keep the running session instead of "
                         "restarting the replay (wards off the seek-never-"
                         "retry flow for missed segments)")
    args = ap.parse_args()

    do_collect = args.collect or not (args.collect or args.record)
    do_record = args.record or not (args.collect or args.record)

    if args.tag is None and args.row is None:
        sys.exit("need --row N (profile mode) or --tag T (arbitrary game "
                 "mode with --touch)")
    tag = args.tag or f"row{args.row}"
    events_path = Path(args.events or ROOT / "out" / f"events_{tag}.jsonl")
    plan_path = Path(args.plan or ROOT / "out" / f"events_plan_{tag}.json")
    out_dir = Path(args.out_dir or ROOT / "out" / f"clips_{tag}")

    if do_collect:
        log("== pass 1: collect (replay at full speed)")
        if args.touch is None:
            if args.tag:
                sys.exit("--tag mode needs an explicit --touch replay path")
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
        replay_path = args.touch
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
            # the UI driver sometimes fails while the game still loads;
            # the connect below just waits for the module
            log("WARNING: UI start reported failure; still waiting for "
                "the module pipe")
        time.sleep(4)
        handle = drive.connect(120)
        t_end = collect(handle, events_path, args.max_minutes)
        log(f"events -> {events_path} (last t={t_end})")
        if replay_path:
            ann = subprocess.run(
                [sys.executable, str(HERE / "annotate_resigns.py"),
                 replay_path, str(events_path)],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace")
            log(ann.stdout.strip())
            ann = subprocess.run(
                [sys.executable, str(HERE / "annotate_orders.py"),
                 replay_path, str(events_path)],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace")
            log(ann.stdout.strip())
        plan = build_plan(events_path, args.row, plan_path)
    else:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))

    if do_record and plan.get("segments"):
        plan["tag"] = tag
        log("== pass 2: record segments (replay at normal speed)")
        segs = [int(x) for x in args.segs.split(",")] if args.segs else None
        if args.reuse:
            handle = drive.connect(30)
        else:
            if not start_replay(args.row, args.search):
                log("WARNING: UI start reported failure; still waiting for "
                    "the module pipe")
            time.sleep(4)
            handle = drive.connect(120)
            if not wait_first_tick(handle, 150):
                log("WARNING: module never streamed ticks; pipe may be "
                    "dead (state screenshot will show)")
                game.save_debug("warning_pipe_no_tick")
        record_plan(handle, plan, out_dir, args.fps, segs=segs,
                    reuse=args.reuse)


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
               str(events_path), "--out", str(plan_path)]
    if row is not None:
        sel_cmd += ["--row", str(row)]
    sel = subprocess.run(sel_cmd, capture_output=True, text=True,
                         encoding="utf-8", errors="replace")
    log(sel.stdout.strip())
    if sel.returncode != 0:
        print(sel.stderr, file=sys.stderr)
        # the plan file must always exist: downstream (and run_mp) reads it
        Path(plan_path).write_text(
            json.dumps({"row": row, "segments": []}), encoding="utf-8")
        return {"row": row, "segments": []}
    return json.loads(plan_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
