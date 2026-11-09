"""Capture primitives: timeline streaming, screen+audio takes, mux.

The reusable core for anything that watches or films a running AoE2 DE
replay: the highlight recorder and the map-animation spinoff both sit on
this. Connect through aoe2hl.pipe, drive the UI with aoe2hl.gameui /
aoe2hl.replays_ui, then stream rows (collect) or record takes
(record_segment) and mux them to real time (mux).
"""
import json
import os
import subprocess
import threading
import time

import cv2
import numpy as np

from aoe2hl import gameui as game
from aoe2hl import pipe as drive

STALL_S = 20.0         # no module time advance -> collect pass ends
FFMPEG = None          # resolved lazily (imageio_ffmpeg)


def log(msg):
    print(msg, flush=True)


_api12_first = True   # one full read per match, then res-only refreshes
_type_names = {}      # mtid -> name, stashed from the meta row
_tree_alive = set()   # alive forest tiles at the last refresh


def _read_objects_raw(row, positions_path, fh, data=None, read_error=None):
    """Reads the packed object buffer the module exposed with
    GetObjectsPtr() straight from the game process: writes every alive
    object to objects_raw.jsonl next to positions.jsonl and one
    api12_out summary row (census + positions for small-count types)
    into the positions stream. On every read it also emits one fish
    and one res row; the big rows only land on the first read."""
    global _api12_first
    import struct

    from aoe2hl import rawmem
    ptr = int(row.get("ptr") or 0)
    count = int(row.get("count") or 0)
    if ptr <= 0 or count <= 0:
        fh.write(json.dumps({"type": "api12_out", "err": "empty buffer",
                             "ptr": ptr, "count": count}) + "\n")
        return
    if read_error is not None:
        fh.write(json.dumps({"type": "api12_out", "err": read_error,
                             "ptr": ptr, "count": count}) + "\n")
        return
    try:
        if data is None:
            data = rawmem.read_buffer(game.game_pid(), ptr,
                                     count * rawmem.OBJ_SIZE)
        if len(data) != count * rawmem.OBJ_SIZE:
            raise OSError("short object buffer: %d/%d bytes"
                          % (len(data), count * rawmem.OBJ_SIZE))
    except Exception as exc:
        fh.write(json.dumps({"type": "api12_out", "err": str(exc),
                             "ptr": ptr, "count": count}) + "\n")
        return
    objs = rawmem.parse_objects(data)
    raw_path = os.path.join(os.path.dirname(positions_path) or ".",
                            "objects_raw.jsonl")
    # fish/aquatic layer: DEAD_OR_FISH objects - alive=0 forever and
    # invisible to every object query (probes api13-16 on the fishtest
    # and mp5 maps: GetObjectsByType/ByClass/GetObjectsInArea all
    # return 0 for them), so they only exist in this buffer. Known ids:
    # shore (pale dot) 53 Perch / 69 Shore / 1141 Box Turtles;
    # deep white fish icon 450-451 Marlin / 455-458 Dorado, Salmon,
    # Tuna, Snapper / 61 + 621 Dolphin (61 x72 hugging the coast ring,
    # 621 x6 in open water; no runtime enum name for either, whales are
    # NOT on mp5 per the user). Fish deplete when gathered but the
    # buffer carries no resource amount for them.
    FISH_SHORE = {53, 69, 1141}
    FISH_DEEP = {450, 451, 455, 456, 457, 458, 61, 621}
    FISH_WHALE = set()   # whale id unknown yet, none seen
    fish = [[x, y, ut] for oid, ut, x, y, player, alive in objs
            if ut in FISH_SHORE or ut in FISH_DEEP or ut in FISH_WHALE]
    # gold & stone mines: alive gaia objects (66/102). With the module
    # re-sending the buffer pointer periodically (~9 game-seconds), one
    # res row lands per refresh and a depleted pile simply leaves the
    # list.
    gold = [[x, y] for oid, ut, x, y, player, alive in objs
            if ut == 66 and alive]
    stone = [[x, y] for oid, ut, x, y, player, alive in objs
             if ut == 102 and alive]
    fh.write(json.dumps({"type": "res", "time": row.get("time"),
                         "g": gold, "s": stone}) + "\n")
    # forest trees: unit ids whose static name ends in TREE (meta info).
    # One full alive list on the first read, then a delta row per
    # refresh carrying only the spots chopped away since the last read
    # (a depleted pile leaves the buffer; the terrain stays painted, so
    # the client draws a stump mark on each removed tile).
    tree_ids = {int(tid) for tid, nm in _type_names.items()
                if nm.upper().endswith("TREE")}
    alive_trees = {(x, y) for oid, ut, x, y, player, alive in objs
                   if ut in tree_ids and alive}
    if _api12_first:
        _api12_first = False
        _tree_alive.clear()
        _tree_alive.update(alive_trees)
        fh.write(json.dumps({"type": "trees", "time": row.get("time"),
                             "all": sorted(t for t in alive_trees)}) + "\n")
        # big rows only land once per match: full raw dump + census +
        # the one-shot fish row
        with open(raw_path, "w", encoding="utf-8") as rf:
            for oid, ut, x, y, player, alive in objs:
                rf.write(json.dumps([oid, ut, x, y, player, alive]) + "\n")
        hist = {}
        pos = {}
        for oid, ut, x, y, player, alive in objs:
            if not alive:
                continue
            key = "%d:%d" % (player, ut)
            n = hist.get(key, 0) + 1
            hist[key] = n
            if n <= 400:                   # skip the thousands of trees
                pos.setdefault(key, []).append("%d,%d" % (x, y))
        fh.write(json.dumps({"type": "api12_out", "entries": len(objs),
                             "alive": sum(hist.values()), "hist": hist,
                             "pos": pos}) + "\n")
        fh.write(json.dumps({"type": "fish", "time": row.get("time"),
                             "n": len(fish), "pts": fish}) + "\n")
        log("objects_raw: %d entries (%d alive) -> %s; fish spots: %d; "
            "gold: %d, stone: %d, trees: %d"
            % (len(objs), sum(hist.values()), raw_path, len(fish),
               len(gold), len(stone), len(alive_trees)))
    else:
        cut = _tree_alive - alive_trees
        if cut:
            fh.write(json.dumps({"type": "trees", "time": row.get("time"),
                                 "cut": sorted(t for t in cut)}) + "\n")
        _tree_alive.clear()
        _tree_alive.update(alive_trees)



def collect(handle, events_path, max_minutes):
    """Stream module rows at FASTEST to events_path until time stalls."""
    global _api12_first
    _api12_first = True        # fresh positions.jsonl -> fresh full read
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
                if r.get("type") in ("stats", "objects"):
                    continue        # big rows, not needed for selection
                fh.write(json.dumps(r) + "\n")
                kept += 1
            if last_t and last_t > 60 and time.time() - last_advance > STALL_S:
                log(f"collect: time stalled at t={last_t:.0f} -> replay "
                    f"-ended, {kept} rows")
                break
    return last_t


def collect_positions(handle, positions_path, max_minutes):
    """Stream map-animation snapshots at FASTEST to positions_path.

    Requires the module's map_mode ON ({"action":"map_mode"}); keeps the
    meta row (players, map size, type names), the per-sample objects
    snapshots, tc_snap and stats (age/resources/pop/score per player),
    drops everything else. Ends on the same
    20-s no-advance stall as collect (replay ended)."""
    global _api12_first, _type_names, _tree_alive
    _api12_first = True        # fresh positions.jsonl -> fresh full read
    _type_names = {}           # re-stash from a fresh meta row
    _tree_alive = set()
    # NOTE: no pre-read after set_speed — rows arriving in that window
    # (a fresh map_mode's meta + tc_snap) are DATA; the loop below
    # filters acks/ticks by type instead.
    drive.send(handle, {"action": "set_speed", "speedName": "FASTEST"})

    deadline = time.time() + max_minutes * 60
    last_t = None
    last_advance = time.time()
    last_data = time.time()
    kept = 0
    with open(positions_path, "w", encoding="utf-8") as fh:
        def prefetch_raw_buffer(r):
            if r.get("type") != "api12_ptr":
                return
            ptr = int(r.get("ptr") or 0)
            count = int(r.get("count") or 0)
            if ptr <= 0 or count <= 0:
                return
            from aoe2hl import rawmem
            try:
                data = rawmem.read_buffer(game.game_pid(), ptr,
                                          count * rawmem.OBJ_SIZE)
                if len(data) != count * rawmem.OBJ_SIZE:
                    raise OSError("short object buffer: %d/%d bytes"
                                  % (len(data), count * rawmem.OBJ_SIZE))
                r["_raw_buffer"] = data
            except Exception as exc:
                r["_raw_buffer_error"] = str(exc)

        while time.time() < deadline:
            rows = drive.read_for(handle, 5, quiet=True,
                                  on_row=prefetch_raw_buffer)
            for r in rows:
                rt = r.get("type")
                if rt == "api12_ptr":
                    data = r.pop("_raw_buffer", None)
                    error = r.pop("_raw_buffer_error", None)
                    _read_objects_raw(r, positions_path, fh, data, error)
                if rt == "meta" and r.get("typeNames"):
                    _type_names.update(
                        (int(k), v) for k, v in r["typeNames"].items()
                        if k.isdigit())
                if (rt not in ("meta", "objects", "tc_snap", "stats",
                               "scores", "chat", "bld_event", "api12_ptr",
                               "tick")):
                    continue
                t = r.get("time")
                if t and (last_t is None or t > last_t):
                    last_t = t
                    last_advance = time.time()
                # empty Lua tables serialize as {} but the client wants
                # arrays everywhere (b/v/m/pts)
                for k, v in r.items():
                    if v == {}:
                        r[k] = []
                fh.write(json.dumps(r) + "\n")
                kept += 1
                last_data = time.time()
            if time.time() - last_data > 45:
                log(f"collect_positions: pipe dead 45 s (last row "
                    f"t={last_t}) -> giving up")
                break
            if last_t and last_t > 60 and time.time() - last_advance > STALL_S:
                log(f"collect_positions: time stalled at t={last_t:.0f} "
                    f"-> replay ended, {kept} rows")
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


def mux(video_path, wav_path, out_path, audio_ss=None, audio_span=None,
        frames=None, speed_mult=1.0):
    global FFMPEG
    if FFMPEG is None:
        import imageio_ffmpeg
        FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
    tmp = out_path.with_suffix(".merged.mp4")
    # the wav is armed before the video writer and joined after it, so it
    # is longer on both ends — trim it to the video's wall span and cap
    # the output at the shorter stream (else the tail shows a frozen
    # last frame over trailing audio: a ~2 s "pause" at clip end)
    cmd = [FFMPEG, "-y"]
    # real-time correction: the writer stamps frames at a fixed fps, but
    # the capture loop may fall behind (or burst) — without this, long
    # takes PLAY SPED-UP (a 57 game-s castle_build clip came out 7 s
    # long at Normal 1.7). itsscale re-stamps the video onto its true
    # wall span, no re-encode. speed_mult (castle_build) compresses on
    # top: a 3x middle speedup between NORMAL 1.69 and FAST 6.76.
    if frames is not None and audio_span:
        nominal = frames / 30.0
        scale = audio_span / nominal / max(0.1, speed_mult)
        if abs(scale - 1.0) > 0.02:
            cmd += ["-itsscale", f"{scale:.5f}"]
    cmd += ["-i", str(video_path)]
    if audio_ss is not None:
        cmd += ["-ss", f"{max(0.0, audio_ss):.3f}"]
        if audio_span is not None:
            cmd += ["-t", f"{max(0.0, audio_span):.3f}"]
    cmd += ["-i", str(wav_path), "-af",
            f"atempo={max(0.5, min(100.0, speed_mult))}",
            "-c:v", "copy", "-c:a", "aac",
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
    Clicks the 'All Visible' checkbox only when needed. The measured
    panel offsets are game-window-relative, so the window may sit on
    any monitor."""
    import cv2
    wr = game.window_rect()
    mm = game.crop_screen(wr["left"] + 1630, wr["top"] + 900,
                          wr["left"] + 1910, wr["top"] + 1060)
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
    from the previous take's tile — ONE module `pan` command tweens
    SetCameraPosition every Update (Update Interval=0.05 game-s in
    settings.timeline.ini) over from_t..until_t of game time, so close
    segments (<=10 game-s apart) get a smooth pan instead of a jump cut
    and the camera lands on the segment tile exactly at the window.
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
        # the settle already landed the segment speed (NORMAL, or the
        # per-segment override e.g. FAST for castle_build) while running
        # (verified engine-side at 1.69x); re-send anyway — harmless if
        # applied, curative if the settle speedName path was skipped.
        # The camera is re-issued here too unless panning: the pan tween
        # owns the camera until the window starts.
        drive.send(handle, {"action": "set_speed",
                            "speedName": seg.get("speedName") or "NORMAL"})
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
        # engine-side tween: ONE command; the module interpolates
        # SetCameraPosition on EVERY Update (Update Interval=0.05 game-s
        # in settings.timeline.ini -> ~34 Hz wall at NORMAL) and game
        # time drives it, so the pan ends exactly when this window
        # starts. Pipe-driven camera sends batch in the IPC pump and
        # visibly step (pan_experiment.py measured both).
        drive.send(handle, {"action": "pan", "x0": pan_x0, "y0": pan_y0,
                            "x1": x, "y1": y,
                            "seconds": round(pan_until - pan_from_t, 3)})
    # camera follows a villager (nomad first-TC grid): poll the builder's
    # live position (module object_pos) once per wall second and tween
    # the camera over; if the builder died, park on the TC site
    follow_id = (seg.get("info") or {}).get("follow_id")
    last_follow = 0.0
    cam_x, cam_y = float(x), float(y)

    t_cur = 0.0
    last_advance = time.time()
    frames = 0
    t_vid0 = None                       # wall time of first/last frame
    t_vid1 = None
    last_end_check = 0.0                # anchor check throttled to wall
    frame_interval = 1.0 / fps          # time: it grabs a SECOND full
    next_frame = time.time()            # screen and doing it every 15
    with __import__("mss").mss() as sct:  # frames starved the writer ->
        mon = sct.monitors[1]           # the mp4 played sped-up
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
            if time.time() - last_end_check > 2.0:
                last_end_check = time.time()
                if game.locate_anchor("leave_map_button") is not None:
                    log("record: replay end screen reached; stopping "
                        "segment")
                    break
            if follow_id and time.time() - last_follow >= 1.0:
                last_follow = time.time()
                drive.send(handle, {"action": "object_pos",
                                    "id": follow_id})
                arows = drive.read_for(handle, 0.35, quiet=True)
                for r in arows:
                    if r.get("action") != "object_pos":
                        continue
                    if r.get("ok") and r.get("pos"):
                        nx = float(r["pos"]["x"])
                        ny = float(r["pos"]["y"])
                        if abs(nx - cam_x) + abs(ny - cam_y) > 0.3:
                            drive.send(handle, {"action": "pan",
                                                "x0": cam_x, "y0": cam_y,
                                                "x1": nx, "y1": ny,
                                                "seconds": 0.9})
                            cam_x, cam_y = nx, ny
                    else:
                        # builder gone: hold the TC site
                        drive.send(handle, {"action": "camera",
                                            "x": x, "y": y})
                        cam_x, cam_y = float(x), float(y)
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
        # late — otherwise the tail is a frozen frame over trailing
        # audio) and tell the mux the true wall span so it can re-stamp
        # the video to real time (frames/wall-s, not the nominal 30);
        # speedMult (castle_build) compresses the clip at mux time
        mux_queue.append((out_path, wav_path, t_vid0 - t_audio0,
                          t_vid1 - t_vid0, frames,
                          seg.get("speedMult", 1.0)))
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

def wait_seek_settled(handle, t0, max_s=300, progress=None, check_end=None):
    """Wait until the module acks the seek (or a tick passes t0).

    Returns True (settled via ack or tick >= t0), "end" (the replay-over
    screen appeared), "pipe" (stream went quiet >45 s mid-chase) or
    False (max_s elapsed). Tick rows carry the game time under "t"
    (older rows under "time"). Checking a tick passing t0 matters when
    the seek command was dropped: without it this waits out max_s
    while the replay tail plays and the game hits FINISHED REPLAYING
    GAME. At FASTEST the chase from a fresh load to a late t needs
    several minutes; max_s must exceed that."""
    deadline = time.time() + max_s
    seen_t = -1.0
    last_end_check = 0.0
    last_data = time.time()
    while time.time() < deadline:
        rows = drive.read_for(handle, 2, quiet=True)
        if rows:
            last_data = time.time()
        for r in rows:
            if r.get("action") == "seek_reached":
                return True
            if r.get("type") == "tick":
                t = r.get("t") or r.get("time") or 0
                if t >= t0:
                    return True
                if progress and t - seen_t > 120:
                    seen_t = t
                    progress(t)
        if check_end and time.time() - last_end_check > 20:
            last_end_check = time.time()
            if check_end():
                return "end"
        if time.time() - last_data > 45:
            return "pipe"
        time.sleep(0.2)
    return False
def _is_muxed(path):
    """True when the mp4 already carries the muxed audio stream."""
    import subprocess
    import imageio_ffmpeg
    pr = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-i", str(path)],
                        capture_output=True, text=True)
    return any("Audio:" in l for l in pr.stderr.splitlines())

