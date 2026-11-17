"""Live-game probe: does the `hl_nohud` UI mod remove the HUD from replay
frames?

Installs the hl_nohud local mod by editing mod-status.json directly
(aoe2hl.nohud; the direct mod-status write is the #1 live unknown this
probe tests), boots the game, loads a replay and collects evidence:

  out/probe_mod/
    mod_probe_t0.png   first grab after settle
    mod_probe_t1.png   grab 2 s later (world-liveness diff pair)
    verdict.json       timestamp, args, anchor confidence, HUD strip
                       variances, liveness number, mod state, paths

Checks (each reported, none hard-exits):
  - anchor replay_all_visible (spectator panel checkbox) must NOT match
    at 0.86: a match means the mod did not load, or the HUD is back and
    fog_off found its toggle (distinct evidence lines below)
  - fixed HUD strips cropped from the GAME WINDOW rect (not the full
    virtual desktop — this machine has 2x1920 monitors and the game
    sits on the right one): resource bar 600x70 at the window's
    top-left, minimap 400x280 at its bottom-right. Raw variances are
    recorded either way; with --baseline PATH (a WITH-HUD grab) the
    same regions are measured on the baseline too — HUD textures read
    high-variance, world+fog generally lower
  - world liveness: mean absdiff between the t0/t1 center 900x400
    crops of the game window; > 0.5 reads as animating

Flag combinations (documented so they cannot contradict):
  default            enable mod -> verify is_enabled -> boot -> probe
                     -> kill game -> restore mod-status
  --only PANEL.json  restrict the generated mod to ONE panel (value
                     must be in nohud.PANELS): nohud.enable() carries
                     a panels kwarg, so the restricted install is a
                     plain enable with panels=[PANEL.json]
  --keep-mod         mod stays ENABLED at the end (game killed unless
                     --stay-open; restore skipped — quick manual
                     follow-up look)
  --stay-open        with --keep-mod: game + mod both left open. With
                     only --stay-open the game is still killed and the
                     mod restored (restoring mod-status while the game
                     runs would just be rewritten on its exit)
  --no-boot          game already running with CONTROL injected and the
                     replay list reachable (touch/kill/launch/inject
                     skipped; pipe still connected). The mod state was
                     prepared by the caller: is_enabled() is verified
                     and printed (hard exit when False) but the probe
                     never enables/restores; the game is killed at the
                     end unless --stay-open

Usage:
    py -3.12 tools\\auto\\probe_mod.py --replay 211553
    py -3.12 tools\\auto\\probe_mod.py --replay 211553 --only mappanel.json
    py -3.12 tools\\auto\\probe_mod.py --replay 211553 --no-boot
    py -3.12 tools\\auto\\probe_mod.py --replay 211553 --no-boot
        --keep-mod --stay-open
    py -3.12 tools\\auto\\probe_mod.py --replay 211553
        --baseline out\\probe\\hud_before.png

--replay is a case-insensitive sub-string matched against
replays\\*.aoe2record; the same text is typed into the in-game replay
search box (row 1 is loaded; bootstrap touches mtime for row 1).
"""
import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import cv2  # noqa: E402  (same stack gameui already pulls in)
import numpy as np  # noqa: E402
import pywintypes  # noqa: E402

import bootstrap_row as boot  # noqa: E402  (touch/kill/launch/inject reused)
from aoe2hl import capture  # noqa: E402  (wait_first_tick, close_control_panel, fog_off)
from aoe2hl import gameui as game  # noqa: E402
from aoe2hl import paths  # noqa: E402
from aoe2hl import pipe as drive  # noqa: E402
from aoe2hl import replays_ui  # noqa: E402
from aoe2hl import annotators, terrain  # noqa: E402

try:
    from aoe2hl import nohud  # noqa: E402  (being written in parallel; API fixed)
except ImportError as exc:
    sys.exit(f"aoe2hl.nohud not importable yet ({exc}); run this probe "
             "after nohud.py exists")

HUD_THRESHOLD = 0.86   # replay_all_visible must NOT match at/above this
LIVE_THRESHOLD = 0.5   # absdiff mean above this reads as animating


def log(msg):
    print(msg, flush=True)


# --- copied from probe_noui.py (probe tool: deliberate copy, kept in
# --- sync by hand; source: tools/auto/probe_noui.py)

def pick_replay(search):
    """Case-insensitive sub-string match under replays\\ (unique)."""
    cands = sorted((paths.ROOT / "replays").glob("*.aoe2record"))
    hits = [c for c in cands if search.lower() in c.name.lower()]
    if not hits:
        log("no replay matches %r; candidates:" % search)
        for c in cands:
            log("  %s" % c.name)
        sys.exit(f"no replay under replays\\ matches {search!r}")
    if len(hits) > 1:
        log("ambiguous %r; matches:" % search)
        for c in hits:
            log("  %s" % c.name)
        sys.exit(f"refine --replay: {len(hits)} matches for {search!r}")
    log(f"replay: {hits[0].name}")
    return hits[0]


def touch_for_boot(matched):
    """Touch the replay copy the UI list will show: prefer the same file
    name inside the profile savegame (that is what the list scans);
    fall back to the repo copy. Uses bootstrap_row.touch_target itself."""
    base = matched.name
    cand_root = Path.home() / "Games" / "Age of Empires 2 DE"
    for d in cand_root.glob("*/savegame"):
        hit = d / base
        if hit.exists():
            log(f"savegame copy found: {hit}")
            return boot.touch_target(1, str(hit))
    log("no savegame copy with that name; touching the repo file "
        "(the in-game list will only show it if it lives in the "
        "profile savegame)")
    return boot.touch_target(1, str(matched))


def safe_send(handle, payload):
    """pipe.send with the pipe-dead prints pipe.py itself uses."""
    try:
        drive.send(handle, payload)
    except pywintypes.error as exc:
        if exc.winerror in (109, 232, 233):   # 232 = closing as well
            log(f"PIPE DEAD on send: winerror={exc.winerror}")
            return False
        raise
    return True


def overlay_payload(replay_path):
    """Build per-player team/color maps for the in-game overlay."""
    colors = ["#0000FF", "#FF0000", "#00A91B", "#D6D61B",
              "#7BEFF0", "#8A13F7", "#666666", "#FF9205"]
    slot_colors = [None, "#0000FF", "#FF0000", "#D6D61B", "#00A91B",
                   "#7BEFF0", "#8A13F7", "#666666", "#FF9205"]
    try:
        teams = annotators.parse_teams(replay_path)
    except Exception:
        teams = {}
    try:
        players = terrain.player_info(replay_path)
    except Exception:
        players = {}
    team_map, color_map = {}, {}
    for pid, team in teams.items():
        team_map[str(pid)] = team
    if not team_map:
        # If the header omits teams, treat each player as its own team.
        for pid in range(1, 9):
            team_map[str(pid)] = pid
    for pid, info in players.items():
        color_id = info.get("colorId")
        if isinstance(color_id, int) and 0 <= color_id < len(colors):
            color_map[str(pid)] = colors[color_id]
    for pid, color in enumerate(slot_colors):
        if pid and color:
            color_map.setdefault(str(pid), color)
    return {"action": "overlay", "enabled": True,
            "teams": team_map, "colors": color_map}


# --- mod install ------------------------------------------------------


def install_restricted(profile_mods, game_widgetui, panels, mode, solo,
                       channel):
    "--only install path: nohud.enable() carries the panels kwarg."
    return nohud.enable(profile_mods, game_widgetui, panels=panels,
                        mode=mode, solo=solo, channel=channel)


def cleanup(args, profile_mods, backup):
    """finally-block cleanup: kill game FIRST (it rewrites mod-status.json
    on exit!), then restore unless --keep-mod / --no-boot."""
    if args.keep_mod:
        if not args.stay_open:
            boot.kill_game()
        log("mod left ENABLED (--keep-mod); the next probe run kills the "
            "game before re-enabling, or restore mod-status.json by hand")
        return
    boot.kill_game()
    # The game self-registers every local mod folder at boot, even when
    # the caller prepared mod-status.json without our entry. Never leave
    # this generated payload behind for the user's next play session.
    import shutil
    local_payload = Path(profile_mods) / "local" / nohud.MOD_NAME
    if local_payload.exists():
        shutil.rmtree(local_payload)
        log(f"removed generated local payload: {local_payload}")
    # forensic snapshot: the game rewrites mod-status.json on exit -
    # BEFORE restoring our backup, keep what the game itself wrote
    # (Enabled state, checksum handling, entry shape for local mods)
    try:
        snap_dir = Path(args.out) if args.out else (paths.OUT /
                                                    "probe_mod")
        snap_dir.mkdir(parents=True, exist_ok=True)
        stat = Path(profile_mods) / "mod-status.json"
        if stat.is_file():
            (snap_dir / "mod_status_after_game_exit.json"
             ).write_bytes(stat.read_bytes())
            log("post-exit mod-status snapshot saved")
    except Exception as exc:
        log(f"snapshot failed (not fatal): {exc}")
    if args.no_boot:
        log("--no-boot: mod state owned by the caller; no restore run")
        return
    if backup is None:
        log("WARNING: no backup path; mod-status.json NOT restored")
        return
    try:
        nohud.restore(profile_mods, backup)
        state = "?"
        try:
            state = nohud.is_enabled(profile_mods)
        except Exception as exc:
            state = f"is_enabled failed: {exc}"
        log(f"restore done (mod-status.json <- {backup}); "
            f"is_enabled now: {state}")
    except Exception as exc:
        log(f"WARNING: restore failed: {exc}")


# --- evidence helpers --------------------------------------------------


def crop_abs(img, gx0, gy0, gx1, gy1):
    """Crop a global-coords rect out of one dump_screen() image (clamped
    to the image; None when the window rect falls outside the grab)."""
    ox, oy = game.screen_offset()
    h, w = img.shape[:2]
    y0, x0 = max(0, gy0 - oy), max(0, gx0 - ox)
    y1, x1 = min(h, gy1 - oy), min(w, gx1 - ox)
    if y1 <= y0 or x1 <= x0:
        return None
    return img[y0:y1, x0:x1]


def variance(region):
    if region is None:
        return None
    gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
    return round(float(np.var(gray)), 1)


def check_anchor(img, name, threshold):
    """One anchor result dict; never raises into the caller."""
    res = {"anchor": name, "threshold": threshold,
           "matched": None, "raw_conf": None, "error": None}
    try:
        hit = game.find_anchor(img, name, threshold=-1.0)
        res["raw_conf"] = round(hit[2], 3) if hit else None
        res["matched"] = bool(hit) and hit[2] >= threshold
    except Exception as exc:
        res["error"] = f"error: {exc}"
    return res


def strip_rects(wr):
    """HUD strip origins in global screen coords, from the window rect."""
    return {
        "resource_bar_600x70": (wr["left"], wr["top"], 600, 70),
        "minimap_400x280": (wr["right"] - 400, wr["bottom"] - 280, 400, 280),
    }


def world_center_crop(wr):
    """Center 900x400 of the game window in global coords (clamped)."""
    wl = wr["right"] - wr["left"]
    hl = wr["bottom"] - wr["top"]
    cw, ch = min(900, wl), min(400, hl)
    x0 = wr["left"] + (wl - cw) // 2
    y0 = wr["top"] + (hl - ch) // 2
    return x0, y0, x0 + cw, y0 + ch


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay", required=True,
                    help="case-insensitive sub-string of the replay file "
                         "name under replays\\; also typed into the "
                         "in-game replay search box")
    ap.add_argument("--only", default=None,
                    help="restrict the generated mod to a comma list of "
                         "panels (each must be one of nohud.PANELS); "
                         "incremental testing when the full set breaks")
    ap.add_argument("--skip", default=None,
                    help="generate the FULL panel set minus this comma "
                         "list (each must be one of nohud.PANELS); the "
                         "suspect-panel exclusion test")
    ap.add_argument("--mode", choices=["empty", "shrink", "raw", "none"],
                    default="shrink",
                    help="panel transform: 'shrink' collapses ViewPort to "
                         "1x1 (widgets clip away; live-tested default), "
                         "'empty' wipes Widgets ([]; crashes this game "
                         "build at session start — kept for comparison)")
    ap.add_argument("--solo", action="store_true",
                    help="disable all other profile mods for this run; "
                         "only hl_nohud stays active; restore() re-enables "
                         "them after")
    ap.add_argument("--channel", choices=["local", "subscribed"],
                    default="subscribed",
                    help="mod delivery channel; subscribed masquerades as "
                         "a workshop mod (the only widgetui channel that "
                         "survives session start on this build)")
    ap.add_argument("--out", default=None,
                    help="output dir (default out\\probe_mod)")
    ap.add_argument("--baseline", default=None,
                    help="optional WITH-HUD grab (PNG) to compare the "
                         "HUD strip variances against")
    ap.add_argument("--seek-t", type=float, default=120,
                    help="game time to seek to after load (default 120)")
    ap.add_argument("--x", type=float, default=60.0,
                    help="camera x sent WITH the seek (default 60); "
                         "without a target the camera sat in fog")
    ap.add_argument("--y", type=float, default=60.0,
                    help="camera y sent WITH the seek (default 60)")
    ap.add_argument("--stay-open", action="store_true",
                    help="with --keep-mod: leave game + mod both open; "
                         "alone: game still killed, mod still restored")
    ap.add_argument("--no-boot", action="store_true",
                    help="skip touch/kill/launch/inject; game already "
                         "running with CONTROL injected, mod state "
                         "prepared by the caller")
    ap.add_argument("--keep-mod", action="store_true",
                    help="leave the mod-status entry enabled at the end "
                         "(default: kill game then restore)")
    ap.add_argument("--prepared", action="store_true",
                    help="caller already prepared everything: mod folder "
                         "payload, mod-status solo state and the "
                         "mod-status.json.bak backup. The probe skips "
                         "enable() and the is_enabled gate (the game "
                         "self-registers a local/ mod folder at boot). "
                         "Cleanup still restores from the default .bak")
    ap.add_argument("--overlay", action="store_true",
                    help="enable the custom team-villager/minimap overlay "
                         "after seek; save overlay_check.png")
    args = ap.parse_args()

    out_dir = Path(args.out) if args.out else paths.OUT / "probe_mod"
    out_dir.mkdir(parents=True, exist_ok=True)
    log(f"out dir: {out_dir}")

    matched = pick_replay(args.replay)

    # 1. resolve nohud paths (lazy discovery), fail fast
    try:
        profile_mods = Path(nohud.PROFILE_MODS_DIR)
        game_widgetui = Path(nohud.GAME_WIDGETUI)
    except AttributeError as exc:
        sys.exit(f"nohud lazy discovery missing attribute: {exc}")
    if not profile_mods.is_dir():
        sys.exit(f"profile_mods dir missing: {profile_mods}")
    if not game_widgetui.exists():
        sys.exit(f"game_widgetui missing: {game_widgetui}")
    log(f"profile_mods: {profile_mods}")
    log(f"game_widgetui: {game_widgetui}")
    log(f"channel: {args.channel} (mode: {args.mode}, solo: {args.solo})")

    panels_used = list(nohud.PANELS)
    for flag in ("only", "skip"):
        raw = getattr(args, flag)
        if not raw:
            continue
        picked = [q.strip() for q in raw.split(",") if q.strip()]
        bad = [q for q in picked if q not in nohud.PANELS]
        if bad:
            sys.exit(f"--{flag}: {bad} not in nohud.PANELS="
                     f"{list(nohud.PANELS)}")
        if flag == "only":
            panels_used = picked
        else:
            panels_used = [q for q in panels_used if q not in picked]

    backup = None
    enabled_pre = None
    if args.prepared:
        cand = Path(profile_mods) / "mod-status.json.bak"
        if cand.is_file():
            backup = cand
            log(f"prepared mode: caller backup found at {cand}")
        else:
            log("WARNING: prepared mode without a mod-status.json.bak; "
                "cleanup will NOT be able to restore")
    try:
        if not args.no_boot:
            # 2. kill FIRST: the game rewrites mod-status.json on exit,
            #    so the edit must happen while the game is dead
            boot.kill_game()
            # 2b. sanitize post-crash state: the game reads its own
            # Age2CrashReport.txt at boot and then shows the
            # 'mods disabled' prompt + session-disables mods. Park the
            # reports (rename in place, reversible) and kill any
            # late-spawned crash reporter before launching.
            import subprocess
            subprocess.run(["taskkill", "/IM", "BsSndRpt64.exe", "/F"],
                           capture_output=True)
            logs_root = (Path.home() / "Games" /
                         "Age of Empires 2 DE" / "logs")
            parked = 0
            for rep_path in sorted(logs_root.glob(
                    "*/Age2CrashReport.txt")):
                dead = rep_path.with_suffix(".txt.parked")
                rep_path.replace(dead)
                parked += 1
                log(f"crash report parked: {dead.parent.name}/"
                    f"{dead.name}")
            log(f"sanitized: {parked} crash report(s) parked, "
                "BugSplat killed if present")
            if args.prepared:
                # 3. caller prepared the folder + mod-status; the game
                # self-registers mod folders it finds at boot. No entry
                # writes and no gate from us.
                log("prepared mode: skipping enable/gate; the game "
                    "self-registers the local/ mod folder at boot")
                enabled_pre = "prepared(caller)"
            else:
                # 3. install (restricted set for --only AND --skip: both
                # deviate from the full PANELS set)
                if args.only or args.skip:
                    backup = install_restricted(profile_mods,
                                                game_widgetui,
                                                panels_used, args.mode,
                                                args.solo, args.channel)
                else:
                    backup = nohud.enable(profile_mods, game_widgetui,
                                          mode=args.mode, solo=args.solo,
                                          channel=args.channel)
                # 4. verify BEFORE launching
                enabled_pre = nohud.is_enabled(profile_mods)
                log(f"mod enabled after install: {enabled_pre}")
                if not enabled_pre:
                    sys.exit("mod-status entry rejected/refactored: "
                             "is_enabled() is False after enable; NOT "
                             "launching (this is the #1 live unknown — "
                             "inspect mod-status.json and verdict.json)")
            # 5. boot
            touch_for_boot(matched)
            boot.launch_game()
            boot.wait_window()
            boot.inject_and_verify()
        else:
            enabled_pre = nohud.is_enabled(profile_mods)
            log(f"mod enabled (--no-boot, caller prepared): {enabled_pre}")
            if not enabled_pre:
                sys.exit("--no-boot: nohud.is_enabled() is False; enable "
                         "the mod first (or drop --no-boot)")

        # 6. load the replay
        log(f"loading replay (search '{args.replay}', row 1)")
        if not replays_ui.run(1, search=args.replay):
            sys.exit("replays_ui could not load the replay")
        time.sleep(4)

        handle = drive.connect(120)
        ticked = capture.wait_first_tick(handle, timeout_s=120)
        if not ticked:
            log("WARNING: no tick in 120 s; dead-module or dead-game "
                "replay load IS the finding - screenshot, verdict, abort")
            import win32gui
            alive = bool(win32gui.FindWindow(None, game.WINDOW_TITLE))
            log(f"game window present after load: {alive}")
            cv2.imwrite(str(out_dir / "mod_probe_dead.png"),
                        game.dump_screen())
            verdict = {
                "timestamp": datetime.now(timezone.utc)
                                .isoformat(timespec="seconds"),
                "args": vars(args).copy(),
                "mod_enabled_pre_launch": enabled_pre,
                "mode": args.mode,
                "channel": args.channel,
                "overlay": args.overlay,
                "solo": args.solo,
                "panels": panels_used,
                "ticked": False,
                "game_window_present_after_load": alive,
                "dead_screenshot": "mod_probe_dead.png",
            }
            (out_dir / "verdict.json").write_text(
                json.dumps(verdict, indent=2), encoding="utf-8")
            log(f"verdict written: {out_dir / 'verdict.json'} "
                "(cleanup still runs)")
            return

        # seek WITH a camera target (probe_noui flaw fix: its targetless
        # seek left the camera sitting in fog on a dark map)
        log(f"seek to t={args.seek_t} with camera ({args.x},{args.y})")
        safe_send(handle, {"action": "seek", "t": args.seek_t,
                           "pause": False, "x": args.x, "y": args.y})
        state = capture.wait_seek_settled(handle, args.seek_t, max_s=420)
        log(f"seek settled: {state}")
        overlay_enabled = False
        if args.overlay:
            payload = overlay_payload(matched)
            overlay_enabled = safe_send(handle, payload)
            log(f"custom overlay enable sent: {overlay_enabled} "
                f"(teams={len(payload['teams'])}, "
                f"colors={len(payload['colors'])})")
            if overlay_enabled:
                time.sleep(2)
                overlay_path = out_dir / "overlay_check.png"
                cv2.imwrite(str(overlay_path), game.dump_screen())
                log(f"saved {overlay_path}")

        # 7. ~20 s of replay running at its recorded speed: the world
        #    must animate for the liveness diff
        log("waiting 20 s of replay running (world liveness)")
        time.sleep(20)

        # best-effort UI tidying, NOT fatal; with the HUD gone these
        # helpers may find nothing — that itself is evidence
        for name, fn in (("close_control_panel", capture.close_control_panel),
                         ("fog_off", capture.fog_off)):
            try:
                fn()
            except Exception as exc:
                log(f"{name}: best-effort failed (not fatal): {exc}")

        fog_toggle = {"reachable": None, "note": None}
        try:
            fog_toggle["reachable"] = game.hit_anchor("replay_all_visible",
                                                      threshold=0.80)
        except Exception as exc:
            fog_toggle["error"] = f"error: {exc}"
        if fog_toggle["reachable"] is False:
            fog_toggle["note"] = ("All Visible checkbox not found after "
                                  "fog_off: the panel owning the toggle is "
                                  "gone -> consistent with the mod being "
                                  "active (fog may stay ON; world evidence "
                                  "still valid, just darker)")
            log("EVIDENCE: " + fog_toggle["note"])

        # 8. evidence
        t0 = game.dump_screen()
        p0 = out_dir / "mod_probe_t0.png"
        cv2.imwrite(str(p0), t0)
        log(f"saved {p0}")
        time.sleep(2)
        t1 = game.dump_screen()
        p1 = out_dir / "mod_probe_t1.png"
        cv2.imwrite(str(p1), t1)
        log(f"saved {p1}")

        anchor = check_anchor(t0, "replay_all_visible", HUD_THRESHOLD)
        log(f"replay_all_visible: raw_conf={anchor['raw_conf']} "
            f"matched={anchor['matched']}")

        wr = None
        try:
            wr = game.window_rect()
        except Exception as exc:
            log(f"WARNING: window_rect failed (not fatal): {exc}")

        strips = {}
        baseline = None
        if args.baseline:
            baseline = cv2.imread(args.baseline)
            if baseline is None:
                log(f"WARNING: --baseline {args.baseline!r} not readable; "
                    "variances recorded without comparison")
        if wr is not None:
            for name, (gx0, gy0, sw, sh) in strip_rects(wr).items():
                entry = {"region": [gx0, gy0, sw, sh],
                         "var": variance(crop_abs(t0, gx0, gy0,
                                                  gx0 + sw, gy0 + sh)),
                         "baseline_var": None}
                if baseline is not None:
                    bh, bw = baseline.shape[:2]
                    by0, bx0 = max(0, gy0), max(0, gx0)
                    bstrip = baseline[by0:min(bh, gy0 + sh),
                                      bx0:min(bw, gx0 + sw)]
                    entry["baseline_var"] = variance(
                        bstrip if bstrip.size else None)
                strips[name] = entry
                log(f"strip {name}: var={entry['var']} "
                    f"baseline_var={entry['baseline_var']}")

        liveness = {"absdiff_mean": None, "animating": None, "error": None}
        if wr is not None:
            try:
                x0, y0, x1, y1 = world_center_crop(wr)
                a = cv2.cvtColor(crop_abs(t0, x0, y0, x1, y1),
                                 cv2.COLOR_BGR2GRAY)
                b = cv2.cvtColor(crop_abs(t1, x0, y0, x1, y1),
                                 cv2.COLOR_BGR2GRAY)
                live = round(float(cv2.absdiff(a, b).mean()), 3)
                liveness["absdiff_mean"] = live
                liveness["animating"] = live > LIVE_THRESHOLD
            except Exception as exc:
                liveness["error"] = f"error: {exc}"
        log(f"liveness: absdiff_mean={liveness['absdiff_mean']} "
            f"animating={liveness['animating']}")

        verdict = {
            "timestamp": datetime.now(timezone.utc)
                         .isoformat(timespec="seconds"),
            "args": vars(args),
            "profile_mods": str(profile_mods),
            "game_widgetui": str(game_widgetui),
            "mod_enabled_pre_launch": enabled_pre,
            "mode": args.mode,
            "channel": args.channel,
            "overlay": {"requested": args.overlay,
                        "command_sent": overlay_enabled},
            "solo": args.solo,
            "panels": panels_used,
            "anchors": [anchor],
            "fog_toggle": fog_toggle,
            "strips": strips,
            "liveness": liveness,
            "window_rect": wr,
            "images": [p0.name, p1.name],
            "overlay_check": ("overlay_check.png"
                              if overlay_enabled else None),
            "out_paths": {"t0": str(p0), "t1": str(p1),
                          "verdict": str(out_dir / "verdict.json")},
        }
        vpath = out_dir / "verdict.json"
        vpath.write_text(json.dumps(verdict, indent=2), encoding="utf-8")
        log(f"saved {vpath}")

        log("\n=== verdict (hint only — decide from the PNGs) ===")
        if anchor["error"]:
            log(f"  replay_all_visible: {anchor['error']}")
        elif anchor["matched"]:
            log(f"  replay_all_visible MATCHED (raw_conf="
                f"{anchor['raw_conf']}): mod did not load, or the HUD is "
                f"back and fog_off ran — check the PNGs")
        else:
            log("  replay_all_visible absent at "
                f"{HUD_THRESHOLD}: HUD panel gone (mod loaded?)")
        for name, entry in strips.items():
            if entry["baseline_var"] is not None:
                d = (entry["var"] - entry["baseline_var"]
                     if entry["var"] is not None else None)
                log(f"  {name}: var={entry['var']} vs with-HUD baseline "
                    f"{entry['baseline_var']} (delta {d}) — high variance "
                    f"= HUD texture likely present")
            else:
                log(f"  {name}: var={entry['var']} (no baseline given)")
        if liveness["error"]:
            log(f"  world liveness: {liveness['error']}")
        else:
            state = ("animating" if liveness["animating"]
                     else "frozen/static?")
            log(f"  world liveness: absdiff_mean="
                f"{liveness['absdiff_mean']} -> {state} "
                "(>0.5 reads as animating)")
        if fog_toggle.get("note"):
            log(f"  fog toggle: {fog_toggle['note']}")

    finally:
        cleanup(args, profile_mods, backup)

    log("done")


if __name__ == "__main__":
    main()
