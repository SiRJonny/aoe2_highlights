"""Local mod `hl_nohud`: hides the in-game HUD for replay capture.

BYTE-SURGICAL panel transforms. The engine's parser rejects our previous
json.load/json.dump round-trip (LF-only, 1-space indent): the game's own
widgetui files — and proven-working subscribed mods — use CRLF line
endings + 2-space indent, and re-serialized payloads crash the game at
replay-session start. generate_mod therefore no longer re-serializes: it
reads the raw file text and edits only the targeted snippet; every other
byte (CRLF endings, indent, key order, BOM) stays identical to the game
original. The result is validated with json.loads but the PARSED data is
never written back.

Modes (MODES; byte-level on the raw text):
    "empty"  collection-level Widgets array becomes [] in the raw text.
             NOTE: this payload shape crashes this game build at session
             start; kept only for regression comparison.
    "shrink" collection-level ViewPort's xorigin/yorigin become 0 and
              width/height become 1 (alignment etc. untouched) so widgets
              clip to nothing. Live-tested. A commandpanel variant that
              kept only ReplayButtons crashed at replay session start;
              default shrink preserves all widget structure.
    "raw"    unchanged byte copy of the game file (control experiment).
    "none"   control mode: info.json only, no widgetui payload.
    transform_collection() keeps the older dict-level API for programmatic
    callers/tests; generate_mod does NOT use it anymore.
    enable(solo=True) also disables every other profile mod for the run
    (hl_nohud is the only active mod); restore() re-enables them.
    prepare() is the production path: game self-registers a local folder
    at boot. Hand-writing a local mod-status entry with widgetui payload
    crashes replay-session start on build 101.103.57257; the game's own
    registration of that same local folder works. Fake subscribed entries
    are dropped by the game.

Pure file I/O: generates the payload under <profile mods>/local/hl_nohud
or <profile mods>/subscribed/999999_hl_nohud (transformed copies of
the game's widgetui/<panel>.json files) and upserts an entry into the
profile's mod-status.json. A byte copy of the ORIGINAL mod-status.json is
kept next to it as mod-status.json.bak on first enable; restore() puts it
back. Never launches or clicks the game.

Panel JSON layout (game install widgetui/*.json):
    {"Collection": {"Name", "ViewPort", "Widgets": [{"Widget": {...}}]}}
mod-status.json layout:
    {"Mods": [{CheckSum, Enabled, LastUpdate(str), Path, Priority, PublishID,
               Title, WorkshopID}, ...], "Unsub": []}
"""
from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

MOD_NAME = "hl_nohud"
MOD_PATH = f"local//{MOD_NAME}"

# Delivery channels. "local" crashed replay-session start on build
# 101.103.57257 even with a byte-identical widgetui payload, so the
# default delivery now masquerades as a subscribed workshop mod.
CHANNELS = ("local", "subscribed")
SUBSCRIBED_ID = 999999


def _mod_path(channel: str) -> str:
    """mod-status.json Path value for `channel` (double slashes, game style)."""
    if channel == "local":
        return MOD_PATH
    if channel == "subscribed":
        return f"subscribed//{SUBSCRIBED_ID}_{MOD_NAME}"
    raise ValueError(f"unknown channel {channel!r}; CHANNELS={list(CHANNELS)}")


def _mod_dir(profile_mods: Path, channel: str) -> Path:
    """Payload dir for `channel` under the profile mods dir."""
    if channel == "local":
        return Path(profile_mods) / "local" / MOD_NAME
    if channel == "subscribed":
        return (Path(profile_mods) / "subscribed" /
                f"{SUBSCRIBED_ID}_{MOD_NAME}")
    raise ValueError(f"unknown channel {channel!r}; CHANNELS={list(CHANNELS)}")


# both Path spellings that can be ours (restore/entry match either)
OUR_PATHS = (MOD_PATH, _mod_path("subscribed"))

# Transform modes for the panel JSON. "empty" crashes this game build at
# session start (live-verified); "shrink" is the live-tested alternative.
MODES = ("empty", "shrink", "raw", "none")

# HUD panel files under widgetui/, game-root-relative. NEVER touch
# menupanel.json, dialog*.json, overlaypanel.json (menus/dialogs must work).
PANELS = (
    "scorepanel.json", "resourcepanel.json", "mappanel.json",
    "commandpanel.json", "timerpanel.json", "worldtimerpanel.json",
    "GameMsgPanel.json", "GameNotificationPanel.json",
    "notificationpanel.json", "objectivespanel.json",
    "technologyprogresspanel.json", "wonderpanel.json",
    "decisionspanel.json", "instructionspanel.json",
    "spectatorprogressoverviewpanel.json",
    "spectatortechnologyoverviewpanel.json",
)

_INFO = {
    "Author": "aoe2hl",
    "CacheStatus": 0,
    "Description": ("Recording mod: removes in-game HUD for replay capture. "
                    "Replays only."),
    "Title": MOD_NAME,
}


def log(msg):
    print(msg, flush=True)


def _default_install() -> Path:
    """widgetui dir of the game install; env override AoE2HL_GAME_WIDGETUI."""
    env = os.environ.get("AoE2HL_GAME_WIDGETUI")
    if env:
        return Path(env)
    return Path(r"C:\Program Files (x86)\Steam\steamapps\common\AoE2DE\widgetui")


def _default_profile_mods() -> Path:
    """Profile mods dir with the richest mod-status.json; env AoE2HL_PROFILE_MODS."""
    env = os.environ.get("AoE2HL_PROFILE_MODS")
    if env:
        return Path(env)
    root = Path.home() / "Games" / "Age of Empires 2 DE"
    candidates = sorted(p for p in root.glob("*/mods")
                        if (p / "mod-status.json").is_file())
    best, best_n = None, -1
    for cand in candidates:
        try:
            data = json.loads((cand / "mod-status.json").read_text("utf-8-sig"))
            n = len(data.get("Mods", []))
        except (OSError, ValueError):
            continue
        if n > best_n:
            best, best_n = cand, n
    if best is None:
        raise FileNotFoundError(
            f"no profile mods dir with mod-status.json under {root}; "
            f"candidates: {candidates}")
    return best


def __getattr__(name):
    # lazy globals: discovery must not run at import (import stays side-effect
    # free and safe on boxes without the game)
    if name == "GAME_WIDGETUI":
        return _default_install()
    if name == "PROFILE_MODS_DIR":
        return _default_profile_mods()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def empty_collection(data: dict, name: str = "panel") -> dict:
    """Copy of a widgetui JSON with Collection.Widgets emptied.

    Only that key changes; key order elsewhere is preserved. Raises ValueError
    if the structure is not a HUD panel.
    """
    coll = data.get("Collection")
    if not isinstance(coll, dict):
        raise ValueError(f"{name}: missing 'Collection' "
                         f"(top-level keys: {list(data)})")
    if "Widgets" not in coll:
        raise ValueError(f"{name}: Collection {coll.get('Name')!r} "
                         "has no 'Widgets'")
    out = dict(data)
    out["Collection"] = dict(coll)
    out["Collection"]["Widgets"] = []
    return out


def transform_collection(data: dict, mode: str = "empty",
                         name: str = "panel") -> dict:
    """Apply one of MODES to a widgetui JSON; see module docstring.

    "empty" -> empty_collection(data, name). "shrink" -> deep-enough copy
    with Collection.ViewPort forced to xorigin=0, yorigin=0, width=1,
    height=1 (other ViewPort keys like alignment preserved); everything
    else — including the Widgets list objects — is untouched. Raises
    ValueError naming `name` when the structure does not fit the mode.
    """
    if mode == "empty":
        return empty_collection(data, name)
    if mode != "shrink":
        raise ValueError(f"{name}: unknown mode {mode!r}; MODES={list(MODES)}")
    coll = data.get("Collection")
    if not isinstance(coll, dict):
        raise ValueError(f"{name}: missing 'Collection' "
                         f"(top-level keys: {list(data)})")
    vp = coll.get("ViewPort")
    if not isinstance(vp, dict):
        raise ValueError(f"{name}: Collection {coll.get('Name')!r} "
                         "has no 'ViewPort' (shrink mode)")
    out = dict(data)
    out["Collection"] = dict(coll)
    out["Collection"]["ViewPort"] = dict(vp)
    out["Collection"]["ViewPort"]["xorigin"] = 0
    out["Collection"]["ViewPort"]["yorigin"] = 0
    out["Collection"]["ViewPort"]["width"] = 1
    out["Collection"]["ViewPort"]["height"] = 1
    return out


def _surgical_shrink(raw: str, name: str) -> str:
    """Byte-surgical shrink: ONLY the four scalar numbers inside the
    collection-level ViewPort block change (xorigin/yorigin -> 0,
    width/height -> 1); key spelling, spacing, order and the surrounding
    bytes stay identical. Nested widget ViewPorts come later in the text
    and are never touched (first '"ViewPort"' after '"Collection"' wins).
    Raises ValueError naming `name` when an anchor is not found."""
    c = raw.find('"Collection"')
    if c < 0:
        raise ValueError(f"{name}: no '\"Collection\"' anchor")
    b1 = raw.find("{", c)
    if b1 < 0:
        raise ValueError(f"{name}: no '{{' after '\"Collection\"'")
    v = raw.find('"ViewPort"', b1)
    if v < 0:
        raise ValueError(f"{name}: no collection-level '\"ViewPort\"' anchor")
    b2 = raw.find("{", v)
    if b2 < 0:
        raise ValueError(f"{name}: no '{{' after '\"ViewPort\"'")
    # ViewPort values are scalars (numbers/strings, no nested braces), so
    # the block ends at the next '}' in the raw text.
    e = raw.find("}", b2)
    if e < 0:
        raise ValueError(f"{name}: unterminated ViewPort block")
    block = raw[b2:e + 1]
    for key, val in (("xorigin", "0"), ("yorigin", "0"),
                     ("width", "1"), ("height", "1")):
        block, n = re.subn(rf'("{key}"\s*:\s*)(-?\d+)',
                           lambda m: m.group(1) + val, block, count=1)
        if n != 1:
            raise ValueError(f"{name}: ViewPort has no scalar '{key}'")
    return raw[:b2] + block + raw[e + 1:]


def _walk_elems(text: str, open_idx: int):
    """Escape-aware element walk of the JSON array whose '[' is at
    open_idx. Returns (spans, close_idx): spans = list of (start, end)
    for each top-level element (the objects at depth 0 inside the
    array, end exclusive), close_idx = index of the array's ']'.
    Brackets inside strings (backslash-escape aware) are ignored.
    Raises ValueError via the caller's naming when unterminated."""
    depth, in_str, esc, start = 0, False, False, None
    spans, close = [], -1
    for i in range(open_idx + 1, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch in "[{":
            if depth == 0 and ch == "{":
                start = i
            depth += 1
        elif ch == "]":
            if depth == 0:
                close = i
                break
            depth -= 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                spans.append((start, i + 1))
                start = None
    return spans, close


def _delete_spans(text: str, spans, bad) -> str:
    """Delete element spans `bad` (a subset of `spans`) from `text`,
    right-to-left, eating one adjacent separator each: separator BEFORE
    the element when a previous element exists, else the separator
    AFTER it. Positions of not-yet-deleted (lefter) spans stay valid."""
    out = text
    for s, e in reversed(bad):
        prevs = [pe for ps, pe in spans if pe < s]
        if prevs:
            out = out[:max(prevs)] + out[e:]
        else:
            nxt = [ps for ps, pe in spans if ps > e]
            out = out[:s] + out[nxt[0] if nxt else e:]
    return out


def _surgical_keep_replay(raw: str, name: str) -> str:
    """Byte-surgical replay-bar keep for commandpanel.json: inside the
    collection-level Widgets array keep EXACTLY the top-level elements
    whose span contains '"ReplayButtons"' (the replay bar owning the
    pipeline's AllVisible fog toggle); inside a kept element, its own
    ChildWidgets list keeps only the ReplayButtons child (drops the
    Buttons command grid and the runtime-filled FlightNameLabel).
    ViewPort NOT shrunk here. Implemented as pure span DELETIONS on the
    original text (right-to-left), so every remaining byte - separators,
    indentation, CRLF - is the game's own. Raises ValueError naming
    `name` when anchors are missing or no ReplayButtons element exists."""
    c = raw.find('"Collection"')
    if c < 0:
        raise ValueError(f'{name}: no "Collection" anchor')
    w = raw.find('"Widgets"', c)
    if w < 0:
        raise ValueError(f'{name}: no collection-level "Widgets" anchor')
    b = raw.find("[", w)
    if b < 0:
        raise ValueError(f"{name}: no '[' after the Widgets anchor")
    spans, end_arr = _walk_elems(raw, b)
    if end_arr < 0:
        raise ValueError(f"{name}: unterminated Widgets array")
    kept = [(s, e) for s, e in spans
            if '"ReplayButtons"' in raw[s:e]]
    if not kept:
        raise ValueError(f"{name}: no top-level element contains "
                         "'\"ReplayButtons\"'")
    out = _delete_spans(raw, spans,
                        [sp for sp in spans if sp not in kept])
    # children filter inside the (first) kept element, re-walked on out
    spans2, _close2 = _walk_elems(out, b)
    k2 = next((sp for sp in spans2
               if '"ReplayButtons"' in out[sp[0]:sp[1]]), None)
    if k2 is None:
        raise ValueError(f"{name}: kept element lost ReplayButtons "
                         "after top-level filter")
    cw = out.find('"ChildWidgets"', k2[0])
    if cw < 0 or cw > k2[1]:
        raise ValueError(f"{name}: kept element has no ChildWidgets")
    ab = out.find("[", cw)
    if ab < 0 or ab > k2[1]:
        raise ValueError(f"{name}: no '[' after ChildWidgets in kept "
                         "replay element")
    cspans, _cclose = _walk_elems(out, ab)
    if not cspans:
        raise ValueError(f"{name}: empty ChildWidgets in kept element")
    good = [sp for sp in cspans
            if '"ReplayButtons"' in out[sp[0]:sp[1]]]
    if not good:
        raise ValueError(f"{name}: no ReplayButtons child in kept "
                         "element")
    out = _delete_spans(out, cspans,
                        [sp for sp in cspans if sp not in good])
    return out


def _surgical_empty(raw: str, name: str) -> str:
    """Byte-surgical empty: the collection-level Widgets ARRAY becomes []
    in the raw text (element span replaced by nothing); every byte before
    '"Widgets": [' and after the matching ']' stays identical. The array
    end is found with a bracket counter that ignores brackets inside JSON
    strings (backslash-escape aware), so nested ChildWidgets arrays go
    with the rest. Raises ValueError naming `name` when an anchor is not
    found."""
    c = raw.find('"Collection"')
    if c < 0:
        raise ValueError(f"{name}: no '\"Collection\"' anchor")
    # exact '"Widgets"' only: '"ChildWidgets"' never matches (no leading
    # quote before 'Widgets' inside it)
    w = raw.find('"Widgets"', c)
    if w < 0:
        raise ValueError(f"{name}: no collection-level '\"Widgets\"' anchor")
    b = raw.find("[", w)
    if b < 0:
        raise ValueError(f"{name}: no '[' after '\"Widgets\"'")
    depth, in_str, esc, end = 0, False, False, -1
    for i in range(b, len(raw)):
        ch = raw[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
                continue
        elif ch == '"':
            in_str = True
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end < 0:
        raise ValueError(f"{name}: unterminated Widgets array")
    return raw[:b] + "[]" + raw[end + 1:]


def generate_mod(game_widgetui: Path, dest: Path, panels=PANELS,
                 mode: str = "empty") -> list[Path]:
    """Write the local mod payload: dest/widgetui/<panel> BYTE-SURGICAL
    transformed copies (see module docstring; no JSON re-serialization)
    plus dest/info.json. Missing source panels are warned and skipped.
    The transformed text is json.loads-validated and never written when
    it fails to parse (ValueError). Returns the panel files written."""
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}; MODES={list(MODES)}")
    dest = Path(dest)
    if mode == "none":
        # CONTROL EXPERIMENT: enabled entry, ZERO payload. Any previous
        # widgetui files are removed so the folder carries info.json
        # only - separates 'active local entry crashes sessions' from
        # 'widgetui payload via local crashes sessions'.
        import shutil
        shutil.rmtree(dest / "widgetui", ignore_errors=True)
        with open(dest / "info.json", "w", encoding="utf-8",
                  newline="") as f:
            json.dump(_INFO, f, indent=1)
        log(f"{MOD_NAME}: mode none - info.json only, payload removed")
        return []
    (dest / "widgetui").mkdir(parents=True, exist_ok=True)
    written = []
    for panel in panels:
        src = Path(game_widgetui) / panel
        if not src.is_file():
            log(f"warning: {MOD_NAME}: source panel missing, skipped: {src}")
            continue
        if mode == "raw":
            # CONTROL EXPERIMENT: byte-identical copy of the game file
            # (serialization/newlines/encoding exactly as shipped). If
            # even this crashes the session, local widgetui shadowing
            # itself is broken on this build and transforms are
            # irrelevant.
            out = dest / "widgetui" / panel
            out.write_bytes(src.read_bytes())
            written.append(out)
            continue
        # newline="" keeps CRLF as \r\n inside the string (no translation)
        with open(src, encoding="utf-8", newline="") as f:
            raw = f.read()
        if mode == "shrink":
            # Keep the known-good transform for every panel. The
            # commandpanel replay-bar variant parsed as JSON but crashed
            # the game at replay-session start; do not ship it by default.
            result = _surgical_shrink(raw, panel)
        else:
            result = _surgical_empty(raw, panel)
        # validation only: the PARSED data is never written back
        probe = result[1:] if result.startswith("\ufeff") else result
        try:
            json.loads(probe)
        except ValueError as exc:
            raise ValueError(
                f"{panel}: surgical result does not parse: {exc}") from exc
        out = dest / "widgetui" / panel
        with open(out, "w", encoding="utf-8", newline="") as f:
            f.write(result)  # newline="": never translate the line endings
        written.append(out)
    with open(dest / "info.json", "w", encoding="utf-8", newline="") as f:
        json.dump(_INFO, f, indent=1)
    return written


def _read_status(profile_mods: Path) -> dict:
    path = Path(profile_mods) / "mod-status.json"
    if not path.is_file():
        return {"Mods": [], "Unsub": []}
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def _write_status(profile_mods: Path, data: dict) -> None:
    path = Path(profile_mods) / "mod-status.json"
    with open(path, "w", encoding="utf-8", newline="") as f:
        # game writes mod-status.json minified
        json.dump(data, f, separators=(",", ":"))


def enable(profile_mods: Path, game_widgetui: Path | None = None,
           backup: Path | None = None, panels=PANELS, mode: str = "empty",
           solo: bool = False, channel: str = "local") -> Path:
    """Legacy low-level writer. Do not use for production widgetui runs:
    `prepare()` plus game self-registration is the proven safe path.
    Generate the mod payload (see generate_mod) into the `channel` dir,
    then upsert its mod-status entry (Priority above all others, min 25).
    When an entry with the exact channel Path already exists, its
    game-computed CheckSum/LastUpdate are preserved and only
    Enabled/Priority/etc. are updated. With solo=True, every OTHER mod
    entry is set Enabled=False in the same write so ours is the only
    active mod. Backs up the ORIGINAL mod-status.json once. Returns the
    backup path."""
    if channel not in CHANNELS:
        raise ValueError(f"unknown channel {channel!r}; "
                         f"CHANNELS={list(CHANNELS)}")
    if game_widgetui is None:
        game_widgetui = GAME_WIDGETUI
    profile_mods = Path(profile_mods)
    generate_mod(game_widgetui, _mod_dir(profile_mods, channel),
                 panels=panels, mode=mode)

    status_path = profile_mods / "mod-status.json"
    data = _read_status(profile_mods)
    if backup is None:
        backup = profile_mods / "mod-status.json.bak"
    backup = Path(backup)
    if not backup.is_file() and status_path.is_file():
        shutil.copy2(status_path, backup)  # once: keep the true original

    our_path = _mod_path(channel)
    prev = next((m for m in data.get("Mods", [])
                 if m.get("Path") == our_path), None)
    mods = [m for m in data.get("Mods", []) if m.get("Path") not in OUR_PATHS]
    # Priority above every other mod (e.g. outrank Priority 12 resource
    # panel mods); the real profile tops out at 24, so this lands >= 25
    priority = max([int(m.get("Priority", 0)) for m in mods] + [0]) + 1
    if solo:
        for m in mods:
            m["Enabled"] = False
        log(f"{MOD_NAME}: solo: disabled {len(mods)} other mods")
    ours = {"CheckSum": "0", "Enabled": True, "LastUpdate": "0",
            "Path": our_path, "Priority": priority, "PublishID": 0,
            "Title": MOD_NAME,
            "WorkshopID": SUBSCRIBED_ID if channel == "subscribed" else 0}
    # upsert merge: keep the game's CheckSum/LastUpdate when it already
    # computed one for this exact Path (game rejects zeroed values?)
    if prev is not None:
        for key in ("CheckSum", "LastUpdate"):
            if key in prev and prev[key] not in (None, "0"):
                ours[key] = prev[key]
    mods.append(ours)
    data["Mods"] = mods
    _write_status(profile_mods, data)
    log(f"{MOD_NAME}: enabled via {channel} (Priority {priority}), "
        f"backup: {backup if backup.is_file() else '(none)'}")
    return backup


def remove_payload(profile_mods: Path) -> None:
    """Delete only hl_nohud's generated local/subscribed payload folders."""
    profile_mods = Path(profile_mods)
    for folder in (profile_mods / "local" / MOD_NAME,
                   profile_mods / "subscribed" /
                   f"{SUBSCRIBED_ID}_{MOD_NAME}"):
        if folder.exists():
            shutil.rmtree(folder)
            log(f"{MOD_NAME}: removed generated payload {folder}")


def prepare(profile_mods: Path, game_widgetui: Path | None = None,
            panels=PANELS, mode: str = "shrink",
            solo: bool = True) -> Path:
    """Prepare game self-registration, without writing our own mod entry.

    Caller MUST stop AoE2 first. The game discovers local mod folders at
    boot and computes its own checksum; hand-written local entries with
    widgetui payloads crash replay-session startup on the tested build.
    Returns a byte-exact backup for restore after the game exits.
    """
    profile_mods = Path(profile_mods)
    if game_widgetui is None:
        game_widgetui = GAME_WIDGETUI
    status = profile_mods / "mod-status.json"
    if not status.is_file():
        raise FileNotFoundError(f"mod-status.json missing: {status}")
    data = _read_status(profile_mods)
    if any(m.get("Path") in OUR_PATHS for m in data.get("Mods", [])):
        raise RuntimeError("stale hl_nohud mod-status entry; restore before "
                           "preparing a new run")

    backup = profile_mods / "mod-status.json.bak"
    shutil.copy2(status, backup)
    try:
        remove_payload(profile_mods)
        mods = data.get("Mods", [])
        if solo:
            for mod in mods:
                mod["Enabled"] = False
            log(f"{MOD_NAME}: prepared solo state; disabled {len(mods)} mods")
        _write_status(profile_mods, data)
        written = generate_mod(game_widgetui,
                               profile_mods / "local" / MOD_NAME,
                               panels=panels, mode=mode)
        log(f"{MOD_NAME}: prepared local payload ({len(written)} panels); "
            "game will self-register at boot")
        return backup
    except Exception:
        shutil.copy2(backup, status)
        remove_payload(profile_mods)
        raise


def restore(profile_mods: Path, backup: Path | None = None) -> None:
    """Put the backed-up original mod-status.json back; without a backup,
    drop our entry (ANY channel: local OR subscribed Path) in place.
    No-op when there is nothing to undo."""
    profile_mods = Path(profile_mods)
    if backup is None:
        backup = profile_mods / "mod-status.json.bak"
    backup = Path(backup)
    status_path = profile_mods / "mod-status.json"
    if backup.is_file():
        shutil.copy2(backup, status_path)
        log(f"{MOD_NAME}: restored from {backup}")
        return
    if not status_path.is_file():
        return
    data = _read_status(profile_mods)
    mods = [m for m in data.get("Mods", [])
            if m.get("Path") not in OUR_PATHS]
    if len(mods) != len(data.get("Mods", [])):
        data["Mods"] = mods
        _write_status(profile_mods, data)
        log(f"{MOD_NAME}: entry removed (no backup present)")


def entry(profile_mods: Path) -> dict | None:
    """Our current mod-status entry (first match across BOTH channels:
    local OR subscribed Path), or None."""
    for m in _read_status(profile_mods).get("Mods", []):
        if m.get("Path") in OUR_PATHS:
            return m
    return None


def is_enabled(profile_mods: Path) -> bool:
    e = entry(profile_mods)
    return bool(e) and e.get("Enabled") is True
