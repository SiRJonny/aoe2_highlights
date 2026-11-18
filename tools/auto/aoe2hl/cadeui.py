"""CadeRemote helpers for the recorder: client singleton + session state.

`--cade` mode drives the game's built-in CaptureAge gRPC server
(localhost:4341) instead of pyautogui for replay loading, pause, fog and
speed. Falls back to the UI paths whenever an RPC fails, so a game
build without the API (or with the server down) still records.
"""
from __future__ import annotations

import time
from pathlib import Path

from aoe2hl import cade

_state = {"enabled": False, "replay_path": None}
_client: cade.Cade | None = None


def configure(enabled: bool, replay_path: str | None = None) -> None:
    _state["enabled"] = bool(enabled)
    _state["replay_path"] = str(replay_path) if replay_path else None


def enabled() -> bool:
    return _state["enabled"]


def replay_path() -> str | None:
    return _state["replay_path"]


def client() -> cade.Cade:
    """Connected CadeRemote client (lazy, process-lifetime singleton)."""
    global _client
    if _client is None:
        _client = cade.Cade()
    return _client


def close() -> None:
    global _client
    if _client is not None:
        _client.close()
        _client = None


def _log_err(context: str, exc: Exception) -> None:
    print(f"cade: {context} FAILED: {exc}", flush=True)


def load_replay(path: str | Path) -> bool:
    """LoadGame{LoadReplay.filePath}; True on empty (success) response."""
    try:
        r = client().unary(cade.LOAD, cade.pb_msg(2, cade.pb_str(1, str(path))))
    except Exception as exc:
        _log_err("load_replay", exc)
        return False
    ok = r == b""
    print(f"cade: load_replay {Path(path).name} -> "
          f"{'ok' if ok else cade.pb_dump(r)}", flush=True)
    return ok


def restart_replay() -> bool:
    """Reload the configured replay (the fast rewind-to-0 primitive)."""
    path = _state["replay_path"]
    if not path:
        print("cade: restart_replay needs a configured replay path",
              flush=True)
        return False
    return load_replay(path)


def pause(paused: bool) -> bool:
    try:
        r = client().pause(paused)
    except Exception as exc:
        _log_err(f"pause({paused})", exc)
        return False
    ok = bool(r)
    print(f"cade: pause({paused}) -> {cade.pb_dump(r) if r else '(empty)'}",
          flush=True)
    return ok


def fog(reveal: bool) -> bool:
    """SetFogOfWar(False) reveals everything ('All Visible' effect)."""
    try:
        r = client().set_fog(reveal)
    except Exception as exc:
        _log_err(f"fog(reveal={reveal})", exc)
        return False
    ok = bool(r)
    print(f"cade: set_fog(reveal={reveal}) -> "
          f"{cade.pb_dump(r) if r else '(empty)'}", flush=True)
    return ok


def speed(name: str) -> bool:
    """SetGameSpeed(ReplaySpeed.SLOW/NORMAL/FAST/FASTEST)."""
    idx = {"SLOW": 0, "NORMAL": 1, "FAST": 2, "FASTEST": 3}.get(name)
    if idx is None:
        print(f"cade: speed unknown {name}", flush=True)
        return False
    try:
        r = client().unary(cade.SPEED, cade.pb_int(1, idx))
    except Exception as exc:
        _log_err(f"speed({name})", exc)
        return False
    ok = bool(r)
    print(f"cade: set_game_speed({name}) -> "
          f"{cade.pb_dump(r) if r else '(empty)'}", flush=True)
    return ok


def perspective(player_id: int) -> bool:
    try:
        r = client().set_perspective(int(player_id))
    except Exception as exc:
        _log_err(f"perspective({player_id})", exc)
        return False
    ok = bool(r)
    print(f"cade: set_perspective({player_id}) -> "
          f"{cade.pb_dump(r) if r else '(empty)'}", flush=True)
    return ok


def wait_replay_running(timeout_s: float = 90.0) -> bool:
    """Block until the Frames stream emits a tick or timeout.

    A replay loaded via RPC shows a loading screen first; the first
    frame proves the session is live (also works without CONTROL).
    """
    end = time.time() + timeout_s
    while time.time() < end:
        try:
            for seq in client().frames_raw(b"", timeout=10.0):
                if any(True for _ in cade.iter_frames(seq)):
                    return True
                if time.time() > end:
                    break
        except Exception:
            pass
        time.sleep(1.0)
    return False
