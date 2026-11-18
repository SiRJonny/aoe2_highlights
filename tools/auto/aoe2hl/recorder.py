"""Two-pass highlight recorder orchestration.

Pass 1 (collect): stream module rows at FASTEST to events.jsonl, run the
annotators, build the plan. Pass 2 (record): restart the replay and film
each planned segment (camera pans between close takes, per-player grid
takes for the nomad first-TC event), then mux every take to real time.

Thin CLI: tools/auto/record_events.py.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from aoe2hl import control
from aoe2hl import gameui as game
from aoe2hl import paths
from aoe2hl import pipe as drive
from aoe2hl import planner
from aoe2hl import replays_ui
from aoe2hl import nohud
from aoe2hl.annotators import annotate_orders, annotate_resigns, parse_teams
from aoe2hl import terrain
from aoe2hl.capture import (close_control_panel, collect, fog_off, log,
                            mux, record_segment, wait_first_tick,
                            wait_seek_settled)

PAN_MAX_GAP = 20.0     # max game-s between takes for a pan transition
PAN_MAX_TILES = 40.0   # max tile distance for a pan transition


def start_nohud_replay(row, search, touch):
    """Restart AoE with a prepared local HUD-removal folder, then load replay.

    Returns (pipe_handle, mod_status_backup). If startup fails after prepare,
    restore profile state and remove the generated folder before raising.
    """
    import bootstrap_row as boot

    profile_mods = Path(nohud.PROFILE_MODS_DIR)
    boot.kill_game()
    backup = nohud.prepare(profile_mods, nohud.GAME_WIDGETUI,
                           mode="shrink", solo=True)
    try:
        # A crash report triggers the game's "mods disabled" prompt at boot.
        subprocess.run(["taskkill", "/IM", "BsSndRpt64.exe", "/F"],
                       capture_output=True)
        logs_root = (Path.home() / "Games" /
                     "Age of Empires 2 DE" / "logs")
        for report in logs_root.glob("*/Age2CrashReport.txt"):
            report.replace(report.with_suffix(".txt.parked"))

        boot.touch_target(row or 1, touch)
        boot.launch_game()
        boot.wait_window()
        boot.inject_and_verify()
        if not start_replay(row, search):
            log("WARNING: UI start reported failure; waiting for module")
        time.sleep(4)
        handle = drive.connect(120)
        if not wait_first_tick(handle, 150):
            game.save_debug("no_hud_no_tick")
            raise RuntimeError("no-HUD replay produced no module tick")
        close_control_panel()
        fog_off()
        if game.hit_anchor("replay_all_visible", threshold=0.86):
            game.save_debug("no_hud_verification_failed")
            raise RuntimeError(
                "--no-hud requested, but replay HUD anchor remains visible")
        log("no-HUD verification passed: replay HUD anchor absent")
        return handle, backup
    except BaseException:
        boot.kill_game()
        nohud.restore(profile_mods, backup)
        nohud.remove_payload(profile_mods)
        raise


def overlay_payload(replay_path):
    """Build the module's custom HUD config from a replay header."""
    teams, players = {}, {}
    if replay_path and Path(replay_path).is_file():
        try:
            teams = parse_teams(replay_path)
        except Exception:
            pass
        try:
            players = terrain.player_info(replay_path)
        except Exception:
            pass
    if not teams:
        teams = {pid: pid for pid in range(1, 9)}
    palette = ["#0000FF", "#FF0000", "#00A91B", "#D6D61B",
               "#7BEFF0", "#8A13F7", "#666666", "#FF9205"]
    slot_palette = [None, "#0000FF", "#FF0000", "#D6D61B", "#00A91B",
                    "#7BEFF0", "#8A13F7", "#666666", "#FF9205"]
    colors = {}
    for pid, info in players.items():
        color_id = info.get("colorId")
        if isinstance(color_id, int) and 0 <= color_id < len(palette):
            colors[str(pid)] = palette[color_id]
    for pid in range(1, 9):
        colors.setdefault(str(pid), slot_palette[pid])
    return {"action": "overlay", "enabled": True,
            "teams": {str(pid): team for pid, team in teams.items()},
            "colors": colors}


def resolve_replay_path(row, search, touch):
    """Find replay header source for overlay config, if available."""
    if touch and Path(touch).is_file():
        return Path(touch)
    root = Path.home() / "Games" / "Age of Empires 2 DE"
    files = [p for folder in root.glob("*/savegame")
             for p in folder.glob("*.aoe2record")]
    if search:
        hits = [p for p in files if search.lower() in p.name.lower()]
        if len(hits) == 1:
            return hits[0]
    if row:
        wanted = f"hl{row} replay copy.aoe2record".lower()
        return next((p for p in files if p.name.lower() == wanted), None)
    return None



def reload_session(tag_row=1):
    """Fresh replay load for grid takes: each take covers the SAME game
    span, so every player (the first included) gets the replay restarted.
    The game is PAUSED right after the match starts (CadeRemote Pause in
    --cade mode, else a single F3 — the leftover replay speed would
    otherwise eat the phase's opening while the pipeline arms; the fresh
    load auto-plays at the previous session's speed). The record_segment
    prelude unpauses."""
    if not start_replay(tag_row, None):
        log("  WARNING: grid reload UI start reported failure")
    game.focus_window()
    from aoe2hl import cadeui
    if cadeui.enabled():
        if drive.connect(30) is not None and cadeui.pause(True):
            time.sleep(2)
        else:
            game.pyautogui.press("f3")   # RPC pause failed; F3 fallback
    else:
        game.pyautogui.press("f3")       # pause the just-started match
    time.sleep(4)
    h = drive.connect(120)
    close_control_panel()
    fog_off()
    return h

def record_grid(handle, seg, out_dir, fps, mux_queue, idx_plan, tag,
                max_t=None):
    """Nomad first-TC grid: one take PER PLAYER over the SAME game-time
    span (game start -> last TC done + tail). The takes overlap in game
    time, so EVERY take (the first included) starts from a fresh paused
    reload. The camera is STATIONARY on that player's TC site from the
    start: the villagers walk into frame and build. Returns the
    (possibly reconnected) handle.

    Crash-resume: a part whose final mp4 is already muxed is skipped;
    a part with a survived raw video + audio wav is requeued for its
    deferred mux; junk (no wav) is deleted and re-recorded."""
    import cv2
    players = seg["info"]["players"]
    for k, pl in enumerate(players):
        out_path = out_dir / (f"clip_{tag}_{idx_plan:02d}_first_tc_grid_"
                              f"p{k}_t{int(seg['t0'])}.mp4")
        wav_path = out_path.with_suffix(".audio.wav")
        if out_path.exists():
            if _is_muxed(out_path):
                log(f"grid part {k + 1}/{len(players)}: already muxed; "
                    "skipping")
                continue
            if wav_path.exists():
                span = wav_path.stat().st_size / (48000 * 2 * 2)  # s
                nfr = cv2.VideoCapture(str(out_path)).get(
                    cv2.CAP_PROP_FRAME_COUNT)
                if span > 15 and nfr > 300:
                    # raw take + audio survived the crash: requeue the
                    # deferred mux (audio trimmed at the video start;
                    # the real-time re-stamp uses frames/wall-s)
                    mux_queue.append((out_path, wav_path, 0.0, span,
                                      int(nfr),
                                      seg.get("speedMult", 1.0)))
                    log(f"grid part {k + 1}/{len(players)}: raw take "
                        f"survived ({int(nfr)} frames); requeued mux")
                    continue
            log(f"grid part {k + 1}/{len(players)}: junk from a killed "
                "run; deleting")
            out_path.unlink(missing_ok=True)
            wav_path.unlink(missing_ok=True)
        handle = reload_session()
        if handle is None:
            log("  grid: reload failed; stopping grid takes")
            break
        pseg = dict(seg)
        pseg["x"] = int(pl["tc_x"])
        pseg["y"] = int(pl["tc_y"])
        pseg["info"] = dict(seg["info"])
        log(f"grid part {k + 1}/{len(players)}: player {pl['pid']} "
            f"TC site {pseg['x']}:{pseg['y']}")
        _, frames = record_segment(
            handle, pseg, seg.get("pb", 2.0), seg.get("pa", 2.0),
            out_path, fps, max_t=max_t, mux_queue=mux_queue)
        if frames < 10:
            log("  grid part produced junk; stopping grid takes")
            break
    return handle


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
    tag = plan.get("tag") or f"row{plan.get('row', '?')}"
    t_cur_last = None                   # replay head after the last take
    last_cam = None                     # camera tile of the last GOOD take
    close_control_panel()
    for k, seg in enumerate(segs):
        if seg.get("kind") == "first_tc_grid":
            # nomad grid: per-player takes with reloads, managed inside
            handle = record_grid(handle, seg, out_dir, fps, mux_queue,
                                 next(j + 1 for j, s in enumerate(all_segs)
                                      if s is seg),
                                 tag, max_t=plan.get("replay_length"))
            t_cur_last = None
            last_cam = None
            continue
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
                # the game keeps running after the take at NORMAL: bump to
                # FASTEST for the chase (applies while running, verified),
                # settle lands NORMAL again at the target
                drive.send(handle, {"action": "set_speed",
                                    "speedName": "FASTEST"})
                # pause=False: the game NEVER stops — settle lands
                # camera+NORMAL while running and keeps moving
                speed = seg.get("speedName") or "NORMAL"
                drive.send(handle, {"action": "seek", "t": t_target, "x": x,
                                    "y": y, "pause": False, "camera": True,
                                    "speedName": speed})
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
    for out_path, wav_path, audio_ss, audio_span, frames, mult in mux_queue:
        if mux(out_path, wav_path, out_path, audio_ss=audio_ss,
               audio_span=audio_span, frames=frames, speed_mult=mult):
            log(f"muxed {out_path.name}")
        else:
            log(f"mux FAILED for {out_path.name}; video-only kept")
    return handle


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
    ap.add_argument("--no-hud", action="store_true",
                    help="record pass only: restart with optional HUD-removal "
                         "mod; restore mods and remove payload after clips")
    ap.add_argument("--overlay", action="store_true",
                    help="record pass only: draw custom team counts and "
                         "terrain minimap in the captured game frames")
    ap.add_argument("--cade", action="store_true",
                    help="drive the game's built-in CadeRemote gRPC API "
                         "(port 4341): replay load/pause/fog via RPC "
                         "instead of pyautogui; falls back to UI driving "
                         "on RPC failure")
    args = ap.parse_args()

    do_collect = args.collect or not (args.collect or args.record)
    do_record = args.record or not (args.collect or args.record)
    if args.no_hud and args.reuse:
        sys.exit("--no-hud cannot combine with --reuse; the game must reboot "
                 "to load the HUD-removal mod")

    from aoe2hl import cadeui
    cadeui.configure(args.cade, resolve_replay_path(args.row, args.search,
                                                    args.touch))
    if args.cade:
        log(f"cade mode: CadeRemote control enabled "
            f"(replay={cadeui.replay_path()})")

    if args.tag is None and args.row is None:
        sys.exit("need --row N (profile mode) or --tag T (arbitrary game "
                 "mode with --touch)")
    tag = args.tag or f"row{args.row}"
    events_path = Path(args.events or paths.events_path(tag))
    plan_path = Path(args.plan or paths.plan_path(tag))
    out_dir = Path(args.out_dir or paths.clips_dir(tag))

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
            control.inject()
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
            annotate_resigns(replay_path, str(events_path))
            annotate_orders(replay_path, str(events_path))
        plan = build_plan(events_path, args.row, plan_path)
    else:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))

    if args.no_hud and not do_record:
        log("--no-hud ignored: no record pass requested")
    if do_record and plan.get("segments"):
        plan["tag"] = tag
        log("== pass 2: record segments (replay at normal speed)")
        segs = [int(x) for x in args.segs.split(",")] if args.segs else None
        backup = None
        handle = None
        try:
            if args.no_hud:
                handle, backup = start_nohud_replay(
                    args.row, args.search, args.touch)
            elif args.reuse:
                handle = drive.connect(30)
            else:
                if not start_replay(args.row, args.search):
                    log("WARNING: UI start reported failure; still waiting "
                        "for the module pipe")
                time.sleep(4)
                handle = drive.connect(120)
                if not wait_first_tick(handle, 150):
                    log("WARNING: module never streamed ticks; pipe may be "
                        "dead (state screenshot will show)")
                    game.save_debug("warning_pipe_no_tick")
            if args.overlay:
                replay_source = resolve_replay_path(
                    args.row, args.search, args.touch)
                payload = overlay_payload(replay_source)
                drive.send(handle, payload)
                log(f"custom overlay enabled for recording "
                    f"(teams={len(payload['teams'])})")
            handle = record_plan(handle, plan, out_dir, args.fps, segs=segs,
                                 reuse=args.reuse)
        finally:
            if args.overlay and handle is not None:
                try:
                    drive.send(handle, {"action": "overlay",
                                        "enabled": False})
                    log("custom overlay disabled after recording")
                except Exception as exc:
                    log(f"could not disable custom overlay: {exc}")
            if args.no_hud:
                import bootstrap_row as boot
                boot.kill_game()
                if backup is not None:
                    nohud.restore(nohud.PROFILE_MODS_DIR, backup)
                    nohud.remove_payload(nohud.PROFILE_MODS_DIR)



def start_replay(row, search):
    """Load the replay through CadeRemote (--cade) or the UI; True on success."""
    from aoe2hl import cadeui
    if cadeui.enabled():
        path = cadeui.replay_path()
        if path and cadeui.load_replay(path):
            return True
        log("WARNING: cade replay load failed; falling back to UI driving")
    return replays_ui.run(1, search)


def build_plan(events_path, row, plan_path):
    """Select priority events -> plan json (aoe2hl.planner)."""
    return planner.build(events_path, row, plan_path)


if __name__ == "__main__":
    main()
