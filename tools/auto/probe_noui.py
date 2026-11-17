"""Live-game evidence probe: does the `noui` chat command remove the HUD?

Sends {"action":"chat","text":"noui"} over the AoE2_Highlights pipe
(SendChatMessage, works in replays) while a replay plays, and collects
screen evidence around the toggle:

  out/probe/
    hud_before.png   baseline with HUD up (after seek settle)
    hud_after.png    3 s after the noui chat send
    hud_after2.png   same moment, second grab (parity check)
    hud_late.png     8 s later (chat-message flash should have faded)
    verdict.json     ISO timestamps, args, per-anchor score triples
                     (before/after/late) and the PNG names

Anchor scores come from aoe2hl.gameui.find_anchor on a full-screen grab
(raw confidence, no threshold cut). Anchors used:
  ca_population_indicator  in-game HUD (population indicator)
  replay_all_visible       spectator panel checkbox (HUD panel)
  control_panel            CONTROL overlay — negative control, it must
                           stay visible through a noui toggle
The human decides from verdict.json + the PNGs; the printed labels
(GONE/SAME/AMBIGUOUS) are hints, not conclusions.

Usage:
    py -3.12 tools\\auto\\probe_noui.py --replay 211553
    py -3.12 tools\\auto\\probe_noui.py --replay 211553 --no-boot --stay-open
    py -3.12 tools\\auto\\probe_noui.py --replay 211553 --seek-t 600

--replay is a case-insensitive sub-string matched against
replays\\*.aoe2record; the same text is typed into the in-game replay
search box (row 1 is then loaded, bootstrap touches mtime for row 1).
--no-boot skips touch/kill/launch/inject (game already running with
CONTROL injected); the pipe is still connected. The game is killed at
the end unless --stay-open. A second noui send restores the UI by
default (--no-restore to skip; the system may ignore chat in replays).
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
import pywintypes  # noqa: E402

import bootstrap_row as boot  # noqa: E402  (touch/kill/launch/inject reused)
from aoe2hl import capture  # noqa: E402  (wait_first_tick, wait_seek_settled)
from aoe2hl import gameui as game  # noqa: E402
from aoe2hl import paths  # noqa: E402
from aoe2hl import pipe as drive  # noqa: E402
from aoe2hl import replays_ui  # noqa: E402

# anchors probed for HUD presence; control_panel is the negative control
ANCHORS = ["ca_population_indicator", "replay_all_visible", "control_panel"]

HIGH = 0.80    # score at/below this ... "clearly present" above it
LOW = 0.60     # ... and "clearly gone" below it


def log(msg):
    print(msg, flush=True)


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
        if exc.winerror in (109, 233):
            log(f"PIPE DEAD on send: winerror={exc.winerror}")
            return False
        raise
    return True


def anchor_scores(names):
    """Raw template-match confidence per anchor on one full grab."""
    img = game.dump_screen()
    scores = {}
    for name in names:
        hit = game.find_anchor(img, name, threshold=-1.0)
        scores[name] = round(hit[2], 3) if hit else None
    return scores


def shot(out_dir, name):
    path = out_dir / name
    cv2.imwrite(str(path), game.dump_screen())
    log(f"saved {path}")
    return path.name


def label(before, after, late):
    if before is None:
        return "NEVER-MATCHED"
    late_s = late if late is not None else 0.0
    after_s = after if after is not None else 0.0
    if before >= HIGH and max(after_s, late_s) < LOW:
        return "GONE"
    if after_s >= HIGH and late_s >= HIGH:
        return "SAME"
    return "AMBIGUOUS"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay", required=True,
                    help="case-insensitive sub-string of the replay file "
                         "name under replays\\; also typed into the "
                         "in-game replay search box")
    ap.add_argument("--seek-t", type=float, default=120,
                    help="game time to seek to after load (default 120)")
    ap.add_argument("--out", default=None,
                    help="output dir (default out\\probe)")
    ap.add_argument("--stay-open", action="store_true",
                    help="leave the game running after the probe")
    ap.add_argument("--no-boot", action="store_true",
                    help="skip touch/kill/launch/inject; game already "
                         "running with CONTROL injected")
    ap.add_argument("--no-restore", action="store_true",
                    help="skip the second noui send (default: restore the "
                         "UI; the system may ignore chat in replays)")
    args = ap.parse_args()

    out_dir = Path(args.out) if args.out else paths.OUT / "probe"
    out_dir.mkdir(parents=True, exist_ok=True)
    log(f"out dir: {out_dir}")

    matched = pick_replay(args.replay)

    if not args.no_boot:
        touch_for_boot(matched)
        boot.kill_game()
        boot.launch_game()
        boot.wait_window()
        boot.inject_and_verify()

    log(f"loading replay (search '{args.replay}', row 1)")
    if not replays_ui.run(1, search=args.replay):
        sys.exit("replays_ui could not load the replay")
    time.sleep(4)

    handle = drive.connect(120)
    if not capture.wait_first_tick(handle, timeout_s=120):
        log("WARNING: no tick in 120 s; probing anyway")

    # seek, then settle ~3 s (pause=false: keep the replay RUNNING)
    log(f"seek to t={args.seek_t}")
    safe_send(handle, {"action": "seek", "t": args.seek_t, "pause": False})
    state = capture.wait_seek_settled(handle, args.seek_t, max_s=300,
                                      progress=lambda t: log(f"  chase t={t:.0f}"))
    log(f"seek settled: {state}")
    time.sleep(3)

    log("baseline (HUD should be present):")
    before = anchor_scores(ANCHORS)
    for name, s in before.items():
        log(f"  {name} -> {s}")
    pngs = [shot(out_dir, "hud_before.png")]

    log("sending chat 'noui'")
    safe_send(handle, {"action": "chat", "text": "noui"})
    acks = [r for r in drive.read_for(handle, 5)
            if r.get("action") == "chat"]
    log(f"chat acks: {len(acks)}")
    for r in acks:
        log(f"  {json.dumps(r)}")

    time.sleep(3)
    log("after noui (+3 s):")
    after = anchor_scores(ANCHORS)
    for name, s in after.items():
        log(f"  {name} -> {s}")
    pngs.append(shot(out_dir, "hud_after.png"))
    pngs.append(shot(out_dir, "hud_after2.png"))

    time.sleep(8)
    log("late (+11 s, chat flash faded):")
    late = anchor_scores(ANCHORS)
    for name, s in late.items():
        log(f"  {name} -> {s}")
    pngs.append(shot(out_dir, "hud_late.png"))

    if not args.no_restore:
        log("restoring UI (second noui send)")
        safe_send(handle, {"action": "chat", "text": "noui"})
        drive.read_for(handle, 3)

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    verdict = {
        "timestamp": now,
        "args": vars(args),
        "anchors": ANCHORS,
        "scores": {name: [before[name], after[name], late[name]]
                   for name in ANCHORS},
        "images": pngs,
    }
    vpath = out_dir / "verdict.json"
    vpath.write_text(json.dumps(verdict, indent=2), encoding="utf-8")
    log(f"saved {vpath}")

    log("\n=== verdict (hint only — decide from the PNGs) ===")
    for name in ANCHORS:
        b, a, l = verdict["scores"][name]
        log(f"  {name}: before={b} after={a} late={l} -> "
            f"{label(b, a, l)}")
    log("  chat overlay flash: inspect hud_after.png vs hud_late.png")

    if not args.stay_open:
        boot.kill_game()
    log(f"done: {vpath}")


if __name__ == "__main__":
    main()
