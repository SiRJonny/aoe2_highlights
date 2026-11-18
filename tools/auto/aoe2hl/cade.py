"""CadeRemote gRPC client for AoE2DE's built-in CaptureAge API server.

The game itself starts a gRPC server on localhost:4341 (TLS). Endpoint,
service and methods verified inside the shipped AoE2DE_s.exe (strings
'localhost:4341', 'Failed to start CaptureAge Api server. Is something
already running on port 4341?' and '/cade_api.rpc.CadeRemote/<Method>'
for Info, Pause, SetFogOfWar, SetPerspective, SetGameSpeed, Frames,
LoadGame, Oracle, AccessFlag, ChangeWindowState, ChangeScreen,
SetDesiredGame).

The public wire format for Info/Pause/SetFogOfWar/SetPerspective/Frames
was reverse engineered by the LibreMatch project
(github.com/librematch/delta-play-replay, AGPL-3.0 - protocol knowledge
only, no code copied here). Method/field numbers for the RPCs that
LibreMatch did not document (SetGameSpeed, LoadGame, AccessFlag, ...)
are probed at runtime; see probe_cade.py.

Certificates under certs/ come from the same public repository: the
game trusts only clients holding the 'cade-client' certificate signed
by 'CaptureAge Root Certificate'.

All calls are local-game operations (replay viewer control). Never use
against online multiplayer sessions.
"""
from __future__ import annotations

import struct
import time
from pathlib import Path

import grpc

HOST_CANDIDATES = ("[::1]:4341", "127.0.0.1:4341")
CERT_DIR = Path(__file__).parent / "certs"
# CN of the game's embedded server certificate (AoE2DE_s.exe 0x3318810)
SERVER_NAME = "ca-game-api"

P = "/cade_api.rpc.CadeRemote/"
INFO = P + "Info"
PAUSE = P + "Pause"
FOG = P + "SetFogOfWar"
PERSP = P + "SetPerspective"
SPEED = P + "SetGameSpeed"
FRAMES = P + "Frames"
LOAD = P + "LoadGame"
ORACLE = P + "Oracle"
ACCESS = P + "AccessFlag"
WINDOW = P + "ChangeWindowState"
SCREEN = P + "ChangeScreen"
DESIRED = P + "SetDesiredGame"

REPLAY_SPEED_NAMES = {0: "SLOW", 1: "NORMAL", 2: "FAST", 3: "FASTEST"}


# ---------------------------------------------------------------- wire ops

def varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _tag(num: int, wt: int) -> bytes:
    return varint((num << 3) | wt)


def pb_int(num: int, v: int) -> bytes:
    return _tag(num, 0) + varint(v)


def pb_bool(num: int, v: bool) -> bytes:
    return _tag(num, 0) + varint(1 if v else 0)


def pb_float(num: int, v: float) -> bytes:
    return _tag(num, 5) + struct.pack("<f", v)


def pb_double(num: int, v: float) -> bytes:
    return _tag(num, 1) + struct.pack("<d", v)


def pb_str(num: int, s: str) -> bytes:
    b = s.encode("utf-8")
    return _tag(num, 2) + varint(len(b)) + b


def pb_bytes(num: int, b: bytes) -> bytes:
    return _tag(num, 2) + varint(len(b)) + b


def pb_msg(num: int, sub: bytes) -> bytes:
    return _tag(num, 2) + varint(len(sub)) + sub


def pb_dump(data: bytes, depth: int = 0, maxdepth: int = 3) -> str:
    """Human-readable dump of a protobuf message with unknown schema."""
    out = []
    i = 0
    pad = "  " * depth
    while i < len(data):
        key = 0
        shift = 0
        while i < len(data):
            b = data[i]
            i += 1
            key |= (b & 0x7F) << shift
            shift += 7
            if not b & 0x80:
                break
        num, wt = key >> 3, key & 7
        if wt == 0:
            v = 0
            shift = 0
            while i < len(data):
                b = data[i]
                i += 1
                v |= (b & 0x7F) << shift
                shift += 7
                if not b & 0x80:
                    break
            out.append(f"{pad}{num}: varint {v}")
        elif wt == 1:
            v = struct.unpack("<d", data[i:i + 8])[0]
            out.append(f"{pad}{num}: double {v}")
            i += 8
        elif wt == 2:
            ln = 0
            shift = 0
            while i < len(data):
                b = data[i]
                i += 1
                ln |= (b & 0x7F) << shift
                shift += 7
                if not b & 0x80:
                    break
            body = data[i:i + ln]
            i += ln
            try:
                txt = body.decode("utf-8")
                printable = all(32 <= ord(c) < 127 or c in "\n\r\t" for c in txt)
            except UnicodeDecodeError:
                printable = False
            if printable and body:
                out.append(f"{pad}{num}: str {txt!r}")
            elif depth < maxdepth and body and _looks_like_message(body):
                out.append(f"{pad}{num}: msg[{ln}] {{")
                out.append(pb_dump(body, depth + 1, maxdepth))
                out.append(f"{pad}}}")
            else:
                out.append(f"{pad}{num}: bytes[{ln}] {body[:24].hex()}{'...' if ln > 24 else ''}")
        elif wt == 5:
            v = struct.unpack("<f", data[i:i + 4])[0]
            out.append(f"{pad}{num}: float {v}")
            i += 4
        else:
            out.append(f"{pad}{num}: wiretype{wt} (abort)")
            break
    return "\n".join(out)


def _looks_like_message(body: bytes) -> bool:
    """Cheap heuristic: first key byte should be a sane tag."""
    if not body:
        return False
    key = body[0]
    if key == 0:
        return False
    num, wt = key >> 3, key & 7
    return 1 <= num <= 64 and wt in (0, 1, 2, 5)


# ------------------------------------------------------------------ client

class Cade:
    """Raw-bytes CadeRemote client (schema-free; responses dumped as fields)."""

    def __init__(self, timeout_connect: float = 8.0, use_client_cert: bool = True):
        self.target = None
        self.channel = None
        ca = (CERT_DIR / "certificate-authority.pem").read_bytes()
        if use_client_cert:
            chain = (CERT_DIR / "cade-client.pem").read_bytes()
            key = (CERT_DIR / "cade-client.key").read_bytes()
            creds = grpc.ssl_channel_credentials(
                root_certificates=ca,
                private_key=key,
                certificate_chain=chain,
            )
        else:
            creds = grpc.ssl_channel_credentials(root_certificates=ca)
        options = [
            ("grpc.ssl_target_name_override", SERVER_NAME),
            ("grpc.default_authority", SERVER_NAME),
            # initial full-state patches arrive as one huge FrameSequence
            ("grpc.max_receive_message_length", 256 * 1024 * 1024),
            ("grpc.max_send_message_length", 16 * 1024 * 1024),
        ]
        last_err = None
        for target in HOST_CANDIDATES:
            ch = grpc.secure_channel(target, creds, options=options)
            try:
                grpc.channel_ready_future(ch).result(timeout=timeout_connect)
                self.target, self.channel = target, ch
                return
            except grpc.FutureTimeoutError as e:
                last_err = e
                ch.close()
        raise ConnectionError(
            f"no CadeRemote endpoint answered on {HOST_CANDIDATES}: {last_err}")

    def close(self):
        if self.channel:
            self.channel.close()
            self.channel = None

    # -- raw ---------------------------------------------------------------
    def unary(self, method: str, payload: bytes = b"", timeout: float = 10.0) -> bytes:
        call = self.channel.unary_unary(method)
        return call(payload, timeout=timeout)

    def stream(self, method: str, payload: bytes = b"", timeout: float | None = None):
        call = self.channel.unary_stream(method)
        return call(payload, timeout=timeout)

    # -- typed helpers (documented part of the protocol) --------------------
    def info(self, timeout: float = 10.0) -> bytes:
        return self.unary(INFO, b"", timeout)

    def pause(self, paused: bool, timeout: float = 10.0) -> bytes:
        return self.unary(PAUSE, pb_bool(1, paused), timeout)

    def set_fog(self, fog_of_war: bool, timeout: float = 10.0) -> bytes:
        return self.unary(FOG, pb_bool(1, fog_of_war), timeout)

    def set_perspective(self, player_id: int, timeout: float = 10.0) -> bytes:
        return self.unary(PERSP, pb_int(1, player_id), timeout)

    def frames_raw(self, request: bytes = b"", timeout: float | None = None):
        call = self.channel.unary_stream(FRAMES)
        return call(request, timeout=timeout)

    # -- probes for the undocumented part -----------------------------------
    def set_game_speed(self, value, encodings: tuple[str, ...] = ("f1_float", "f1_double", "f1_varint"),
                       timeout: float = 10.0) -> dict[str, bytes]:
        """Try plausible encodings for SetGameSpeedRequest (default all).

        Returns {label: response_bytes}. Caller decides which label worked
        by observing the replay (or via a Frames watch around the call).
        """
        out = {}
        builders = {
            "f1_float": lambda v: pb_float(1, float(v)),
            "f1_double": lambda v: pb_double(1, float(v)),
            "f1_varint": lambda v: pb_int(1, int(v)),
        }
        for label in encodings:
            payload = builders[label](value)
            try:
                out[label] = self.unary(SPEED, payload, timeout)
            except grpc.RpcError as e:
                out[label] = f"ERR {e.code().name}: {e.details()}".encode()
        return out

    def load_game_probe(self, kind: str, arg: str = "",
                        timeout: float = 10.0, tag: int | None = None) -> dict[str, bytes]:
        """Probe LoadGameRequest oneof tags.

        kind: 'loadreplay' | 'restart' | 'cancel' | 'spectate'
        The oneof field number is unknown; by default try tags 1..4, or a
        single tag via `tag`. Response is either an error (wrong oneof
        case => parse ok but no-op) or a real state change (verified by
        the caller).
        """
        results = {}
        if kind == "loadreplay":
            sub = pb_str(1, arg)              # LoadReplay.filePath
        elif kind == "spectate":
            sub = pb_int(1, int(arg or "0"))  # SpectateLobby.lobbyId
        elif kind in ("restart", "cancel"):
            sub = b""
        else:
            raise ValueError(kind)
        tags = [tag] if tag else range(1, 5)
        for t in tags:
            payload = pb_msg(t, sub)
            try:
                results[f"oneof{t}"] = self.unary(LOAD, payload, timeout)
            except grpc.RpcError as e:
                results[f"oneof{t}"] = f"ERR {e.code().name}: {e.details()}".encode()
        return results

    def access_flag(self, timeout: float = 10.0) -> bytes:
        return self.unary(ACCESS, b"", timeout)

    # -- convenience ---------------------------------------------------------
    def watch_time(self, seconds: float, request: bytes = b"") -> list[int]:
        """Consume the Frames stream for `seconds`, return the frame times.

        Note: a paused replay emits NO frames; the RPC deadline bounds the
        wait (DeadlineExceeded is swallowed when zero frames arrived).
        """
        times = []
        end = time.time() + seconds
        try:
            for seq in self.frames_raw(request, timeout=seconds + 5):
                for frame in iter_frames(seq):
                    times.append(frame[0])
                if time.time() > end:
                    break
        except (grpc.RpcError,) as e:
            code = e.code()
            if code not in (grpc.StatusCode.CANCELLED, grpc.StatusCode.DEADLINE_EXCEEDED):
                raise
        return times


def iter_frames(seq: bytes):
    """Iterate (time, patch, reverse_patch, n_events, n_commands) frames of a
    serialized FrameSequence without a schema (raw field walk)."""
    for num, wt, val in _fields(seq):
        if num == 1 and wt == 2:  # Frame
            t = 0
            patch = rev = b""
            nev = ncmd = 0
            for fnum, fwt, fval in _fields(val):
                if fnum == 1 and fwt == 0:
                    t = fval
                elif fnum == 2 and fwt == 2:
                    patch = fval
                elif fnum == 4 and fwt == 2:
                    rev = fval
                elif fnum == 3 and fwt == 2:
                    nev += 1
                elif fnum == 6 and fwt == 2:
                    ncmd += 1
            yield t, patch, rev, nev, ncmd


def _fields(data: bytes):
    """Yield (num, wiretype, value) of a protobuf message's top-level fields."""
    i = 0
    n = len(data)
    while i < n:
        key = 0
        shift = 0
        while i < n:
            b = data[i]
            i += 1
            key |= (b & 0x7F) << shift
            shift += 7
            if not b & 0x80:
                break
        num, wt = key >> 3, key & 7
        if wt == 0:
            v = 0
            shift = 0
            while i < n:
                b = data[i]
                i += 1
                v |= (b & 0x7F) << shift
                shift += 7
                if not b & 0x80:
                    break
            yield num, wt, v
        elif wt == 1:
            yield num, wt, data[i:i + 8]
            i += 8
        elif wt == 2:
            ln = 0
            shift = 0
            while i < n:
                b = data[i]
                i += 1
                ln |= (b & 0x7F) << shift
                shift += 7
                if not b & 0x80:
                    break
            yield num, wt, data[i:i + ln]
            i += ln
        elif wt == 5:
            yield num, wt, data[i:i + 4]
            i += 4
        else:
            return
